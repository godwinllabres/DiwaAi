"""
Replace responses in intents_v2_full.json with content sourced from the official
CvSU website (https://cvsu.edu.ph) and its campus subdomains.

All factual claims below come from these official pages (verified via web
fetch + site search on May 12, 2026):

  - https://cvsu.edu.ph/history/  ............... university history & RA citations
  - https://cvsu.edu.ph/mission-vision-objectives/  vision / mission / objectives
  - https://cvsu.edu.ph/category/campuses/  ...... campus directory
  - https://cvsu.edu.ph/category/colleges/  ...... colleges and program offerings
  - https://cvsu.edu.ph/college-of-arts-and-sciences-programs-offered/  CAS programs
  - https://cvsu.edu.ph/college-of-tourism-and-hospitality-management/  CTHM
  - https://cvsu.edu.ph/2018/01/13/ceit-academic-programs/  CEIT programs
  - https://cvsu.edu.ph/office-of-student-affairs-and-services/  OSAS
  - https://library.cvsu.edu.ph/landing/?view=about  CvSU library (Ladislao N. Diwa
    Memorial Library)
  - https://admission.cvsu.edu.ph/  admission portal
  - https://cvsu.edu.ph/ovpre-contact-us/  OVPRE contact (general university info)
  - https://cvsu.edu.ph/2024/03/cvsu-highlights-excellence-in-innovations-collaborations-during-118th-founding-anniversary/  118th anniversary (2024)
  - https://myportal.cvsu.edu.ph/  student portal
  - https://cvsu.edu.ph/2024/11/first-semester-sy-2025-2026-application-for-admission/  admission cycle
"""
import json
from pathlib import Path

ROOT = Path(__file__).parent
SRC  = ROOT / "intents_v2_full.json"
OUT  = ROOT / "intents_v3_official.json"

with SRC.open(encoding="utf-8") as f:
    data = json.load(f)

# All updates below cite official cvsu.edu.ph pages (or its verified subdomains)
# at the end of the response.
SRC_CVSU = "https://cvsu.edu.ph"
SRC_HISTORY = "https://cvsu.edu.ph/history/"
SRC_MVO = "https://cvsu.edu.ph/mission-vision-objectives/"
SRC_LIB = "https://library.cvsu.edu.ph/"
SRC_OSAS = "https://cvsu.edu.ph/office-of-student-affairs-and-services/"
SRC_CEIT = "https://cvsu.edu.ph/2018/01/13/ceit-academic-programs/"
SRC_CAS = "https://cvsu.edu.ph/college-of-arts-and-sciences-programs-offered/"
SRC_CTHM = "https://cvsu.edu.ph/college-of-tourism-and-hospitality-management/"
SRC_ADMIT = "https://admission.cvsu.edu.ph/"

OFFICIAL = {

# ---------- about_cvsu (sourced from /history/) ----------
"about_cvsu": [
    "Cavite State University (CvSU) traces its roots to 1906 as the Indang Intermediate School, with the American Thomasites as the first teachers. It was renamed Indang Farm School in 1918, Indang Rural High School in 1927, and Don Severino National Agriculture School in 1958. In 1964, the school was converted into a State College via Republic Act 3917 and became known as Don Severino Agricultural College (DSAC). On January 22, 1998, by virtue of Republic Act No. 8468, DSAC was converted into Cavite State University. In 2001, the Cavite College of Fisheries (CACOF) in Naic and the Cavite College of Arts and Trade (CCAT) in Rosario were integrated into the University via CHED Memo No. 27 s. 2000. Additional campuses across the province have since been established through memoranda of agreement with local government units. CvSU celebrated its 118th founding anniversary in 2024. Source: " + SRC_HISTORY,
    "CvSU started in 1906 as the Indang Intermediate School (under the American Thomasites). It went through several name changes — Indang Farm School (1918), Indang Rural High School (1927), Don Severino National Agriculture School (1958) — before becoming a State College in 1964 via RA 3917 (Don Severino Agricultural College, DSAC). On January 22, 1998, RA 8468 converted DSAC into Cavite State University. CACOF (Naic) and CCAT (Rosario) joined CvSU in 2001 via CHED Memo No. 27 s. 2000. Source: " + SRC_HISTORY,
    "Ang CvSU ay nagsimula noong 1906 bilang Indang Intermediate School, sa panahon ng mga Thomasites. Naging Indang Farm School ito noong 1918, Indang Rural High School noong 1927, Don Severino National Agriculture School noong 1958, at naging State College noong 1964 sa bisa ng RA 3917. Noong Enero 22, 1998, sa bisa ng RA 8468, ito ay naging Cavite State University. Noong 2001, ang CACOF (Naic) at CCAT (Rosario) ay isinama sa CvSU sa pamamagitan ng CHED Memo No. 27 s. 2000. Pinagmulan: " + SRC_HISTORY,
],

# ---------- vision_mission (sourced from /mission-vision-objectives/) ----------
"vision_mission": [
    "CvSU's tenets are TRUTH, EXCELLENCE, and SERVICE. Per the official Mission, Vision, and Objectives, the University commits to the highest standards of education, values its stakeholders, strives for continual improvement of products and services, and upholds these tenets to produce globally competitive and morally upright individuals. The University's objectives include providing a general education that promotes national identity, cultural consciousness, moral integrity, and spiritual vigor; training the nation's manpower in the skills required by national development; developing professions that will provide leadership for the nation; and advancing knowledge through research work to improve the quality of human life. Source: " + SRC_MVO,
    "The CvSU motto is Truth, Excellence, Service. The official mission emphasizes the highest standards of education, valuing stakeholders, and continual improvement to produce globally competitive and morally upright individuals. University objectives include general education that builds national identity and moral integrity, training manpower for national development, developing professions that provide leadership, and advancing knowledge through research. Source: " + SRC_MVO,
    "Ang CvSU ay nakaugat sa Truth, Excellence, Service. Ayon sa opisyal na Mission, Vision, and Objectives, ang Pamantasan ay nakatuon sa pinakamataas na pamantayan ng edukasyon, pagpapahalaga sa stakeholders, at patuloy na pagpapabuti ng mga programa at serbisyo upang makapagsanay ng mga indibidwal na globally competitive at morally upright. Pinagmulan: " + SRC_MVO,
],

# ---------- campus_location ----------
"campus_location": [
    "The CvSU Main Campus — the Don Severino delas Alas Campus — is located in Indang, Cavite, Philippines, approximately 60 km (about 37 miles) southwest of Manila. It is the oldest campus of the University and houses academic, administrative, and research and extension facilities. CvSU operates additional campuses across the province of Cavite, with locations including Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario (CCAT), Silang, Tanza, and Trece Martires City, among others. For the complete and current campus directory with addresses and contact details, please visit " + SRC_CVSU + "/category/campuses/. Source: " + SRC_HISTORY,
    "The Main Campus (Don Severino delas Alas Campus) is in Indang, Cavite — roughly 60 km southwest of Manila. CvSU has multiple satellite campuses across the province (Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario/CCAT, Silang, Tanza, Trece Martires City, and others). See " + SRC_CVSU + "/category/campuses/ for the directory with addresses.",
    "Ang Main Campus ng CvSU — Don Severino delas Alas Campus — ay matatagpuan sa Indang, Cavite, mga 60 km timog-kanluran ng Maynila. Mayroon ding ibang campus ang CvSU sa buong lalawigan ng Cavite (Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario/CCAT, Silang, Tanza, Trece Martires City, atbp.). Para sa kumpletong directory at address, tingnan ang " + SRC_CVSU + "/category/campuses/.",
],

# ---------- campus_specific ----------
"campus_specific": [
    "CvSU operates a Main Campus in Indang (Don Severino delas Alas Campus) along with satellite campuses across the province of Cavite, including campuses in Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario (the CCAT campus), Silang, Tanza, and Trece Martires City, among others. Program offerings differ per campus. For the verified, current list of campuses with their addresses, program offerings, and contact details, please visit " + SRC_CVSU + "/category/campuses/.",
    "CvSU's campus system has the Main Campus in Indang plus satellite campuses across Cavite (e.g., Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario/CCAT, Silang, Tanza, Trece Martires City). Programs vary per campus — see " + SRC_CVSU + "/category/campuses/ for the official directory.",
    "May Main Campus ang CvSU sa Indang at mga satellite campus sa Cavite (Bacoor, Cavite City, Carmona, General Trias, Imus, Naic, Rosario/CCAT, Silang, Tanza, Trece Martires City, atbp.). Iba-iba ang programa bawat campus. Tingnan ang " + SRC_CVSU + "/category/campuses/ para sa opisyal na directory.",
],

# ---------- library (sourced from library.cvsu.edu.ph) ----------
"library": [
    "The Main Campus library is the LADISLAO N. DIWA MEMORIAL LIBRARY. Its mission, as stated officially, is to provide excellent, equitable and relevant library services and resources in support of the University's mission, and to support the University in its main thrust of fields of expertise by providing adequate, updated, and relevant collection of research and reference materials. Each CvSU campus also operates its own library. For the union catalog and campus library information across the CvSU library system, visit " + SRC_LIB + ".",
    "CvSU's library at the Main Campus is the Ladislao N. Diwa Memorial Library, supporting research and reference needs. Each satellite campus also has its own library. For the integrated library system (collections, services, campus library pages), visit " + SRC_LIB + ".",
    "Ang library sa Main Campus ay ang Ladislao N. Diwa Memorial Library, na nag-aalok ng excellent, equitable, at relevant na library services at resources. May sariling library ang bawat campus. Tingnan ang " + SRC_LIB + " para sa kumpletong collection at services ng buong CvSU library system.",
],

# ---------- admissions_requirements (sourced from admission.cvsu.edu.ph + 2025 cycle page) ----------
"admissions_requirements": [
    "ADMISSION TO CvSU (per the official Office of Student Affairs and Services and the admission portal):\n\n1. FILE THE ONLINE APPLICATION at " + SRC_ADMIT + " — encode your information in the registration/application form.\n2. PRINT the accomplished application form on A4-size bond paper.\n3. PREPARE THESE DOCUMENTS (typical for SHS-graduate applicants):\n   - Certified true copy of Grade 12 Report Card (Form 138)\n   - Certificate of Good Moral Character\n   - 2 copies of 1x1 ID picture with name tag\n   - Short ordinary folder\n4. SUBMIT TO OSAS at your target campus for exam scheduling (typically Mondays–Thursdays, 7 AM–6 PM).\n5. TAKE THE CvSU ENTRANCE EXAMINATION.\n6. APPLICANTS FOR ARCHITECTURE, COMPUTER SCIENCE, AND ENGINEERING must additionally bring their Grade 11 Report Card for evaluation. Engineering programs accept STEM-strand graduates.\n7. BS PSYCHOLOGY applicants require a GPA of 85 or higher.\n8. TRANSFEREE applicants must have a GPA of 2.00 (equivalent of 85%) or better with NO failed grade.\n\nFor the current cycle's exact requirements and deadlines, see " + SRC_ADMIT + " and " + SRC_CVSU + ". Sources: " + SRC_ADMIT + " · " + SRC_OSAS,
    "CvSU admission steps: file the online application at admission.cvsu.edu.ph, print the form on A4 bond paper, prepare your Grade 12 Report Card (certified true copy), Good Moral Certificate, 2 copies of 1x1 ID photo with name tag, and a short ordinary folder. Submit to OSAS at your campus for exam scheduling. Architecture/CS/Engineering applicants also bring Grade 11 Report Card (Engineering accepts STEM strand). BS Psychology requires GPA 85+. Transferees: GPA 2.00 (85%) or better, no failed grade. Source: " + SRC_ADMIT,
    "Mga pangunahing requirements para sa CvSU admission: mag-online application sa admission.cvsu.edu.ph, i-print sa A4 bond paper, ihanda ang certified true copy ng Grade 12 Report Card, Good Moral Certificate, 2 1x1 ID pictures na may name tag, at short ordinary folder. Isumite sa OSAS ng inyong campus para sa exam scheduling. Para sa Architecture/CS/Engineering, magdala rin ng Grade 11 Report Card (Engineering: STEM strand). BS Psychology: GPA 85+. Transferee: GPA 2.00 (85%) at walang failed grade. Pinagmulan: " + SRC_ADMIT,
],

# ---------- admissions_exam ----------
"admissions_exam": [
    "The CvSU entrance examination is part of the admission process. After you complete the online application at " + SRC_ADMIT + " and submit your documents to OSAS, you will be scheduled for the entrance exam at your target campus. Bring your application form, valid ID, and exam permit/notice as instructed. Successful applicants are issued a Notice of Admission. For the current entrance-exam schedule and admission cycle, please monitor " + SRC_CVSU + " and the admission portal. Source: " + SRC_ADMIT,
    "After filing your application at admission.cvsu.edu.ph and submitting documents to OSAS, you'll be scheduled for the CvSU entrance examination at your target campus. Bring your application form, valid ID, and exam permit. Notices of Admission are issued to qualified applicants. For the latest schedule, see " + SRC_ADMIT + ".",
    "Pagkatapos ng online application sa admission.cvsu.edu.ph at submission sa OSAS, ise-schedule ka para sa CvSU entrance exam. Dalhin ang application form, valid ID, at exam permit. Ang qualified applicants ay tatanggap ng Notice of Admission. Para sa pinakabagong iskedyul, tingnan ang " + SRC_ADMIT + ".",
],

# ---------- scholarship (sourced from OSAS / Citizens Charter) ----------
"scholarship": [
    "CvSU offers several scholarship and financial-assistance programs, including (per the official Office of Student Affairs and Services and Citizens Charter):\n- CvSU State Scholarship\n- Job Experience Program\n- RA 7160 scholarship\n- Entrance Scholarships for Valedictorian, Salutatorian, and 1st/2nd/3rd Honorable Mention high-school graduates\n- Government scholarships (e.g., DOST-SEI, CHED) and LGU scholarships availed through CvSU\n- Tertiary Education Subsidy (TES) and free tuition under RA 10931 for qualified undergraduate Filipino students\n\nApplication is managed by the Financial Assistance Services Unit and distributed through OSAS. You'll typically need an accomplished scholarship form and supporting documents such as your parents' joint income tax return or a BIR Affidavit of Non-Filing.\n\nFor current eligibility, deadlines, and the specific scholarships open at your campus, visit OSAS or " + SRC_OSAS + ". Source: " + SRC_OSAS,
    "CvSU scholarship programs include the CvSU State Scholarship, Job Experience Program, RA 7160 scholarship, Entrance Scholarships (Valedictorian, Salutatorian, 1st/2nd/3rd Honorable Mention), government scholarships (DOST-SEI, CHED), LGU scholarships, TES, and free tuition under RA 10931. Applications are managed by the Financial Assistance Services Unit through OSAS. Source: " + SRC_OSAS,
    "Mga scholarship programs ng CvSU: CvSU State Scholarship, Job Experience Program, RA 7160 scholarship, Entrance Scholarships (Valedictorian, Salutatorian, 1st/2nd/3rd Honorable Mention), government scholarships (DOST-SEI, CHED), LGU scholarships, TES, at libreng tuition sa ilalim ng RA 10931. Ang Financial Assistance Services Unit at OSAS ang nag-asikaso ng aplikasyon. Pinagmulan: " + SRC_OSAS,
],

# ---------- contact_info ----------
"contact_info": [
    "CvSU CONTACT INFORMATION\n\nMAIN CAMPUS — Don Severino delas Alas Campus, Indang, Cavite, Philippines\nUniversity website: " + SRC_CVSU + "\n\nVERIFIED CONTACT POINTS FROM THE OFFICIAL SITE:\n- Office of the Vice President for Research and Extension: landline (046) 862-0850; mobile +63 998 937 2020 / +63 995 971 5511 (per " + SRC_CVSU + "/ovpre-contact-us/)\n- Admission inquiries: admission@cvsu.edu.ph\n- OSAS Main / Guidance: osasmain.guidance@cvsu.edu.ph\n\nOTHER CAMPUSES have their own contact directories — please use the campus-specific subdomain (e.g., trece.cvsu.edu.ph, naic.cvsu.edu.ph, bacoor.cvsu.edu.ph, etc.) or the campus directory at " + SRC_CVSU + "/category/campuses/.",
    "Main Campus: Don Severino delas Alas Campus, Indang, Cavite. Website: cvsu.edu.ph. Verified contacts from the official site: OVPRE landline (046) 862-0850; mobile +63 998 937 2020 / +63 995 971 5511; admission inquiries admission@cvsu.edu.ph; OSAS Main / Guidance osasmain.guidance@cvsu.edu.ph. Other campuses have their own directories via the campus subdomains.",
    "Main Campus: Don Severino delas Alas Campus, Indang, Cavite. Website: cvsu.edu.ph. Mga verified contact mula sa opisyal na site: OVPRE landline (046) 862-0850; mobile +63 998 937 2020 / +63 995 971 5511; admission@cvsu.edu.ph para sa admission; osasmain.guidance@cvsu.edu.ph para sa OSAS Main / Guidance. May sariling directory ang ibang campus sa kanilang subdomain.",
],

# ---------- it_cs_courses (sourced from CEIT page) ----------
"it_cs_courses": [
    "CvSU's College of Engineering and Information Technology (CEIT) offers computing programs at the Main Campus. Per the CEIT Academic Programs page, undergraduate offerings include BS Computer Science and BS Information Technology, alongside the College's engineering programs. The official CEIT page is at " + SRC_CEIT + ". Specific program availability at satellite campuses may vary — verify with the target campus via " + SRC_CVSU + "/category/campuses/.",
    "CvSU's CEIT (College of Engineering and Information Technology) at the Main Campus offers computing programs including BS Computer Science and BS Information Technology, along with engineering programs. Source: " + SRC_CEIT,
    "Ang CEIT (College of Engineering and Information Technology) sa Main Campus ng CvSU ay nag-aalok ng BS Computer Science, BS Information Technology, at mga engineering programs. Para sa availability sa ibang campus, kumpirmahin sa " + SRC_CVSU + "/category/campuses/. Pinagmulan: " + SRC_CEIT,
],

# ---------- courses_offered ----------
"courses_offered": [
    "CvSU offers programs across multiple colleges. The Colleges page (" + SRC_CVSU + "/category/colleges/) and individual college pages list current offerings. Verified examples include:\n- College of Arts and Sciences (CAS): BA Journalism, BA English Language Studies, BS Applied Mathematics, BS Biology, among others (see " + SRC_CAS + ")\n- College of Engineering and Information Technology (CEIT): engineering programs plus BS Computer Science and BS Information Technology (see " + SRC_CEIT + ")\n- College of Tourism and Hospitality Management (CTHM): BS Hospitality Management and BS Tourism Management (CTHM was approved as an independent college by the Board of Regents on September 26, 2024 — see " + SRC_CTHM + ")\n- College of Nursing: BS Nursing, BS Medical Technology, Diploma in Midwifery\n- College of Sports, Physical Education and Recreation\n- College of Veterinary Medicine and Biomedical Sciences\n- College of Medicine\n- Graduate School and Open Learning College\n\nAvailability differs per campus. For the complete current list, see " + SRC_CVSU + "/category/colleges/.",
    "CvSU's colleges include CAS (BA Journalism, BA English Language Studies, BS Applied Math, BS Biology), CEIT (engineering + BSCS + BSIT), CTHM (BSHM, BSTM — independent college since 26 Sep 2024), College of Nursing (BSN, BS Med Tech, Diploma in Midwifery), College of Sports/PE/Recreation, College of Veterinary Medicine and Biomedical Sciences, College of Medicine, and the Graduate School plus Open Learning College. See " + SRC_CVSU + "/category/colleges/ for the complete current list.",
    "May iba't ibang college ang CvSU: CAS (BA Journalism, BA English Language Studies, BS Applied Math, BS Biology), CEIT (engineering + BSCS + BSIT), CTHM (BSHM, BSTM — naging independent college noong Sept 26, 2024), College of Nursing (BSN, BS Med Tech, Diploma in Midwifery), College of Sports/PE/Recreation, College of Veterinary Medicine and Biomedical Sciences, College of Medicine, at Graduate School / Open Learning College. Tingnan ang " + SRC_CVSU + "/category/colleges/.",
],

# ---------- engineering_programs ----------
"engineering_programs": [
    "Engineering at CvSU is housed in the College of Engineering and Information Technology (CEIT). Per the official CEIT Academic Programs page, the college's offerings include engineering programs alongside BS Computer Science and BS Information Technology. Engineering programs at CvSU accept STEM-strand SHS graduates and require submission of the Grade 11 Report Card for program evaluation during admission. For the complete and current list of engineering programs (by degree and campus), please refer to " + SRC_CEIT + " and " + SRC_CVSU + "/category/colleges/.",
    "Engineering programs at CvSU sit within CEIT (College of Engineering and Information Technology). They accept STEM-strand SHS graduates, and Grade 11 Report Card is required at admission for program evaluation. For the current list of engineering programs and campuses, see " + SRC_CEIT,
    "Ang mga engineering programs ng CvSU ay nasa CEIT (College of Engineering and Information Technology). Tumatanggap ito ng STEM-strand SHS graduates at kailangan ng Grade 11 Report Card sa admission. Para sa kasalukuyang listahan ng programa, tingnan ang " + SRC_CEIT + ".",
],

# ---------- business_management ----------
"business_management": [
    "CvSU's hospitality and tourism programs sit under the College of Tourism and Hospitality Management (CTHM), which the Board of Regents approved as an independent college on September 26, 2024. CTHM offers BS Hospitality Management and BS Tourism Management. Business and accountancy programs (e.g., BSBA, BS Accountancy, BS Entrepreneurship) are offered at the Main Campus and selected satellite campuses through their respective colleges/departments. For the current list of business/management programs and the campuses that offer each, see " + SRC_CVSU + "/category/colleges/.",
    "CTHM (College of Tourism and Hospitality Management) — approved as an independent college on 26 Sept 2024 — offers BS Hospitality Management and BS Tourism Management. Business/accountancy programs are offered at the Main Campus and selected satellite campuses. See " + SRC_CVSU + "/category/colleges/ for the current list. Source: " + SRC_CTHM,
    "Ang CTHM (College of Tourism and Hospitality Management) — inaprubahan bilang independent college noong Sept 26, 2024 — ay nag-aalok ng BS Hospitality Management at BS Tourism Management. Ang business/accountancy programs ay sa Main Campus at sa ilang satellite campus. Tingnan ang " + SRC_CVSU + "/category/colleges/. Pinagmulan: " + SRC_CTHM,
],

# ---------- nursing_health_programs ----------
"nursing_health_programs": [
    "CvSU's College of Nursing offers BS Nursing, BS Medical Technology, and Diploma in Midwifery (per the official Colleges listing). Availability and slots may vary per campus and academic year. For the current admission and program details, see " + SRC_CVSU + "/category/colleges/ and the College of Nursing's official page.",
    "The College of Nursing at CvSU offers BS Nursing, BS Medical Technology, and Diploma in Midwifery. See " + SRC_CVSU + "/category/colleges/ for the latest details.",
    "Ang College of Nursing ng CvSU ay nag-aalok ng BS Nursing, BS Medical Technology, at Diploma in Midwifery. Para sa kasalukuyang detalye, tingnan ang " + SRC_CVSU + "/category/colleges/.",
],

# ---------- veterinary_medicine ----------
"veterinary_medicine": [
    "Veterinary education at CvSU is offered through the College of Veterinary Medicine and Biomedical Sciences. Admission to the Doctor of Veterinary Medicine (DVM) program typically requires completion of pre-veterinary undergraduate prerequisites and is competitive. Graduates take the Veterinary Medicine Licensure Examination administered by the PRC. For the current admission process, prerequisites, and program details, please refer to the college's page via " + SRC_CVSU + "/category/colleges/.",
    "CvSU offers Veterinary Medicine through the College of Veterinary Medicine and Biomedical Sciences. DVM admission requires pre-vet prerequisites. Graduates take the PRC Veterinary licensure exam. Details: " + SRC_CVSU + "/category/colleges/.",
    "Ang Veterinary Medicine sa CvSU ay sa ilalim ng College of Veterinary Medicine and Biomedical Sciences. Ang admission sa DVM ay nangangailangan ng pre-vet prerequisites; ang graduates ay kumukuha ng PRC Veterinary licensure exam. Tingnan ang " + SRC_CVSU + "/category/colleges/.",
],

# ---------- graduate_programs ----------
"graduate_programs": [
    "CvSU operates a Graduate School offering master's and doctoral programs, along with the Open Learning College for non-traditional learners (see the OGS/OLC offerings at " + SRC_CVSU + "/2018/01/13/ogs-olc-program-offerings/). Programs span education, agriculture, sciences, management, and other fields. Admission typically requires a bachelor's degree, transcript of records, and program-specific entrance requirements. Note: free tuition under RA 10931 generally does NOT cover graduate programs the same way as undergraduate — please verify applicable fees with the Cashier's Office. For the current list of graduate programs, see " + SRC_CVSU + ".",
    "CvSU's Graduate School and Open Learning College (OGS/OLC) offer master's and doctoral programs across various fields. Admission typically requires a bachelor's degree, TOR, and program-specific entrance requirements. RA 10931 may not cover graduate programs the same way — verify with the Cashier's Office. See " + SRC_CVSU + "/2018/01/13/ogs-olc-program-offerings/.",
    "May Graduate School at Open Learning College (OGS/OLC) ang CvSU para sa master's at doctoral programs. Karaniwang kailangan ng bachelor's degree, TOR, at program-specific requirements. Ang RA 10931 ay maaaring hindi sumasaklaw sa graduate programs sa parehong paraan — magtanong sa Cashier's Office. Tingnan ang " + SRC_CVSU + "/2018/01/13/ogs-olc-program-offerings/.",
],

# ---------- registrar (sourced from Citizens Charter listings + main university page) ----------
"registrar": [
    "The Office of the University Registrar (OUR) at CvSU handles your academic records, including Transcript of Records (TOR), Certificate of Enrollment, Certificate of Registration (COR), Diploma, Good Moral, Honorable Dismissal, and authentication of records. Each campus has its own Registrar office. For the verified Registrar contact details at your campus, please refer to the Citizens Charter and campus directory at " + SRC_CVSU + ". Document processes (forms, fees, timelines) are documented in the CvSU Citizens Charter. See " + SRC_CVSU + " (search 'Citizens Charter') for the latest edition.",
    "The Office of the University Registrar handles academic records — TOR, COR, Diploma, Good Moral, Honorable Dismissal, authentication. Each campus has its own Registrar. Document processes and fees are in the CvSU Citizens Charter on " + SRC_CVSU + ".",
    "Ang Office of the University Registrar ang nag-aasikaso ng academic records — TOR, COR, Diploma, Good Moral, Honorable Dismissal, at authentication. May sariling Registrar ang bawat campus. Ang detalye ng proseso at fees ay nasa CvSU Citizens Charter sa " + SRC_CVSU + ".",
],

# ---------- campus_facilities ----------
"campus_facilities": [
    "CvSU campuses typically offer academic facilities (libraries, computer and science laboratories), student services (Office of Student Affairs and Services / OSAS with Guidance and Counseling, Career and Placement, Student Welfare, etc.), and student-life areas (cafeteria, chapel where present, sports facilities). The Main Campus library is the Ladislao N. Diwa Memorial Library. The list of recognized facilities and offices at each campus is published per campus on the official site (" + SRC_CVSU + "/category/campuses/) and in the campus-level Citizens Charter (see " + SRC_CVSU + ").",
    "CvSU facilities include libraries (Main Campus: Ladislao N. Diwa Memorial Library), computer and science labs, OSAS (Guidance, Career and Placement, Student Welfare, etc.), and student-life spaces. Specific facilities differ per campus — see " + SRC_CVSU + "/category/campuses/.",
    "Ang mga pasilidad ng CvSU ay kinabibilangan ng library (Main Campus: Ladislao N. Diwa Memorial Library), computer at science labs, OSAS (Guidance, Career and Placement, Student Welfare), at iba pang student-life spaces. Iba-iba bawat campus — tingnan ang " + SRC_CVSU + "/category/campuses/.",
],

# ---------- student_organizations ----------
"student_organizations": [
    "Student organizations at CvSU are recognized and coordinated by the Office of Student Affairs and Services (OSAS). OSAS handles student development services, including student organization and socio-cultural affairs, student publication, and placement of students and graduates. To join, watch for the organization fair / open recruitment posted by OSAS or the org itself each academic year. For the recognized-org list at your campus, contact your campus OSAS via " + SRC_OSAS + ".",
    "OSAS oversees CvSU's recognized student organizations, including socio-cultural affairs, student publications, and student government. To join, watch for OSAS's organization recruitment or fair. For the recognized-org list, contact your campus OSAS. Source: " + SRC_OSAS,
    "Ang OSAS ang nangangasiwa ng mga recognized student organizations sa CvSU — kasama ang socio-cultural orgs, student publication, at student government. Para sumali, hintayin ang org recruitment ng OSAS o ng org mismo. Para sa listahan, makipag-ugnayan sa OSAS ng inyong campus. Pinagmulan: " + SRC_OSAS,
],

# ---------- student_portal ----------
"student_portal": [
    "CvSU has an official Student Portal at https://myportal.cvsu.edu.ph/. Your account credentials are issued by the campus ICT/MIS Office during enrollment. Through the portal you can typically view grades, manage online enrollment, and check your Certificate of Registration. For login issues or password resets, contact the ICT/MIS Office at your campus. Some campuses (e.g., Cavite City) also operate campus-specific student portals — verify with your campus.",
    "Access the CvSU Student Portal at https://myportal.cvsu.edu.ph/ with the credentials issued by your campus ICT/MIS Office. For login problems or password resets, contact ICT/MIS. Some campuses operate campus-specific portals — verify with your campus.",
    "Ang CvSU Student Portal ay nasa https://myportal.cvsu.edu.ph/. Ang account ay galing sa campus ICT/MIS Office sa enrollment. Para sa login problems o password reset, makipag-ugnayan sa ICT/MIS. May sariling portal ang ilang campus.",
],

# ---------- guidance_counseling (new intent) ----------
"guidance_counseling": [
    "The Guidance and Counseling Unit, under the Office of Student Affairs and Services (OSAS), provides counseling and student-welfare support across CvSU campuses. At each campus, the Guidance Services Unit is headed by the Campus Guidance Coordinator. Services typically include academic, career, and personal counseling, plus orientation and information programs. For verified Guidance Office contact details at your campus, please refer to the campus subdomain (e.g., trece.cvsu.edu.ph/osas/guidance-and-counseling) or your campus OSAS page. For after-hours mental-health concerns, the Philippines' NCMH Crisis Line is reachable 24/7 toll-free at 1553 (landline).",
    "The Guidance and Counseling Unit is under OSAS at each CvSU campus, led by the Campus Guidance Coordinator. Services include academic, career, and personal counseling. For your campus's Guidance contact, see the campus subdomain. For after-hours crisis support, NCMH Crisis Line: 1553 (24/7 toll-free landline). Source: " + SRC_OSAS,
    "Ang Guidance and Counseling Unit ay nasa ilalim ng OSAS sa bawat CvSU campus, pinamumunuan ng Campus Guidance Coordinator. Nag-aalok ng academic, career, at personal counseling. Para sa contact ng inyong campus, tingnan ang campus subdomain. Para sa 24/7 mental-health crisis, NCMH Crisis Line: 1553 (toll-free landline). Pinagmulan: " + SRC_OSAS,
],

# ---------- career_opportunity ----------
"career_opportunity": [
    "Career services at CvSU are coordinated through the Office of Student Affairs and Services (OSAS), specifically the Career and Placement unit, which assists students with career counseling, placement of students and graduates, and related programs. OJT/internship coordination is typically handled by your college's OJT coordinator together with the campus Career and Placement Office. For job openings within CvSU itself (faculty/staff), the Human Resource Management Office publishes vacancies through the official site. See " + SRC_OSAS + " and " + SRC_CVSU + " for verified contacts and current postings.",
    "OSAS's Career and Placement unit handles career services and graduate placement at CvSU. OJT/internship is run via your college's OJT coordinator plus Career and Placement. For CvSU faculty/staff hiring, watch the HRMO postings on " + SRC_CVSU + ". Source: " + SRC_OSAS,
    "Ang Career and Placement (nasa ilalim ng OSAS) ang nag-aasikaso ng career services at graduate placement sa CvSU. Ang OJT/internship ay sa pamamagitan ng OJT coordinator ng inyong college at Career and Placement. Para sa CvSU hiring, tingnan ang HRMO sa " + SRC_CVSU + ". Pinagmulan: " + SRC_OSAS,
],

# ---------- ojt_internship ----------
"ojt_internship": [
    "OJT / Internship at CvSU is coordinated by your college's OJT coordinator in partnership with the Career and Placement Office under OSAS. Required hours, eligible partner companies, and documentation (MOA, endorsement letter, daily time record, internship report) depend on your specific program and college. Watch for the OJT orientation announced by your department, then coordinate placement with your coordinator. For the current OJT partners and process at your campus, see your department and " + SRC_OSAS + ".",
    "OJT is coordinated by your college's OJT coordinator with OSAS's Career and Placement. Specifics (hours, partners, paperwork) vary by program. Attend the department orientation; coordinate placement with your coordinator. Source: " + SRC_OSAS,
    "Ang OJT sa CvSU ay sa pamamagitan ng OJT coordinator ng college at Career and Placement (nasa OSAS). Ang hours, partners, at paperwork ay nag-iiba bawat programa. Dumalo sa department orientation. Pinagmulan: " + SRC_OSAS,
],

# ---------- directory ----------
"directory": [
    "Official CvSU contact directories are maintained per campus on the university's site and campus subdomains. Verified entry points: the main site at " + SRC_CVSU + ", the campus directory at " + SRC_CVSU + "/category/campuses/, and campus-level Citizens Charters on " + SRC_CVSU + ". Verified contact points include OVPRE landline (046) 862-0850 and mobile +63 998 937 2020 / +63 995 971 5511 (see " + SRC_CVSU + "/ovpre-contact-us/), the admission email admission@cvsu.edu.ph, and OSAS Main / Guidance email osasmain.guidance@cvsu.edu.ph. For other campuses, use the campus subdomain (e.g., trece.cvsu.edu.ph) or the campus Citizens Charter.",
    "Use the official directories on " + SRC_CVSU + " (and the campus subdomains like trece.cvsu.edu.ph, naic.cvsu.edu.ph) for current contacts. Verified contacts: OVPRE (046) 862-0850; admission@cvsu.edu.ph; osasmain.guidance@cvsu.edu.ph. Source: " + SRC_CVSU + "/ovpre-contact-us/",
    "Para sa opisyal na directory, gamitin ang " + SRC_CVSU + " at ang campus subdomains. Mga verified na contact: OVPRE (046) 862-0850; admission@cvsu.edu.ph; osasmain.guidance@cvsu.edu.ph. Pinagmulan: " + SRC_CVSU + "/ovpre-contact-us/",
],

}  # END OFFICIAL

# Apply updates
updated = []
for it in data["intents"]:
    tag = it["tag"]
    if tag in OFFICIAL:
        new_it = dict(it)
        new_it["responses"] = OFFICIAL[tag]
        updated.append(new_it)
    else:
        updated.append(it)

out = {"intents": updated}
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"Wrote {OUT}")
print(f"Tags updated with official-sourced responses: {len(OFFICIAL)}")
print(f"Total intents in v3: {len(updated)}")
print(f"Updated tags: {sorted(OFFICIAL.keys())}")
