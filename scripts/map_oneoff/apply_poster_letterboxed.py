"""
apply_poster_letterboxed.py — apply official Matayuyon poster marker
positions to the SVG viewBox, accounting for the fact that the saved
poster file is 667×1024 (portrait) and is letterboxed inside the 2000×2000
viewBox via preserveAspectRatio="xMidYMid meet".

Letterbox math (image fits height: 2000/1024 = 1.953):
    image displayed width  = 667 × 1.953 = 1303 px
    image x range          = (2000 - 1303)/2 = 348 .. 1651
    image y range          = 0 .. 2000

So a marker at (poster_x, poster_y) in a 2000×2000 poster reference frame
that was non-uniformly scaled to 667×1024 ends up at:

    viewBox_x = 348 + poster_x × (1303 / 2000) = 348 + poster_x × 0.6515
    viewBox_y = poster_y × (2000 / 2000)       = poster_y  (unchanged)
"""
import re
from pathlib import Path

# 2000×2000 poster reference positions (same as apply_poster_coords_2k.py).
POSTER_REF = {
    "plaza":           (442, 1670),
    "gate_1":          (446, 1610),
    "softball":        (290, 1400),
    "intl_house":      (560, 1612),
    "bleachers":       (665, 1610),
    "open_court":      (665, 1490),
    "oval":            (515, 1265),
    "mall":            (760, 1610),
    "osas":            (840, 1370),
    "gym":             (890, 1510),
    "boys_dorm":       (890, 1610),
    "bahay_alumni":    (985, 1545),
    "girls_dorm":     (1080, 1610),
    "ncrdec":         (1125, 1370),
    "demo_farm":      (1135, 1275),
    "processing":     (1015, 1275),
    "cafenr":          (940, 1155),
    "dcs":             (890, 1145),
    "cemds":          (1020, 1040),
    "research_center":(1010,  950),
    "cthm":            (965,  910),
    "cas":             (810,  910),
    "old_cemds":       (635,  910),
    "admin":           (440, 1015),
    "ceit":            (335,  910),
    "child_dev":       (260,  910),
    "gate_2":          (260,  985),
    "chapel":          (250, 1095),
    "ccj":             (250, 1170),
    "clinic":          (250, 1265),
    "sci_hs":          (385, 1145),
    "quadrangle":      (430, 1140),
    "coed":            (385, 1060),
    "hostel":         (1075,  870),
    "agri_eco":       (1140,  845),
    "saluysoy":       (1060,  780),
    "bano_resort":    (1140,  715),
    "icon":            (700,  720),
    "con":             (810,  855),
    "cvmbs":           (615,  595),
    "star_farm":       (745,  515),
    "das":             (835,  600),
    "gate_3":          (705, 1610),
    "lagoon":          (775,  760),
    "library":         (875, 1040),
    "gender_dev":      (775,  975),
    "grad":            (595, 1040),
    "rolle_hall":      (660,  760),
}

# Letterbox transform
X_OFFSET = 348
X_SCALE = 1303 / 2000   # 0.6515

NEW_COORDS = {
    pid: (int(round(X_OFFSET + px * X_SCALE)), py)
    for pid, (px, py) in POSTER_REF.items()
}


SEVI_AI = Path(__file__).parent
CAMPUS_PY = SEVI_AI / "api" / "campus_places.py"
CAMPUS_TS = SEVI_AI.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"


def patch_python(path: Path, coords):
    text = path.read_text(encoding="utf-8")
    updated = 0
    for pid, (x, y) in coords.items():
        pattern = re.compile(
            rf'("{re.escape(pid)}":\s*\{{[^}}]*?)"x":\s*-?\d+,\s*"y":\s*-?\d+',
            flags=re.DOTALL,
        )
        new_text, n = pattern.subn(rf'\1"x": {x}, "y": {y}', text, count=1)
        if n:
            text = new_text
            updated += 1
    gx, gy = coords["gate_1"]
    text = re.sub(
        r'_GATE\s*=\s*\{\s*"x":\s*-?\d+,\s*"y":\s*-?\d+',
        f'_GATE = {{"x": {gx}, "y": {gy}',
        text,
    )
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: updated {updated}/{len(coords)} entries")


def patch_typescript(path: Path, coords):
    text = path.read_text(encoding="utf-8")
    updated = 0
    for pid, (x, y) in coords.items():
        pattern = re.compile(
            rf'(id:\s*"{re.escape(pid)}"[^}}]*?)x:\s*-?\d+,\s*y:\s*-?\d+',
            flags=re.DOTALL,
        )
        new_text, n = pattern.subn(rf'\1x: {x}, y: {y}', text, count=1)
        if n:
            text = new_text
            updated += 1
    gx, gy = coords["gate_1"]
    text, _ = re.subn(
        r'(MAIN_GATE\s*=\s*\{\s*)x:\s*-?\d+,\s*y:\s*-?\d+',
        rf'\1x: {gx}, y: {gy}',
        text,
    )
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: updated {updated}/{len(coords)} entries")


if __name__ == "__main__":
    print(f"Letterbox transform: viewBox_x = 348 + poster_x × {X_SCALE:.4f}")
    print(f"                    viewBox_y = poster_y (unchanged)")
    print()
    print("Sample: gate_1 (446, 1610) -> ({}, {})".format(*NEW_COORDS["gate_1"]))
    print()
    patch_python(CAMPUS_PY, NEW_COORDS)
    patch_typescript(CAMPUS_TS, NEW_COORDS)
