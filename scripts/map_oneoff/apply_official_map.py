"""
apply_official_map.py — replace OSM-projected coordinates with positions read
directly off the official Matayuyon Crop Science Society campus map (the
2048×2048 poster the user provided). All coordinates are in the 2000×2000
SVG viewBox frame (poster is displayed at 2000×2000 ≈ 0.977× the original).

Run once to revert the bad OSM placement and align with the canonical artwork.
"""
import re
from pathlib import Path

# Pixel positions read from the official Matayuyon map (yellow numbered marker
# center). Order follows the legend numbers 1–48.
NEW_COORDS = {
    "plaza":           ( 420, 1810),  # 1
    "gate_1":          ( 437, 1750),  # 2  (red marker)
    "softball":        ( 295, 1495),  # 3
    "intl_house":      ( 550, 1750),  # 4  (International House — Marketing)
    "bleachers":       ( 640, 1745),  # 5
    "open_court":      ( 640, 1620),  # 6
    "oval":            ( 525, 1395),  # 7  (Grandstand / oval center)
    "mall":            ( 740, 1745),  # 8
    "osas":            ( 820, 1495),  # 9
    "gym":             ( 880, 1645),  # 10 (CSPEAR Gymnasium)
    "boys_dorm":       ( 880, 1750),  # 11
    "bahay_alumni":    ( 945, 1685),  # 12
    "girls_dorm":      (1075, 1750),  # 13
    "ncrdec":          (1120, 1495),  # 14
    "demo_farm":       (1120, 1390),  # 15
    "processing":      (1015, 1395),  # 16
    "cafenr":          ( 960, 1255),  # 17
    "dcs":             ( 905, 1245),  # 18
    "cemds":           (1035, 1130),  # 19
    "research_center": (1015, 1045),  # 20
    "cthm":            ( 965,  985),  # 21
    "cas":             ( 815,  985),  # 22
    "old_cemds":       ( 640,  985),  # 23
    "admin":           ( 445, 1085),  # 24
    "ceit":            ( 330,  985),  # 25
    "child_dev":       ( 255,  985),  # 26
    "gate_2":          ( 260, 1055),  # 27
    "chapel":          ( 245, 1150),  # 28
    "ccj":             ( 245, 1230),  # 29
    "clinic":          ( 245, 1320),  # 30
    "sci_hs":          ( 395, 1255),  # 31
    "quadrangle":      ( 440, 1245),  # 32
    "coed":            ( 385, 1170),  # 33 (CED Building)
    "hostel":          (1075,  925),  # 34
    "agri_eco":        (1150,  895),  # 35
    "saluysoy":        (1060,  845),  # 36
    "bano_resort":     (1140,  770),  # 37
    "icon":            ( 700,  780),  # 38
    "con":             ( 815,  935),  # 39
    "cvmbs":           ( 615,  645),  # 40
    "star_farm":       ( 745,  540),  # 41
    "das":             ( 830,  645),  # 42
    "gate_3":          ( 700, 1750),  # 43
    "lagoon":          ( 775,  815),  # 44
    "library":         ( 875, 1135),  # 45
    "gender_dev":      ( 770, 1050),  # 46
    "grad":            ( 590, 1130),  # 47 (Graduate School)
    "rolle_hall":      ( 665,  815),  # 48
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
    path.write_text(text, encoding="utf-8")
    # Also fix the _GATE constant
    text = path.read_text(encoding="utf-8")
    gx, gy = coords["gate_1"]
    text = re.sub(
        r'_GATE\s*=\s*\{\s*"x":\s*-?\d+,\s*"y":\s*-?\d+',
        f'_GATE = {{"x": {gx}, "y": {gy}',
        text,
    )
    path.write_text(text, encoding="utf-8")
    print(f"  Updated {updated}/{len(coords)} entries in {path.name}")


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
    text, gate_n = re.subn(
        r'(MAIN_GATE\s*=\s*\{\s*)x:\s*-?\d+,\s*y:\s*-?\d+',
        rf'\1x: {gx}, y: {gy}',
        text,
    )
    path.write_text(text, encoding="utf-8")
    print(f"  Updated {updated}/{len(coords)} entries in {path.name}; "
          f"MAIN_GATE -> ({gx}, {gy})")


# Campus outline tracing the green polygon in the official map.
CAMPUS_OUTLINE = [
    # South gate row (Indang–Trece Martires Road side)
    (360, 1840), (520, 1860), (700, 1865), (900, 1860), (1075, 1855), (1180, 1830),
    # South-east corner (NCRDEC bulge)
    (1190, 1700), (1180, 1570), (1175, 1430),
    # East boundary along agri zone
    (1180, 1300), (1180, 1180), (1180, 1050), (1185, 920),
    # North-east (Bano Resort area)
    (1175, 800), (1130, 700), (1050, 620),
    # Up to Star Farm apex
    (980, 540), (900, 480), (820, 470), (760, 460),
    # Star Farm tip
    (700, 480), (680, 540),
    # Down west side past CVMBS
    (560, 580), (560, 700), (600, 800), (640, 900),
    # West shoulder past CON/CAS row
    (560, 940), (380, 940), (260, 935),
    # West side down past Gate 2 and chapel
    (220, 1010), (200, 1100), (200, 1250), (210, 1380),
    # Out to Softball Field (west lobe)
    (180, 1450), (200, 1530), (250, 1560), (290, 1530),
    # Back to south side
    (320, 1620), (340, 1750), (360, 1840),
]

# Road network (light-green pathways visible on the official map)
ROADS = [
    # 1. South perimeter (Indang–Trece Martires Road, outside the campus)
    [(150, 1920), (1300, 1920)],

    # 2. South gate row connecting Plaza→Gate 1→Intl House→Bleachers→Mall→Gate 3→Boys Dorm→Girls Dorm
    [NEW_COORDS["plaza"], NEW_COORDS["gate_1"], NEW_COORDS["intl_house"],
     NEW_COORDS["bleachers"], NEW_COORDS["mall"], NEW_COORDS["gate_3"],
     NEW_COORDS["boys_dorm"], NEW_COORDS["girls_dorm"]],

    # 3. Entrance road from Gate 1 north past Oval to Admin
    [NEW_COORDS["gate_1"], NEW_COORDS["plaza"], (470, 1620),
     NEW_COORDS["oval"], (470, 1180), NEW_COORDS["admin"]],

    # 4. East-side ring (Oval east → OSAS → NCRDEC → Demo Farm)
    [NEW_COORDS["oval"], NEW_COORDS["open_court"], NEW_COORDS["osas"],
     NEW_COORDS["gym"], NEW_COORDS["bahay_alumni"]],

    # 5. Quad-row crossing (CCJ → Sci HS → Quad → Grad → OSAS)
    [NEW_COORDS["ccj"], NEW_COORDS["sci_hs"], NEW_COORDS["quadrangle"],
     NEW_COORDS["grad"], NEW_COORDS["gender_dev"], NEW_COORDS["library"]],

    # 6. North college row (Child Dev → CEIT → Old CEMDS → CAS → CTHM)
    [NEW_COORDS["child_dev"], NEW_COORDS["ceit"], NEW_COORDS["old_cemds"],
     NEW_COORDS["cas"], NEW_COORDS["cthm"], NEW_COORDS["research_center"]],

    # 7. East agri ring (CAFENR → DCS → CEMDS → Research → Hostel)
    [NEW_COORDS["cafenr"], NEW_COORDS["dcs"], NEW_COORDS["cemds"],
     NEW_COORDS["research_center"], NEW_COORDS["hostel"], NEW_COORDS["agri_eco"]],

    # 8. North spine (CON → Hostel → Lagoon → Rolle → ICON)
    [NEW_COORDS["con"], NEW_COORDS["hostel"], NEW_COORDS["lagoon"],
     NEW_COORDS["rolle_hall"], NEW_COORDS["icon"]],

    # 9. Northern outliers (Saluysoy → Bano Resort)
    [NEW_COORDS["agri_eco"], NEW_COORDS["saluysoy"], NEW_COORDS["bano_resort"]],

    # 10. Far north (CVMBS → Star Farm → DAS)
    [NEW_COORDS["cvmbs"], NEW_COORDS["star_farm"], NEW_COORDS["das"]],

    # 11. West-side connector (Gate 2 → Chapel → CCJ → Clinic → Softball)
    [NEW_COORDS["gate_2"], NEW_COORDS["chapel"], NEW_COORDS["ccj"],
     NEW_COORDS["clinic"], NEW_COORDS["softball"]],

    # 12. Admin connectors
    [NEW_COORDS["admin"], NEW_COORDS["coed"], NEW_COORDS["ceit"]],
    [NEW_COORDS["admin"], NEW_COORDS["quadrangle"], NEW_COORDS["oval"]],

    # 13. NCRDEC / Processing east cluster
    [NEW_COORDS["osas"], NEW_COORDS["processing"], NEW_COORDS["ncrdec"], NEW_COORDS["demo_farm"]],
    [NEW_COORDS["processing"], NEW_COORDS["cafenr"]],

    # 14. Hostel→Bano connector along east edge
    [NEW_COORDS["hostel"], NEW_COORDS["saluysoy"], NEW_COORDS["bano_resort"]],
]


def regen_geometry(ts_path):
    text = ts_path.read_text(encoding="utf-8")

    # CAMPUS_OUTLINE
    outline_pts = ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in CAMPUS_OUTLINE)
    outline_block = (
        "// Campus outline — traced from the official Matayuyon campus map.\n"
        "export const CAMPUS_OUTLINE: ReadonlyArray<{ x: number; y: number }> = [\n"
        f"  {outline_pts},\n"
        "];\n"
    )
    text = re.sub(
        r"// Campus outline[\s\S]*?export const CAMPUS_OUTLINE[\s\S]*?\];\n",
        outline_block,
        text,
    )

    # ROADS
    road_lines = []
    for road in ROADS:
        pts = ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in road)
        road_lines.append(f"  {{ points: [{pts}] }}")
    roads_block = (
        "// Pathways traced from the official Matayuyon campus map.\n"
        "export const ROADS: readonly Road[] = [\n"
        + ",\n".join(road_lines)
        + ",\n];\n"
    )
    text = re.sub(
        r"// (Road network[\s\S]*?|Pathway[\s\S]*?)export const ROADS[\s\S]*?\];\n",
        roads_block,
        text,
        count=1,
    )

    # Remove the fake CAMPUS_RIVER — the official map doesn't show a river
    # through the campus. Replace its contents with an empty array.
    text = re.sub(
        r"export const CAMPUS_RIVER: ReadonlyArray<\{ x: number; y: number \}> = \[[\s\S]*?\];\n",
        (
            "export const CAMPUS_RIVER: ReadonlyArray<{ x: number; y: number }> = [\n"
            "  // No internal river on the official campus map.\n"
            "];\n"
        ),
        text,
    )

    ts_path.write_text(text, encoding="utf-8")
    print(f"  CAMPUS_OUTLINE: {len(CAMPUS_OUTLINE)} points")
    print(f"  ROADS:          {len(ROADS)} polylines")
    print(f"  CAMPUS_RIVER:   cleared (not on the official map)")


if __name__ == "__main__":
    print(f"Patching {CAMPUS_PY.name}...")
    patch_python(CAMPUS_PY, NEW_COORDS)
    print(f"\nPatching {CAMPUS_TS.name}...")
    patch_typescript(CAMPUS_TS, NEW_COORDS)
    print(f"\nRegenerating geometry in {CAMPUS_TS.name}...")
    regen_geometry(CAMPUS_TS)
