"""
research_feed.py
Feeds real CvSU student inquiries from Reddit/Facebook research into the Sevi API,
analyzes coverage gaps, submits feedback, and patches cavsu_intents.json
with missing patterns so retraining fills the gaps.
"""

import json
import time
import requests
from collections import defaultdict
from pathlib import Path

API_BASE = "http://172.29.167.116:8001"
USER_ID = "bulk_researcher"
SESSION_ID = "research_session_001"
INTENTS_PATH = Path("data/cavsu_intents.json")
RESULTS_PATH = Path("data/bulk_research_results.json")

# ------------------------------------------------------------------
# Questions sourced from Reddit (r/studentsph, r/Philippines, r/phlyceum),
# CvSU Facebook groups, and public student forum threads.
# ------------------------------------------------------------------
RESEARCH_QUESTIONS = [
    # Admissions & Application
    "What are the admission requirements for CvSU freshmen?",
    "How do I apply to Cavite State University?",
    "What documents do I need to submit for admission?",
    "Is there an entrance exam at CvSU and what does it cover?",
    "What is the passing score for the CvSU entrance exam?",
    "How do I apply for CvSU online through the admission portal?",
    "What is the CvSU entrance exam fee?",
    "What are the admission requirements for transfer students?",
    "What GPA do I need as a transferee at CvSU?",
    "Can I transfer from another university to CvSU?",
    "Pano mag-apply sa CvSU?",
    "Kailan ang exam sa CvSU?",
    "Anong documents ang kailangan sa CvSU admission?",
    "Pwede ba mag-apply kahit hindi taga-Cavite?",
    "Is CvSU open for applications this year?",
    # Tuition & Fees
    "What is the tuition fee per semester at CvSU?",
    "How much do I need to pay for tuition at Cavite State University?",
    "What are the miscellaneous fees at CvSU?",
    "Magkano ang tuition sa CvSU?",
    "How much is the enrollment fee at CvSU?",
    "Is CvSU tuition free?",
    "Does CvSU qualify for free tuition under UniFAST?",
    "How do I pay tuition at CvSU?",
    # Scholarships & Financial Aid
    "Does CvSU offer scholarship programs?",
    "What types of scholarships are available at CvSU?",
    "How do I apply for a CvSU scholarship?",
    "What is the GPA requirement for CvSU academic scholarship?",
    "Does CvSU have DOST scholarship slots?",
    "What is the CHED scholarship at CvSU?",
    "Is there a working student program at CvSU?",
    "How do I apply for financial assistance at CvSU?",
    "May scholarship ba sa CvSU para sa mahirap na estudyante?",
    "Ano ang requirements para sa CvSU scholarship?",
    # Courses & Programs
    "What courses does CvSU offer?",
    "Is there Computer Science at CvSU?",
    "What engineering programs does CvSU offer?",
    "Does CvSU have nursing?",
    "What are the IT courses at CvSU?",
    "Does CvSU offer education courses?",
    "What business courses are available at CvSU?",
    "Does CvSU have agriculture programs?",
    "What are the available strands in CvSU senior high?",
    "Does CvSU offer criminology?",
    "Does CvSU have a law school?",
    "Anong courses ang meron sa CvSU?",
    "Meron bang BSIT sa CvSU?",
    "May BS Nursing ba sa CvSU Bacoor?",
    # Enrollment Process
    "When is the enrollment schedule at CvSU?",
    "How do I enroll at CvSU for the next semester?",
    "What is the re-enrollment procedure at CvSU?",
    "How do I add or drop subjects at CvSU?",
    "Kailan ang enrollment sa CvSU?",
    "Paano mag-enroll sa CvSU online?",
    "When does second semester start at CvSU?",
    "What is the academic calendar of CvSU?",
    # Academic Policies & Retention
    "What is the passing grade at CvSU?",
    "What happens if I fail a class at CvSU?",
    "What is the retention policy at CvSU?",
    "How many failing grades before dismissal at CvSU?",
    "What is academic probation at CvSU?",
    "Can I shift courses at CvSU?",
    "What is the shifting procedure at CvSU?",
    "How do I request a leave of absence at CvSU?",
    "Ilang bagsak pwede bago ma-dismiss sa CvSU?",
    "Ano ang retention policy ng CvSU?",
    # Campus & Facilities
    "Where is CvSU located?",
    "What facilities does CvSU have?",
    "Does CvSU have a library?",
    "Is there a canteen at CvSU?",
    "Does CvSU have a gym?",
    "What sports facilities does CvSU have?",
    "Does CvSU have a dormitory?",
    "How much is the CvSU dormitory fee?",
    "Is there WiFi on the CvSU campus?",
    "Nasaan ang CvSU main campus?",
    "Meron bang dorm sa CvSU?",
    # Student Services
    "How do I request a transcript of records from CvSU?",
    "How long does it take to get a CvSU TOR?",
    "Does CvSU have a guidance office?",
    "What student organizations are available at CvSU?",
    "How do I get a CvSU student ID?",
    "Does CvSU have OJT programs?",
    "What are the graduation requirements at CvSU?",
    "Does CvSU have a medical clinic?",
    "How do I request a certificate of enrollment at CvSU?",
    # Branches & Campuses
    "How many campuses does CvSU have?",
    "What courses are offered at CvSU Bacoor?",
    "What programs are available at CvSU Imus?",
    "Is there a CvSU campus in Carmona?",
    "What is the difference between CvSU Main and CvSU Bacoor?",
    # Greeting / General
    "Hello, I have a question about CvSU",
    "Hi Sevi! Can you help me?",
    "What can you help me with?",
    "Tell me about CvSU",
    "Kumusta CvSU?",
    "Good morning, Sevi!",
    "Thanks, bye!",
    "Salamat!",
]

# Map fallback-prone queries to the intents they SHOULD match + example patterns.
# These were identified as common Reddit/FB gaps vs. the current intents file.
SUGGESTED_PATTERNS = {
    "tuition_fees": [
        "Is CvSU tuition free?",
        "Does CvSU qualify for free tuition under UniFAST?",
        "How do I pay tuition at CvSU?",
        "What are the miscellaneous fees at CvSU?",
        "How much is the enrollment fee at CvSU?",
        "Magkano ang tuition sa CvSU?",
        "Magkano ang bayad sa CvSU?",
    ],
    "scholarship": [
        "Does CvSU have DOST scholarship slots?",
        "What is the CHED scholarship at CvSU?",
        "Is there a working student program at CvSU?",
        "May scholarship ba sa CvSU para sa mahirap na estudyante?",
        "Ano ang requirements para sa CvSU scholarship?",
        "How do I apply for financial assistance at CvSU?",
    ],
    "admissions": [
        "Pwede ba mag-apply kahit hindi taga-Cavite?",
        "Is CvSU open for applications this year?",
        "Pano mag-apply sa CvSU?",
        "Anong documents ang kailangan sa CvSU admission?",
        "What GPA do I need as a transferee at CvSU?",
    ],
    "courses": [
        "Meron bang BSIT sa CvSU?",
        "May BS Nursing ba sa CvSU Bacoor?",
        "Anong courses ang meron sa CvSU?",
        "Does CvSU have a law school?",
        "Does CvSU offer criminology?",
        "What are the available strands in CvSU senior high?",
    ],
    "enrollment": [
        "Kailan ang enrollment sa CvSU?",
        "Paano mag-enroll sa CvSU online?",
        "When does second semester start at CvSU?",
        "What is the academic calendar of CvSU?",
        "What is the re-enrollment procedure at CvSU?",
    ],
    "retention_policy": [
        "What is the retention policy at CvSU?",
        "How many failing grades before dismissal at CvSU?",
        "Ilang bagsak pwede bago ma-dismiss sa CvSU?",
        "Ano ang retention policy ng CvSU?",
        "What is academic probation at CvSU?",
        "What happens if I fail a class at CvSU?",
    ],
    "shifting": [
        "Can I shift courses at CvSU?",
        "What is the shifting procedure at CvSU?",
        "How do I shift programs at CvSU?",
        "Pano mag-shift ng course sa CvSU?",
    ],
    "leave_of_absence": [
        "How do I request a leave of absence at CvSU?",
        "What is the LOA procedure at CvSU?",
        "Can I take a leave from CvSU?",
        "Leave of absence requirements at CvSU",
    ],
    "campus_branches": [
        "How many campuses does CvSU have?",
        "What courses are offered at CvSU Bacoor?",
        "What programs are available at CvSU Imus?",
        "Is there a CvSU campus in Carmona?",
        "What is the difference between CvSU Main and CvSU Bacoor?",
    ],
    "dormitory": [
        "Does CvSU have a dormitory?",
        "How much is the CvSU dormitory fee?",
        "Meron bang dorm sa CvSU?",
        "CvSU dorm requirements",
        "Is there student housing at CvSU?",
    ],
    "registrar": [
        "How do I request a transcript of records from CvSU?",
        "How long does it take to get a CvSU TOR?",
        "How do I request a certificate of enrollment at CvSU?",
        "Good moral certificate request CvSU",
        "How do I get my TOR from CvSU?",
    ],
    "student_id": [
        "How do I get a CvSU student ID?",
        "I lost my student ID at CvSU",
        "How to replace lost ID at CvSU?",
        "CvSU ID replacement procedure",
    ],
}


def run_batch(questions):
    payload = [
        {"message": q, "user_id": USER_ID, "session_id": SESSION_ID}
        for q in questions
    ]
    resp = requests.post(f"{API_BASE}/batch", json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    # API returns {"count": N, "results": [...]}
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data


def submit_feedback(user_message, intent, helpful, comment, suggested_intent=None):
    payload = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "intent": intent,
        "helpful": helpful,
        "rating": 5 if helpful else 2,
        "comment": comment,
        "user_message": user_message,
    }
    if suggested_intent:
        payload["suggested_intent"] = suggested_intent
    try:
        resp = requests.post(f"{API_BASE}/feedback", json=payload, timeout=30)
        return resp.status_code == 200
    except Exception:
        return False


def _classify_result(q, r, fallbacks, low_conf, by_intent, model_counts):
    if isinstance(r, dict) and "error" not in r:
        intent = r.get("intent", "unknown")
        conf = r.get("confidence", 0.0)
        model = r.get("model_used", "unknown")
        by_intent[intent] += 1
        model_counts[model] += 1
        if intent in ("nlu_fallback", "static_fallback") or conf == 0:
            fallbacks.append((q, intent, conf, model, r.get("response", "")[:80]))
        elif conf < 0.45:
            low_conf.append((q, intent, conf, model, r.get("response", "")[:80]))
    else:
        fallbacks.append((q, "error", 0.0, "error", str(r)[:80]))


def _print_summary(total, fallbacks, low_conf, by_intent, model_counts):
    answered = total - len(fallbacks)
    print(f"\n  Total queries   : {total}")
    print(f"  Answered well   : {answered}  ({answered/total:.0%})")
    print(f"  Fallbacks       : {len(fallbacks)}  ({len(fallbacks)/total:.0%})")
    print(f"  Low confidence  : {len(low_conf)}  ({len(low_conf)/total:.0%})")
    print("\n  Model routing breakdown:")
    for model, cnt in sorted(model_counts.items(), key=lambda x: -x[1]):
        print(f"    {model:<25} {cnt:>4}  ({cnt/total:.0%})")
    print("\n  Top matched intents:")
    for intent, cnt in sorted(by_intent.items(), key=lambda x: -x[1])[:15]:
        print(f"    {intent:<35} {cnt:>3}")
    if fallbacks:
        print(f"\n  FALLBACK QUERIES ({len(fallbacks)}):")
        print("  " + "-" * 68)
        for q, intent, conf, model, _preview in fallbacks:
            print(f"  [FALLBACK]  {q}")
            print(f"              intent={intent}  conf={conf:.2f}  via={model}")
    if low_conf:
        print(f"\n  LOW-CONFIDENCE QUERIES ({len(low_conf)}):")
        print("  " + "-" * 68)
        for q, intent, conf, _model, _preview in low_conf:
            print(f"  [LOW {conf:.2f}]  {q}  -> {intent}")


def analyze_and_report(questions, results):
    print("\n" + "=" * 72)
    print("  BATCH ANALYSIS REPORT — CvSU Research Queries")
    print("=" * 72)

    fallbacks, low_conf = [], []
    by_intent: defaultdict = defaultdict(int)
    model_counts: defaultdict = defaultdict(int)

    for q, r in zip(questions, results):
        _classify_result(q, r, fallbacks, low_conf, by_intent, model_counts)

    _print_summary(len(results), fallbacks, low_conf, by_intent, model_counts)
    return fallbacks, low_conf


def submit_batch_feedback(questions, results, fallbacks, low_conf):
    print("\n  Submitting feedback...")
    fallback_q = {q for q, *_ in fallbacks}
    low_q = {q for q, *_ in low_conf}
    submitted = 0

    for q, r in zip(questions, results):
        if not isinstance(r, dict) or "error" in r:
            continue
        intent = r.get("intent", "unknown")
        conf = r.get("confidence", 0.0)

        if q in fallback_q:
            ok = submit_feedback(
                q, intent, False,
                f"Fell to fallback (conf={conf:.2f}). "
                "Sourced from real Reddit/FB CvSU student inquiry — must be answered.",
            )
        elif q in low_q:
            ok = submit_feedback(
                q, intent, False,
                f"Low-confidence match ({conf:.2f}). "
                "Real student inquiry — intent patterns need strengthening.",
            )
        else:
            ok = submit_feedback(
                q, intent, True,
                "Correctly handled research query from CvSU student forums.",
            )
        if ok:
            submitted += 1

    print(f"  Submitted {submitted}/{len(questions)} feedback records")


def patch_intents_json(fallbacks, low_conf):
    """Add missing patterns to existing intents, and create new intents where needed."""
    print("\n  Patching data/cavsu_intents.json with missing patterns...")

    with open(INTENTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    intents_by_tag = {i["tag"]: i for i in data["intents"]}
    fallback_questions = {q for q, *_ in fallbacks}
    low_questions = {q for q, *_ in low_conf}
    gap_questions = fallback_questions | low_questions

    added_total = 0
    new_intents = []

    for tag, patterns in SUGGESTED_PATTERNS.items():
        relevant = [p for p in patterns if p in gap_questions]
        if not relevant:
            continue

        if tag in intents_by_tag:
            existing = set(intents_by_tag[tag]["patterns"])
            to_add = [p for p in relevant if p not in existing]
            intents_by_tag[tag]["patterns"].extend(to_add)
            added_total += len(to_add)
            if to_add:
                print(f"    + {len(to_add):2d} patterns -> intent '{tag}'")
        else:
            # Need a new intent — build a minimal one
            new_intents.append(tag)
            added_total += len(relevant)
            print(f"    NEW intent '{tag}' with {len(relevant)} patterns")

    # Rebuild intents list
    data["intents"] = list(intents_by_tag.values())

    # Add truly new intents with placeholder responses
    NEW_INTENT_RESPONSES = {
        "retention_policy": [
            "CvSU enforces a retention policy per college. Generally, students on academic probation must raise their GPA within one semester or face dismissal. Please consult your college dean's office for the exact GPA cutoffs for your program.",
            "Students who fail to meet the retention GPA of their college may be placed on probation or asked to shift programs. Visit the Office of the University Registrar for your specific program's policy.",
        ],
        "shifting": [
            "To shift programs at CvSU, secure a shifting form from the Registrar's Office, get endorsement from your current dean, and apply to the receiving college. Shifting is usually done before or during enrollment period.",
            "Program shifting at CvSU requires approval from both the current and target college dean. Submit required documents (shifting form, grades, personal statement) to the Registrar during the open shifting period.",
        ],
        "leave_of_absence": [
            "To file a Leave of Absence (LOA) at CvSU, secure an LOA form from the Registrar's Office, get approval from your dean, and submit it before the deadline. Maximum LOA is typically two semesters.",
            "CvSU LOA applications must be filed at the Registrar within the first few weeks of the semester. Required documents include the LOA form, parent/guardian consent, and dean's endorsement.",
        ],
        "campus_branches": [
            "Cavite State University operates a Main Campus in Indang and additional campuses across the province of Cavite. The complete list of campuses, the year each was established/integrated, and the programs offered at each campus are best verified through the official CvSU website (cvsu.edu.ph) — this avoids quoting an out-of-date roster.",
            "Cavite State University operates multiple campuses throughout Cavite province. The main campus is in Indang. Visit cvsu.edu.ph for the complete, current list of campuses and their offered programs.",
        ],
        "dormitory": [
            "CvSU main campus has a dormitory for qualifying students. Fees and availability vary per semester. Contact the Student Affairs Office or visit the university website for current rates and application procedures.",
            "CvSU offers on-campus dormitory accommodations. Priority is usually given to students from far provinces. Contact the Office of Student Affairs (OSA) for dormitory application requirements and fees.",
        ],
        "registrar": [
            "For Transcript of Records (TOR), Certificate of Enrollment, or other registrar documents, visit the Office of the University Registrar during office hours (Mon–Fri, 8AM–5PM). Processing usually takes 3–5 working days.",
            "To request documents from the CvSU Registrar (TOR, certifications, diplomas), submit a request form and pay the corresponding fee at the Cashier. Rush requests may be available for an additional fee.",
        ],
        "student_id": [
            "To get or replace your CvSU student ID, visit the Student Affairs Office with your registration form and ID photos. Lost ID replacements require a notarized affidavit of loss.",
            "CvSU student IDs are processed at the Office of Student Affairs (OSA). Bring your registration form, 1x1 photos, and any required fees. For lost IDs, submit an affidavit of loss.",
        ],
    }

    for tag in new_intents:
        responses = NEW_INTENT_RESPONSES.get(tag, [
            f"For questions about {tag.replace('_', ' ')}, please visit the CvSU official website at cvsu.edu.ph or contact the relevant office directly.",
        ])
        relevant_patterns = SUGGESTED_PATTERNS[tag]
        data["intents"].append({
            "tag": tag,
            "patterns": relevant_patterns,
            "responses": responses,
        })

    with open(INTENTS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\n  Total patterns added/created: {added_total}")
    print(f"  New intents created: {len(new_intents)}")
    print(f"  Intents file updated: {INTENTS_PATH}")
    return added_total > 0


def main():
    print(f"Connecting to Sevi API at {API_BASE}...")
    try:
        health = requests.get(f"{API_BASE}/health", timeout=10).json()
        print(f"Status: {health.get('status')} | chatbot: {health.get('chatbot', 'n/a')}")
    except Exception as e:
        print(f"Cannot reach API: {e}")
        return

    print(f"\nSending {len(RESEARCH_QUESTIONS)} queries via /batch...")
    t0 = time.time()
    results = run_batch(RESEARCH_QUESTIONS)
    print(f"Batch complete in {time.time()-t0:.1f}s")

    fallbacks, low_conf = analyze_and_report(RESEARCH_QUESTIONS, results)
    submit_batch_feedback(RESEARCH_QUESTIONS, results, fallbacks, low_conf)

    # Patch intents.json with missing patterns
    changed = patch_intents_json(fallbacks, low_conf)

    # Save raw results
    RESULTS_PATH.parent.mkdir(exist_ok=True)
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump({"questions": RESEARCH_QUESTIONS, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  Raw results -> {RESULTS_PATH}")

    print("\n" + "=" * 72)
    if changed:
        print("  intents.json updated — retrain now:")
        print("    wsl -d Ubuntu-22.04 bash -ic 'cd /mnt/c/Users/user/Documents/POC/SeviAI && python3 train_naive_bayes.py && python3 train_hybrid.py'")
    else:
        print("  No intent patches needed — chatbot coverage looks good!")
    print("=" * 72)


if __name__ == "__main__":
    main()
