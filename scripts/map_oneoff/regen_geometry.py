"""
regen_geometry.py — regenerate CAMPUS_OUTLINE, ROADS, CAMPUS_RIVER, and
WAYPOINTS in campusMap.ts so they match the new OSM-projected building
coordinates. Run after apply_osm_coords.py.

Strategy:
  • Outline: traces the building cluster with a small buffer (south = gates,
    north = farm/resort, east = NCRDEC/agri, west = chapel/CEIT).
  • Roads: connects key buildings through canonical spines (south gate row,
    central N-S spine, college row, east agri ring).
  • River: simple curve along the east-side OSM stream.
  • Waypoints: drop a node beside every road junction, edges = shared road.
"""
import re
from pathlib import Path

# New OSM-projected coordinates (same as apply_osm_coords.py)
B = {
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


def fmt_points(pts):
    return ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in pts)


# Campus outline: traces the perimeter, buffered ~80 px outside the cluster
CAMPUS_OUTLINE = [
    # South perimeter (Indang–Trece Martires road row)
    (520, 1830), (700, 1840), (899, 1850), (1101, 1840), (1290, 1820), (1430, 1780),
    # South-east corner / dorms bulge
    (1460, 1700), (1460, 1550), (1440, 1430), (1410, 1300),
    # East boundary along agri ring
    (1430, 1180), (1430, 1050), (1430,  920), (1420,  800), (1400,  680),
    # North-east — Demo Farm + agri-eco
    (1430,  600), (1400,  500), (1340,  420), (1300,  350),
    # North — Star Farm apex
    (1200,  280), (1100,  100), (1000,  150), (940,  230),
    # North-west shoulder going down past CVMBS / Rolle / ICON
    (900,  400), (820,  500), (760,  600), (700,  720),
    # West boundary down past CEIT / chapel
    (700,  830), (700,  920), (680, 1010), (620, 1050),
    # Gate 2 area and the western shoulder
    (520, 1080), (490, 1180), (490, 1320),
    # West side down past CCJ / clinic / softball
    (520, 1430), (560, 1550), (600, 1680), (640, 1780),
    (700, 1820), (520, 1830),
]


# Roads — main pathways connecting the buildings. Each polyline traces a
# canonical campus path.
ROADS = [
    # 1. South perimeter (public road outside Gate 1)
    [(450, 1830), (1430, 1830)],

    # 2. South gate row: plaza -> gate_1 -> mall -> gate_3 -> dorms
    [B["plaza"], B["gate_1"], (1000, 1760), B["mall"], B["gate_3"],
     B["boys_dorm"], B["girls_dorm"]],

    # 3. Main entrance spine: gate_1 -> plaza -> oval -> admin
    [B["gate_1"], B["plaza"], (1080, 1280), B["oval"], (1050, 1080), B["admin"]],

    # 4. East ring around oval: oval -> bleachers -> open_court -> osas -> gym -> mall
    [B["oval"], B["bleachers"], B["open_court"], B["osas"], B["gym"], B["mall"]],

    # 5. College row (E-W) at admin/grad/library level
    [B["chapel"], B["coed"], B["admin"], B["quadrangle"], B["grad"],
     B["old_cemds"], B["library"], B["cemds"]],

    # 6. Northern college row: ceit -> child_dev -> cas -> con -> hostel
    [B["ceit"], B["child_dev"], B["cas"], B["con"], B["hostel"], B["intl_house"]],

    # 7. West side connector: gate_2 -> chapel -> ccj -> clinic -> softball
    [B["gate_2"], B["chapel"], B["ccj"], B["clinic"], B["softball"]],

    # 8. East agri ring: cafenr -> dcs -> cemds -> research_center -> hostel
    [B["cafenr"], B["dcs"], B["cemds"], B["research_center"], B["hostel"]],

    # 9. Far north spine: rolle_hall -> icon -> processing -> ncrdec -> cthm
    [B["rolle_hall"], B["icon"], B["processing"], B["ncrdec"], B["cthm"], B["agri_eco"]],

    # 10. Star Farm + CVMBS at the apex
    [B["cvmbs"], B["star_farm"], B["das"], B["bano_resort"]],

    # 11. North-south spine (admin -> oval -> grad -> library -> cas)
    [B["admin"], B["quadrangle"], B["oval"], B["grad"], B["library"], B["cas"]],

    # 12. Sci HS connector (south-west)
    [B["plaza"], B["sci_hs"], B["ccj"]],

    # 13. Demo farm / agri-eco
    [B["agri_eco"], B["demo_farm"], B["hostel"]],

    # 14. Saluysoy / lagoon north-east passage
    [B["lagoon"], B["saluysoy"], B["bano_resort"]],

    # 15. GAD / bahay_alumni east route
    [B["library"], B["gender_dev"], B["bahay_alumni"], B["girls_dorm"]],
]


# East-side river (simple curve along OSM stream)
CAMPUS_RIVER = [
    (1480,   50), (1470,  200), (1455,  360), (1440,  520),
    (1430,  680), (1430,  840), (1430, 1000), (1440, 1160),
    (1450, 1320), (1460, 1480), (1470, 1640), (1480, 1800),
]


# Waypoints — one per major intersection.
WAYPOINTS = {
    "wp_gate1":      {"xy": B["gate_1"],           "neighbors": ["wp_plaza", "wp_south_row"]},
    "wp_plaza":      {"xy": B["plaza"],            "neighbors": ["wp_gate1", "wp_south_row", "wp_central", "wp_west"]},
    "wp_south_row":  {"xy": (1100, 1700),          "neighbors": ["wp_plaza", "wp_mall", "wp_gate3", "wp_gate1"]},
    "wp_mall":       {"xy": B["mall"],             "neighbors": ["wp_south_row", "wp_osas", "wp_boys_dorm"]},
    "wp_gate3":      {"xy": B["gate_3"],           "neighbors": ["wp_south_row", "wp_boys_dorm"]},
    "wp_boys_dorm":  {"xy": B["boys_dorm"],        "neighbors": ["wp_gate3", "wp_mall", "wp_girls_dorm"]},
    "wp_girls_dorm": {"xy": B["girls_dorm"],       "neighbors": ["wp_boys_dorm", "wp_bahay"]},
    "wp_bahay":      {"xy": B["bahay_alumni"],     "neighbors": ["wp_girls_dorm", "wp_gym"]},
    "wp_osas":       {"xy": B["osas"],             "neighbors": ["wp_mall", "wp_gym", "wp_oval", "wp_library"]},
    "wp_gym":        {"xy": B["gym"],              "neighbors": ["wp_osas", "wp_bahay"]},
    "wp_oval":       {"xy": B["oval"],             "neighbors": ["wp_plaza", "wp_osas", "wp_grad", "wp_admin"]},
    "wp_central":    {"xy": (1000, 1200),          "neighbors": ["wp_plaza", "wp_admin", "wp_sci_hs"]},
    "wp_admin":      {"xy": B["admin"],            "neighbors": ["wp_central", "wp_oval", "wp_grad", "wp_chapel", "wp_coed"]},
    "wp_coed":       {"xy": B["coed"],             "neighbors": ["wp_admin", "wp_ceit"]},
    "wp_chapel":     {"xy": B["chapel"],           "neighbors": ["wp_admin", "wp_ccj", "wp_west"]},
    "wp_west":       {"xy": (700, 1300),           "neighbors": ["wp_plaza", "wp_chapel", "wp_softball"]},
    "wp_ccj":        {"xy": B["ccj"],              "neighbors": ["wp_chapel", "wp_clinic"]},
    "wp_clinic":     {"xy": B["clinic"],           "neighbors": ["wp_ccj", "wp_softball", "wp_sci_hs"]},
    "wp_softball":   {"xy": B["softball"],         "neighbors": ["wp_clinic", "wp_west"]},
    "wp_sci_hs":     {"xy": B["sci_hs"],           "neighbors": ["wp_central", "wp_clinic"]},
    "wp_grad":       {"xy": B["grad"],             "neighbors": ["wp_oval", "wp_library", "wp_admin"]},
    "wp_library":    {"xy": B["library"],          "neighbors": ["wp_grad", "wp_osas", "wp_cas", "wp_cemds"]},
    "wp_cas":        {"xy": B["cas"],              "neighbors": ["wp_library", "wp_con", "wp_ceit"]},
    "wp_ceit":       {"xy": B["ceit"],             "neighbors": ["wp_coed", "wp_cas", "wp_gate2"]},
    "wp_gate2":      {"xy": B["gate_2"],           "neighbors": ["wp_ceit", "wp_chapel"]},
    "wp_con":        {"xy": B["con"],              "neighbors": ["wp_cas", "wp_hostel", "wp_processing"]},
    "wp_cemds":      {"xy": B["cemds"],            "neighbors": ["wp_library", "wp_cafenr", "wp_research"]},
    "wp_cafenr":     {"xy": B["cafenr"],           "neighbors": ["wp_cemds", "wp_research", "wp_hostel"]},
    "wp_research":   {"xy": B["research_center"],  "neighbors": ["wp_cemds", "wp_cafenr", "wp_hostel"]},
    "wp_hostel":     {"xy": B["hostel"],           "neighbors": ["wp_con", "wp_cafenr", "wp_research", "wp_north_ring"]},
    "wp_processing": {"xy": B["processing"],       "neighbors": ["wp_con", "wp_icon", "wp_north_ring"]},
    "wp_icon":       {"xy": B["icon"],             "neighbors": ["wp_processing", "wp_rolle", "wp_north_ring"]},
    "wp_rolle":      {"xy": B["rolle_hall"],       "neighbors": ["wp_icon", "wp_cvmbs"]},
    "wp_cvmbs":      {"xy": B["cvmbs"],            "neighbors": ["wp_rolle", "wp_star_farm", "wp_das"]},
    "wp_star_farm":  {"xy": B["star_farm"],        "neighbors": ["wp_cvmbs"]},
    "wp_das":        {"xy": B["das"],              "neighbors": ["wp_cvmbs", "wp_bano"]},
    "wp_bano":       {"xy": B["bano_resort"],      "neighbors": ["wp_das", "wp_north_ring"]},
    "wp_north_ring": {"xy": (1300,  600),          "neighbors": ["wp_processing", "wp_icon", "wp_hostel", "wp_bano"]},
}


def emit_outline():
    pts = ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in CAMPUS_OUTLINE)
    return (
        "// Campus outline — traces the OSM-derived perimeter of the campus.\n"
        "export const CAMPUS_OUTLINE: ReadonlyArray<{ x: number; y: number }> = [\n"
        f"  {pts},\n"
        "];\n"
    )


def emit_river():
    pts = ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in CAMPUS_RIVER)
    return (
        "// East-side river (OSM stream along the campus eastern boundary).\n"
        "export const CAMPUS_RIVER: ReadonlyArray<{ x: number; y: number }> = [\n"
        f"  {pts},\n"
        "];\n"
    )


def emit_roads():
    body = []
    for road in ROADS:
        pts = ", ".join(f"{{ x: {x}, y: {y} }}" for x, y in road)
        body.append(f"  {{ points: [{pts}] }}")
    return (
        "// Pathway polylines, OSM-anchored to the new building positions.\n"
        "export const ROADS: readonly Road[] = [\n"
        + ",\n".join(body)
        + ",\n];\n"
    )


def emit_waypoints():
    lines = []
    for name, wp in WAYPOINTS.items():
        x, y = wp["xy"]
        neigh = ", ".join(f'"{n}"' for n in wp["neighbors"])
        lines.append(f'  {name}: {{ id: "{name}", x: {x}, y: {y}, neighbors: [{neigh}] }}')
    return (
        "// Routing waypoints, calibrated to OSM-projected positions.\n"
        "export const WAYPOINTS: Record<string, Waypoint> = {\n"
        + ",\n".join(lines)
        + ",\n};\n"
    )


# Now patch the file
TS_PATH = Path(__file__).parent.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"
text = TS_PATH.read_text(encoding="utf-8")

# Replace CAMPUS_OUTLINE block
text = re.sub(
    r"// Campus outline[\s\S]*?export const CAMPUS_OUTLINE[\s\S]*?\];\n",
    emit_outline(),
    text,
)

# Replace CAMPUS_RIVER block
text = re.sub(
    r"// .*?river[\s\S]*?export const CAMPUS_RIVER[\s\S]*?\];\n",
    emit_river(),
    text,
)

# Replace ROADS block
text = re.sub(
    r"// .*?Road network[\s\S]*?export const ROADS[\s\S]*?\];\n",
    emit_roads(),
    text,
    count=1,
)

# Replace WAYPOINTS block
text = re.sub(
    r"// Waypoints sit[\s\S]*?export const WAYPOINTS[\s\S]*?\};\n",
    emit_waypoints(),
    text,
    count=1,
)

TS_PATH.write_text(text, encoding="utf-8")
print(f"Patched {TS_PATH}")
print(f"  CAMPUS_OUTLINE: {len(CAMPUS_OUTLINE)} points")
print(f"  CAMPUS_RIVER:   {len(CAMPUS_RIVER)} points")
print(f"  ROADS:          {len(ROADS)} polylines")
print(f"  WAYPOINTS:      {len(WAYPOINTS)} nodes")
