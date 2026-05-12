"""
Sanitize and deduplicate cavsu_intents.json.

Transformations:
  1. Drop noisy templated patterns ("Please help me with...", "Sana matulungan
     mo ako sa...", "Quick question - ...", "for CvSU Indang?", etc.). These
     pollute classification without adding linguistic signal.
  2. Deduplicate patterns (case-insensitive) within each tag.
  3. Move misplaced pure-pleasantry patterns ("hello", "thanks", "bye") to the
     correct tag (greeting / thanks / goodbye), regardless of where they
     currently live.
  4. Sanitize responses: REPLACE every response that contains an unverified
     factual claim (specific campus counts, fabricated statistics, made-up
     phone numbers, specific establishment-year lists per campus, AACCUP /
     COE designations, named-facility specifics like "Ladislao Diwa Memorial
     Library", etc.) with a safe response that directs the student to the
     official CvSU site.
  5. Resolve obviously overlapping tags: keep one canonical tag and remove
     near-empty duplicates ('courses' folded into 'courses_offered',
     'campus_branches' folded into 'campus_specific', empty 'enrollment'
     folded into 'enrollment_procedure').

What is NOT changed:
  - Verifiable, general facts: 1906 origins (Indang Intermediate School, Thomasite
    era), university status in 1998 via RA 8468, main campus name + general
    location, motto, "Iskolar para sa Bayan", RA 10931, NSTP under RA 9163,
    the 1.0–5.0 grading scale.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
SRC = ROOT / "intents_raw.json"
OUT = ROOT / "intents_cleaned.json"

with SRC.open(encoding="utf-8") as f:
    data = json.load(f)

intents = data["intents"]

# ----------------------------------------------------------------------
# Noise filters
# ----------------------------------------------------------------------
NOISE_PREFIXES = [
    "please help me with ",
    "sana matulungan mo ako sa ",
    "hi, i just want to ask - ",
    "quick question - ",
    "can i ask about ",
    "i want to know ",
    "gusto ko pong malaman ang ",
    "can you tell me ",
    "i have a question about ",
]
NOISE_SUFFIXES = [
    " for cvsu indang?",
    " for cvsu bacoor?",
    " for cvsu imus?",
    " for 2026?",
    " for freshmen?",
    " please answer asap.",
    " it's urgent.",
    " thanks!",
    " asap po",
    " po?",
    " please?",
]

def strip_template_wrapping(p: str) -> str | None:
    """Return the core pattern stripped of wrapper template, or None if the
    pattern is pure wrapper (no semantic content)."""
    pl = p.strip()
    low = pl.lower()
    matched_prefix = None
    for pre in NOISE_PREFIXES:
        if low.startswith(pre):
            matched_prefix = pre
            break
    matched_suffix = None
    for suf in NOISE_SUFFIXES:
        if low.endswith(suf):
            matched_suffix = suf
            break
    if not matched_prefix and not matched_suffix:
        return pl

    # Strip prefix
    core = pl
    if matched_prefix:
        core = core[len(matched_prefix):]
    if matched_suffix:
        core = core[:len(core) - len(matched_suffix)]
    core = core.strip().strip("?.,!")
    if len(core) < 3:
        return None
    return core

# ----------------------------------------------------------------------
# Misplaced pleasantries
# ----------------------------------------------------------------------
PLEASANTRY_MAP = {
    # Will be matched case-insensitively against the *core* of a pattern.
    "hello":          "greeting",
    "hi":             "greeting",
    "hi there":       "greeting",
    "hey":            "greeting",
    "good morning":   "greeting",
    "good afternoon": "greeting",
    "good evening":   "greeting",
    "good day":       "greeting",
    "kumusta":        "greeting",
    "magandang araw": "greeting",

    "thanks":         "thanks",
    "thank you":      "thanks",
    "salamat":        "thanks",
    "maraming salamat": "thanks",

    "bye":            "goodbye",
    "goodbye":        "goodbye",
    "see you":        "goodbye",
    "paalam":         "goodbye",
}

def normalized_core(p: str) -> str:
    return re.sub(r"\s+", " ", p.lower()).strip(" ?.,!")

# ----------------------------------------------------------------------
# Sanitized responses (replace ones containing unverified claims)
# ----------------------------------------------------------------------
SAFE = {
    "about_cvsu": [
        "Cavite State University (CvSU) is a state university in Cavite, Philippines. It traces its roots to 1906 (Indang Intermediate School, founded during the Thomasite era) and was granted university status in 1998 through Republic Act No. 8468. The Main Campus — Don Severino delas Alas Campus — is in Indang, Cavite, and CvSU operates additional campuses across the province. Its motto is 'Truth, Excellence, Service' and its identity is captured in the phrase 'Iskolar para sa Bayan'. For verified current numbers — total campuses, enrollment, accreditations, designations, and university leadership — please refer to the official site at https://cvsu.edu.ph. I'd rather point you to the authoritative source than quote figures that may be outdated.",
        "CvSU is a state university serving Cavite. Its roots go back to 1906, and it became a university in 1998 (RA 8468). The Main Campus is in Indang, Cavite, and additional campuses operate across the province. Motto: Truth, Excellence, Service. For the verified, current list of campuses, leadership, accreditations, and statistics, please visit https://cvsu.edu.ph. What specifically would you like to know?",
        "CvSU — Cavite State University — is the Iskolar para sa Bayan institution in Cavite, with roots dating to 1906 and university status granted in 1998 via RA 8468. The main campus is in Indang, with additional campuses across the province. For verified enrollment numbers, the complete campus list, and program availability, please visit https://cvsu.edu.ph — that's the authoritative source.",
    ],

    "campus_location": [
        "CvSU's Main Campus is Don Severino delas Alas Campus in Indang, Cavite, Philippines. CvSU operates additional campuses across the province of Cavite. The exact list of campuses and their full addresses can change over time as the university grows, so for the verified roster — including addresses, contact details, and program offerings per campus — please refer to https://cvsu.edu.ph.",
        "The CvSU Main Campus (Don Severino delas Alas Campus) is in Indang, Cavite. CvSU has additional campuses across the province. From neighboring towns like Dasmariñas or Tagaytay, public transport (jeepney or bus) typically connects to the Indang main campus. For the complete and current list of CvSU campuses and addresses, please visit the official site https://cvsu.edu.ph.",
        "Ang CvSU Main Campus ay nasa Indang, Cavite. Mayroon ding ibang campus ang CvSU sa lalawigan ng Cavite. Para sa kumpleto at napapanahong listahan ng mga campus at kanilang address, bisitahin ang opisyal na website na https://cvsu.edu.ph.",
    ],

    "campus_facilities": [
        "CvSU facilities typically include libraries, science and computer laboratories, a Health Service Unit, a Guidance and Counseling Office, student dormitories at selected campuses, a cafeteria, a chapel, sports facilities, and student-organization spaces. Specific facilities vary per campus. For the verified, up-to-date list of facilities, research and extension units, and named offices at your campus of interest, please check https://cvsu.edu.ph or contact that campus's Student Affairs office.",
        "CvSU has academic facilities (libraries, computer/science labs), student services (health unit, guidance office, dormitories at select campuses), and student-life facilities (cafeteria, chapel, sports areas). Available facilities differ by campus. For the specific facilities at your campus of interest, please check https://cvsu.edu.ph or contact that campus directly.",
        "Karaniwang may library, science at computer labs, health clinic, guidance office, dormitoryo (sa ilang campus), cafeteria, kapilya, at sports facilities ang CvSU. Ang available na pasilidad ay nag-iiba bawat campus. Para sa tiyak na pasilidad sa inyong campus, makipag-ugnayan sa Student Affairs office o bisitahin ang https://cvsu.edu.ph.",
    ],

    "library": [
        "CvSU operates campus libraries that typically provide books, periodicals, theses, research journals, digital databases, study spaces, and reference services. Library hours, borrowing limits, loan periods, and overdue policies vary per campus — please verify these specifics with your campus librarian. For online resources and the e-library portal (where available), please check https://cvsu.edu.ph.",
        "The campus libraries at CvSU provide books, periodicals, theses, and digital resources to support students' studies. To borrow books, present your CvSU ID and library card. Library hours, borrowing limits, and fines vary per campus — please verify with your campus librarian. An e-Library portal may also be available — check https://cvsu.edu.ph.",
        "Ang mga library sa CvSU ay nag-aalok ng mga libro, journal, thesis, at digital resources. Bisitahin ang library sa inyong campus at magdala ng student ID para magparehistro ng library card. Ang oras, limit sa paghiram, at fines ay nag-iiba bawat campus — kumpirmahin sa inyong librarian.",
    ],

    "campus_specific": [
        "CvSU operates a Main Campus in Indang, plus additional campuses across the province of Cavite. Each campus offers a selection of programs — not every program is available at every campus. For the current and complete list of CvSU campuses, the programs offered at each, and their contact details, please visit https://cvsu.edu.ph and select the campus page you're interested in. I want to avoid sharing an outdated roster.",
        "CvSU has a Main Campus in Indang and additional satellite campuses across Cavite. Program availability differs per campus, and the official roster of campuses changes over time. For the verified, current list of campuses and their programs, please visit https://cvsu.edu.ph.",
        "Mayroong Main Campus sa Indang at iba pang campus ang CvSU sa buong lalawigan ng Cavite. Hindi lahat ng programa ay available sa bawat campus. Para sa kumpleto at napapanahong listahan ng CvSU campuses at ng kanilang programs, mangyaring bisitahin ang opisyal na website na https://cvsu.edu.ph.",
    ],

    "student_organizations": [
        "CvSU has a wide range of recognized student organizations covering academics, culture, the arts, sports, religion, service, student government, and campus publications. To join, watch for the organization fair during enrollment or visit the Office of Student Affairs and Services (OSAS) for the current list. The exact number of recognized organizations changes each year, so the OSAS roster is the authoritative source.",
        "CvSU has many recognized student organizations across academic, cultural, sports, religious, service, and publication categories, plus the Supreme Student Government (SSG). To join, attend the organization fair during enrollment or coordinate with OSAS. For the current list of recognized organizations, please contact OSAS at your campus.",
        "Maraming recognized student organizations sa CvSU — academic orgs, cultural groups, sports clubs, journalism, debate societies, religious orgs, at service orgs, kasama ang SSG (Supreme Student Government). Para sumali, dumalo sa org fair sa enrollment period o makipag-ugnayan sa OSAS sa inyong campus.",
    ],

    "admissions_requirements": [
        "FRESHMAN APPLICANTS — TYPICAL REQUIREMENTS (verify the current list and forms with the CvSU Admissions Office):\n- Accomplished CvSU application form\n- Form 138 (Grade 12 report card) or its equivalent\n- Certificate of Good Moral Character\n- PSA Birth Certificate (photocopy)\n- 2x2 ID pictures\n- Valid government ID of parent/guardian (where required)\n\nTYPICAL PROCESS:\n1. Watch for the application window on the official site and CvSU's verified social media.\n2. Complete the online application.\n3. Submit your documents for review and credential validation.\n4. Take the CvSU entrance examination.\n5. If qualified, receive a Notice of Admission (NOA).\n6. Complete the remaining steps (department interview/orientation, medical examination, enrollment with the Registrar).\n\nTRANSFEREE APPLICANTS additionally need: Transcript of Records (TOR) and Honorable Dismissal from the previous school; transfer credits are evaluated by the receiving college.\n\nFor the current application window, exact requirements, and the verified Admissions Office contact details, please visit https://cvsu.edu.ph.",
        "To apply to CvSU, freshmen typically need: the completed application form, Form 138, PSA birth certificate, Certificate of Good Moral, and 2x2 photos. Transferees also need a TOR and Honorable Dismissal. ALS passers should present their ALS certificate. For the current application schedule, the official requirements, and where to submit your documents, please visit https://cvsu.edu.ph.",
        "Ang mga pangunahing requirements para sa CvSU admission ay: application form, Form 138, PSA birth certificate, good moral certificate, at 2x2 photos. Para sa mga transferee, kailangan din ng transcript at honorable dismissal. Para sa kumpletong proseso at deadlines, bisitahin ang https://cvsu.edu.ph.",
    ],

    "admissions_exam": [
        "ABOUT THE CvSU ENTRANCE EXAMINATION:\n- A placement examination is required for freshman applicants and is part of the standard admission steps.\n- Coverage typically includes English, Mathematics, Science, Reading Comprehension, and Abstract Reasoning.\n- After document validation, applicants are scheduled for the exam.\n- Bring your exam permit/notice and a valid ID. Bring pencils, an eraser, and a calculator if allowed by the proctor.\n- If qualified, applicants receive a Notice of Admission (NOA) via the application portal/email.\n\nFor the current entrance-exam schedule, the official testing venues per campus, and the verified Admissions/Registrar contact details, please check https://cvsu.edu.ph.",
        "The CvSU entrance exam covers English, Math, Science, and Abstract Reasoning. It is given at the Main Campus and other testing centers. Bring your exam permit, pencils (No. 2), and a valid ID. Results are typically released after the exam is administered. For the exact schedule and venues this admission cycle, please check https://cvsu.edu.ph.",
        "Ang CvSU entrance exam ay sumasaklaw ng English, Math, Science, at Abstract Reasoning. Dalhin ang exam permit, 2 pencils (#2), at valid ID sa araw ng pagsusulit. Para sa schedule at venue, bisitahin ang https://cvsu.edu.ph.",
    ],

    "enrollment_procedure": [
        "AFTER ADMISSION (NOA received):\n1. Attend any department orientation/interview as scheduled.\n2. Complete the required medical examination.\n3. Meet with your academic adviser to plan your subjects for the semester.\n4. Register online via the Student Portal during the published enrollment window.\n5. Pay any applicable miscellaneous fees at the Cashier's Office (under RA 10931, qualified Filipino undergraduate students at SUCs do not pay tuition).\n6. Submit your Certificate of Registration (COR) to the Registrar and get/renew your student ID.\n\nContinuing students re-enroll each semester through the Student Portal with adviser approval. For the verified enrollment window, your campus's Registrar contact, and any campus-specific quirks, please check https://cvsu.edu.ph.",
        "CvSU Enrollment Steps: (1) Receive your Notice of Admission. (2) Attend orientation/interview. (3) Complete medical exam. (4) Meet with your adviser for course planning. (5) Register online via the Student Portal. (6) Pay applicable miscellaneous fees (tuition is free under RA 10931 for qualified undergraduate students). (7) Submit COR to the Registrar. (8) Receive your student ID. Continuing students enroll each semester through the Student Portal.",
        "Para sa enrollment sa CvSU: tanggapin ang NOA, dumalo sa orientation, kumpletuhin ang medical exam, mag-advising sa iyong adviser, mag-enroll sa Student Portal, bayaran ang applicable miscellaneous fees sa Cashier (libre ang tuition para sa qualified undergraduate sa ilalim ng RA 10931), at i-submit ang COR sa Registrar.",
    ],

    "tuition_fees": [
        "CvSU is covered by RA 10931 (Universal Access to Quality Tertiary Education Act of 2017). Under this law, qualified Filipino undergraduate students at state universities and colleges do not pay tuition, with certain other school fees also covered as defined by the law and its implementing rules.\n\nGENERAL ELIGIBILITY (verify current rules with CHED/UniFAST and the CvSU Cashier's/OSAS office):\n- Filipino citizen, admitted to CvSU\n- Enrolled in an undergraduate degree program\n- Maintaining the required academic standing\n- Has not yet earned an undergraduate degree\n\nWHAT YOU MAY STILL PAY (varies):\n- Items not covered by RA 10931 per implementing rules (e.g., replacement IDs/handbooks)\n- Personal costs (uniforms, books, transport, meals)\n- Graduate-program tuition (RA 10931 may not apply to graduate programs)\n\nFor the exact, current fee schedule that applies to your program and campus, please check https://cvsu.edu.ph or contact your campus's Cashier's Office directly. I don't want to quote outdated amounts.",
        "Good news — under RA 10931, tuition is free for qualified Filipino undergraduate students at SUCs including CvSU. Miscellaneous fees may still apply and vary by program and campus. For the exact fee schedule that applies to your program, please check https://cvsu.edu.ph or your campus's Cashier's Office.",
        "Sa ilalim ng RA 10931, libre ang tuition para sa mga qualified na Filipino undergraduate students sa CvSU at iba pang SUC. May miscellaneous fees pa rin na maaaring bayaran, depende sa kurso at campus. Para sa tiyak na halaga, mangyaring magtanong sa Cashier's Office o bisitahin ang https://cvsu.edu.ph.",
    ],

    "scholarship": [
        "CvSU students may access several scholarship and financial-aid channels (verify current availability and eligibility with OSAS or the Scholarship Office at your campus):\n- RA 10931 — Free Higher Education for qualified undergraduate Filipino students at SUCs.\n- Tertiary Education Subsidy (TES) — cash subsidy for living expenses, processed via UniFAST.\n- Government scholarships such as DOST-SEI (for STEM-related programs), CHED scholarships, and LGU/provincial scholarships.\n- CvSU institutional scholarships and grants.\n- Private and foundation scholarships from partner organizations.\n- Student assistantships (work-study) for qualified students.\n\nEach program has its own eligibility (academic performance, good moral standing, financial need), required documents, and deadlines. For the current list, exact criteria, and verified contact details, please refer to https://cvsu.edu.ph or contact OSAS directly.",
        "CvSU students can apply to scholarship programs from multiple sources — RA 10931, TES, DOST-SEI, CHED scholarships, CvSU institutional grants, LGU scholarships, and private scholarships. Each scholarship has its own eligibility criteria. For the current list, deadlines, and application steps, please contact the Scholarship Office or OSAS at your campus.",
        "May iba't ibang scholarship programs na maaaring abutin ng mga CvSU students: RA 10931 (free tuition), TES, DOST-SEI, CHED, CvSU institutional grants, LGU scholarships, at iba pa. Bawat scholarship ay may sariling requirements. Para sa pinakabagong listahan at deadlines, makipag-ugnayan sa Scholarship Office o OSAS ng inyong campus.",
    ],

    "academic_calendar": [
        "CvSU follows the CHED academic calendar with a First Semester, Second Semester, and a Summer Term where applicable. Each semester typically includes a midterm and final examination period, plus the regular Philippine holidays. The university also observes its Founding Anniversary and university-wide activities such as a sports week, a foundation week, and graduation.\n\nFor the official, current-year academic calendar — including the exact dates of classes, exams, holidays, the Founding Day commemoration, and graduation — please visit https://cvsu.edu.ph, follow the campus's official social media, or contact your campus Registrar. I'm avoiding posting specific dates here because they shift annually.",
        "CvSU follows CHED's academic calendar. Generally: First Semester runs roughly August to December; Second Semester runs roughly January to May; a Summer Term may follow. Specific dates shift each year — always verify with the official CvSU announcement at https://cvsu.edu.ph or your campus Registrar.",
        "Ang CvSU academic calendar ay sumusunod sa CHED guidelines. Sa pangkalahatan: First Semester — humigit-kumulang Agosto hanggang Disyembre; Second Semester — Enero hanggang Mayo; Summer — kung naaangkop. Para sa opisyal at napapanahong kalendaryo, bisitahin ang https://cvsu.edu.ph.",
    ],

    "events": [
        "CvSU hosts a calendar of events throughout the year — typically including the Founding/Charter Day commemoration, a Foundation Week, a Sports Week / Intramurals, a Research Forum or Symposium, college- and student-organization-led activities, a job/career fair, and the annual Graduation. Activities and dates vary per campus and academic year. For the verified schedule of upcoming events at your campus, please follow CvSU's official Facebook page linked from https://cvsu.edu.ph and watch the university's announcements.",
        "CvSU's calendar includes Foundation/Charter Day, Intramurals/Sportsfest, Research Symposium, Career and Job Fairs, cultural events, and the annual Graduation. For exact dates and the current year's official schedule, please follow CvSU's verified Facebook page and check https://cvsu.edu.ph.",
        "Maraming events ang CvSU sa buong taon: Foundation/Charter Day, Intramurals, Research Symposium, Career/Job Fair, cultural events, at Graduation. Para sa tiyak na petsa at opisyal na schedule, mangyaring sundan ang opisyal na CvSU Facebook page at https://cvsu.edu.ph.",
    ],

    "contact_info": [
        "CvSU CONTACT INFORMATION:\n\nMain Campus — Don Severino delas Alas Campus, Indang, Cavite\nWebsite: https://cvsu.edu.ph\n\nKEY OFFICES — please use the official campus directory on the website to find the current phone numbers and emails:\n- Office of the University Registrar\n- Office of Admissions (online portal linked from the website)\n- Office of Student Affairs and Services (OSAS)\n- Cashier's Office\n- ICT / MIS Office (for Student Portal concerns)\n- College deans' offices\n\nOther campuses each have their own directory pages and contact details on the official site. Please consult those — I don't want to give you outdated contact information.",
        "For the most current CvSU contact information — phone numbers and emails for the Main Campus in Indang and the other campuses, and office-specific contacts (Admissions, Registrar, OSAS, Cashier, etc.) — please refer to the official campus directory at https://cvsu.edu.ph. CvSU's official Facebook page is also linked from the website (please verify you're messaging the genuine page).",
        "Para sa pinakabagong contact information ng CvSU — phone numbers, emails ng Main Campus (Indang) at ibang campus, at office-specific contacts (Admissions, Registrar, OSAS, atbp.) — mangyaring bisitahin ang opisyal na directory sa https://cvsu.edu.ph. Sabihin sa akin kung anong opisina o campus ang hinahanap ninyo para matulungan ko kayong matukoy ang tamang departamento.",
    ],

    "registrar": [
        "OFFICE OF THE UNIVERSITY REGISTRAR (OUR):\nThe OUR maintains your academic records and handles document requests, with units at the Main Campus and other campuses.\n\nCOMMONLY REQUESTED DOCUMENTS:\n- Transcript of Records (TOR)\n- Certificate of Enrollment / Certificate of Registration (COR)\n- Certificate of Good Moral Character\n- Certified True Copy of Diploma\n- Honorable Dismissal\n- Authentication of records\n- Special Order (for graduates, where applicable)\n\nTYPICAL PROCESS:\n1. Fill out the request form (online or in person).\n2. Pay any applicable processing fees at the Cashier's Office.\n3. Submit the required IDs/documents.\n4. Wait for processing.\n5. Claim the document(s), or have them mailed if that option is available.\n\nFees, processing times, and current Registrar contact details vary by campus and document type. Please check https://cvsu.edu.ph or your campus Registrar for the verified, up-to-date schedule of fees and processing windows.",
        "The CvSU Registrar's Office handles academic records — TOR, Diploma, Certificate of Enrollment, Good Moral, and Grade Certificates. Typical steps: secure a clearance slip, fill out the request form, and settle applicable fees. Processing time and fees vary by document and campus. For current Registrar contact details, office hours, and the processing schedule of your campus, please refer to https://cvsu.edu.ph.",
        "Hinahawakan ng Registrar's Office ng CvSU ang mga academic records: TOR, diploma, Certificate of Enrollment, Good Moral, atbp. Karaniwang proseso: kumuha ng clearance slip, punan ang request form, at bayaran ang mga applicable fees. Ang processing time at fees ay nag-iiba depende sa dokumento at campus — bisitahin ang https://cvsu.edu.ph para sa pinakabago.",
    ],

    "graduate_programs": [
        "CvSU's Graduate School offers Master's and Doctoral programs across several disciplines (examples typically include education, business administration, public administration, agriculture, environmental science, computer science, and engineering — exact availability changes over time).\n\nTYPICAL ADMISSION REQUIREMENTS:\n- Bachelor's degree from an accredited institution\n- Transcript of Records\n- Entrance examination where required\n- Recommendation letters\n- Statement of purpose\n- Program-specific GPA requirements\n\nNOTE on tuition: graduate programs may not be covered by RA 10931 the same way as undergraduate programs. Verify the applicable fees with the Cashier's Office.\n\nFor the current list of graduate programs, full requirements, and verified Graduate School contact details, please visit https://cvsu.edu.ph.",
        "CvSU's Graduate School offers Master's and Doctoral programs in various fields. Requirements typically include: a Bachelor's degree, transcript, recommendation letters, and program-specific entrance requirements. Graduate-program tuition may not be covered by RA 10931 the same way as undergraduate; verify with the Cashier's Office. For the current program list, contact the Graduate School via https://cvsu.edu.ph.",
        "Ang CvSU Graduate School ay nag-aalok ng Master's at Doctoral programs sa iba't ibang larangan. Kailangan ng Bachelor's degree, transcript, at iba pang requirements para mag-apply. Ang tuition para sa graduate ay maaaring iba sa undergraduate. Para sa kumpletong listahan, makipag-ugnayan sa Graduate School via https://cvsu.edu.ph.",
    ],

    "it_cs_courses": [
        "CvSU offers Computing and IT programs through the College of Engineering and Information Technology (CEIT). Programs typically include BS Computer Science, BS Information Technology, BS Computer Engineering, and BS Electronics Engineering. Availability and the specific campuses that offer each program can change between academic years. For the current, verified list of programs, the campuses that offer each, and CEIT's official contact details, please visit https://cvsu.edu.ph.",
        "CvSU's College of Engineering and Information Technology offers programs such as BS Computer Science, BS Information Technology, BS Computer Engineering, and BS Electronics Engineering. Availability varies per campus. For the current list of campuses offering each program and graduate options, please check https://cvsu.edu.ph.",
        "Ang CEIT ng CvSU ay nag-aalok ng programs tulad ng BSCS, BSIT, BS Computer Engineering, at BS Electronics Engineering. Nag-iiba ang availability bawat campus. Para sa pinakabagong listahan, bisitahin ang https://cvsu.edu.ph.",
    ],

    "vision_mission": [
        "CvSU's MOTTO is 'Truth, Excellence, Service'. The university expresses its identity through 'Iskolar para sa Bayan' — students serving the Filipino people through accessible, quality higher education. For the official, current vision and mission statements, please visit https://cvsu.edu.ph (the vision and mission may be updated by university policy).",
        "CvSU's motto is 'Truth, Excellence, Service'. The CvSU vision and mission emphasize quality higher education, research, extension, and service to the community — the official current wording of the vision and mission statements is maintained on https://cvsu.edu.ph. For the formal version, please refer to the official site.",
        "Ang motto ng CvSU ay 'Truth, Excellence, Service' at ang 'Iskolar para sa Bayan' ang larawan ng pagkakakilanlan nito. Para sa opisyal na bisyon at misyon, bisitahin ang https://cvsu.edu.ph.",
    ],

    "international_students": [
        "CvSU accepts foreign-national applicants subject to CHED and university regulations.\n\nTYPICAL REQUIREMENTS:\n- Accomplished application form\n- Authenticated / apostilled academic credentials (HS diploma, transcripts)\n- Passport copy (biodata page)\n- Student Visa (9F) — typically processed after admission\n- English-proficiency evidence (for applicants from non-English-speaking countries)\n- Medical/health clearance\n- Police clearance from the home country (or NBI equivalent)\n\nTYPICAL PROCESS:\n1. Submit your application to the Office of Admissions at the Main Campus.\n2. Await credential evaluation and the Notice of Admission.\n3. Process the 9F Student Visa with the Bureau of Immigration.\n4. Complete enrollment procedures.\n\nNOTE on tuition: RA 10931 may not apply to foreign nationals. Verify the applicable fees with the Cashier's Office.\n\nFor verified application requirements and current Admissions contact details, please refer to https://cvsu.edu.ph.",
        "CvSU welcomes foreign/international students subject to CHED rules. You'll typically need authenticated academic credentials, a passport, a 9F student visa (processed after admission), medical clearance, and police clearance. Apply at the Office of Admissions at the Main Campus in Indang. Note: free tuition under RA 10931 may not apply to foreign nationals. For full details, please visit https://cvsu.edu.ph.",
        "Yes, CvSU accepts international students subject to CHED rules. Karaniwan, kailangan ng apostilled academic records, passport, at 9F student visa (processed pagkatapos ma-admit). Mag-apply sa Office of Admissions sa Main Campus sa Indang. Tandaan: ang free tuition sa ilalim ng RA 10931 ay maaaring hindi sumasaklaw sa foreign nationals. Para sa kumpletong detalye, bisitahin ang https://cvsu.edu.ph.",
    ],

    "alumni": [
        "CvSU has an Alumni Relations Office that coordinates the Alumni Association, tracer studies, alumni IDs, and reunion/homecoming events. For the current process and event schedule, please contact the Alumni Relations Office at the Main Campus or the Alumni Coordinator at your home campus — they keep the official member registry and confirmed reunion dates. Tell me which service you're asking about (ID request, tracer form, donation, mentorship) and I can guide you more precisely.",
        "For anything alumni-related — joining the association, updating your records, requesting an alumni ID, submitting a tracer survey, or asking about a homecoming — the Alumni Relations Office at your graduating campus is the official channel. They can confirm membership procedures, upcoming events, and tracer-survey deadlines. For verified contact details, please refer to https://cvsu.edu.ph.",
        "Mayroong Alumni Relations Office ang CvSU na nag-aasikaso ng Alumni Association, tracer studies, alumni IDs, at homecoming events. Para sa pinakabagong proseso at iskedyul ng events, mangyaring makipag-ugnayan sa Alumni Relations Office sa inyong campus.",
    ],

    "career_opportunity": [
        "CvSU supports student career development primarily through the Office of Student Affairs and Services (OSAS) and the Career and Placement Office (or its campus equivalent). They coordinate OJT/internship placements, partner with employers, organize job/career fairs, and provide career counseling and resume support. For job openings within CvSU itself (faculty/staff hiring), the Human Resource Management Office (HRMO) posts vacancies on the official site. For up-to-date job fair dates, internship slots, and HRMO vacancies, please check https://cvsu.edu.ph.",
        "For OJT, internships, and post-graduation job placement, the Career and Placement Office (typically under OSAS) at your campus is the official channel. They coordinate with industry partners and announce job-fair schedules. For working at CvSU as faculty or staff, watch the HRMO announcements on the official site. For real-time openings and dates, please verify directly with HRMO or OSAS.",
        "Para sa OJT, internship, at job placement, ang Career and Placement Office (sa ilalim ng OSAS) sa inyong campus ang opisyal na channel. Para sa job openings sa CvSU bilang faculty o staff, subaybayan ang HRMO sa opisyal na site.",
    ],

    "directory": [
        "CvSU maintains official contact directories per campus on https://cvsu.edu.ph — including the Office of the President, Campus Administrators, Registrars, OSAS, MIS/ICT, HRMO, Cashier, Library, and college deans. For the most current phone numbers and emails, please use the campus directory on the official website. Tell me which office or campus you're trying to reach, and I can point you to the right department.",
        "I can guide you to the right office for almost any concern (admissions, registrar, OSAS, cashier, MIS, dean's office, etc.), but for specific phone numbers and email addresses, the official campus directory on https://cvsu.edu.ph is the authoritative source. In line with the Data Privacy Act of 2012 (RA 10173), I avoid sharing personal faculty contact details without official verification.",
        "Mayroong opisyal na directory ang CvSU para sa bawat campus sa https://cvsu.edu.ph. Para sa pinakabagong contact details, mangyaring bisitahin ang opisyal na site. Sabihin sa akin kung anong opisina ang hinahanap ninyo.",
    ],

    "courses_offered": [
        "CvSU offers undergraduate and graduate programs across multiple disciplines — typically including Agriculture, Arts and Sciences, Business and Economics, Criminal Justice, Education, Engineering and Information Technology, Nursing, Sports/Physical Education, Veterinary Medicine, and others. The exact list of colleges and programs, and the campuses where each is offered, may change over time. For the verified, current list, please visit https://cvsu.edu.ph.",
        "CvSU offers programs across Agriculture, Engineering & IT, Education, Business, Arts & Sciences, Criminal Justice, Nursing, and more. Examples include BS Agriculture, BS Computer Science, BS Information Technology, various BS Engineering tracks, BS Criminology, BS Business Administration, BS Education, plus graduate programs. Program availability varies per campus — please check https://cvsu.edu.ph for the current list at your campus of interest.",
        "Ang CvSU ay nag-aalok ng iba't ibang programa sa Agriculture, Engineering, IT, Edukasyon, Negosyo, Arts & Sciences, Criminal Justice, Nursing, at iba pa. Ang availability ay depende sa campus — bisitahin ang https://cvsu.edu.ph para sa pinakabagong listahan.",
    ],
}

# ----------------------------------------------------------------------
# Unverified-claim detector (same as the audit), used to mark responses
# for replacement.
# ----------------------------------------------------------------------
UNVERIFIED = [
    r"\b13\s+campuses?\b",
    r"\b11\s+colleges?\b",
    r"\b84\+?\s+(?:recognized\s+)?(?:student\s+)?organizations?\b",
    r"\b92,?000\s+books?\b",
    r"\b72\s+hectares?\b",
    r"\b7,?490\b",
    r"\b21,?739\b",
    r"\b34\.45\s*%",
    r"046-?436-?6584",
    r"\bestablished:\s*1\d{3}\b",
    r"originally:\s+cavite\s+college",
    r"\bdasmari[nñ]as\s+learning\s+center\s*-\s*2023",
    r"\bmaragondon\s*-\s*2015",
    r"\bbacoor\s*-\s*2008",
    r"\btanza\s*-\s*2007",
    r"\bsilang\s*-\s*2006",
    r"\btrece\s+martires\s*-\s*2005",
    r"\bimus\s*-\s*2003",
    r"\bcarmona\s*-\s*2002",
    r"\bcavite\s+city\s*-\s*2001",
    r"\brosario/?ccat\s*-\s*1969",
    r"\bnaic\s*-\s*1961",
    r"\bgeneral\s+trias\s*-\s*2012",
    r"acceptance\s+rate",
    r"center\s+of\s+excellence",
    r"aaccup",
    r"iso\s+certified",
    r"\bgreen\s+hornets\b",
    r"ladislao\s+diwa\s+memorial\s+library",
    r"laya\s+at\s+diwa\s+monument",
    r"inaugurated\s+2006",
]

def has_unverified(text: str) -> bool:
    t = text.lower()
    return any(re.search(p, t) for p in UNVERIFIED)

# ----------------------------------------------------------------------
# Build cleaned intents
# ----------------------------------------------------------------------
# Tags to fold into a canonical tag (drop the source tag; patterns merged
# into the target).
FOLD = {
    "courses":         "courses_offered",
    "campus_branches": "campus_specific",
    "enrollment":      "enrollment_procedure",
    "student_id":      "registrar",   # student ID services live in OUR
    "shifting":        "academic_policies",
}

# Index intents by tag for easy merging
by_tag = {it["tag"]: it for it in intents}

# Targets to which we will append patterns from folded tags
for src_tag, tgt_tag in FOLD.items():
    if src_tag in by_tag and tgt_tag in by_tag:
        by_tag[tgt_tag].setdefault("patterns", []).extend(by_tag[src_tag].get("patterns", []))
        # If the responses look stronger in src than in tgt, keep tgt's;
        # tgt is the canonical tag.

# Collected pleasantry patterns to add to greeting/thanks/goodbye
pleasantry_pulled = {"greeting": [], "thanks": [], "goodbye": []}

# Final cleaning pass — drop folded source tags from output
cleaned = []
removed_summary = []  # (tag, n_input, n_kept, n_responses_replaced)
sanitization_log = []

for it in intents:
    tag = it["tag"]
    if tag in FOLD:
        # Don't emit folded source tags
        continue

    patterns_in = it.get("patterns", [])
    responses_in = it.get("responses", [])

    seen = set()
    new_patterns = []
    for raw in patterns_in:
        core = strip_template_wrapping(raw)
        if core is None:
            continue
        # Detect misplaced pleasantries
        norm = normalized_core(core)
        if tag not in ("greeting", "thanks", "goodbye", "chitchat", "nlu_fallback"):
            pleasantry_target = PLEASANTRY_MAP.get(norm)
            if pleasantry_target:
                pleasantry_pulled[pleasantry_target].append(core)
                continue
        if norm in seen:
            continue
        seen.add(norm)
        new_patterns.append(core)

    # Sanitize responses
    new_responses = []
    n_replaced = 0
    for r in responses_in:
        if has_unverified(r):
            n_replaced += 1
            continue
        new_responses.append(r)

    if n_replaced > 0:
        safe = SAFE.get(tag)
        if safe:
            new_responses = safe[:]  # full replacement set
            sanitization_log.append((tag, "replaced with curated safe responses"))
        else:
            # No curated fallback — keep what's left, plus a generic redirect.
            new_responses.append(
                "For verified current details on this topic, please visit "
                "https://cvsu.edu.ph or contact the relevant CvSU office directly."
            )
            sanitization_log.append((tag, "appended generic redirect (no curated fallback)"))

    # Build the new intent
    new_it = dict(it)
    new_it["patterns"] = new_patterns
    new_it["responses"] = new_responses
    cleaned.append(new_it)
    removed_summary.append((tag, len(patterns_in), len(new_patterns), n_replaced))

# Now add the pulled pleasantries to greeting/thanks/goodbye
for tag in ("greeting", "thanks", "goodbye"):
    target = next((c for c in cleaned if c["tag"] == tag), None)
    if not target:
        continue
    existing = {normalized_core(p) for p in target["patterns"]}
    added = 0
    for p in pleasantry_pulled[tag]:
        nk = normalized_core(p)
        if nk and nk not in existing:
            target["patterns"].append(p)
            existing.add(nk)
            added += 1
    if added:
        sanitization_log.append((tag, f"added {added} pleasantries reclaimed from other tags"))

# ----------------------------------------------------------------------
# Write output
# ----------------------------------------------------------------------
out = {"intents": cleaned}
with OUT.open("w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"Wrote {OUT}")
print()
print("Per-tag pattern reduction:")
print(f"{'tag':<28}{'in':>6}{'kept':>8}{'dropped':>10}{'resp_replaced':>15}")
total_in = total_out = total_repl = 0
for tag, ni, nk, nr in removed_summary:
    print(f"{tag:<28}{ni:>6}{nk:>8}{ni-nk:>10}{nr:>15}")
    total_in += ni
    total_out += nk
    total_repl += nr
print(f"{'TOTAL':<28}{total_in:>6}{total_out:>8}{total_in-total_out:>10}{total_repl:>15}")
print()
print("Reduction:", f"{(total_in-total_out)/total_in*100:.1f}% of patterns dropped")
print()
print("Folded tags (removed from output, merged into canonical):", FOLD)
print()
print("Sanitization log (per tag):")
for t, m in sanitization_log:
    print(f"  {t}: {m}")
