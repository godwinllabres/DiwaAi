"""
apply_poster_coords_2k.py — set every marker (x, y) to its position on the
official Matayuyon poster (reference frame: 2000×2000, matching the SVG
viewBox 1:1).

This assumes the user has saved the OFFICIAL POSTER (with legend on the
right) as `SeviWeb/public/cvsu-campus-map.png` at the same 2000×2000 size
so the image fills the viewBox without letterboxing.

If the saved file is a different size, the markers will be offset; use
remap_to_new_image.py or nudge_markers.py to compensate.
"""
import re
from pathlib import Path

# Marker centers as read from the official 2000×2000 Matayuyon poster.
POSTER_COORDS = {
    "plaza":           (442, 1670),  # 1
    "gate_1":          (446, 1610),  # 2  red marker
    "softball":        (290, 1400),  # 3
    "intl_house":      (560, 1612),  # 4
    "bleachers":       (665, 1610),  # 5
    "open_court":      (665, 1490),  # 6
    "oval":            (515, 1265),  # 7
    "mall":            (760, 1610),  # 8
    "osas":            (840, 1370),  # 9
    "gym":             (890, 1510),  # 10
    "boys_dorm":       (890, 1610),  # 11
    "bahay_alumni":    (985, 1545),  # 12
    "girls_dorm":     (1080, 1610),  # 13
    "ncrdec":         (1125, 1370),  # 14
    "demo_farm":      (1135, 1275),  # 15
    "processing":     (1015, 1275),  # 16
    "cafenr":          (940, 1155),  # 17
    "dcs":             (890, 1145),  # 18
    "cemds":          (1020, 1040),  # 19
    "research_center":(1010,  950),  # 20
    "cthm":            (965,  910),  # 21
    "cas":             (810,  910),  # 22
    "old_cemds":       (635,  910),  # 23
    "admin":           (440, 1015),  # 24
    "ceit":            (335,  910),  # 25
    "child_dev":       (260,  910),  # 26
    "gate_2":          (260,  985),  # 27
    "chapel":          (250, 1095),  # 28
    "ccj":             (250, 1170),  # 29
    "clinic":          (250, 1265),  # 30
    "sci_hs":          (385, 1145),  # 31
    "quadrangle":      (430, 1140),  # 32
    "coed":            (385, 1060),  # 33  CED
    "hostel":         (1075,  870),  # 34
    "agri_eco":       (1140,  845),  # 35
    "saluysoy":       (1060,  780),  # 36
    "bano_resort":    (1140,  715),  # 37
    "icon":            (700,  720),  # 38
    "con":             (810,  855),  # 39
    "cvmbs":           (615,  595),  # 40
    "star_farm":       (745,  515),  # 41
    "das":             (835,  600),  # 42
    "gate_3":          (705, 1610),  # 43
    "lagoon":          (775,  760),  # 44
    "library":         (875, 1040),  # 45
    "gender_dev":      (775,  975),  # 46
    "grad":            (595, 1040),  # 47
    "rolle_hall":      (660,  760),  # 48
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
    print(f"Patching with official Matayuyon poster coordinates (2000×2000):")
    print()
    patch_python(CAMPUS_PY, POSTER_COORDS)
    patch_typescript(CAMPUS_TS, POSTER_COORDS)
    print()
    print("Done. If the image at /public/cvsu-campus-map.png is also 2000×2000,")
    print("the markers will land exactly on the printed yellow tags.")
