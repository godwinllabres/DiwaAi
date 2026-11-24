"""Rewrite the worst Tier 1/2 templates from reference-document style into chat
replies, patching data/cavsu_intents.json + models/responses_map.json + the DB
together (they must stay identical per intent, or load_intents() deletes and
rebuilds the DB — which also drops the campus_places / map_* / seasons tables).

Observed problem: "who is the president" was answered with "Per the CvSU
officials roster effective 1 January - 31 December 2026, the University
President of Cavite State University is ..." and other answers opened with
ALL-CAPS headers ("CvSU ACADEMIC CALENDAR") and closed with "Salamat sa inyong
tanong!". The rewrite answers first, moves attribution to a closing line, uses
sentence-case labels and ends with one follow-up offer naming a typeable topic.

Facts are kept verbatim: the script refuses to write if any URL, email, number,
honorific name or acronym of the old text is missing from the new text, or if
the variant _select_response serves (EN = min Filipino ratio, FIL = max) moves.

Idempotent: a variant already equal to its new text is skipped; a variant that
matches neither the expected old prefix nor the new text aborts the run.
No retrain is needed — responses are not SHA-pinned (models/trusted_hashes.json
pins only the pkl/h5 files). A running server needs /model/reload or a restart.

Usage:  LLM_PROVIDER=none python scripts/rewrite_template_tone.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LLM_PROVIDER", "none")

INTENTS_JSON = ROOT / "data" / "cavsu_intents.json"
RESP_MAP = ROOT / "models" / "responses_map.json"
INTENTS_DB = ROOT / "data" / "cavsu_intents.db"

# ── Shared verbatim blocks ───────────────────────────────────────────────────

_VPS = (
    "- Dr. Cristina M. Signo — Vice President for Academic Affairs\n"
    "- Dr. Melbourne R. Talactac — Vice President for Research, Innovation and Extension\n"
    "- Mr. John Xavier B. Nepomuceno — Vice President for Administrative and Support Services\n"
    "- Dr. Mary Jane D. Tepora — Vice President for Planning and Development\n"
    "- Dr. Almira G. Magcawas — Vice President for Business and Resource Generation"
)
_DEANS = (
    "- CAFENR (College of Agriculture, Food, Environment and Natural Resources): Dr. Abigail P. Daria\n"
    "- CAS (College of Arts and Sciences): Dr. Ammie P. Ferrer\n"
    "- CCJ (College of Criminal Justice): Dr. Marissa L. Ikan\n"
    "- CED (College of Education): Dr. Liza C. Costa\n"
    "- CEMDS (College of Economics, Management and Development Studies): Dr. Maria Soledad M. Lising\n"
    "- CEIT (College of Engineering and Information Technology): Dr. Willie C. Buclatin\n"
    "- COM (College of Medicine): Dr. Eleanor J. Galvez\n"
    "- CON (College of Nursing): Dr. Evelyn M. Del Mundo\n"
    "- CSPEAR (College of Sports, Physical Education and Recreation): Dr. Victor F. Piores\n"
    "- CTHM (College of Tourism and Hospitality Management): Dr. Katherine D. Peren\n"
    "- CVMBS (College of Veterinary Medicine and Biomedical Sciences): Dr. Rezin C. Bahia\n"
    "- CAPS (College of Advance and Professional Studies): Dr. Bettina Joyce P. Ilagan"
)
_CAMPUS_HEADS = (
    "- Bacoor City Campus: Asst. Prof. Menvyluz S. Macalalad (Campus Administrator)\n"
    "- Carmona Campus: Dr. Jenny Beb F. Espineli (Campus Administrator)\n"
    "- Cavite City Campus: Asso. Prof. Ma. Cristina J. Baesa (Campus Administrator)\n"
    "- General Trias Campus: Asso. Prof. Marilou P. Luseco (Campus Administrator)\n"
    "- Imus Campus: Dr. Armi Grace B. Desingaño (Campus Administrator)\n"
    "- Naic Campus: Asst. Prof. Armina B. Pugay (Campus Administrator)\n"
    "- Rosario (CCAT) Campus: Dr. Lauro B. Pascua (Campus Administrator)\n"
    "- Silang Campus: Dr. Jonathan R. Digma (Campus Administrator)\n"
    "- Tanza Campus: Asso. Prof. Mildred A. Sebastian (Campus Administrator)\n"
    "- Trece Martires City Campus: Asso. Prof. Noel A. Sedigo (Campus Administrator)\n"
    "- Maragondon Campus: Prof. Jazmin P. Cubillo (Principal)\n"
    "- Dasmariñas Learning Center Campus: Dr. Gemma S. Legaspi (Center Administrator)"
)
_ROSTER_EN = "1 January - 31 December 2026"
_SRC_ROSTER = "https://cvsu.edu.ph/contact-us-2/"
_VM_URL = "https://cvsu.edu.ph/mandate-mission-vision-core-values-quality-policy/"
_OSAS_URL = "https://cvsu.edu.ph/office-of-student-affairs-and-services/"
_ASCEND_EN = (
    "The University's current strategy is CvSU ASCEND — Advancing Sustained Change and "
    "Excellence for National Development — its 2025-2028 Strategic Framework, implemented "
    "through the four-year IDEAL Plan:\n"
    "- Inclusive and Accessible Education\n"
    "- Dynamic and Competitive Research and Innovation\n"
    "- Empowered Communities and Stronger Partnership\n"
    "- Accountable and Client-Centered Governance\n"
    "- Long-Lasting Sustainable Resource Generation\n"
    "This is how CvSU continues its journey toward becoming a Premier Global University."
)

# ── Rewrites: (tag, index, expected old prefix, new text) ────────────────────
# Index is the position in the intent's responses list; it never changes, so
# _select_response's language ranking is the only thing that could move the
# served variant — checked in verify_served().

REWRITES: list[tuple[str, int, str, str]] = [
    ("university_officials", 0, "Per the CvSU officials roster effective",
     "CvSU's University President is Dr. Ma. Agnes P. Nuestro.\n\n"
     "The five Vice Presidents are:\n" + _VPS + "\n\n"
     "CvSU is governed by a Board of Regents chaired by Hon. Myrna Q. Mallari (CHED "
     "Commissioner), with the University President as Vice Chairperson. Ms. Catherine J. "
     "Quiñones is Director of the Presidential Management Coordinating Office.\n\n"
     f"This is from the CvSU officials roster effective {_ROSTER_EN}. Source: {_SRC_ROSTER} — "
     "designations change, so please verify there for the current roster.\n\n"
     "Looking for a college dean or a campus administrator? Just ask, for example "
     "\"list of deans\" or \"campus administrators\"."),
    ("university_officials", 1, "Ayon sa opisyal na listahan ng CvSU na epektibo",
     "Ang Pangulo ng Cavite State University ay si Dr. Ma. Agnes P. Nuestro.\n\n"
     "Ang limang Vice President ay sina:\n" + _VPS + "\n\n"
     "Pinamumunuan ang Board of Regents ni Hon. Myrna Q. Mallari (CHED Commissioner), at ang "
     "Pangulo ng unibersidad ang Vice Chairperson. Si Ms. Catherine J. Quiñones ang Director "
     "ng Presidential Management Coordinating Office.\n\n"
     "Mula ito sa opisyal na listahan ng CvSU na epektibo mula 1 Enero hanggang 31 Disyembre "
     f"2026. Sanggunian: {_SRC_ROSTER} — nagbabago ang mga designasyon, kaya mangyaring "
     "tingnan doon ang kasalukuyang listahan.\n\n"
     "Kung kailangan ninyo ang dean ng isang kolehiyo o ang pinuno ng isang campus, itanong "
     "lang po (hal. \"sino ang dean ng CEIT\")."),
    ("college_deans", 0, "Per the CvSU officials roster effective",
     "Each CvSU college has its own dean. Here's the current list — just find your college:\n"
     + _DEANS + "\n\n"
     f"From the CvSU officials roster effective {_ROSTER_EN}. Source: {_SRC_ROSTER} — "
     "designations change, so please verify there.\n\n"
     "I can also list the programs a college offers — just ask, e.g. \"engineering programs\"."),
    ("college_deans", 1, "Ayon sa opisyal na listahan ng CvSU (epektibo",
     "May sariling dean ang bawat kolehiyo ng CvSU. Narito ang kasalukuyang listahan:\n"
     + _DEANS + "\n\n"
     "Mula sa opisyal na listahan ng CvSU (epektibo 1 Enero - 31 Disyembre 2026). "
     f"Sanggunian: {_SRC_ROSTER} — nagbabago ang mga designasyon.\n\n"
     "Maaari ko ring ilista ang mga programa ng isang kolehiyo — itanong lang po "
     "(hal. \"engineering programs\")."),
    ("campus_officials", 0, "Per the CvSU officials roster effective",
     "Each CvSU campus has its own head. Here's the current list, including the learning "
     "center:\n"
     + _CAMPUS_HEADS + "\n\n"
     f"From the CvSU officials roster effective {_ROSTER_EN}. Source: {_SRC_ROSTER} — "
     "designations change, so please verify there.\n\n"
     "Need the University President or a college dean instead? Just ask, e.g. "
     "\"who is the CvSU president\" or \"list of deans\"."),
    ("campus_officials", 1, "Ayon sa opisyal na listahan ng CvSU (epektibo",
     "May sariling pinuno ang bawat campus ng CvSU. Narito ang kasalukuyang listahan:\n"
     + _CAMPUS_HEADS + "\n\n"
     "Mula sa opisyal na listahan ng CvSU (epektibo 1 Enero - 31 Disyembre 2026). "
     f"Sanggunian: {_SRC_ROSTER}\n\n"
     "Kung kailangan ninyo ang Pangulo ng unibersidad o ang dean ng isang kolehiyo, "
     "itanong lang po."),
    ("academic_calendar", 0, "CvSU ACADEMIC CALENDAR (general guide",
     "CvSU follows the CHED academic calendar with a First Semester, Second Semester, and "
     "(where applicable) a Summer Term. Each semester typically includes midterm and final "
     "examination periods, plus the regular Philippine holidays.\n\n"
     "The university also observes its own Founding Anniversary and university-wide events "
     "such as Sportsfest, Foundation Week, and Graduation.\n\n"
     "Exact dates shift every year, so I'd rather not post dates that may be outdated. For "
     "the official, current-year academic calendar — including exact dates of classes, exams, "
     "holidays, the Founding Day commemoration, and graduation:\n"
     "- Visit https://cvsu.edu.ph (and your specific campus page)\n"
     "- Follow CvSU's official social media\n"
     "- Contact your campus registrar\n\n"
     "I can also walk you through the enrollment process or the enrollment schedule — just ask."),
    ("academic_calendar", 1, "Ang CvSU academic calendar ay sumusunod sa CHED",
     "Ang CvSU academic calendar ay sumusunod sa CHED academic calendar — may First Semester, "
     "Second Semester, at (kung naaangkop) Summer Term, kasama ang midterm at final exams at "
     "ang mga regular na holidays.\n\n"
     "Nagbabago ang eksaktong petsa bawat taon, kaya hindi ko po ibinibigay dito ang mga ito "
     "para maiwasan ang lumang impormasyon. Para sa opisyal at kasalukuyang kalendaryo — "
     "kasama ang eksaktong petsa ng klase, exam, holidays, at graduation — pakibisita po ang "
     "https://cvsu.edu.ph (at ang page ng inyong campus), sundan ang opisyal na social media "
     "ng CvSU, o makipag-ugnayan sa inyong campus registrar.\n\n"
     "Maaari ko ring ipaliwanag ang proseso o iskedyul ng enrollment — itanong lang po."),
    ("enrollment_procedure", 0, "CvSU ENROLLMENT PROCEDURE:",
     "Here's how enrollment works at CvSU once you've received your Notice of Admission (NOA):\n\n"
     "1. Pre-enrollment: receive your Notice of Admission (NOA), attend the department "
     "interview/orientation, complete the medical examination, and get your medical clearance.\n"
     "2. Academic advising: meet with your department adviser to discuss your curriculum and "
     "course load, and get your program of study.\n"
     "3. Registration: enroll online via the student portal, select your courses for the "
     "semester, and print your Certificate of Registration (COR).\n"
     "4. Payment (misc fees only - tuition is free under RA 10931): pay the miscellaneous fees "
     "at the Cashier's Office and get your official receipt.\n"
     "5. Finalization: submit your COR to the Registrar, get your student ID, and receive your "
     "class schedule.\n\n"
     "Continuing students: register online through the student portal, select subjects with "
     "adviser approval, pay fees if applicable, and submit your COR.\n\n"
     "For the verified Office of the University Registrar contacts (email and phone) for your "
     "campus, please check www.cvsu.edu.ph.\n\n"
     "Want the enrollment schedule or the admission requirements next? Just ask."),
    ("enrollment_procedure", 1, "Para sa enrollment sa CvSU: tanggapin ang NOA",
     "Para sa enrollment sa CvSU: tanggapin ang NOA, dumalo sa departmental orientation, "
     "kumpletuhin ang medical exam, mag-advising sa iyong adviser, mag-enroll sa Student "
     "Portal, bayaran ang miscellaneous fees sa Cashier (libre ang tuition!), at i-submit ang "
     "COR sa Registrar. Para sa continuing students, gamitin ang Student Portal sa enrollment "
     "period. Kung kailangan ninyo rin ang iskedyul ng enrollment o ang admission "
     "requirements, itanong lang po."),
    ("enrollment_schedule", 0, "CvSU ENROLLMENT SCHEDULE:",
     "CvSU announces enrollment dates each academic year, and specific dates vary by campus "
     "and academic year.\n\n"
     "CvSU follows an academic calendar with First Semester, Second Semester, and (where "
     "offered) a Summer Term.\n\n"
     "Application periods, entrance-exam schedules, Notice of Admission release, and "
     "enrollment windows are announced each academic year by the university.\n\n"
     "Good to know:\n"
     "- Late enrollment may be subject to penalties\n"
     "- An Add/Drop period typically falls early in each semester\n\n"
     "For this academic year's dates:\n"
     "- Visit https://cvsu.edu.ph (and your campus's page)\n"
     "- Visit https://admission.cvsu.edu.ph for application schedules\n"
     "- Follow the official CvSU Facebook page\n"
     "- For verified phone/email contacts, see the official campus directory\n\n"
     "Note: always check the academic calendar of your specific CvSU campus.\n\n"
     "I can also walk you through the enrollment process step by step — just ask."),
    ("enrollment_schedule", 1, "Ang enrollment schedule ng CvSU ay inihahayag",
     "Ang enrollment schedule ng CvSU ay inihahayag sa www.cvsu.edu.ph at sa opisyal na "
     "Facebook page. Sa pangkalahatan: first semester — Hunyo hanggang Hulyo; second semester "
     "— Nobyembre hanggang Disyembre; summer — Abril hanggang Mayo. Para sa tiyak na petsa, "
     "subaybayan ang opisyal na announcements ng CvSU. Kung kailangan ninyo ang mga hakbang "
     "sa enrollment, itanong lang po."),
    ("admissions_requirements", 0, "ADMISSION TO CvSU (per the official",
     "Here's how to apply to CvSU, step by step (from the official Office of Student Affairs "
     "and Services and the admission portal):\n\n"
     "1. File the online application at https://admission.cvsu.edu.ph/ — encode your "
     "information in the registration/application form.\n"
     "2. Print the accomplished application form on A4-size bond paper.\n"
     "3. Prepare these documents (typical for SHS-graduate applicants):\n"
     "   - Certified true copy of Grade 12 Report Card (Form 138)\n"
     "   - Certificate of Good Moral Character\n"
     "   - 2 copies of 1x1 ID picture with name tag\n"
     "   - Short ordinary folder\n"
     "4. Submit them to OSAS at your target campus for exam scheduling (typically "
     "Mondays–Thursdays, 7 AM–6 PM).\n"
     "5. Take the CvSU Entrance Examination.\n\n"
     "A few program-specific rules:\n"
     "- Applicants for Architecture, Computer Science, and Engineering must additionally bring "
     "their Grade 11 Report Card for evaluation. Engineering programs accept STEM-strand "
     "graduates.\n"
     "- BS Psychology applicants require a GPA of 85 or higher.\n"
     "- Transferee applicants must have a GPA of 2.00 (equivalent of 85%) or better with no "
     "failed grade.\n\n"
     "For the current cycle's exact requirements and deadlines, see "
     "https://admission.cvsu.edu.ph/ and https://cvsu.edu.ph.\n\n"
     "Want to know about the entrance exam schedule or scholarships next? Just ask."),
    ("admissions_requirements", 1, "Mga pangunahing requirements para sa CvSU admission",
     "Mga pangunahing requirements para sa CvSU admission: mag-online application sa "
     "admission.cvsu.edu.ph, i-print sa A4 bond paper, ihanda ang certified true copy ng "
     "Grade 12 Report Card, Good Moral Certificate, 2 1x1 ID pictures na may name tag, at "
     "short ordinary folder. Isumite sa OSAS ng inyong campus para sa exam scheduling. Para "
     "sa Architecture/CS/Engineering, magdala rin ng Grade 11 Report Card (Engineering: STEM "
     "strand). BS Psychology: GPA 85+. Transferee: GPA 2.00 (85%) at walang failed grade. "
     "Kung gusto ninyong malaman ang entrance exam o mga scholarship, itanong lang po. "
     "Pinagmulan: https://admission.cvsu.edu.ph/"),
    ("tuition_fees", 0, "CvSU TUITION FEES & FREE EDUCATION:",
     "Good news: under RA 10931 (Universal Access to Quality Tertiary Education Act of 2017), "
     "qualified Filipino undergraduate students at SUCs like CvSU pay no tuition fee.\n\n"
     "Other school fees are also covered as defined by the law and implementing rules.\n\n"
     "Who generally qualifies for free tuition:\n"
     "- Filipino citizen\n"
     "- Passes admission requirements\n"
     "- Maintains the required academic standing\n"
     "- Enrolled in an undergraduate degree program\n"
     "- Has not yet earned a bachelor's degree\n\n"
     "What you may still pay (varies):\n"
     "- Replacement of school ID or handbook\n"
     "- Certain optional items (uniform, books)\n"
     "- Personal expenses (transportation, meals)\n"
     "- Graduate-school tuition (RA 10931 may not apply to graduate programs)\n\n"
     "Scholarships & financial aid may include:\n"
     "- Tertiary Education Subsidy (TES) for living-cost stipends\n"
     "- Government scholarships\n"
     "- Private scholarships\n"
     "- Academic merit scholarships\n\n"
     "For the current fee schedule, the precise list of fees covered/not covered, "
     "graduate-program tuition rates, and verified Cashier's Office contact details, please "
     "check www.cvsu.edu.ph or contact your campus Cashier's Office directly.\n\n"
     "I can also tell you more about scholarship programs or the RA 10931 details — just ask."),
    ("tuition_fees", 1, "Sa ilalim ng RA 10931, libre ang tuition",
     "Sa ilalim ng RA 10931, libre ang tuition para sa mga qualified na Filipino students sa "
     "CvSU at iba pang SUC. May miscellaneous fees pa rin na maaaring bayaran (lab, library, "
     "ID, atbp.) na nag-iiba ayon sa kurso at campus. Para sa tiyak na halaga, mangyaring "
     "magtanong sa Cashier's Office ng inyong campus o bisitahin ang www.cvsu.edu.ph. Kung "
     "gusto ninyong malaman ang mga scholarship, itanong lang po."),
    ("scholarship", 0, "CvSU offers several scholarship and financial-assistance programs, including (per",
     "CvSU offers several scholarship and financial-assistance programs. They include:\n"
     "- CvSU State Scholarship\n"
     "- Job Experience Program\n"
     "- RA 7160 scholarship\n"
     "- Entrance Scholarships for Valedictorian, Salutatorian, and 1st/2nd/3rd Honorable "
     "Mention high-school graduates\n"
     "- Government scholarships (e.g., DOST-SEI, CHED) and LGU scholarships availed through CvSU\n"
     "- Tertiary Education Subsidy (TES) and free tuition under RA 10931 for qualified "
     "undergraduate Filipino students\n\n"
     "Application is managed by the Financial Assistance Services Unit and distributed through "
     "OSAS. You'll typically need an accomplished scholarship form and supporting documents "
     "such as your parents' joint income tax return or a BIR Affidavit of Non-Filing.\n\n"
     "For current eligibility, deadlines, and the specific scholarships open at your campus, "
     f"visit OSAS or {_OSAS_URL}.\n\n"
     f"Source: {_OSAS_URL} (Office of Student Affairs and Services and Citizens Charter)\n\n"
     "I can also explain who qualifies for free tuition — just ask for \"RA 10931 details\"."),
    ("free_tuition_law_details", 0, "RA 10931 — Universal Access to Quality Tertiary Education Act of 2017 — provides free tuition and other school fees (per",
     "RA 10931 — Universal Access to Quality Tertiary Education Act of 2017 — provides free "
     "tuition and other school fees (as defined by the law's implementing rules) for qualified "
     "Filipino students in undergraduate programs at state universities and colleges, "
     "including CvSU.\n\n"
     "Who's generally eligible (verify current rules with UniFAST and your CvSU Cashier/OSAS "
     "office):\n"
     "- Filipino citizen\n"
     "- Admitted to CvSU\n"
     "- Enrolled in an undergraduate degree program\n"
     "- Maintains the academic standing required by the institution and the law\n"
     "- Has not earned a previous undergraduate degree\n\n"
     "Good to know:\n"
     "- The Tertiary Education Subsidy (TES) is a separate cash subsidy under the same law, "
     "processed via UniFAST — priority goes to students from low-income households.\n"
     "- RA 10931 may not cover graduate programs the same way as undergraduate programs.\n"
     "- Students may opt out of the free-tuition benefit if they wish.\n"
     "- Some fees may still apply (replacement IDs, certain personal items, etc.).\n\n"
     "For the verified, current implementation at CvSU, please check https://cvsu.edu.ph or "
     "contact your campus Cashier/OSAS.\n\n"
     "I can also list CvSU's scholarship programs — just ask."),
    ("academic_policies", 0, "CvSU ACADEMIC POLICIES & GRADING SYSTEM:",
     "CvSU uses a 1.0–5.0 grading scale, and the minimum passing grade is 3.0 (75%).\n\n"
     "Grading scale:\n"
     "1.0 = Excellent | 1.25 = Superior | 1.5 = Very Good\n"
     "1.75 = Good | 2.0 = Satisfactory | 2.25 = Fairly Satisfactory\n"
     "2.5 = Passing | 2.75 = Conditional Pass | 3.0 = Passed (75%)\n"
     "5.0 = Failed | INC = Incomplete | W = Withdrawn\n\n"
     "GWA: weighted average of all course grades × credit units.\n\n"
     "Incomplete (INC):\n"
     "- Must be completed within 1 academic year\n"
     "- Becomes 5.0 (Failed) if not resolved\n\n"
     "Leave of absence (LOA):\n"
     "- Approved by the Dean; maximum 2 consecutive semesters\n"
     "- Submit LOA form to the Registrar's Office\n\n"
     "Shifting:\n"
     "- Requires Dean's approval; subject to slot availability\n"
     "- May need to retake non-credited subjects\n\n"
     "Retention: varies per college — consult your department for specific GWA requirements.\n\n"
     "Contact the Registrar's Office for official grade records and policy clarifications.\n\n"
     "I can also explain how to remove an INC grade or how shifting works — just ask."),
    ("academic_policies", 1, "Ang passing grade sa CvSU ay 3.0 (75%).",
     "Ang passing grade sa CvSU ay 3.0 (75%). Gumagamit ang CvSU ng 1.0–5.0 grading scale. "
     "Ang GWA ay kinukuha mula sa weighted average ng lahat ng iyong grades. Ang INC grade ay "
     "kailangang ayusin sa loob ng isang taon. Para sa LOA, course shifting, o retention "
     "policy, makipag-ugnayan sa iyong Dean at sa Registrar's Office. Kung gusto ninyong "
     "malaman kung paano tanggalin ang INC, itanong lang po."),
    ("nstp", 0, "NATIONAL SERVICE TRAINING PROGRAM (NSTP) AT CvSU:",
     "NSTP (National Service Training Program) is mandatory for all first-year college "
     "students at CvSU — 2 semesters (NSTP 1 & NSTP 2).\n\n"
     "You choose one of three components:\n"
     "- ROTC: Reserve Officers' Training Corps — military/AFP training\n"
     "- CWTS: Civic Welfare Training Service — community development projects\n"
     "- LTS: Literacy Training Service — teaching literacy in communities\n\n"
     "Enrolling:\n"
     "- Register during first-year enrollment\n"
     "- Coordinate with your college/department for available components\n"
     "- Some campuses may offer limited components — check with OSAS\n\n"
     "To complete it:\n"
     "- NSTP certificate is required for graduation\n"
     "- Must complete both NSTP 1 and NSTP 2\n\n"
     "For questions, contact the Office of Student Affairs & Services (OSAS) or your "
     "department adviser."),
    ("international_students", 0, "CvSU INTERNATIONAL / FOREIGN STUDENT ADMISSIONS:",
     "CvSU accepts foreign nationals, subject to CHED and university regulations.\n\n"
     "Typical requirements:\n"
     "- Accomplished application form\n"
     "- Authenticated/apostilled academic credentials (HS diploma, transcripts)\n"
     "- Passport copy (biodata page)\n"
     "- Student Visa (9F) — typically processed after admission\n"
     "- English-proficiency proof (if from a non-English-speaking country)\n"
     "- Medical/health clearance\n"
     "- Police clearance (NBI or equivalent from home country)\n\n"
     "Typical process:\n"
     "1. Submit application to the Office of Admissions (Main Campus, Indang)\n"
     "2. Await credential evaluation and Notice of Admission (NOA)\n"
     "3. Process the 9F Student Visa with the Bureau of Immigration\n"
     "4. Complete enrollment procedures\n\n"
     "Tuition note: free tuition under RA 10931 may not apply to foreign students. Contact the "
     "Cashier's Office for the applicable fees.\n\n"
     "For verified contact details:\n"
     "Office of Admissions — email: admissions@cvsu.edu.ph\n"
     "Website: www.cvsu.edu.ph (use the official directory for current phone numbers).\n\n"
     "For the OSAS requirements and testing fee, just ask about \"foreign student admission\"."),
    ("online_programs", 0, "CvSU ONLINE & FLEXIBLE LEARNING PROGRAMS:",
     "CvSU has three flexible-learning options: ETEEAP, flexible/blended learning, and modular "
     "distance learning (MDL).\n\n"
     "ETEEAP (Expanded Tertiary Education Equivalency and Accreditation Program):\n"
     "- For working professionals seeking a college degree through prior learning recognition\n"
     "- Credits work experience, training, and non-formal education toward a degree\n"
     "- Available for select programs — inquire at the Office of Academic Affairs\n"
     "- Requirements: Work portfolio, assessment/evaluation, applicable fees\n\n"
     "Flexible / blended learning:\n"
     "- Some programs offer hybrid delivery (face-to-face + online components)\n"
     "- Availability varies per campus and program per semester\n"
     "- Requires internet access and compatible device\n\n"
     "Modular distance learning (MDL):\n"
     "- Learning modules delivered digitally or in print\n"
     "- Available for select programs as approved by CHED\n\n"
     "How to apply for ETEEAP:\n"
     "1. Inquire at the Office of Academic Affairs (Main Campus)\n"
     "2. Submit your educational & professional portfolio\n"
     "3. Undergo assessment and evaluation\n"
     "4. Enroll if qualified\n\n"
     "Contact:\n"
     "Office of Academic Affairs | Email: academic@cvsu.edu.ph\n"
     "Website: www.cvsu.edu.ph\n\n"
     "I can also tell you about CvSU's graduate programs — just ask."),
    ("online_programs", 1, "Oo, may flexible learning programs ang CvSU!",
     "Oo, may flexible learning programs ang CvSU! Ang ETEEAP ay para sa mga nagtatrabaho na "
     "gustong makakuha ng degree batay sa kanilang karanasan. May iba ring programs na "
     "nag-aalok ng blended o modular learning. Para sa ETEEAP, makipag-ugnayan sa Office of "
     "Academic Affairs sa Main Campus. Kung gusto ninyong malaman ang mga graduate program, "
     "itanong lang po."),
    ("vision_mission", 0, "Per the official University page, CvSU is mandated",
     "CvSU's vision is \"The premier university in historic Cavite globally recognized for "
     "excellence in character development, academics, research, innovation and sustainable "
     "community engagement.\"\n\n"
     "Its mission:\n- \"Cavite State University shall provide excellent, equitable and "
     "relevant educational opportunities in the arts, sciences and technology through quality "
     "instruction and responsive research and development activities. It shall produce "
     "professional, skilled and morally upright individuals for global competitiveness.\"\n\n"
     "The University's core values are Truth, Integrity, Excellence, and Service.\n\n"
     "CvSU is mandated under Section 2 of Republic Act No. 8468 to \"primarily provide advance "
     "instruction and professional training in agriculture, science and technology, education "
     "and other related fields, undertake research and extension services, and provide "
     "progressive leadership in these areas.\"\n\n"
     "Its quality policy commits to the highest standards of education, values its "
     "stakeholders, strives for continual improvement of its products and services, and "
     "upholds the University's tenets of Truth, Excellence, and Service to produce globally "
     "competitive and morally upright individuals.\n\n"
     + _ASCEND_EN + "\n\n"
     f"Source: {_VM_URL} (official University page)"),
    ("vision_mission", 2, "Ayon sa opisyal na pahina ng Pamantasan, ang mithiin",
     "Ang mithiin (vision) ng CvSU ay \"Ang nangungunang pamantasan sa makasaysayang Kabite na "
     "kinikilala sa kahusayan sa paghubog ng mga indibidwal na may pandaigdigang kakayahan at "
     "kagandahang asal.\"\n\n"
     "Ang hangarin (mission) nito ay \"Ang Cavite State University ay makapagbigay ng mahusay, "
     "pantay at makabuluhang edukasyon sa sining, agham at teknolohiya sa pamamagitan ng may "
     "kalidad na pagtuturo at tumutugon sa pangangailangang pananaliksik at mga gawaing "
     "pangkaunlaran.\"\n\n"
     "Ang mga core values ng Pamantasan ay Truth (Katotohanan), Integrity (Integridad), "
     "Excellence (Kahusayan), at Service (Paglilingkod).\n\n"
     "Ang kasalukuyang estratehiya ng Pamantasan ay ang CvSU ASCEND (Advancing Sustained "
     "Change and Excellence for National Development), ang 2025-2028 Strategic Framework nito, "
     "na ipinatutupad sa pamamagitan ng apat-na-taong IDEAL Plan, habang tinatahak ng CvSU ang "
     "landas tungo sa pagiging Premier Global University.\n\n"
     f"Pinagmulan (opisyal na pahina ng Pamantasan): {_VM_URL}"),
]

# Citizens' Charter procedure intents: replace the ALL-CAPS title line with a
# lead sentence and sentence-case the "WHO MAY AVAIL:" / "FEES:" labels. The
# "Source: CvSU Citizen's Charter 2026 — ..., p.NNN" line is untouched.
CHARTER_LEADS: list[tuple[str, int, str, str]] = [
    ("student_refund", 0, "STUDENT REFUND (Accounting Office):",
     "Here's how to request a student refund from the Accounting Office."),
    ("student_clearance_signing", 0, "STUDENT CLEARANCE — Accounting Office signing:",
     "Here's how to get your student clearance signed by the Accounting Office."),
    ("foreign_student_admission", 0, "FOREIGN STUDENT ADMISSION (OSAS):",
     "Here's how foreign students apply for admission through OSAS."),
    ("counseling_procedure", 0, "OSAS COUNSELING PROCEDURE:",
     "Here's how to request a counseling session at OSAS."),
    ("off_campus_activities", 0, "OFF-CAMPUS ACTIVITIES — Student Organizations (OSAS):",
     "Here's how student organizations get an off-campus activity approved through OSAS."),
    ("library_card_replacement", 0, "LIBRARY ACCOUNT STICKER REPLACEMENT:",
     "Here's how to get a replacement library account sticker."),
    ("tcp_admission", 0, "TEACHER CERTIFICATE PROGRAM (TCP) ADMISSION:",
     "Here's how to apply to the Teacher Certificate Program (TCP)."),
    ("visitor_accommodation", 0,
     "VISITOR ACCOMMODATIONS (Public Affairs and Communications Office / PACO):",
     "Here's how an external group can arrange a visit through the Public Affairs and "
     "Communications Office (PACO)."),
]

_CAPS_LABEL_RE = re.compile(r"^([A-Z][A-Z /&'-]*[A-Z])((?:\s*\([^)]*\))?):", re.MULTILINE)


def _charter_rewrite(old: str, title: str, lead: str) -> str:
    first, rest = old.split("\n", 1)
    assert first == title, (first, title)
    rest = _CAPS_LABEL_RE.sub(lambda m: m.group(1).capitalize() + m.group(2) + ":", rest)
    return lead + "\n" + rest


# Uppercase words whose disappearance is intentional (header words that the
# lead sentence rephrases); every other acronym must survive verbatim.
DROPPED_CAPS_OK = {
    "AFTER", "PROCEDURE", "IMPORTANT", "CURRENT", "COVERAGE", "ELIGIBILITY", "NOTES",
    "SYSTEM", "POLICIES", "COMPLETION", "INTERNATIONAL", "ACTIVITIES", "VISITOR",
    "ACCOMMODATIONS",
}

# ── Fact extraction ──────────────────────────────────────────────────────────
URL_RE = re.compile(r"https?://[^\s<>\)\]\"',]+")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
HONORIFIC_RE = re.compile(
    r"\b(?:Dr|Prof|Asst|Asso|Hon|Mr|Ms)\. +(?:(?:Prof|Ma)\. +)?"
    r"[A-Z][\w'ñÑ-]*(?: +(?:[A-Z]\.|[A-Z][\w'ñÑ-]*))*"
)
NUMBER_RE = re.compile(r"\d+(?:[.,:]\d+)*")
CAPS_RE = re.compile(r"\b[A-Z][A-Z0-9&']+\b")
_LIST_ORDINAL_RE = re.compile(r"^\s*\d{1,2}\.\s", re.MULTILINE)


def _facts(text: str) -> dict:
    no_ordinals = _LIST_ORDINAL_RE.sub(" ", text)
    return {
        "url": Counter(u.rstrip(".") for u in URL_RE.findall(text)),
        "email": Counter(EMAIL_RE.findall(text)),
        "name": Counter(HONORIFIC_RE.findall(text)),
        "number": Counter(NUMBER_RE.findall(no_ordinals)),
    }


def fact_problems(old: str, new: str) -> list[str]:
    """Facts of `old` missing from `new`. Multisets for URL/email/name; a number
    only has to survive once (headers/labels may have repeated it)."""
    problems = []
    fo, fn = _facts(old), _facts(new)
    for kind in ("url", "email", "name"):
        missing = fo[kind] - fn[kind]
        if kind == "url":
            # A URL repeated in the old text may legitimately be cited once less.
            missing = Counter({k: v for k, v in missing.items() if k not in fn[kind]})
        if missing:
            problems.append(f"{kind} missing: {sorted(missing)}")
    num_missing = set(fo["number"]) - set(fn["number"])
    if num_missing:
        problems.append(f"number missing: {sorted(num_missing)}")
    new_lower = new.lower()
    for word in set(CAPS_RE.findall(old)):
        if word in new:
            continue
        if word in DROPPED_CAPS_OK or word.lower() in new_lower:
            continue
        problems.append(f"acronym/caps word missing: {word}")
    return problems


def style_problems(new: str) -> list[str]:
    problems = []
    if "**" in new:
        problems.append("contains ** (Messenger shows it raw)")
    if new.startswith(("Assuming the Main Campus", "Ipagpalagay nating")):
        problems.append("starts with a campus assumption (_maybe_state_assumption owns that)")
    if "Salamat sa inyong tanong" in new:
        problems.append("stiff 'Salamat sa inyong tanong' closer")
    if re.match(r"^(Per the|As per|Ayon sa opisyal)", new):
        problems.append("attribution lead-in")
    if re.search(r"\b(based on|according to the)\b", new, re.IGNORECASE):
        problems.append("'based on/according to the' wording")
    for line in new.splitlines():
        s = line.strip()
        if len(s) > 12 and re.match(r"^[A-Z0-9][A-Z0-9 ,()/&'’.:–—-]{2,}:?$", s):
            problems.append(f"ALL-CAPS header line: {s!r}")
    return problems


# ── Served-variant check ─────────────────────────────────────────────────────

def served_indices(responses: list[str]) -> tuple[int, int]:
    """(EN index, FIL index) exactly as HybridChatbot._select_response picks."""
    from api.hybrid_chatbot import _filipino_ratio
    ratios = [_filipino_ratio(r) for r in responses]
    out = []
    for best in (min(ratios), max(ratios)):
        pool = [i for i, r in enumerate(ratios) if r == best]
        out.append(max(pool, key=lambda i: ("http" in responses[i], len(responses[i]),
                                            responses[i])))
    return out[0], out[1]


# ── IO helpers ───────────────────────────────────────────────────────────────

def _read_json(path: Path):
    raw = path.read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    trailing = raw.endswith(b"\n")
    return json.loads(raw.decode("utf-8")), newline, trailing


def _write_json(path: Path, doc, newline: str, trailing: bool) -> None:
    text = json.dumps(doc, ensure_ascii=False, indent=2)
    if trailing:
        text += "\n"
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)


def build_plan(doc) -> list[tuple[str, int, str, str]]:
    """[(tag, idx, old, new)] for variants that still need rewriting."""
    by_tag = {it["tag"]: it for it in doc["intents"]}
    plan, errors = [], []
    specs = [(t, i, p, n) for t, i, p, n in REWRITES]
    for tag, idx, title, lead in CHARTER_LEADS:
        cur = by_tag[tag]["responses"][idx]
        if cur.startswith(lead):
            specs.append((tag, idx, lead, cur))  # already rewritten
        else:
            specs.append((tag, idx, title, _charter_rewrite(cur, title, lead)))
    for tag, idx, prefix, new in specs:
        if tag not in by_tag:
            errors.append(f"{tag}: intent missing")
            continue
        cur = by_tag[tag]["responses"][idx]
        if cur == new:
            continue
        if not cur.startswith(prefix):
            errors.append(f"{tag}[{idx}]: text drifted (neither old prefix nor new text): "
                          f"{cur[:70]!r}")
            continue
        plan.append((tag, idx, cur, new))
    if errors:
        raise SystemExit("ABORT:\n  " + "\n  ".join(errors))
    return plan


def check_plan(doc, plan) -> None:
    by_tag = {it["tag"]: it for it in doc["intents"]}
    errors = []
    for tag, idx, old, new in plan:
        for p in fact_problems(old, new):
            errors.append(f"{tag}[{idx}] {p}")
        for p in style_problems(new):
            errors.append(f"{tag}[{idx}] {p}")
    for tag in sorted({t for t, *_ in plan}):
        before = list(by_tag[tag]["responses"])
        after = list(before)
        for t, idx, old, new in plan:
            if t == tag:
                after[idx] = new
        if served_indices(before) != served_indices(after):
            errors.append(f"{tag}: served variant moved {served_indices(before)} -> "
                          f"{served_indices(after)}")
    if errors:
        raise SystemExit("ABORT (no files written):\n  " + "\n  ".join(errors))


def patch_db(plan) -> int:
    con = sqlite3.connect(str(INTENTS_DB))
    try:
        cur = con.cursor()
        for tag, idx, old, new in plan:
            cur.execute(
                "UPDATE responses SET response_text=? WHERE response_text=? AND "
                "intent_id=(SELECT id FROM intents WHERE tag=?)", (new, old, tag))
            if cur.rowcount != 1:
                con.rollback()
                raise SystemExit(f"ABORT: DB update for {tag}[{idx}] hit {cur.rowcount} rows")
        con.commit()
    finally:
        con.close()
    return len(plan)


def _db_tables() -> list[str]:
    con = sqlite3.connect(str(INTENTS_DB))
    try:
        return sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        con.close()


def _backup_db(dest: str) -> None:
    # The DB runs in WAL mode: copying the .db file alone can miss committed
    # pages still in the -wal file. The backup API copies a consistent snapshot.
    src, dst = sqlite3.connect(str(INTENTS_DB)), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def post_check() -> None:
    doc, _, _ = _read_json(INTENTS_JSON)
    rmap, _, _ = _read_json(RESP_MAP)
    con = sqlite3.connect(str(INTENTS_DB))
    db = {}
    for tag, text in con.execute(
            "SELECT i.tag, r.response_text FROM responses r JOIN intents i "
            "ON i.id = r.intent_id ORDER BY r.id"):
        db.setdefault(tag, []).append(text)
    con.close()
    js = {it["tag"]: it["responses"] for it in doc["intents"]}
    mp = {k: v for k, v in rmap.items() if k != "llm_unavailable"}
    assert "llm_unavailable" in rmap, "llm_unavailable lost from responses_map"
    bad = [t for t in js if not (js[t] == mp.get(t) == db.get(t))]
    assert not bad and set(js) == set(mp) == set(db), f"3-copy drift: {bad}"
    tables_before = _db_tables()
    mtime = INTENTS_DB.stat().st_mtime_ns
    from intents_db import load_intents
    loaded = load_intents(json_path=str(INTENTS_JSON), db_path=str(INTENTS_DB))
    # A rebuild os.remove()s the file and drops the map/seasons tables.
    assert INTENTS_DB.stat().st_mtime_ns == mtime, "load_intents() rebuilt the DB"
    assert _db_tables() == tables_before, "load_intents() changed the DB tables"
    assert {i["tag"]: i["responses"] for i in loaded} == js
    print("post-check OK: JSON == responses_map == DB; load_intents() did not rebuild")


def main() -> None:
    dry = "--dry-run" in sys.argv
    doc, nl_j, tr_j = _read_json(INTENTS_JSON)
    rmap, nl_m, tr_m = _read_json(RESP_MAP)
    plan = build_plan(doc)
    print(f"variants to rewrite: {len(plan)}")
    if not plan:
        post_check()
        return
    check_plan(doc, plan)
    for tag, idx, _old, _new in plan:
        print(f"  {tag}[{idx}]")
    by_tag = {it["tag"]: it for it in doc["intents"]}
    for tag, idx, old, new in plan:
        if len(rmap.get(tag, [])) <= idx or rmap[tag][idx] != old:
            raise SystemExit(f"ABORT: responses_map[{tag}][{idx}] differs from the JSON")
    if dry:
        print("dry run: nothing written")
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for path in (INTENTS_JSON, RESP_MAP):
        shutil.copy2(path, f"{path}.bak_{ts}")
    _backup_db(f"{INTENTS_DB}.bak_{ts}")
    # DB first: it is the only transactional copy, so a failed UPDATE aborts
    # before either JSON file is touched.
    print(f"  cavsu_intents.db   : {patch_db(plan)} replaced")
    for tag, idx, old, new in plan:
        by_tag[tag]["responses"][idx] = new
        rmap[tag][idx] = new
    _write_json(INTENTS_JSON, doc, nl_j, tr_j)
    _write_json(RESP_MAP, rmap, nl_m, tr_m)
    print(f"  cavsu_intents.json : {len(plan)} replaced")
    print(f"  responses_map.json : {len(plan)} replaced")
    print(f"backups written with suffix .bak_{ts}")
    post_check()


if __name__ == "__main__":
    main()
