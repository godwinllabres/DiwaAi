"""
One-shot patch: point the curated `graduate_programs` answer at CvSU's current
College of Advanced and Professional Studies (CAPS) catalog instead of the
2018 GS-OLC page (2026-11-25).

Source: https://cvsu.edu.ph/programs-offered-caps/ (14 degree titles; no date
shown on the page, WordPress publication date 2026-11-05; read 2026-11-25); CAPS line (046) 890-5926 from the CvSU directory,
https://cvsu.edu.ph/contact-us-2/. The full title list lives in
data/college_programs.json, which the College Programs tier serves.

Updates, in lockstep (same rule as previous intent patches):
  - data/cavsu_intents.json   (training source of truth)
  - models/responses_map.json (runtime fallback / NB artifact)
  - data/cavsu_intents.db     (runtime primary — rows updated in place, so
                               the campus_places / map_* / seasons tables stay)

Idempotent. Run from repo root:
  .venv/Scripts/python.exe scripts/patch_caps_catalog.py
"""

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"
RESPONSES_MAP_PATH = ROOT / "models" / "responses_map.json"
DB_PATH = ROOT / "data" / "cavsu_intents.db"
TAG = "graduate_programs"

NEW = [
    "CvSU's graduate programs are offered by the College of Advanced and Professional Studies "
    "(CAPS), formerly the Graduate School and Open Learning College (GS-OLC). Its current "
    "catalog lists 14 master's and doctoral degrees in agriculture, education, English language "
    "studies, biology, business administration, management, public administration, engineering, "
    "information technology, and food science: https://cvsu.edu.ph/programs-offered-caps/. The "
    "catalog gives degree titles only, so confirm majors and which programs accept applicants "
    "this semester with CAPS at (046) 890-5926. Admission typically requires a bachelor's "
    "degree, transcript of records, and program-specific requirements. Free tuition under "
    "RA 10931 generally does not cover graduate programs the way it covers undergraduate ones "
    "— check applicable fees with the Cashier's Office.",

    "CvSU's College of Advanced and Professional Studies (CAPS, formerly GS-OLC) offers 14 "
    "master's and doctoral programs, listed in its current catalog: "
    "https://cvsu.edu.ph/programs-offered-caps/. Admission typically requires a bachelor's "
    "degree, TOR, and program-specific requirements. For majors and this semester's intake, "
    "call CAPS at (046) 890-5926; for fees, ask the Cashier's Office, since RA 10931 may not "
    "cover graduate programs the same way.",

    "Ang College of Advanced and Professional Studies (CAPS, dating GS-OLC) ang nag-aalok ng "
    "mga master's at doctoral program ng CvSU — 14 na programa sa kasalukuyang catalog: "
    "https://cvsu.edu.ph/programs-offered-caps/. Karaniwang kailangan ng bachelor's degree, "
    "TOR, at program-specific requirements. Para sa mga major at kung aling programa ang "
    "tumatanggap ng aplikante ngayong semestre, tumawag po sa CAPS sa (046) 890-5926. Ang "
    "RA 10931 ay maaaring hindi sumasaklaw sa graduate programs sa parehong paraan — "
    "magtanong sa Cashier's Office.",
]


def _dump(path: Path, data) -> None:
    raw = path.read_text(encoding="utf-8")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text + ("\n" if raw.endswith("\n") else ""), encoding="utf-8", newline="\n")


def main() -> None:
    intents = json.loads(INTENTS_PATH.read_text(encoding="utf-8"))
    items = intents["intents"] if isinstance(intents, dict) else intents
    entry = next(i for i in items if i.get("tag") == TAG)
    old = list(entry["responses"])
    entry["responses"] = list(NEW)
    _dump(INTENTS_PATH, intents)
    print(f"✓ {INTENTS_PATH.name}: {TAG} ({len(old)} → {len(NEW)} responses)")

    rmap = json.loads(RESPONSES_MAP_PATH.read_text(encoding="utf-8"))
    rmap[TAG] = list(NEW)
    _dump(RESPONSES_MAP_PATH, rmap)
    print(f"✓ {RESPONSES_MAP_PATH.name}: {TAG} replaced")

    con = sqlite3.connect(str(DB_PATH))
    try:
        with con:
            (intent_id,) = con.execute("SELECT id FROM intents WHERE tag=?", (TAG,)).fetchone()
            con.execute("DELETE FROM responses WHERE intent_id=?", (intent_id,))
            con.executemany("INSERT INTO responses (intent_id, response_text) VALUES (?, ?)",
                            [(intent_id, text) for text in NEW])
    finally:
        con.close()
    print(f"✓ {DB_PATH.name}: {TAG} rows replaced")


if __name__ == "__main__":
    main()
