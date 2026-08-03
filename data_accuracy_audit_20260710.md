# Data Accuracy Audit — campus_places.py vs data/ vs official CvSU sources

**Date:** 2026-07-10
**Scope:** `api/campus_places.py` (canonical), `data/` folder (DB + JSON), `SeviWeb/app/lib/campusMap.ts`, official cvsu.edu.ph pages.

## Verdict

Internal sync is clean — the canonical file, the DB, and the frontend map agree exactly. The real problems are **4 likely-wrong emails**, **2 wrong facility names vs official CvSU**, a **stale gate constant**, and **4 dead intent routings**.

## What checks out ✅

| Check | Result |
|---|---|
| Legend numbers 1–48 | 48 unique, no gaps, no duplicates |
| `_PLACE_METADATA` (py) vs `campus_places` DB table (49 rows) | 0 field diffs |
| py vs `SeviWeb/app/lib/campusMap.ts` (names, nums, x/y) | 0 diffs |
| `waypoints_override.json` (46) vs `map_waypoints` DB (46) | identical |
| `coords_override.json`, `custom_markers.json` | empty `{}` — nothing shadowing canonical |
| Intents: DB (112) vs `cavsu_intents.json` (112) | identical tag sets, 0 inactive |
| All marker coords within 0–2000 viewbox | pass |
| `_INTENT_TO_PLACE` targets | all point at valid place_ids |
| `registrarmain@cvsu.edu.ph`, `ceit@cvsu.edu.ph` | confirmed on official contact page |
| Ladislao N. Diwa Memorial Library, Hostel Tropicana, Research Center, NCRDEC, CCJ, CED, CAS, CON, CTHM names | match official |

## Discrepancies vs official CvSU (cvsu.edu.ph, June 2025 listing)

### Names

| place_id | Canonical file | Official |
|---|---|---|
| `cvmbs` | College of Veterinary Medicine and **Biological** Sciences | College of Veterinary Medicine and **Biomedical** Sciences |
| `gender_dev` | Gender and Development **Research** Center | Gender and Development **Resource** Center (GAD) |
| `gym` | College of Sports **and** Physical Education and Recreation | College of Sports**,** Physical Education and Recreation (CSPEAR) |
| `agri_eco` | CvSU Agri-Eco Park | Agri-Eco **Tourism** Park (minor) |
| `grad` | Graduate School | Graduate School **and Open Learning College** (minor — responses already say "OGS/OLC") |

Also update `_PLACE_KEYWORDS`: `cvmbs` alias `"biological sciences"` → add/replace with `"biomedical sciences"`.

### Emails in `_INTENT_TO_DIRECTORY`

| Used in code | Official | Status |
|---|---|---|
| `osasmain@cvsu.edu.ph` | `cvsuosasmain@cvsu.edu.ph` | **wrong** (used in 7 entries via `_OSAS_EMAIL`) |
| `librarymain@cvsu.edu.ph` | `cvsulibrary@cvsu.edu.ph` | **wrong** |
| `gradschoolmain@cvsu.edu.ph` | `gs.olc@cvsu.edu.ph` | **wrong** |
| `alumnimain@cvsu.edu.ph` | `alumniaffairs@cvsu.edu.ph` | **wrong** |
| `mis@cvsu.edu.ph` (student_portal) | `icto@cvsu.edu.ph` — office is now "ICTO" | likely outdated |
| `admissionmain@`, `cashiermain@`, `scholarshipmain@`, `internationalmain@`, `info@` | not on official page | unverified — confirm before shipping |

## Internal inconsistencies

1. **Stale gate constant.** `_GATE = (639, 1610)` (py) = `MAIN_GATE` (ts), but the `gate_1` marker is at (868, 1670) and routing waypoint `wp_gate1` at (857, 1686). (639, 1610) sits next to the Clinic, ~237px from the actual gate marker. Correlation of `walk_minutes_from_gate` with pixel distance: r = 0.95 from the marker vs r = 0.83 from the constant — minutes were authored from the marker. Currently dormant (`MAIN_GATE` has no importers; `payload["gate"]` unused by frontend), but fix or delete before anyone consumes it.
2. **Dead intent keys** in `_INTENT_TO_PLACE` / `_INTENT_TO_DIRECTORY` — no such tags exist in the 112 intents: `enrollment`, `shifting` (actual tag: `shifting_program`), `student_id` (actual: `lost_id_replacement`, `id_verification_disambiguation`), `campus_branches` (no branch tag). These mappings never fire.
3. **Unrouted real intents** — exist in DB but get no map/directory card: `enrollment_problems`, `late_enrollment`, `shifting_program`, `lost_id_replacement`. Probably all should route to `osas`.
4. **Duplicate `intl_house` entry** in `_PLACE_KEYWORDS` (two separate tuples). Harmless but merge them.

## Not verifiable remotely

Pixel coordinates against the official Matayuyon Crop Science Society (2026) printed map image, walk-minute ground truth, office hours, and phone numbers (phone deliberately sanitized to `None`). The 48-place legend itself matches internally everywhere. Note: official site now lists a **College of Medicine** (`medicine@cvsu.edu.ph`) — not on the 48-place map; consider whether it needs a marker/routing.

## Recommended fix order

1. Fix 4 wrong emails (user-facing directory cards).
2. Rename `cvmbs` full name + keyword alias, `gender_dev` full name.
3. Route `shifting_program`, `late_enrollment`, `enrollment_problems`, `lost_id_replacement` → `osas`; delete the 4 dead keys.
4. Reconcile `_GATE`/`MAIN_GATE` with the gate_1 marker (or remove both).
5. Sync any change to all three: `campus_places.py` + DB `campus_places` table + `campusMap.ts` (docstring mandates lockstep).

**Sources:** [CvSU Contact Us / University Officials](https://cvsu.edu.ph/contact-us-2/) · [CSPEAR Officials](https://cvsu.edu.ph/college-of-sports-physical-education-and-recreation-officials/) · [CCJ](https://cvsu.edu.ph/2018/01/12/college-of-criminal-justice/)
