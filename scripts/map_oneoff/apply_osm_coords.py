"""
apply_osm_coords.py — apply the OSM-projected coordinates to both source files.

Updates:
  • api/campus_places.py        (Python dict, "x": NN, "y": NN syntax)
  • ../SeviWeb/app/lib/campusMap.ts (TypeScript objects, x: NN, y: NN syntax)
  • Also realigns MAIN_GATE in campusMap.ts to the projected gate_1 coords.

Run once after project_osm.py to push the new coordinates.
"""
import re
from pathlib import Path

# Output of project_osm.py — projected (x, y) for each place_id
NEW_COORDS = {
    "admin":           (1019, 1011),
    "agri_eco":        (1318,  756),
    "bahay_alumni":    (1304, 1305),
    "bano_resort":     (1277,  280),
    "bleachers":       (1155, 1111),
    "boys_dorm":       (1271, 1338),
    "cafenr":          (1245,  985),
    "cas":             (1163,  868),
    "ccj":             ( 893, 1267),
    "ceit":            ( 986,  930),
    "cemds":           (1315,  988),
    "chapel":          ( 832, 1061),
    "child_dev":       ( 901,  950),
    "clinic":          ( 893, 1201),
    "coed":            ( 944, 1015),
    "con":             (1145,  788),
    "cthm":            (1250,  668),
    "cvmbs":           (1096,  455),
    "das":             (1171,  462),
    "dcs":             (1304,  986),
    "demo_farm":       (1372,  834),
    "gate_1":          ( 899, 1762),
    "gate_2":          ( 561, 1069),
    "gate_3":          (1101, 1734),
    "gender_dev":      (1182,  931),
    "girls_dorm":      (1359, 1335),
    "grad":            (1117,  965),
    "gym":             (1244, 1242),
    "hostel":          (1256,  767),
    "icon":            (1145,  620),
    "intl_house":      (1284,  851),
    "lagoon":          (1155,  751),
    "library":         (1206,  952),
    "mall":            (1211, 1406),
    "ncrdec":          (1182,  668),
    "old_cemds":       (1072,  867),
    "open_court":      (1182, 1208),
    "osas":            (1206, 1174),
    "oval":            (1095, 1149),
    "plaza":           (1045, 1410),
    "processing":      (1073,  720),
    "quadrangle":      ( 948, 1121),
    "research_center": (1294,  922),
    "rolle_hall":      (1116,  688),
    "saluysoy":        (1236,  515),
    "sci_hs":          ( 975, 1181),
    "softball":        ( 628, 1415),
    "star_farm":       (1101,  169),
}


SEVI_AI = Path(__file__).parent
CAMPUS_PY  = SEVI_AI / "api" / "campus_places.py"
CAMPUS_TS  = SEVI_AI.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"


def patch_python(path: Path, coords):
    text = path.read_text(encoding="utf-8")
    updated = 0
    for pid, (x, y) in coords.items():
        # Match a line like:
        #   "plaza": {... "x": 380, "y": 1830},
        pattern = re.compile(
            rf'("{re.escape(pid)}":\s*\{{[^}}]*?)"x":\s*-?\d+,\s*"y":\s*-?\d+',
            flags=re.DOTALL,
        )
        new_text, n = pattern.subn(rf'\1"x": {x}, "y": {y}', text, count=1)
        if n:
            text = new_text
            updated += 1
        else:
            print(f"  [WARN] no match for {pid!r} in {path.name}")
    path.write_text(text, encoding="utf-8")
    print(f"  Updated {updated}/{len(coords)} entries in {path.name}")


def patch_typescript(path: Path, coords):
    text = path.read_text(encoding="utf-8")
    updated = 0
    for pid, (x, y) in coords.items():
        # Match a TS entry like:
        #   id: "plaza", num: 1, ..., x: 397, y: 1800,
        pattern = re.compile(
            rf'(id:\s*"{re.escape(pid)}"[^}}]*?)x:\s*-?\d+,\s*y:\s*-?\d+',
            flags=re.DOTALL,
        )
        new_text, n = pattern.subn(rf'\1x: {x}, y: {y}', text, count=1)
        if n:
            text = new_text
            updated += 1
        else:
            print(f"  [WARN] no match for {pid!r} in {path.name}")
    # Also update MAIN_GATE to match gate_1's new position
    gate_x, gate_y = coords["gate_1"]
    text, gate_n = re.subn(
        r'(MAIN_GATE\s*=\s*\{\s*)x:\s*-?\d+,\s*y:\s*-?\d+',
        rf'\1x: {gate_x}, y: {gate_y}',
        text,
    )
    if gate_n:
        print(f"  MAIN_GATE updated -> ({gate_x}, {gate_y})")
    path.write_text(text, encoding="utf-8")
    print(f"  Updated {updated}/{len(coords)} entries in {path.name}")


if __name__ == "__main__":
    print(f"Patching {CAMPUS_PY.name}...")
    patch_python(CAMPUS_PY, NEW_COORDS)
    print()
    print(f"Patching {CAMPUS_TS}...")
    patch_typescript(CAMPUS_TS, NEW_COORDS)
