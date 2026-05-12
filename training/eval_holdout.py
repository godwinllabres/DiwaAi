"""
In-process held-out evaluation for the SeviAi classifier.

Loads the trained model directly (no HTTP) and runs a curated set of
paraphrased queries that are NOT in the training data — so a high score
on this set means the classifier actually generalizes, not that it
memorized patterns.

Usage:
    python training/eval_holdout.py [--min-accuracy 0.50]

Exit code is 1 when overall accuracy drops below --min-accuracy, so
this can gate CI / pre-deploy.
"""

import argparse
import json
import os
import sys
from collections import defaultdict

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from api.hybrid_chatbot import HybridChatbot  # noqa: E402

# Curated paraphrases — kept apart from data/cavsu_intents.json on purpose.
# These represent the kind of natural-language queries a real user would ask
# and are the gate for "does the model actually work."
HOLDOUT_QUERIES = {
    "greeting": [
        "Hi", "Hello", "Hey", "Good morning", "Howdy",
        "Hey there", "Hello Sevi", "Hi there",
    ],
    "goodbye": [
        "Bye", "Goodbye", "See you later", "Take care", "Bye bye",
        "See you", "Done", "Until later",
    ],
    "thanks": [
        "Thanks", "Thank you", "Thanks a lot", "Appreciate it",
        "Salamat", "Thank you so much", "Thanks for helping",
    ],
    "admissions_requirements": [
        "What are admission requirements?", "How do I apply?",
        "What documents needed?", "CvSU admission process",
        "Requirements to enroll as a freshman", "How to get admitted?",
        "Admission checklist for transferees", "Mga requirements para sumali sa CvSU",
    ],
    "admissions_exam": [
        "When is the entrance exam?", "CVSUCAT schedule",
        "How do I register for the admission test?", "When is CVSUCAT?",
        "College entrance exam date",
    ],
    "enrollment_procedure": [
        "How to enroll?", "Enrollment process", "Steps to enroll",
        "How do I register for classes?", "What's the enrollment procedure?",
    ],
    "enrollment_schedule": [
        "When is enrollment?", "Enrollment dates", "When to enroll?",
        "Enrollment period for first semester",
    ],
    "courses_offered": [
        "What courses are available?", "What programs do you offer?",
        "List of courses", "Available degrees", "What can I study?",
    ],
    "it_cs_courses": [
        "Does CvSU offer Computer Science?", "IT courses",
        "Is there BSIT?", "Information technology degree", "BSCS program",
    ],
    "graduate_programs": [
        "Graduate programs", "Masters degree", "PhD programs",
        "Post graduate courses", "Masters at CvSU",
    ],
    "tuition_fees": [
        "How much is tuition?", "School fees", "Cost of enrollment",
        "Tuition price", "Is CvSU free?", "Fee breakdown",
        "Magkano ang tuition?",
    ],
    "scholarship": [
        "Are there scholarships?", "Scholarship programs",
        "Financial aid", "CHED scholarship", "DOST scholarship", "TES",
    ],
    "campus_location": [
        "Where is CvSU?", "CvSU address", "How to get there",
        "Main campus location", "CvSU Indang",
    ],
    "campus_facilities": [
        "What facilities do you have?", "Library and gym",
        "Dormitory available?", "Canteen", "Student facilities",
    ],
    "library": [
        "Library hours", "CvSU library", "Online library",
        "Library services", "How to access the library",
    ],
    "events": [
        "Upcoming events", "CvSU events this month", "Sportsfest",
        "Cultural events", "What's happening on campus",
    ],
    "academic_calendar": [
        "Academic calendar", "When does school start?",
        "Semester schedule", "School year dates",
    ],
    "contact_info": [
        "How can I contact CvSU?", "Phone number",
        "What's the email?", "CvSU hotline", "Contact details",
    ],
    "registrar": [
        "Registrar office", "How to get TOR?", "Request for transcript",
        "Diploma request", "Graduation requirements",
    ],
    "about_cvsu": [
        "What is CvSU?", "Tell me about CvSU", "History of CvSU",
        "About Cavite State University",
    ],
    "vision_mission": [
        "CvSU vision", "CvSU mission", "Core values",
        "What does CvSU stand for?",
    ],
    "student_organizations": [
        "Student organizations", "Clubs at CvSU", "How to join an org",
        "Student council",
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-accuracy", type=float, default=0.50,
                        help="Fail if overall accuracy is below this fraction (default 0.50)")
    parser.add_argument("--report", default="models/eval_holdout.json",
                        help="Where to write the report")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    os.chdir(PROJECT_ROOT)
    chatbot = HybridChatbot(model_dir="models", responses_path="models/responses_map.json")

    per_intent = defaultdict(lambda: {"total": 0, "correct": 0, "confidences": [], "wrong": []})
    total = correct = 0

    for expected, queries in HOLDOUT_QUERIES.items():
        for q in queries:
            intent, _, conf, _, _ = chatbot.predict(q)
            per_intent[expected]["total"] += 1
            per_intent[expected]["confidences"].append(conf)
            total += 1
            if intent == expected:
                per_intent[expected]["correct"] += 1
                correct += 1
            else:
                per_intent[expected]["wrong"].append({
                    "query": q, "predicted": intent, "confidence": round(conf, 4)
                })

    overall = correct / total if total else 0.0
    if not args.quiet:
        print(f"\nHold-out accuracy: {overall:.2%} ({correct}/{total})")
        print(f"{'Intent':<28} {'Acc':>6} {'AvgConf':>9}  Notes")
        print("-" * 70)
        for intent in sorted(per_intent):
            d = per_intent[intent]
            acc = d["correct"] / d["total"] if d["total"] else 0
            avg = sum(d["confidences"]) / len(d["confidences"]) if d["confidences"] else 0
            mark = "OK" if acc >= 0.80 else ("WARN" if acc >= 0.60 else "FAIL")
            print(f"{intent:<28} {acc:>5.0%} {avg:>9.2f}  [{mark}]")

    report = {
        "overall_accuracy": round(overall, 4),
        "total_queries": total,
        "total_correct": correct,
        "min_accuracy_gate": args.min_accuracy,
        "passed_gate": overall >= args.min_accuracy,
        "per_intent": {
            intent: {
                "total": d["total"],
                "correct": d["correct"],
                "accuracy": round(d["correct"] / d["total"], 4) if d["total"] else 0,
                "avg_confidence": round(
                    sum(d["confidences"]) / len(d["confidences"]), 4
                ) if d["confidences"] else 0,
                "wrong": d["wrong"],
            }
            for intent, d in per_intent.items()
        },
    }
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    if not args.quiet:
        print(f"\nReport: {args.report}")
        print(f"Gate: {'PASS' if report['passed_gate'] else 'FAIL'} "
              f"(threshold {args.min_accuracy:.2%})")

    return 0 if report["passed_gate"] else 1


if __name__ == "__main__":
    sys.exit(main())
