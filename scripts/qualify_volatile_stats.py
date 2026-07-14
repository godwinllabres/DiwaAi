"""Append an 'as of 2026, verify latest' qualifier to the volatile-stat
naive_bayes responses, patching responses_map.json + cavsu_intents.json + the
DB together (they must stay byte-identical per intent).

Volatile = rankings, licensure/board results, and accreditation levels/years —
accurate now (verified against the CvSU mirror) but they rot each cycle.
Idempotent: re-running does nothing once the qualifier is present.
"""
import json
import re
import shutil
import sqlite3
from datetime import datetime

RESP_MAP = "models/responses_map.json"
INTENTS_JSON = "data/cavsu_intents.json"
INTENTS_DB = "data/cavsu_intents.db"

VOLATILE_INTENTS = [
    "about_cvsu", "agriculture_programs", "business_management",
    "compare_to_other_school", "courses_offered", "it_cs_courses",
    "nursing_health_programs", "education_programs", "engineering_programs",
    "criminology_program", "board_exam_review",
]

EN_Q = (" (Rankings, board-exam results, and accreditation levels cited here are"
        " as of 2026 — verify the latest figures at https://cvsu.edu.ph.)")
FIL_Q = (" (Ang mga ranking, resulta ng board exam, at accreditation level na"
         " nabanggit dito ay hanggang 2026 — tingnan ang pinakabago sa"
         " https://cvsu.edu.ph.)")

# Marker of the qualifier already being present (language-agnostic substring).
ALREADY = "cited here are as of 2026"
ALREADY_FIL = "nabanggit dito ay hanggang 2026"

_FIL_MARKERS = re.compile(
    r"\b(ang|ng|mga|nag-aalok|Tingnan|Pinagmulan|Walang|Inaalok|Hindi|Nakakuha|Ayon)\b"
)


def is_filipino(s: str) -> bool:
    return bool(_FIL_MARKERS.search(s))


def qualify(s: str) -> str:
    if ALREADY in s or ALREADY_FIL in s:
        return s  # idempotent
    return s + (FIL_Q if is_filipino(s) else EN_Q)


def build_mapping() -> dict:
    """old_text -> new_text for every response string in the volatile intents."""
    rm = json.load(open(RESP_MAP, encoding="utf-8"))
    mapping = {}
    for tag in VOLATILE_INTENTS:
        for s in rm.get(tag, []):
            new = qualify(s)
            if new != s:
                mapping[s] = new
    return mapping


def patch_json_map(mapping):
    rm = json.load(open(RESP_MAP, encoding="utf-8"))
    n = 0
    for tag, resps in rm.items():
        for i, s in enumerate(resps):
            if s in mapping:
                resps[i] = mapping[s]
                n += 1
    json.dump(rm, open(RESP_MAP, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return n


def patch_intents_json(mapping):
    doc = json.load(open(INTENTS_JSON, encoding="utf-8"))
    n = 0
    for it in doc["intents"]:
        for i, s in enumerate(it.get("responses", [])):
            if s in mapping:
                it["responses"][i] = mapping[s]
                n += 1
    json.dump(doc, open(INTENTS_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return n


def patch_db(mapping):
    con = sqlite3.connect(INTENTS_DB)
    cur = con.cursor()
    n = 0
    for old, new in mapping.items():
        cur.execute(
            "UPDATE responses SET response_text=? WHERE response_text=?", (new, old)
        )
        n += cur.rowcount
    con.commit()
    con.close()
    return n


def main():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for path in (RESP_MAP, INTENTS_JSON, INTENTS_DB):
        shutil.copy2(path, f"{path}.bak_{ts}")
    mapping = build_mapping()
    print(f"strings to qualify: {len(mapping)}")
    print(f"  responses_map.json: {patch_json_map(mapping)} replaced")
    print(f"  cavsu_intents.json: {patch_intents_json(mapping)} replaced")
    print(f"  cavsu_intents.db  : {patch_db(mapping)} replaced")
    print(f"backups written with suffix .bak_{ts}")


if __name__ == "__main__":
    main()
