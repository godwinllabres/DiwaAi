# DIWA / CvSU Intents — Quality Audit

Source file: `data/cavsu_intents.json`

## Top-level numbers

- **Tags:** 42
- **Total patterns:** 3,435
- **Total responses:** 119
- **Noisy / templated patterns** (e.g., starting with 'Please help me with...', 'Sana matulungan mo ako sa...'): **1,232**
- **Responses containing unverified factual claims:** **25**
- **Patterns that appear in more than one tag:** 130

## Critical issues — must fix before training

- `vision_mission`: 1 response(s) contain unverified facts — examples: [specific named landmark (verify)]
- `campus_facilities`: 2 response(s) contain unverified facts — examples: [Center-of-Excellence claim (verify)]; [specific named facility (verify)]
- `library`: 2 response(s) contain unverified facts — examples: [specific named facility (verify)]; [specific named facility (verify)]
- `it_cs_courses`: 3 response(s) contain unverified facts — examples: [specific email (verify)]; [specific email (verify)]; [specific email (verify)]
- `graduate_programs`: 3 response(s) contain unverified facts — examples: [specific email (verify)]; [specific email (verify)]; [specific email (verify)]
- `admissions_requirements`: 1 response(s) contain unverified facts — examples: [fabricated admission stat (7,490)]
- `admissions_exam`: 1 response(s) contain unverified facts — examples: [subdomain (verify still active)]
- `enrollment_schedule`: 1 response(s) contain unverified facts — examples: [subdomain (verify still active)]
- `contact_info`: 1 response(s) contain unverified facts — examples: [subdomain (verify still active)]
- `student_organizations`: 3 response(s) contain unverified facts — examples: [org count (84+)]; [org count (84+)]; [org count (84+)]
- `campus_specific`: 5 pleasantry patterns leaked in (e.g., ['hello', 'hi there', 'thank you'])
- `campus_specific`: 1 response(s) contain unverified facts — examples: [specific college count (11)]
- `chitchat`: 4 pleasantry patterns leaked in (e.g., ['good morning', 'good afternoon', 'good evening'])
- `dormitory`: 1 response(s) contain unverified facts — examples: [specific email (verify)]
- `student_portal`: 1 response(s) contain unverified facts — examples: [specific email (verify)]
- `international_students`: 3 response(s) contain unverified facts — examples: [specific email (verify)]; [specific email (verify)]; [specific email (verify)]
- `online_programs`: 1 response(s) contain unverified facts — examples: [specific email (verify)]

## Warnings — should fix

- `greeting`: 4 duplicate patterns within the same tag
- `greeting`: 24 noisy templated patterns (e.g., 'Please help me with...')
- `thanks`: 6 duplicate patterns within the same tag
- `thanks`: 58 noisy templated patterns (e.g., 'Please help me with...')
- `about_cvsu`: 2 duplicate patterns within the same tag
- `about_cvsu`: 283 noisy templated patterns (e.g., 'Please help me with...')
- `vision_mission`: 22 noisy templated patterns (e.g., 'Please help me with...')
- `campus_location`: 1 duplicate patterns within the same tag
- `campus_location`: 21 noisy templated patterns (e.g., 'Please help me with...')
- `campus_facilities`: 33 noisy templated patterns (e.g., 'Please help me with...')
- `courses_offered`: 2 duplicate patterns within the same tag
- `courses_offered`: 38 noisy templated patterns (e.g., 'Please help me with...')
- `graduate_programs`: 68 noisy templated patterns (e.g., 'Please help me with...')
- `admissions_requirements`: 39 noisy templated patterns (e.g., 'Please help me with...')
- `admissions_exam`: 23 noisy templated patterns (e.g., 'Please help me with...')
- `enrollment_procedure`: 31 noisy templated patterns (e.g., 'Please help me with...')
- `enrollment_schedule`: 24 noisy templated patterns (e.g., 'Please help me with...')
- `tuition_fees`: 28 noisy templated patterns (e.g., 'Please help me with...')
- `scholarship`: 66 noisy templated patterns (e.g., 'Please help me with...')
- `contact_info`: 32 noisy templated patterns (e.g., 'Please help me with...')
- `campus_specific`: 120 noisy templated patterns (e.g., 'Please help me with...')
- `chitchat`: 125 noisy templated patterns (e.g., 'Please help me with...')
- `academic_policies`: 86 noisy templated patterns (e.g., 'Please help me with...')
- `international_students`: 22 noisy templated patterns (e.g., 'Please help me with...')

## Patterns that appear in MORE THAN ONE tag (top 30)

These are direct sources of model confusion — the same exact pattern is labeled with different intents.

| Pattern | Appears in tags |
| --- | --- |
| `can a pwd student apply for accommodation at cvsu?` | campus_specific, chitchat, dormitory, thanks |
| `can i take a leave of absence at cvsu?` | academic_policies, campus_specific, chitchat, thanks |
| `can international students apply to cvsu?` | campus_specific, chitchat, international_students, thanks |
| `cvsu accreditation status` | about_cvsu, campus_specific, chitchat, thanks |
| `cvsu president 2026` | about_cvsu, campus_specific, chitchat, thanks |
| `does cvsu have a library?` | campus_specific, chitchat, library, thanks |
| `does cvsu offer distance learning?` | campus_specific, chitchat, online_programs, thanks |
| `how many campuses does cvsu have?` | about_cvsu, campus_branches, campus_location, campus_specific |
| `is 3.0 a passing grade at cvsu?` | academic_policies, campus_specific, chitchat, thanks |
| `is there academic probation at cvsu?` | academic_policies, campus_specific, chitchat, thanks |
| `may nstp ba sa cvsu? paano pumili ng component?` | campus_specific, chitchat, nstp, thanks |
| `what is the cvsu grading system?` | academic_policies, campus_specific, chitchat, thanks |
| `what is the cvsu leave of absence policy?` | academic_policies, campus_specific, chitchat, thanks |
| `what is the cvsu retention policy?` | academic_policies, campus_specific, chitchat, thanks |
| `ano ang misyon ng cvsu?` | campus_specific, chitchat, thanks |
| `apply?` | campus_specific, chitchat, thanks |
| `are you a robot?` | campus_specific, chitchat, thanks |
| `campus?` | campus_specific, chitchat, thanks |
| `can i enroll in cvsu without going to campus?` | campus_specific, chitchat, thanks |
| `can i take elective subjects outside my course at cvsu?` | campus_specific, chitchat, thanks |
| `can you help me?` | campus_specific, chitchat, thanks |
| `contact?` | campus_specific, chitchat, thanks |
| `cvsu facilities list` | campus_specific, chitchat, thanks |
| `cvsu free tution` | campus_specific, chitchat, thanks |
| `cvsu online application` | campus_specific, chitchat, thanks |
| `does cvsu have a law school?` | campus_specific, courses, courses_offered |
| `does cvsu have a linkage with foreign universities?` | campus_specific, chitchat, thanks |
| `enrollment?` | campus_specific, chitchat, thanks |
| `exam?` | campus_specific, chitchat, thanks |
| `good morning` | campus_specific, chitchat, greeting |

## Per-tag stats

| tag | patterns | unique | dupes | noisy | responses | tagalog? | misplaced | unverified resp. |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| greeting | 103 | 99 | 4 | 24 | 3 | yes | 0 | 0 |
| goodbye | 76 | 76 | 0 | 8 | 3 | yes | 0 | 0 |
| thanks | 152 | 146 | 6 | 58 | 3 | yes | 0 | 0 |
| about_cvsu | 426 | 424 | 2 | 283 | 3 | yes | 0 | 0 |
| vision_mission | 79 | 79 | 0 | 22 | 3 | yes | 0 | 1 |
| campus_location | 63 | 62 | 1 | 21 | 3 | yes | 0 | 0 |
| campus_facilities | 81 | 81 | 0 | 33 | 3 | yes | 0 | 2 |
| library | 56 | 56 | 0 | 0 | 3 | yes | 0 | 2 |
| courses_offered | 101 | 99 | 2 | 38 | 3 | yes | 0 | 0 |
| it_cs_courses | 48 | 48 | 0 | 13 | 3 | yes | 0 | 3 |
| graduate_programs | 114 | 114 | 0 | 68 | 3 | yes | 0 | 3 |
| admissions_requirements | 133 | 133 | 0 | 39 | 3 | yes | 0 | 1 |
| admissions_exam | 61 | 61 | 0 | 23 | 3 | yes | 0 | 1 |
| enrollment_procedure | 85 | 85 | 0 | 31 | 3 | yes | 0 | 0 |
| enrollment_schedule | 57 | 57 | 0 | 24 | 3 | yes | 0 | 1 |
| tuition_fees | 90 | 90 | 0 | 28 | 3 | yes | 0 | 0 |
| scholarship | 118 | 118 | 0 | 66 | 3 | yes | 0 | 0 |
| academic_calendar | 58 | 58 | 0 | 12 | 3 | yes | 0 | 0 |
| events | 39 | 39 | 0 | 0 | 3 | yes | 0 | 0 |
| contact_info | 72 | 72 | 0 | 32 | 3 | yes | 0 | 1 |
| registrar | 68 | 68 | 0 | 10 | 3 | yes | 0 | 0 |
| student_organizations | 50 | 50 | 0 | 5 | 3 | yes | 0 | 3 |
| campus_specific | 263 | 263 | 0 | 120 | 3 | yes | 5 | 1 |
| nlu_fallback | 5 | 5 | 0 | 0 | 3 | yes | 0 | 0 |
| chitchat | 270 | 270 | 0 | 125 | 3 | yes | 4 | 0 |
| nstp | 52 | 52 | 0 | 9 | 3 | yes | 0 | 0 |
| academic_policies | 145 | 145 | 0 | 86 | 3 | yes | 0 | 0 |
| dormitory | 59 | 59 | 0 | 15 | 3 | yes | 0 | 1 |
| student_portal | 39 | 39 | 0 | 0 | 3 | yes | 0 | 1 |
| international_students | 55 | 55 | 0 | 22 | 3 | yes | 0 | 3 |
| online_programs | 55 | 55 | 0 | 17 | 3 | yes | 0 | 1 |
| alumni | 46 | 46 | 0 | 0 | 3 | yes | 0 | 0 |
| career_opportunity | 60 | 60 | 0 | 0 | 3 | yes | 0 | 0 |
| directory | 49 | 49 | 0 | 0 | 3 | yes | 0 | 0 |
| retention_policy | 85 | 85 | 0 | 0 | 3 | yes | 0 | 0 |
| retention_policy_grades | 43 | 43 | 0 | 0 | 3 | yes | 0 | 0 |
| retention_policy_appeal | 30 | 30 | 0 | 0 | 3 | yes | 0 | 0 |
| shifting | 26 | 26 | 0 | 0 | 2 | yes | 0 | 0 |
| student_id | 7 | 7 | 0 | 0 | 2 | yes | 0 | 0 |
| enrollment | 5 | 5 | 0 | 0 | 1 | no | 0 | 0 |
| campus_branches | 5 | 5 | 0 | 0 | 2 | no | 0 | 0 |
| courses | 6 | 6 | 0 | 0 | 1 | no | 0 | 0 |

## What 'unverified' means here

The audit flags responses containing claims I cannot verify from training data and that should not be presented to students as fact:

- Specific campus *counts* (e.g., '13 campuses', '11 colleges') — the number changes over time.
- A specific *list* of campuses with establishment years (these dates in the source file are not corroborated).
- Specific student-population, acceptance-rate, or library-book statistics (e.g., '92,000 books', '7,490 admitted', '34.45% acceptance rate', '84+ recognized organizations', '72 hectares').
- Specific phone numbers and named offices' email addresses — these change and should be looked up on the official directory.
- Awards, accreditations, and 'Center of Excellence' designations — these are time-sensitive and program-specific.

**What stays in the dataset:** CvSU's broad history (1906 origins as Indang Intermediate School during the Thomasite era; university status granted in 1998 via RA 8468), main-campus location (Don Severino delas Alas Campus, Indang, Cavite), motto ('Truth, Excellence, Service'), 'Iskolar para sa Bayan', RA 10931 (Universal Access to Quality Tertiary Education Act of 2017), NSTP under RA 9163, and the general 1.0–5.0 grading scale used by Philippine SUCs.
