"""
One-shot patch: replace stale contact numbers (OVPRE landline/mobiles) with
the static CvSU trunkline + PABX local directory.

Updates, in lockstep (same rule as previous intent patches):
  - data/cavsu_intents.json   (training source of truth)
  - models/responses_map.json (runtime fallback / NB artifact)
  - data/cavsu_intents.db     (runtime primary — rebuilt from JSON)

Run from repo root:
  py -3.11 scripts/patch_contact_directory.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTENTS_PATH = ROOT / "data" / "cavsu_intents.json"
RESPONSES_MAP_PATH = ROOT / "models" / "responses_map.json"

TRUNK = "4839250"
DIAL_NOTE = "Note: To make an outside call, press 9 followed by the number. (e.g. 94150013)"

# CvSU Phone Directory — verbatim static values (office, local no.)
ADMIN_BUILDING = [
    ("OUP", 1001), ("OUP - SECRETARY", 1002),
    ("OVPAA", 1003), ("OVPAA - SECRETARY", 1004),
    ("OVPBARG", 1005), ("OVPBARG - SECRETARY", 1006),
    ("OVPRIE", 1007), ("OVPRIE - SECRETARY", 1008),
    ("OVPASS", 1009), ("OVPASS - SECRETARY", 1010),
    ("OVPPD", 1011), ("OVPPD - SECRETARY", 1012),
    ("ACCOUNTING", 1013), ("BUDGET", 1014), ("CASHIER", 1015),
    ("COA", 1016), ("HRDO", 1017), ("INTERNAL AUDIT", 1018),
    ("LEGAL OFFICE", 1019), ("OBS", 1020), ("PACO", 1021),
    ("PROCUREMENT", 1022), ("RECORDS OFFICE", 1023), ("SUPPLY OFFICE", 1024),
]

OTHER_OFFICES = [
    ("AGRI ECO", 1036), ("ALUMNI", 1049), ("BAMO", 1106), ("BRITE CENTER", 1037),
    ("CAFENR - ENSCI", 1073), ("CAFENR - ANSCI", 1071), ("CAFENR - CROP", 1072),
    ("CAFENR - DEAN", 1069), ("CAFENR - FOOD TECH", 1074), ("CAFENR - REGISTRAR", 1070),
    ("CAS - BIOSCI", 1077), ("CAS - DCOM", 1078), ("CAS - DEAN", 1075),
    ("CAS - DHUM", 1079), ("CAS - DSS", 1080), ("CAS - PHYSCI", 1081),
    ("CAS - REGISTRAR", 1076),
    ("CCJ - DEAN", 1097), ("CCJ - FACULTY", 1099), ("CCJ - REGISTRAR", 1098),
    ("CDC", 1104),
    ("CED - REGISTRAR", 1065), ("CED - TED", 1067), ("CED - DEAN", 1064),
    ("CEIT - DAFE", 1057), ("CEIT - DCEA", 1058), ("CEIT - DCEE", 1059),
    ("CEIT - DIET", 1060), ("CEIT - DIT 1", 1061), ("CEIT - DIT 2", 1062),
    ("CEIT - REGISTRAR", 1056), ("CEIT - DEAN", 1055),
    ("CELLAR", 1041),
    ("CEMDS - DEAN", 1085), ("CEMDS - ECONOMICS", 1087), ("CEMDS - MNGMNT", 1088),
    ("CEMDS - OFAD", 1089), ("CEMDS - REGISTRAR", 1086),
    ("COM - DEAN", 1100),
    ("CON - DEAN", 1090), ("CON - MEDTECH", 1092), ("CON - MIDWIFERY", 1093),
    ("CON - REGISTRAR", 1091),
    ("CSPEAR - DEAN", 1094), ("CSPEAR - FACULTY", 1096), ("CSPEAR - REGISTRAR", 1095),
    ("CTHM - DEAN", 1066),
    ("CVMBS - DEAN", 1082), ("CVMBS - HOSPITAL", 1084), ("CVMBS - REGISTRAR", 1083),
    ("DATA CENTER", 1063), ("EMO", 1105), ("ERB", 1028), ("EXTENSION", 1029),
    ("GAD", 1046), ("GSO", 1027), ("GSOLC", 1042),
    ("GUARD ADMIN", 1103), ("GUARD GATE 1", 1044), ("GUARD GATE 2", 1045),
    ("HOSTEL", 1035), ("ICTO", 1054), ("ILCLO", 1043), ("INFIRMARY", 1040),
    ("ITSO", 1032), ("KMC", 1030), ("LIBRARY", 1038), ("MACAPUNO", 1033),
    ("MUSEUM", 1101), ("NCRDEC", 1034), ("OSAS", 1025), ("PCO", 1051),
    ("PDO", 1048), ("PRG", 1053), ("QAO", 1047), ("REGISTRAR MAIN", 1026),
    ("RESEARCH", 1031), ("SAKA", 1102), ("SHS", 1068), ("SPRINT", 1050),
    ("STARRDEC", 1039), ("USD0", 1052),
]


def _directory_text() -> str:
    lines = [
        f"CvSU Phone Directory — CvSU Number: {TRUNK}",
        DIAL_NOTE,
        "",
        "ADMINISTRATION BUILDING:",
    ]
    lines += [f"- {office} — loc. {local}" for office, local in ADMIN_BUILDING]
    lines += ["", "OTHER OFFICES:"]
    lines += [f"- {office} — loc. {local}" for office, local in OTHER_OFFICES]
    return "\n".join(lines)


CONTACT_INFO_RESPONSES = [
    (
        "CvSU Main Campus: Don Severino delas Alas Campus, Indang, Cavite. "
        "Website: https://cvsu.edu.ph. "
        f"CvSU Number: {TRUNK}. {DIAL_NOTE} "
        "Key office locals: REGISTRAR MAIN loc. 1026, OSAS loc. 1025, CASHIER loc. 1015, "
        "HRDO loc. 1017, LIBRARY loc. 1038, ICTO loc. 1054. "
        "Email contacts: admission@cvsu.edu.ph for admission inquiries, "
        "osasmain.guidance@cvsu.edu.ph for OSAS Main / Guidance, and obs@cvsu.edu.ph for the "
        "Office of the Board Secretary (2nd Floor, Administration Building). "
        "Ask me for the 'phone directory' to see the full list of office locals."
    ),
    (
        "Main Campus: Don Severino delas Alas Campus, Indang, Cavite. Website: cvsu.edu.ph. "
        f"CvSU Number: {TRUNK}. {DIAL_NOTE} "
        "Mga pangunahing local: REGISTRAR MAIN loc. 1026, OSAS loc. 1025, CASHIER loc. 1015, "
        "HRDO loc. 1017, LIBRARY loc. 1038, ICTO loc. 1054. "
        "Email: admission@cvsu.edu.ph para sa admission; osasmain.guidance@cvsu.edu.ph para sa "
        "OSAS Main / Guidance; obs@cvsu.edu.ph para sa Office of the Board Secretary. "
        "Itanong ang 'phone directory' para sa kumpletong listahan ng office locals."
    ),
]

DIRECTORY_RESPONSES = [_directory_text()]


def patch_intents_json() -> None:
    with open(INTENTS_PATH, "r", encoding="utf-8") as f:
        doc = json.load(f)
    intent_map = {i["tag"]: i for i in doc["intents"]}
    intent_map["contact_info"]["responses"] = CONTACT_INFO_RESPONSES
    intent_map["directory"]["responses"] = DIRECTORY_RESPONSES
    with open(INTENTS_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, indent=2, ensure_ascii=False))
    print(f"✓ {INTENTS_PATH.name}: contact_info ({len(CONTACT_INFO_RESPONSES)} responses), "
          f"directory ({len(DIRECTORY_RESPONSES)} response)")


def patch_responses_map() -> None:
    with open(RESPONSES_MAP_PATH, "r", encoding="utf-8") as f:
        rmap = json.load(f)
    rmap["contact_info"] = CONTACT_INFO_RESPONSES
    rmap["directory"] = DIRECTORY_RESPONSES
    with open(RESPONSES_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(rmap, f, ensure_ascii=False, indent=2)
    print(f"✓ {RESPONSES_MAP_PATH.name}: contact_info, directory replaced")


def rebuild_db() -> None:
    sys.path.insert(0, str(ROOT))
    from intents_db import create_intents_database
    db_path = create_intents_database(
        json_path=str(INTENTS_PATH),
        db_path=str(ROOT / "data" / "cavsu_intents.db"),
        recreate=True,
    )
    print(f"✓ {db_path} rebuilt from JSON")


if __name__ == "__main__":
    patch_intents_json()
    patch_responses_map()
    rebuild_db()
    print("\n=== PATCH COMPLETE ===")
