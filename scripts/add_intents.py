"""
Append three new intents (alumni, career_opportunity, directory) to cavsu_intents.json.
Patterns cover English, Taglish, formal/informal variants.
Responses follow Sevi's in-depth instructions: confidence tier, redirect to verifying office.
"""
import json

NEW_INTENTS = [
    {
        "tag": "alumni",
        "description": "Questions about CvSU alumni network, association, tracer studies, IDs, reunions.",
        "active": True,
        "patterns": [
            "alumni",
            "CvSU alumni",
            "alumni association",
            "alumni network",
            "alumni community",
            "CvSU alumni association",
            "where do I register as an alumnus",
            "how to join the alumni association",
            "I'm an alumnus, how do I update my records",
            "alumni ID",
            "request alumni ID",
            "how do I get an alumni ID",
            "alumni card",
            "alumni tracer survey",
            "tracer study",
            "where do alumni file tracer forms",
            "alumni homecoming",
            "alumni reunion",
            "when is the alumni homecoming",
            "alumni event",
            "alumni gathering",
            "how do I reconnect with classmates from CvSU",
            "I graduated from CvSU, what next",
            "I'm a graduate of CvSU",
            "I want to attend the alumni meet",
            "alumni office",
            "where is the alumni office",
            "contact alumni office",
            "alumni relations office",
            "alumni affairs",
            "ako po ay graduate ng CvSU",
            "paano sumali sa alumni association",
            "saan po makakakuha ng alumni ID",
            "may alumni group ba ang CvSU",
            "kailan ang alumni homecoming",
            "may homecoming po ba sa CvSU",
            "alumni newsletter",
            "alumni website",
            "alumni mentorship",
            "give back to alma mater",
            "donate to CvSU",
            "alumni donation",
            "endowment",
            "outstanding alumni award",
            "notable CvSU alumni",
            "famous CvSU graduates"
        ],
        "responses": [
            "CvSU has an active Alumni Relations Office that coordinates the Alumni Association, tracer studies, alumni IDs, and homecoming events. For the most current process and event schedule, please contact the Alumni Relations Office at the main campus in Indang or the Alumni Coordinator at your home campus — they keep the official member registry and confirmed reunion dates. Is there a specific service (ID request, tracer form, donation, mentorship) you're asking about so I can point you more precisely?",
            "For anything alumni-related — joining the association, updating your records, requesting an alumni ID, submitting a tracer survey, or asking about homecoming — the Alumni Relations Office is the official channel. As of now, the best path is to email or visit the Alumni Affairs Office at your graduating campus. They can confirm membership fees (if any), upcoming events, and tracer-survey deadlines. Want me to clarify which campus's office to approach?",
            "CvSU recognizes alumni as part of the 'Iskolar para sa Bayan' community and runs alumni programs through the Alumni Relations Office at each campus. Tracer studies, homecoming announcements, and alumni IDs are coordinated there. I can give you general guidance, but for confirmed dates, current member benefits, and specific requirements, please verify directly with the Alumni Office — they have the live records."
        ]
    },
    {
        "tag": "career_opportunity",
        "description": "Questions about career services, internships (OJT), job openings, job fairs, employer partners, and graduate placement.",
        "active": True,
        "patterns": [
            "career",
            "careers",
            "career opportunity",
            "career opportunities",
            "job opportunity",
            "job opportunities",
            "job openings",
            "is CvSU hiring",
            "CvSU job openings",
            "CvSU careers",
            "work at CvSU",
            "faculty hiring",
            "staff hiring",
            "non-teaching positions",
            "teaching positions",
            "career services",
            "career office",
            "career development",
            "career guidance",
            "OJT",
            "OJT placement",
            "internship",
            "internship program",
            "how to apply for OJT",
            "where do I do my OJT",
            "on the job training",
            "practicum",
            "job fair",
            "career fair",
            "when is the next job fair",
            "employer partners",
            "industry partners",
            "linkage with companies",
            "where do CvSU graduates work",
            "employment rate of CvSU graduates",
            "graduate employability",
            "job placement assistance",
            "help me find a job after graduation",
            "career counseling",
            "resume help",
            "I need help with my resume",
            "interview preparation",
            "scholarship for working students",
            "may job fair ba sa CvSU",
            "may hiring po ba ang CvSU",
            "paano mag apply ng OJT",
            "saan po pwede mag OJT",
            "kailan ang career fair",
            "may career office ba",
            "trabaho after graduation",
            "anong trabaho pwede sa kurso ko",
            "linkedin CvSU",
            "alumni job referral",
            "post-graduation employment"
        ],
        "responses": [
            "CvSU supports career development through the Office of Student Affairs and Services (OSAS) and a Career and Placement Office (or its campus equivalent), which handles OJT placement, internship coordination, job fairs, employer partnerships, and career counseling. For current job openings within CvSU itself (faculty/staff hiring), the Human Resource Management Office (HRMO) posts vacancies on the official cvsu.edu.ph site. Could you tell me whether you're asking as a student looking for OJT/jobs, or about working at CvSU? I can point you to the right office.",
            "For OJT, internships, and post-graduation job placement, the Career and Placement Office (typically under OSAS) at your campus is the official channel — they coordinate with industry partners and announce job fair schedules. For working at CvSU as faculty or staff, watch the HRMO announcements on cvsu.edu.ph and the university's official social media. I don't have real-time job postings, so please verify current vacancies and event dates with HRMO or OSAS directly.",
            "Career-related services at CvSU include OJT/internship coordination, job fairs, employer linkages, resume help, and graduate tracer studies — these run through OSAS and the Career and Placement Office at your campus. CvSU also publishes its own faculty and staff vacancies through HRMO on the official website. For up-to-date openings, dates, and application requirements, please confirm with the relevant office since these change throughout the year."
        ]
    },
    {
        "tag": "directory",
        "description": "Requests for contact details — offices, faculty, deans, campus phone numbers, emails, address book.",
        "active": True,
        "patterns": [
            "directory",
            "CvSU directory",
            "office directory",
            "faculty directory",
            "staff directory",
            "phone directory",
            "contact directory",
            "list of offices",
            "list of contact numbers",
            "campus contact list",
            "who do I contact for",
            "who handles",
            "who is in charge of",
            "contact details",
            "email of",
            "phone number of",
            "office of the president",
            "office of the campus administrator",
            "office of the dean",
            "dean of the college",
            "department head",
            "department chair",
            "program chair",
            "office of admissions contact",
            "registrar contact",
            "registrar email",
            "registrar phone number",
            "OSAS contact",
            "cashier contact",
            "MIS contact",
            "guidance office contact",
            "library contact",
            "how do I email the registrar",
            "how do I reach the dean",
            "saan ko makukuha ang contact ng",
            "may directory po ba",
            "anong email ng registrar",
            "anong number ng OSAS",
            "saan po contact ng dean",
            "official email CvSU",
            "general inquiries number",
            "main switchboard",
            "trunk line",
            "PABX",
            "campus phone number",
            "main campus contact",
            "Imus campus contact",
            "satellite campus contacts",
            "branch directory"
        ],
        "responses": [
            "CvSU maintains official contact directories per campus on cvsu.edu.ph — including the Office of the President, Campus Administrators, Registrars, OSAS, MIS, HRMO, Cashier, Library, and college deans. For the most current phone numbers and emails, please refer to the campus directory page on the official website, since contacts can change. Which office or person are you trying to reach? If you tell me the campus, I can point you to the right department.",
            "I can guide you to the right office for almost any concern (admissions, registrar, OSAS, cashier, MIS, dean's office, etc.), but for specific phone numbers and email addresses, the official campus directory on cvsu.edu.ph is the authoritative source. I avoid sharing individual faculty contact details without official verification, in line with the Data Privacy Act of 2012 (RA 10173). What office or service are you trying to reach?",
            "For an authoritative directory of CvSU offices, deans, registrars, and campus administrators, please visit the official CvSU website (cvsu.edu.ph) — each campus typically publishes its own directory page with current emails and phone numbers. I can help you identify which office handles a particular concern, then you can pull the verified contact details from the official directory. Which campus and concern can I narrow down for you?"
        ]
    }
]

src = "data/cavsu_intents.json"
with open(src, "r", encoding="utf-8") as f:
    data = json.load(f)

existing_tags = {it["tag"] for it in data["intents"]}
added = 0
for new_intent in NEW_INTENTS:
    if new_intent["tag"] in existing_tags:
        print(f"  [SKIP] {new_intent['tag']} already exists")
        continue
    data["intents"].append(new_intent)
    added += 1
    print(f"  [ADD]  {new_intent['tag']}: {len(new_intent['patterns'])} patterns, {len(new_intent['responses'])} responses")

with open(src, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)

print(f"\nDone. {added} intents added. Total intents now: {len(data['intents'])}")
