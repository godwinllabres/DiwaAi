"""
One-shot patch script for cavsu_intents.json.

Applies all refinements from the training gap analysis:
  1. Fix nlu_fallback patterns (remove test garbage, add real noise)
  2. Expand weak intents: about_cvsu, goodbye, greeting, vision_mission, library
  3. Add 6 new intents: nstp, academic_policies, dormitory, student_portal,
                        international_students, online_programs
  4. Add 2 response variants to each of the 20 single-response intents

Run:
  py -3.11 patch_intents.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"

with open(INTENTS_PATH, "r", encoding="utf-8") as f:
    doc = json.load(f)

intents = doc["intents"]
intent_map = {i["tag"]: i for i in intents}

def add_patterns(tag: str, new_patterns: list[str]) -> int:
    existing = set(intent_map[tag]["patterns"])
    added = [p for p in new_patterns if p not in existing]
    intent_map[tag]["patterns"].extend(added)
    return len(added)

def set_patterns(tag: str, patterns: list[str]) -> None:
    intent_map[tag]["patterns"] = patterns

def add_responses(tag: str, new_responses: list[str]) -> None:
    existing = set(intent_map[tag]["responses"])
    for r in new_responses:
        if r not in existing:
            intent_map[tag]["responses"].append(r)

def add_intent(tag: str, patterns: list[str], responses: list[str], active: bool = True) -> None:
    if tag in intent_map:
        add_patterns(tag, patterns)
        add_responses(tag, responses)
    else:
        new_intent = {"tag": tag, "active": active, "patterns": patterns, "responses": responses}
        intents.append(new_intent)
        intent_map[tag] = new_intent

# ===========================================================================
# 1. Fix nlu_fallback — replace test garbage with real production noise
# ===========================================================================
set_patterns("nlu_fallback", [
    "...",
    "???",
    "aaaaaa",
    "xxxxxxxxxxx",
    "jkjkjk",
    "1234567890",
    "draw me a picture",
])
print("✓ nlu_fallback patterns replaced")

# ===========================================================================
# 2. Expand about_cvsu (50 → ~200 patterns)
# ===========================================================================
n = add_patterns("about_cvsu", [
    # Leadership
    "who is the president of CvSU", "who is the CvSU president", "CvSU president 2026",
    "who leads CvSU", "CvSU administration", "pangulo ng CvSU", "sino ang presidente ng CvSU",
    "head of CvSU", "CvSU university officials", "who is the chancellor of CvSU",
    # Accreditation / rankings
    "is CvSU accredited", "CvSU accreditation status", "AACCUP rating of CvSU",
    "CvSU AACCUP", "is CvSU a center of excellence", "CvSU center of excellence",
    "CvSU COE", "CvSU recognition", "CvSU awards", "CvSU ranking",
    "top state university Philippines", "CvSU ranking Philippines",
    "CvSU ISO certified", "CvSU quality", "CvSU performance",
    # Size / reach
    "how many students does CvSU have", "CvSU student population",
    "how many are enrolled in CvSU", "CvSU total enrollment",
    "how many campuses does CvSU have", "number of CvSU campuses",
    "CvSU 13 campuses", "how many faculty in CvSU", "CvSU staff count",
    "how big is CvSU", "CvSU size", "CvSU scale",
    # History
    "when was CvSU founded", "when was CvSU established", "CvSU history",
    "history of CvSU", "CvSU founding", "CvSU 1906", "thomasites CvSU",
    "CvSU became university", "RA 8468", "when did CvSU become a university",
    "kailan naitayo ang CvSU", "kasaysayan ng CvSU", "pinagmulan ng CvSU",
    "CvSU 1998 university", "brief history of CvSU", "CvSU short history",
    "1906 Indang school", "CvSU origin",
    # General overview
    "tell me about CvSU", "what is CvSU", "CvSU overview", "CvSU information",
    "CvSU facts", "CvSU profile", "about Cavite State University",
    "Cavite State University information", "what kind of university is CvSU",
    "is CvSU a state university", "is CvSU public or private",
    "CvSU public university", "CvSU SUC", "SUC CvSU",
    "CvSU government university", "ano ang CvSU",
    "ano ang Cavite State University", "CvSU general info",
    "CvSU details", "CvSU background", "CvSU basic info", "CvSU description",
    "sabihin mo tungkol sa CvSU", "kwentuhan mo ako tungkol sa CvSU",
    # Location / identity
    "CvSU is in Cavite", "Cavite university", "state university in Cavite",
    "CvSU Indang main campus history", "Indang Cavite university",
    "magkano ang CvSU", "anong uri ng paaralan ang CvSU",
    # Motto / identity
    "CvSU motto", "truth excellence service meaning",
    "what is the motto of CvSU", "CvSU slogan", "Iskolar para sa Bayan CvSU",
    "CvSU green hornets", "CvSU mascot", "CvSU athletics",
    # Specific factual queries
    "CvSU Don Severino", "Don Severino delas Alas campus",
    "CvSU research university", "CvSU agriculture", "CvSU extension programs",
    "CvSU research centers", "CvSU AREC", "CvSU SPRINT", "CvSU BRITE",
    "CvSU national coffee research", "CvSU bee research",
])
print(f"✓ about_cvsu: +{n} patterns")

add_responses("about_cvsu", [
    (
        "Cavite State University (CvSU) is a state university in Cavite, Philippines. Its origins trace back to 1906, "
        "and it was granted university status in 1998 via RA 8468. CvSU operates multiple campuses across the province "
        "of Cavite, with the main campus in Indang. Its motto is 'Truth, Excellence, Service' — Iskolar para sa Bayan. "
        "For current statistics (enrollment, campus list, accreditations), please refer to www.cvsu.edu.ph. "
        "Anything specific you'd like to know?"
    ),
    (
        "CvSU — Cavite State University — is your Iskolar para sa Bayan institution in Cavite, with roots dating back "
        "to 1906 and university status granted in 1998 (RA 8468). The main campus is in Indang, Cavite, and CvSU operates "
        "additional campuses across the province. For verified enrollment figures and the complete list of campuses, "
        "please visit www.cvsu.edu.ph. Salamat sa inyong tanong — what else would you like to know?"
    ),
])

# ===========================================================================
# 3. Expand goodbye
# ===========================================================================
n = add_patterns("goodbye", [
    "I'm done", "that's all", "nothing else", "I'm finished", "no more questions",
    "I'm good now", "all good thanks", "wala na akong tanong", "okay na ko",
    "sige na", "uwi na ko", "salamat na lang", "good bye", "good-bye",
    "take care", "see you around", "later", "ciao", "I'll go now",
    "gotta go", "I have to go", "that's it for now", "nothing more",
    "tapos na", "okay na", "alis na ko", "mauuna na ko", "ingat",
    "ingat ka", "mag-ingat ka", "hanggang sa muli", "hanggang sa susunod",
    "done", "all set", "we're good", "that covers it", "nothing further",
    "I think that's everything", "I got what I needed", "no further questions",
    "that will be all", "thanks and goodbye", "salamat at paalam",
])
print(f"✓ goodbye: +{n} patterns")

# ===========================================================================
# 4. Expand greeting
# ===========================================================================
n = add_patterns("greeting", [
    "good day", "mabuting umaga", "magandang umaga", "magandang hapon",
    "magandang gabi", "magandang araw", "kamusta", "kumusta ka",
    "kumusta po", "hey there", "howdy", "what's up", "greetings",
    "hi po", "hello po", "good morning po", "good afternoon po",
    "good evening po", "musta", "uy", "uy hello", "hello CvSU", "hi CvSU",
    "good morning Sevi", "hello Sevi", "hi Sevi", "hey Sevi",
    "maligayang pagdating", "welcome", "hoy", "hoy Sevi",
    "Sevi good morning", "Sevi good afternoon", "Sevi good evening",
    "yo Sevi", "start", "begin", "let's start", "let's begin",
])
print(f"✓ greeting: +{n} patterns")

# ===========================================================================
# 5. Expand vision_mission
# ===========================================================================
n = add_patterns("vision_mission", [
    "core values of CvSU", "university goals", "CvSU principles",
    "what does truth excellence service mean", "bakit truth excellence service ang motto",
    "CvSU philosophy", "CvSU mandate", "CvSU objectives", "CvSU goals",
    "university mission statement", "university vision statement",
    "CvSU advocacy", "iskolar para sa bayan meaning", "what is truth excellence service",
    "TES CvSU", "CvSU TES", "CvSU institutional values", "CvSU strategic goals",
    "ano ang misyon ng CvSU", "ano ang bisyon ng CvSU",
    "saan patutungo ang CvSU", "layunin ng CvSU", "adhikain ng CvSU",
    "CvSU direction", "CvSU purpose", "CvSU commitment",
    "what CvSU stands for", "CvSU beliefs", "CvSU guiding principles",
    "why truth excellence service", "CvSU motto explained",
])
print(f"✓ vision_mission: +{n} patterns")

# ===========================================================================
# 6. Expand library
# ===========================================================================
n = add_patterns("library", [
    "how to borrow books at CvSU", "library card CvSU", "how to get a library card",
    "library borrowing rules", "how many books can I borrow", "library return policy",
    "quiet room CvSU", "reading room CvSU", "diwa library",
    "Ladislao Diwa library", "diwa memorial library", "92000 books CvSU",
    "CvSU library books", "CvSU library resources", "CvSU library membership",
    "library schedule CvSU", "CvSU library open", "when does the library open",
    "when does the library close", "how to use the library", "library fine CvSU",
    "overdue books CvSU", "CvSU library online resources", "e-library CvSU",
    "digital library CvSU", "may librong pwede hiramin sa CvSU",
    "paano mag-borrow ng libro sa CvSU", "CvSU library rules",
    "CvSU library services", "CvSU library facilities", "library reference section",
    "periodicals CvSU library", "journal CvSU library",
    "thesis section CvSU library", "CvSU theses collection",
])
print(f"✓ library: +{n} patterns")

add_responses("library", [
    (
        "The Ladislao Diwa Memorial Library at the CvSU Main Campus, and the libraries at other campuses, "
        "provide books, periodicals, theses, and digital resources for students. "
        "To borrow books, present your CvSU ID and library card. "
        "Library hours, borrowing limits, loan periods, and overdue fines vary per campus — "
        "please verify the specifics with your campus librarian. "
        "An e-Library portal may also be available — check www.cvsu.edu.ph."
    ),
    (
        "CvSU's library system provides access to books, journals, theses, and digital databases. "
        "Visit the Ladislao Diwa Memorial Library at the Main Campus, or your campus library for local resources. "
        "Bring your student ID to register for a library card. "
        "Borrowing rules, schedules, and fines vary per campus — check with your librarian for details. "
        "Salamat sa inyong tanong!"
    ),
])

# ===========================================================================
# 7. Add 6 NEW intents
# ===========================================================================

# --- NSTP ---
add_intent("nstp", patterns=[
    "NSTP", "national service training program", "NSTP sa CvSU",
    "NSTP component", "ROTC vs CWTS", "ROTC vs LTS", "CWTS vs LTS",
    "how to enroll in NSTP", "NSTP enrollment", "NSTP requirements",
    "NSTP schedule", "NSTP units", "NSTP certificate",
    "ROTC CvSU", "CWTS CvSU", "LTS CvSU",
    "civic welfare training service", "literacy training service",
    "reserve officers training corps CvSU",
    "is NSTP mandatory", "NSTP for first year", "NSTP graduation requirement",
    "ano ang NSTP", "paano mag-enroll sa NSTP", "anong component ng NSTP",
    "NSTP CvSU schedule", "NSTP CvSU requirements", "NSTP CvSU meaning",
    "military training CvSU", "community service CvSU program",
    "NSTP 1", "NSTP 2", "NSTP 111", "NSTP 112",
    "OSAS NSTP", "student affairs NSTP",
    "how many semesters is NSTP", "NSTP duration",
    "can I choose my NSTP component", "NSTP component selection",
    "NSTP certificate of completion", "NSTP diploma requirement",
], responses=[
    (
        "NATIONAL SERVICE TRAINING PROGRAM (NSTP) AT CvSU:\n\n"
        "NSTP is MANDATORY for all first-year college students — 2 semesters (NSTP 1 & NSTP 2).\n\n"
        "THREE COMPONENTS (choose one):\n"
        "- ROTC: Reserve Officers' Training Corps — military/AFP training\n"
        "- CWTS: Civic Welfare Training Service — community development projects\n"
        "- LTS: Literacy Training Service — teaching literacy in communities\n\n"
        "ENROLLMENT:\n"
        "- Register during first-year enrollment\n"
        "- Coordinate with your college/department for available components\n"
        "- Some campuses may offer limited components — check with OSAS\n\n"
        "COMPLETION:\n"
        "- NSTP certificate is REQUIRED for graduation\n"
        "- Must complete both NSTP 1 and NSTP 2\n\n"
        "CONTACT: Office of Student Affairs & Services (OSAS) or your department adviser."
    ),
    (
        "CvSU's NSTP program is required for all freshmen (2 semesters). "
        "Choose from ROTC (military training), CWTS (community service), or LTS (literacy training). "
        "Enroll during your first semester and complete both NSTP 1 & 2 for your certificate — "
        "which is required for graduation. Contact OSAS or your department for component availability at your campus."
    ),
    (
        "Ang NSTP (National Service Training Program) ay sapilitan para sa lahat ng unang taon na estudyante sa CvSU. "
        "Pumili ng isa sa tatlong component: ROTC, CWTS, o LTS. "
        "Dapat makumpleto ang NSTP 1 at NSTP 2 para sa sertipiko na kailangan sa graduation. "
        "Makipag-ugnayan sa OSAS o sa iyong departamento para sa detalye."
    ),
])
print("✓ nstp intent added")

# --- ACADEMIC POLICIES ---
add_intent("academic_policies", patterns=[
    "grading system CvSU", "CvSU grades", "CvSU grading scale",
    "passing grade CvSU", "what is passing grade", "is 3.0 passing",
    "GWA computation CvSU", "how to compute GWA", "CvSU GWA",
    "weighted average CvSU", "grade point average CvSU",
    "incomplete grade CvSU", "INC grade CvSU", "how to remove INC",
    "academic probation CvSU", "CvSU retention policy",
    "leave of absence CvSU", "LOA CvSU", "how to apply LOA",
    "shifting course CvSU", "how to shift course",
    "how many units to graduate", "CvSU units requirement",
    "can I retake a subject", "re-take failed subject",
    "failed subject CvSU", "5.0 grade CvSU",
    "maximum residency CvSU", "how long can I study",
    "academic standing CvSU", "dean's list CvSU",
    "CvSU academic rules", "CvSU academic regulations",
    "overload subject policy", "underload policy CvSU",
    "withdrawal from subject", "dropped subject CvSU",
    "what happens if I fail", "consequences of failing",
    "1.0 grade CvSU", "what is 1.0 in CvSU",
    "ano ang passing grade sa CvSU", "paano mag-compute ng GWA",
    "pwede bang mag-shift ng course", "mag-LOA sa CvSU",
    "academic calendar policies", "semester rules CvSU",
    "cross enrollment policy", "crediting of subjects",
], responses=[
    (
        "CvSU ACADEMIC POLICIES & GRADING SYSTEM:\n\n"
        "GRADING SCALE (1.0–5.0):\n"
        "1.0 = Excellent | 1.25 = Superior | 1.5 = Very Good\n"
        "1.75 = Good | 2.0 = Satisfactory | 2.25 = Fairly Satisfactory\n"
        "2.5 = Passing | 2.75 = Conditional Pass | 3.0 = Passed (75%)\n"
        "5.0 = Failed | INC = Incomplete | W = Withdrawn\n\n"
        "MINIMUM PASSING GRADE: 3.0 (75%)\n\n"
        "GWA: Weighted average of all course grades × credit units\n\n"
        "INCOMPLETE (INC):\n"
        "- Must be completed within 1 academic year\n"
        "- Becomes 5.0 (Failed) if not resolved\n\n"
        "LEAVE OF ABSENCE (LOA):\n"
        "- Approved by the Dean; maximum 2 consecutive semesters\n"
        "- Submit LOA form to the Registrar's Office\n\n"
        "SHIFTING:\n"
        "- Requires Dean's approval; subject to slot availability\n"
        "- May need to retake non-credited subjects\n\n"
        "RETENTION: Varies per college — consult your department for specific GWA requirements.\n\n"
        "Contact the Registrar's Office for official grade records and policy clarifications."
    ),
    (
        "At CvSU, the passing grade is 3.0 (equivalent to 75%). "
        "Grades run on a 1.0–5.0 scale where 1.0 is Excellent and 5.0 is Failed. "
        "GWA is the weighted average of all your grades. "
        "An INC (Incomplete) must be resolved within one year or it becomes 5.0. "
        "For LOA, shifting, or retention policies, coordinate with your Dean's office and the Registrar."
    ),
    (
        "Ang passing grade sa CvSU ay 3.0 (75%). "
        "Gumagamit ang CvSU ng 1.0–5.0 grading scale. "
        "Ang GWA ay kinukuha mula sa weighted average ng lahat ng iyong grades. "
        "Ang INC grade ay kailangang ayusin sa loob ng isang taon. "
        "Para sa LOA, course shifting, o retention policy, makipag-ugnayan sa iyong Dean at sa Registrar's Office. "
        "Salamat sa inyong tanong!"
    ),
])
print("✓ academic_policies intent added")

# --- DORMITORY ---
add_intent("dormitory", patterns=[
    "dormitory CvSU", "dorm CvSU", "how to apply for dorm",
    "CvSU dorm application", "dorm requirements CvSU",
    "dorm fee CvSU", "how much is the dorm", "dorm rate CvSU",
    "may dorm ba sa CvSU", "paano mag-apply ng dorm sa CvSU",
    "CvSU dormitory rules", "dorm rules CvSU",
    "CvSU boarding house", "stay in CvSU campus",
    "student housing CvSU", "on campus housing",
    "bed space CvSU", "CvSU dorm availability",
    "can I stay in CvSU dorm", "dorm for freshmen CvSU",
    "dorm for transferees CvSU", "CvSU residential hall",
    "student residence CvSU", "how to get a dorm room",
    "CvSU dorm curfew", "dorm curfew CvSU", "dorm visitation rules",
    "CvSU dorm application form", "dorm slots CvSU",
    "CvSU dorm contact", "OSAS dorm", "student affairs dorm",
    "dormitory indang CvSU", "CvSU Indang dorm",
    "accommodation CvSU", "lodging CvSU campus",
    "saan makita ang dorm sa CvSU", "libre ba ang dorm sa CvSU",
], responses=[
    (
        "CvSU STUDENT DORMITORY:\n\n"
        "AVAILABILITY:\n"
        "- Dormitories available at selected campuses (including Main Campus, Indang)\n"
        "- Limited slots — priority given to students from distant areas\n"
        "- Apply early — slots fill quickly during enrollment\n\n"
        "APPLICATION REQUIREMENTS:\n"
        "- Certificate of Registration (proof of enrollment)\n"
        "- Medical / health clearance\n"
        "- Barangay clearance\n"
        "- 2x2 ID photos\n"
        "- Parent/guardian consent letter\n"
        "- Accomplished application form (from OSAS)\n\n"
        "RATES:\n"
        "- Fees vary per campus and room type\n"
        "- Basic utilities usually included; electricity may be charged separately\n\n"
        "RULES:\n"
        "- Curfew strictly enforced\n"
        "- No outside visitors after curfew\n"
        "- Governed by dormitory policies\n\n"
        "CONTACT: Office of Student Affairs & Services (OSAS)\n"
        "Email: osas@cvsu.edu.ph | Visit your campus Student Affairs office."
    ),
    (
        "CvSU offers student dormitories at select campuses with limited slots. "
        "To apply, get an application form from the OSAS office and submit: COR, medical clearance, "
        "barangay clearance, photos, and parent consent. "
        "Fees vary by campus. Curfew and visitation rules apply. "
        "Contact OSAS or your campus Student Affairs office for current availability and rates."
    ),
    (
        "May dormitoryo ang CvSU sa ilang campus, kasama na ang Main Campus sa Indang. "
        "Limitado ang slots, kaya mag-apply nang maaga. "
        "Kailangan ng COR, medical clearance, barangay clearance, at pahintulot ng magulang. "
        "Para sa detalye ng bayad at patakaran, makipag-ugnayan sa OSAS office ng inyong campus. "
        "Salamat sa inyong tanong!"
    ),
])
print("✓ dormitory intent added")

# --- STUDENT PORTAL ---
add_intent("student_portal", patterns=[
    "student portal CvSU", "CvSU student portal", "portal login CvSU",
    "how to access student portal", "CvSU portal login",
    "student portal password", "forgot portal password CvSU",
    "CvSU online portal", "DEMS CvSU", "my.cvsu.edu.ph",
    "portal not working", "cannot login to portal",
    "CvSU portal registration", "how to register in portal",
    "CvSU portal grades", "view grades online CvSU",
    "CvSU portal enrollment", "enroll online CvSU portal",
    "CvSU portal schedule", "class schedule portal",
    "CvSU portal COR", "certificate of registration portal",
    "ICT office CvSU", "CvSU ICT portal support",
    "reset password CvSU portal", "portal account CvSU",
    "paano i-access ang CvSU portal", "hindi ako makapag-login sa portal",
    "nakalimutan ko password sa CvSU portal",
    "CvSU online system", "CvSU learning management system",
    "CvSU LMS", "Google classroom CvSU", "CvSU online classes",
    "student information system CvSU", "SIS CvSU",
    "enrollment system CvSU", "online enrollment portal",
    "bakit hindi gumagana ang CvSU portal",
], responses=[
    (
        "CvSU STUDENT PORTAL:\n\n"
        "The CvSU Student Portal is your online gateway for academic services.\n\n"
        "WHAT YOU CAN DO:\n"
        "- View grades and academic records\n"
        "- Online enrollment / subject registration\n"
        "- Check class schedules\n"
        "- View/print Certificate of Registration (COR)\n"
        "- Track enrollment status\n\n"
        "HOW TO ACCESS:\n"
        "1. Go to: www.cvsu.edu.ph\n"
        "2. Click 'Student Portal'\n"
        "3. Log in with your student ID and password\n\n"
        "FIRST-TIME LOGIN:\n"
        "- Credentials issued during enrollment\n"
        "- Default password is usually your birthdate (MMDDYYYY) or student ID\n"
        "- Change your password after first login\n\n"
        "FORGOT PASSWORD / TECHNICAL ISSUES:\n"
        "Contact your campus ICT Office with your student ID.\n"
        "Email: ictmain@cvsu.edu.ph (Main Campus)\n\n"
        "For portal-related concerns, visit the ICT Office or Student Services."
    ),
    (
        "Access the CvSU Student Portal via www.cvsu.edu.ph to view grades, enroll in subjects, "
        "check schedules, and download your COR. "
        "Use your student ID number and the password issued during enrollment. "
        "If you forgot your password, contact your campus ICT Office with your student ID for a reset."
    ),
    (
        "Ang CvSU Student Portal ay accessible sa www.cvsu.edu.ph. "
        "Gamitin ang iyong student ID at password para mag-login. "
        "Doon mo mahahanap ang iyong grades, schedule, at COR. "
        "Kung nakalimutan mo ang password, pumunta sa ICT Office ng iyong campus na may dalang student ID. "
        "Salamat sa inyong tanong!"
    ),
])
print("✓ student_portal intent added")

# --- INTERNATIONAL STUDENTS ---
add_intent("international_students", patterns=[
    "foreign students CvSU", "international students CvSU",
    "can international students apply to CvSU",
    "can foreign nationals apply to CvSU",
    "foreign student admission CvSU", "international student requirements CvSU",
    "visa requirements CvSU", "student visa CvSU", "9F visa CvSU",
    "apply as international student", "non-Filipino student CvSU",
    "foreign applicant CvSU", "CvSU accepts foreigners",
    "international student tuition CvSU",
    "does CvSU accept foreign students",
    "CvSU international office", "Office of International Affairs CvSU",
    "OIA CvSU", "CvSU OIA",
    "English proficiency CvSU", "foreign credential evaluation CvSU",
    "apostille documents CvSU", "authenticated documents CvSU",
    "NBI clearance for foreigners CvSU",
    "immigration CvSU", "Bureau of Immigration student visa",
    "Korean students CvSU", "Chinese students CvSU",
    "Japanese students CvSU", "foreign exchange student CvSU",
    "study in CvSU as a foreigner",
], responses=[
    (
        "CvSU INTERNATIONAL / FOREIGN STUDENT ADMISSIONS:\n\n"
        "CvSU accepts foreign nationals subject to CHED and university regulations.\n\n"
        "REQUIREMENTS:\n"
        "- Accomplished application form\n"
        "- Authenticated/apostilled academic credentials (HS diploma, transcripts)\n"
        "- Passport copy (biodata page)\n"
        "- Student Visa (9F) — apply at Bureau of Immigration AFTER admission\n"
        "- English proficiency proof (if from non-English speaking country)\n"
        "- Medical/health clearance\n"
        "- Equivalent police clearance (NBI or from home country)\n\n"
        "PROCESS:\n"
        "1. Submit application to Office of Admissions (Main Campus, Indang)\n"
        "2. Await credential evaluation and Notice of Admission (NOA)\n"
        "3. Process 9F Student Visa at Bureau of Immigration\n"
        "4. Complete enrollment procedures\n\n"
        "TUITION NOTE:\n"
        "Free tuition under RA 10931 may NOT apply to foreign students.\n"
        "Contact the Cashier's Office for applicable fees.\n\n"
        "CONTACT:\n"
        "Office of Admissions | Email: admissions@cvsu.edu.ph\n"
        "Website: www.cvsu.edu.ph (use the official directory for the current phone number)"
    ),
    (
        "CvSU welcomes foreign/international students subject to CHED rules. "
        "You'll need authenticated academic credentials, passport, student visa (9F), "
        "and medical clearance. Apply at the Office of Admissions in the Main Campus, Indang. "
        "Note: Free tuition (RA 10931) may not apply to foreign nationals. "
        "Contact admissions@cvsu.edu.ph for full details."
    ),
    (
        "Yes, CvSU accepts international students subject to CHED rules. "
        "Typical requirements include apostilled academic records, a passport, and a 9F student visa processed after admission. "
        "Apply at the Office of Admissions, Main Campus, Indang, Cavite. "
        "For tuition rates and the latest application requirements and contact numbers, "
        "please reach out to admissions@cvsu.edu.ph or check www.cvsu.edu.ph."
    ),
])
print("✓ international_students intent added")

# --- ONLINE PROGRAMS ---
add_intent("online_programs", patterns=[
    "distance learning CvSU", "online degree CvSU", "ETEEAP CvSU",
    "ETEEAP requirements", "ETEEAP application", "what is ETEEAP",
    "part-time study CvSU", "modular learning CvSU",
    "online courses CvSU", "does CvSU offer online classes",
    "CvSU blended learning", "hybrid learning CvSU",
    "flexible learning CvSU", "CvSU flexible education",
    "work while studying CvSU", "working student program",
    "earn degree while working", "prior learning CvSU",
    "non-traditional student CvSU", "adult learner CvSU",
    "CvSU continuing education", "lifelong learning CvSU",
    "CvSU modular distance learning", "MDL CvSU",
    "online enrollment CvSU", "virtual classes CvSU",
    "CvSU e-learning", "LMS CvSU", "CvSU Google classroom",
    "asynchronous learning CvSU", "synchronous learning CvSU",
    "CHED flexible learning CvSU",
    "alternative learning CvSU", "ALS to college CvSU",
    "may online program ba sa CvSU", "puwedeng mag-aral online sa CvSU",
], responses=[
    (
        "CvSU ONLINE & FLEXIBLE LEARNING PROGRAMS:\n\n"
        "ETEEAP (Expanded Tertiary Education Equivalency and Accreditation Program):\n"
        "- For working professionals seeking a college degree through prior learning recognition\n"
        "- Credits work experience, training, and non-formal education toward a degree\n"
        "- Available for select programs — inquire at the Office of Academic Affairs\n"
        "- Requirements: Work portfolio, assessment/evaluation, applicable fees\n\n"
        "FLEXIBLE / BLENDED LEARNING:\n"
        "- Some programs offer hybrid delivery (face-to-face + online components)\n"
        "- Availability varies per campus and program per semester\n"
        "- Requires internet access and compatible device\n\n"
        "MODULAR DISTANCE LEARNING (MDL):\n"
        "- Learning modules delivered digitally or in print\n"
        "- Available for select programs as approved by CHED\n\n"
        "HOW TO APPLY FOR ETEEAP:\n"
        "1. Inquire at the Office of Academic Affairs (Main Campus)\n"
        "2. Submit your educational & professional portfolio\n"
        "3. Undergo assessment and evaluation\n"
        "4. Enroll if qualified\n\n"
        "CONTACT:\n"
        "Office of Academic Affairs | Email: academic@cvsu.edu.ph\n"
        "Website: www.cvsu.edu.ph"
    ),
    (
        "CvSU offers flexible learning options including ETEEAP for working professionals "
        "who want to earn a degree through prior learning recognition. "
        "Select programs also offer blended/hybrid and modular distance learning. "
        "For ETEEAP, inquire at the Office of Academic Affairs at the Main Campus. "
        "Visit www.cvsu.edu.ph for the latest program offerings."
    ),
    (
        "Oo, may flexible learning programs ang CvSU! "
        "Ang ETEEAP ay para sa mga nagtatrabaho na gustong makakuha ng degree batay sa kanilang karanasan. "
        "May iba ring programs na nag-aalok ng blended o modular learning. "
        "Para sa ETEEAP, makipag-ugnayan sa Office of Academic Affairs sa Main Campus. "
        "Salamat sa inyong tanong!"
    ),
])
print("✓ online_programs intent added")

# ===========================================================================
# 8. Add 2 response variants to each of the 20 single-response intents
# ===========================================================================

# --- vision_mission (1 existing) ---
add_responses("vision_mission", [
    (
        "CvSU's VISION: A premier state university that develops highly competent, "
        "morally upright, and socially responsible professionals. "
        "MISSION: Provide quality higher technological and professional education; "
        "undertake research, extension, and production; and engage in resource generation. "
        "MOTTO: Truth, Excellence, Service — the guiding principles for every Iskolar para sa Bayan."
    ),
    (
        "Ang bisyon ng CvSU ay maging isang nangungunang state university na nagpapalaki ng mga "
        "competent, moral, at responsableng propesyonal. "
        "Ang misyon nito ay magbigay ng de-kalidad na edukasyon, pananaliksik, at serbisyo sa komunidad. "
        "Ang motto: Truth, Excellence, Service — para sa bawat Iskolar para sa Bayan. "
        "Salamat sa inyong tanong!"
    ),
])

# --- campus_location (1 existing) ---
add_responses("campus_location", [
    (
        "CvSU's Main Campus (Don Severino delas Alas Campus) is located in Indang, Cavite. "
        "CvSU also operates additional campuses across the province of Cavite. "
        "Public transport (jeepney/bus) from Dasmarinas or Tagaytay connects to the Indang main campus. "
        "For the complete and current list of CvSU campuses and their addresses, please visit www.cvsu.edu.ph."
    ),
    (
        "Ang CvSU Main Campus ay nasa Indang, Cavite. "
        "Mayroon ding ibang campus ang CvSU sa lalawigan ng Cavite. "
        "Maaaring sumakay ng jeepney o bus papunta sa Indang mula Dasmarinas o Tagaytay. "
        "Para sa kumpleto at napapanahong listahan ng mga campus, bisitahin ang www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# --- campus_facilities (1 existing) ---
add_responses("campus_facilities", [
    (
        "CvSU's facilities typically include the Ladislao Diwa Memorial Library, "
        "science and computer laboratories, a Health Service Unit, Guidance & Counseling office, "
        "student dormitories (at select campuses), cafeteria, chapel, and sports facilities. "
        "Available facilities vary per campus. "
        "For the specific facilities at your campus of interest, please check www.cvsu.edu.ph."
    ),
    (
        "Karaniwang may library, science at computer labs, health clinic, guidance office, "
        "dormitoryo (sa ilang campus), cafeteria, kapilya, at sports facilities ang CvSU. "
        "Ang available na pasilidad ay nag-iiba bawat campus. "
        "Para sa tiyak na pasilidad ng inyong campus, makipag-ugnayan sa student affairs office o bisitahin ang www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# --- courses_offered (1 existing) ---
add_responses("courses_offered", [
    (
        "CvSU offers programs across Agriculture, Engineering & IT, Education, Business, "
        "Arts & Sciences, Criminal Justice, and more. "
        "Key programs: BS Agriculture, BS Computer Science, BS Information Technology, "
        "BS Engineering (Civil, Electrical, Electronics, Computer, Agricultural), "
        "BS Criminology, BS Business Administration, BS Education, and graduate programs. "
        "Program availability varies per campus — check www.cvsu.edu.ph for your nearest campus offerings."
    ),
    (
        "Ang CvSU ay nag-aalok ng iba't ibang programa sa Agriculture, Engineering, IT, Edukasyon, "
        "Negosyo, at iba pa. "
        "Kasama na rito ang BS Agriculture, BSCS, BSIT, iba't ibang Engineering, BS Criminology, "
        "at marami pang iba. "
        "Ang availability ng bawat programa ay depende sa campus — bisitahin ang www.cvsu.edu.ph para sa kumpletong listahan. "
        "Salamat sa inyong tanong!"
    ),
])

# --- it_cs_courses (1 existing) ---
add_responses("it_cs_courses", [
    (
        "CvSU's College of Engineering and Information Technology (CEIT) offers BS Computer Science (BSCS), "
        "BS Information Technology (BSIT), BS Computer Engineering, and BS Electronics Engineering. "
        "These 4-year programs are available at Indang, Imus, Bacoor, and other campuses. "
        "Graduate option: Master of Science in Computer Science. Contact: ceit@cvsu.edu.ph"
    ),
    (
        "Ang BSCS at BSIT ay available sa ilang campus ng CvSU tulad ng Indang, Imus, at Bacoor. "
        "4-taon ang programa na may modern computer labs, industry partnerships, at internship. "
        "Para sa master's program (MSCS), makipag-ugnayan sa Graduate School. "
        "Email: ceit@cvsu.edu.ph. Salamat sa inyong tanong!"
    ),
])

# --- graduate_programs (1 existing) ---
add_responses("graduate_programs", [
    (
        "CvSU Graduate School offers Master's and Doctoral programs including: "
        "MS Agriculture, MS Computer Science, Master of Arts in Education, "
        "Master in Public Administration, MS Environmental Science, and PhD programs in Agriculture. "
        "Requirements: Bachelor's degree, application form, transcripts, and recommendation letters. "
        "Tuition under RA 10931 may apply. Contact: graduate@cvsu.edu.ph"
    ),
    (
        "Ang CvSU Graduate School ay nag-aalok ng Master's at Doctoral programs sa iba't ibang larangan. "
        "Kailangan ng Bachelor's degree, transcript, at application form para mag-apply. "
        "Para sa kumpletong listahan ng programs at requirements, makipag-ugnayan sa Graduate School office. "
        "Email: graduate@cvsu.edu.ph. Salamat sa inyong tanong!"
    ),
])

# --- admissions_requirements (1 existing) ---
add_responses("admissions_requirements", [
    (
        "To apply to CvSU, freshmen need: accomplished application form, Form 137/SF9 (HS card), "
        "PSA birth certificate, good moral certificate, and 2x2 photos. "
        "Transferees additionally need a transcript of records and honorable dismissal. "
        "ALS passers must present their ALS certificate. "
        "Submit to the Admissions Office. Visit www.cvsu.edu.ph for the current application schedule."
    ),
    (
        "Ang mga pangunahing requirements para sa CvSU admission ay: application form, Form 137/SF9, "
        "PSA birth certificate, good moral certificate, at 2x2 photos. "
        "Para sa mga transferee, kailangan din ng transcript at honorable dismissal. "
        "Para sa kumpletong proseso, bisitahin ang www.cvsu.edu.ph o makipag-ugnayan sa Admissions Office. "
        "Salamat sa inyong tanong!"
    ),
])

# --- admissions_exam (1 existing) ---
add_responses("admissions_exam", [
    (
        "The CvSU College Admission Test (CAT) covers English, Math, Science, and Abstract Reasoning. "
        "It is administered at the Main Campus and other testing centers. "
        "Bring your exam permit, 2 pencils (#2), and a valid ID. "
        "Results are typically released 1–2 weeks after the exam. "
        "Check www.cvsu.edu.ph for the exact schedule and testing venues per campus."
    ),
    (
        "Ang CvSU entrance exam (CAT) ay sumasaklaw ng English, Math, Science, at Abstract Reasoning. "
        "Dalhin ang exam permit, 2 pencils (#2), at valid ID sa araw ng pagsusulit. "
        "Ang resulta ay ilalabas pagkatapos ng 1–2 linggo. "
        "Bisitahin ang www.cvsu.edu.ph para sa schedule at venue ng exam. "
        "Salamat sa inyong tanong!"
    ),
])

# --- enrollment_procedure (1 existing) ---
add_responses("enrollment_procedure", [
    (
        "CvSU Enrollment Steps: "
        "1) Receive Notice of Admission (NOA) | "
        "2) Attend orientation/interview with your department | "
        "3) Complete medical exam | "
        "4) Meet with your academic adviser for course selection | "
        "5) Register online via the Student Portal | "
        "6) Pay miscellaneous fees at the Cashier (tuition is FREE under RA 10931) | "
        "7) Submit COR to the Registrar | "
        "8) Get your student ID. "
        "For continuing students, enroll via the Student Portal during the scheduled period."
    ),
    (
        "Para sa enrollment sa CvSU: tanggapin ang NOA, dumalo sa departmental orientation, "
        "kumpletuhin ang medical exam, mag-advising sa iyong adviser, mag-enroll sa Student Portal, "
        "bayaran ang miscellaneous fees sa Cashier (libre ang tuition!), "
        "at i-submit ang COR sa Registrar. "
        "Para sa continuing students, gamitin ang Student Portal sa enrollment period. "
        "Salamat sa inyong tanong!"
    ),
])

# --- enrollment_schedule (1 existing) ---
add_responses("enrollment_schedule", [
    (
        "CvSU enrollment periods are announced before each semester via the official website and social media. "
        "First semester typically begins enrollment in June–July; "
        "second semester in November–December; summer in April–May. "
        "Freshmen usually have a separate enrollment day from continuing students. "
        "Monitor www.cvsu.edu.ph and CvSU's official Facebook page for exact dates."
    ),
    (
        "Ang enrollment schedule ng CvSU ay inihahayag sa www.cvsu.edu.ph at sa opisyal na Facebook page. "
        "Sa pangkalahatan: first semester — Hunyo hanggang Hulyo; "
        "second semester — Nobyembre hanggang Disyembre; summer — Abril hanggang Mayo. "
        "Para sa tiyak na petsa, subaybayan ang opisyal na announcements ng CvSU. "
        "Salamat sa inyong tanong!"
    ),
])

# --- tuition_fees (1 existing) ---
add_responses("tuition_fees", [
    (
        "Good news — CvSU tuition is FREE under RA 10931 (Universal Access to Quality Tertiary Education Act) "
        "for Filipino students in undergraduate programs! "
        "However, miscellaneous fees still apply (laboratory, library, student ID, etc.) — "
        "typically ₱3,000–₱6,000 per semester depending on your course and campus. "
        "Pay at the Cashier's Office on campus. Some fees may be payable online — check your campus."
    ),
    (
        "Libre ang tuition sa CvSU para sa mga Filipino undergraduate students dahil sa RA 10931! "
        "Pero may miscellaneous fees pa rin na kailangang bayaran (laboratory, library, ID, atbp.) "
        "na karaniwang ₱3,000–₱6,000 bawat semester depende sa kurso at campus. "
        "Bayaran ito sa Cashier's Office ng inyong campus. "
        "Salamat sa inyong tanong!"
    ),
])

# --- scholarship (1 existing) ---
add_responses("scholarship", [
    (
        "CvSU offers and facilitates various scholarship programs including: "
        "CHED scholarships (merit-based), DOST-SEI scholarships (science & engineering), "
        "CvSU institutional grants, LGU scholarships (from your municipality), "
        "and private scholarships from partner organizations. "
        "Requirements typically include GWA of at least 1.75–2.0, good moral standing, "
        "and financial need (for some grants). "
        "Apply at the Scholarship Office or Office of Student Affairs. Contact: scholarship@cvsu.edu.ph"
    ),
    (
        "Ang CvSU ay nag-aalok ng iba't ibang scholarship: CHED, DOST-SEI, CvSU institutional grants, "
        "at LGU scholarships mula sa inyong munisipyo. "
        "Karaniwang requirement: GWA na hindi bababa sa 1.75–2.0 at magandang moral standing. "
        "Mag-apply sa Scholarship Office o OSAS. "
        "Email: scholarship@cvsu.edu.ph. Salamat sa inyong tanong!"
    ),
])

# --- academic_calendar (1 existing) ---
add_responses("academic_calendar", [
    (
        "CvSU follows CHED's academic calendar. Typically: "
        "First Semester — August to December | Second Semester — January to May | Summer — June to July. "
        "(Note: Calendar may shift annually — always verify with the official CvSU announcement.) "
        "Graduation ceremonies are usually held in June. "
        "For the official academic calendar, visit www.cvsu.edu.ph or check the Registrar's Office."
    ),
    (
        "Ang CvSU academic calendar ay sumusunod sa CHED guidelines. "
        "Sa pangkalahatan: First Semester — Agosto hanggang Disyembre; "
        "Second Semester — Enero hanggang Mayo; Summer — Hunyo hanggang Hulyo. "
        "Ang graduation ay karaniwang sa Hunyo. "
        "Para sa opisyal na kalendaryo, bisitahin ang www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# --- events (1 existing) ---
add_responses("events", [
    (
        "CvSU hosts a rich calendar of events throughout the year including: "
        "Foundation Day (January 22 — commemorating RA 8468), "
        "Intramurals / Sports Fest, Research Symposium, Career & Job Fairs, "
        "Cultural Festivals, Lantern Parade (December), Graduation Ceremony (June), "
        "and various college-level academic competitions. "
        "Follow CvSU's official Facebook page and website for event schedules and announcements."
    ),
    (
        "Ang CvSU ay maraming events sa buong taon: Foundation Day (Enero 22), Intramurals, "
        "Research Symposium, Career/Job Fair, Cultural Festival, Lantern Parade (Disyembre), "
        "at Graduation (Hunyo). "
        "Sundan ang opisyal na Facebook page at website ng CvSU para sa pinakabagong schedule. "
        "Salamat sa inyong tanong!"
    ),
])

# --- contact_info (1 existing) ---
add_responses("contact_info", [
    (
        "For the most current CvSU contact information — phone numbers, emails for the Main Campus (Indang) "
        "and other campuses, and office-specific contacts (Admissions, Registrar, OSAS, etc.) — "
        "please refer to the official directory at www.cvsu.edu.ph. "
        "The official Facebook page is also linked from the website (please verify it is the genuine page before messaging). "
        "Telling me the office or campus you need will help me point you to the right department."
    ),
    (
        "Para sa pinakabagong contact information ng CvSU — phone numbers, emails ng Main Campus (Indang) "
        "at ibang campus, at office-specific contacts — mangyaring bisitahin ang opisyal na directory sa www.cvsu.edu.ph. "
        "Sabihin sa akin kung anong opisina o campus ang hinahanap ninyo para matulungan ko kayong matukoy ang tamang departamento. "
        "Salamat sa inyong tanong!"
    ),
])

# --- registrar (1 existing) ---
add_responses("registrar", [
    (
        "The CvSU Registrar's Office handles academic records including Transcript of Records (TOR), "
        "Diploma, Certificate of Enrollment, Good Moral Certificate, and Grade Certificates. "
        "Typical steps: secure a clearance slip, fill out the request form, and settle the applicable fees. "
        "Processing time and exact fees vary by document and campus. "
        "For the current registrar contact details, office hours, and processing schedule of your campus, "
        "please refer to www.cvsu.edu.ph."
    ),
    (
        "Hinahawakan ng Registrar's Office ng CvSU ang mga academic records: TOR, diploma, "
        "Certificate of Enrollment, Good Moral, atbp. "
        "Karaniwang proseso: kumuha ng clearance slip, punan ang request form, at bayaran ang mga applicable fees. "
        "Ang processing time at fees ay nag-iiba depende sa dokumento at campus. "
        "Para sa pinakabagong contact at office hours, mangyaring bisitahin ang www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# --- student_organizations (1 existing) ---
add_responses("student_organizations", [
    (
        "CvSU has a number of recognized student organizations covering academics, culture, arts, sports, "
        "and advocacy. These typically include college-based organizations, a university-wide student government, "
        "literary and journalism orgs, debate clubs, cultural dance troupes, and more. "
        "To join: attend the organization fair during enrollment, or inquire directly with the org officers. "
        "Registration is handled through the Office of Student Affairs & Services (OSAS). "
        "For the current list of recognized organizations, please check www.cvsu.edu.ph or OSAS at your campus."
    ),
    (
        "Ang CvSU ay may iba't ibang recognized student organizations — academic orgs, "
        "cultural groups, sports clubs, hanggang sa journalism at debate societies. "
        "Para sumali, dumalo sa org fair sa enrollment period o makipag-ugnayan sa OSAS. "
        "Para sa pinakabagong listahan ng mga recognized organization, bisitahin ang www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# --- campus_specific (1 existing) ---
add_responses("campus_specific", [
    (
        "CvSU operates a Main Campus in Indang, along with additional campuses across the province of Cavite. "
        "Each campus offers a selection of programs — not all courses are available at every campus. "
        "For the current and complete list of CvSU campuses, the programs offered at each, and their contact details, "
        "please visit www.cvsu.edu.ph and select the campus page you're interested in."
    ),
    (
        "Mayroong Main Campus sa Indang at iba pang campus ang CvSU sa buong lalawigan ng Cavite. "
        "Hindi lahat ng programa ay available sa bawat campus. "
        "Para sa kumpleto at napapanahong listahan ng CvSU campuses at ng kanilang programs, "
        "mangyaring bisitahin ang opisyal na website na www.cvsu.edu.ph. "
        "Salamat sa inyong tanong!"
    ),
])

# ===========================================================================
# Save
# ===========================================================================
doc["intents"] = list(intent_map.values())
output = json.dumps(doc, indent=2, ensure_ascii=False)
import sys
with open(INTENTS_PATH, "w", encoding="utf-8") as f:
    f.write(output)

print("\n=== PATCH COMPLETE ===")
total_patterns = sum(len(i["patterns"]) for i in doc["intents"])
print(f"Intents: {len(doc['intents'])}")
print(f"Total patterns: {total_patterns}")
for intent in sorted(doc["intents"], key=lambda x: len(x["patterns"]), reverse=True):
    print(f"  {intent['tag']}: {len(intent['patterns'])} patterns, {len(intent['responses'])} responses")
