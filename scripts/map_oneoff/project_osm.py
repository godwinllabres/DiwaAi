"""
project_osm.py — one-shot script to re-anchor the campus map to OSM coordinates.

Reads /tmp/cvsu_osm.json (Overpass API dump) and prints new (x, y) coordinates
for each place_id, projected into the 2000×2000 SVG viewBox while preserving
the real-world aspect ratio.

For buildings that OSM does not name (gates, sports areas, farms, etc.), we
interpolate positions from named neighbors that ARE in OSM.
"""

import json
import math
from typing import Dict, Tuple

with open("/tmp/cvsu_osm.json") as f:
    osm_data = json.load(f)


def _coords(e):
    if "center" in e:
        return e["center"]["lat"], e["center"]["lon"]
    return e.get("lat"), e.get("lon")


osm_by_name: Dict[str, Tuple[float, float]] = {}
for e in osm_data["elements"]:
    name = e.get("tags", {}).get("name")
    if not name:
        continue
    lat, lon = _coords(e)
    if lat is None or lon is None:
        continue
    if name not in osm_by_name:
        osm_by_name[name] = (lat, lon)


# place_id -> OSM feature name
OSM_NAME = {
    "admin":           "Administration Building",
    "coed":            "CED Building",
    "cemds":           "CEMDS Building",
    "old_cemds":       "Old CEMDS Building",
    "cafenr":          "CAFENR Building",
    "cas":             "College of Arts and Sciences Building",
    "ceit":            "College of Engineering and Information Technology",
    "con":             "College of Nursing Building",
    "cvmbs":           "College of Veterinary Medicine and Biomedical Sciences",
    "grad":            "OGS Building",
    "osas":            "Office of Student Affairs Building",
    "plaza":           "CvSU Plaza",
    "child_dev":       "CvSU Child Development Center",
    "library":         "University Library",
    "mall":            "University Mall",
    "chapel":          "University Chapel",
    "ccj":             "Old CCJ Building",
    "hostel":          "Hostel Tropicana",
    "girls_dorm":      "Ladies Dormitory",
    "boys_dorm":       "Men's Dormitory",
    "clinic":          "CvSU Infirmary",
    "sci_hs":          "High School Building",
    "gym":             "CSPEAR Building",
    "icon":            "Cavite State University International Convention Center",
    "agri_eco":        "CvSU Agri-Eco Tourism Park",
    "das":             "Animal Science Building",
    "oval":            "Athletic Field",
    "research_center": "Research Center",
    "quadrangle":      "Quadrangle",
    "intl_house":      "International House 1",
    "rolle_hall":      "Santiago M. Rolle Hall",
    "processing":      "Coffee Processing Center",
}


# Bounding box of the campus (slightly extended beyond OSM-known buildings so
# everything fits with a 100px viewBox margin).
LAT_NORTH = 14.2055
LAT_SOUTH = 14.1925
LON_WEST  = 120.8770
LON_EAST  = 120.8845

LAT_RANGE = LAT_NORTH - LAT_SOUTH        # 0.013
LON_RANGE = LON_EAST  - LON_WEST         # 0.0075

# Latitude meters-per-degree ~ 110.5 km. Longitude meters-per-degree depends
# on latitude: 111.32 km × cos(lat). At lat ~14.20, cos ≈ 0.9694.
COS_LAT = math.cos(math.radians((LAT_NORTH + LAT_SOUTH) / 2))
LAT_M_PER_DEG = 110_574
LON_M_PER_DEG = 111_320 * COS_LAT

# Real-world dimensions of the bbox
HEIGHT_M = LAT_RANGE * LAT_M_PER_DEG     # ~1437 m
WIDTH_M  = LON_RANGE * LON_M_PER_DEG     # ~810 m

# Fit the campus into the 2000×2000 viewBox preserving aspect ratio.
# Leave a 100px margin on all sides ⇒ usable area is 1800×1800.
USABLE = 1800
MARGIN = 100

if HEIGHT_M > WIDTH_M:
    y_span = USABLE
    x_span = USABLE * (WIDTH_M / HEIGHT_M)
else:
    x_span = USABLE
    y_span = USABLE * (HEIGHT_M / WIDTH_M)

x_offset = (2000 - x_span) / 2
y_offset = (2000 - y_span) / 2


def project(lat: float, lon: float) -> Tuple[int, int]:
    """lat/lon -> (x, y) in the 2000×2000 SVG viewBox."""
    norm_y = (LAT_NORTH - lat) / LAT_RANGE   # 0 at north (top), 1 at south
    norm_x = (lon - LON_WEST) / LON_RANGE    # 0 at west, 1 at east
    x = x_offset + norm_x * x_span
    y = y_offset + norm_y * y_span
    return int(round(x)), int(round(y))


print(f"# campus bbox: lat {LAT_SOUTH}..{LAT_NORTH} ({HEIGHT_M:.0f} m), "
      f"lon {LON_WEST}..{LON_EAST} ({WIDTH_M:.0f} m)")
print(f"# viewBox usable: x_span={x_span:.0f} y_span={y_span:.0f} "
      f"offsets=({x_offset:.0f}, {y_offset:.0f})")
print()

# Project all OSM-matched places
matched = {}
for pid, oname in OSM_NAME.items():
    if oname in osm_by_name:
        lat, lon = osm_by_name[oname]
        matched[pid] = (project(lat, lon), (lat, lon))


# For places NOT in OSM, derive positions from neighbors or campus geometry.
# These are educated guesses based on the user's OSM screenshot + the
# canonical CvSU campus map. The Gate 1 reference (south main entrance)
# anchors the lower-perimeter features.
estimated_latlon = {
    # South / perimeter — gates
    "gate_1":     (14.1935, 120.8800),  # main gate, south
    "gate_2":     (14.1985, 120.8775),  # west gate, near CEIT / chapel
    "gate_3":     (14.1937, 120.8815),  # east-south gate, near dorms
    # Sports area — around the oval (14.1979, 120.8814)
    "softball":   (14.1960, 120.8780),  # south-west of oval
    "bleachers":  (14.1982, 120.8819),  # east edge of oval
    "open_court": (14.1975, 120.8821),  # east of oval
    # University-mall / canteen corridor (south)
    # Crop / agri research north-east cluster
    "dcs":        (14.1991, 120.8830),  # near CAFENR
    "demo_farm":  (14.2002, 120.8835),  # north of CAFENR/Hostel
    "ncrdec":     (14.2014, 120.8821),  # Coffee Processing area
    # Tourism / hospitality (CTHM) — northwest of the agri-eco park
    "cthm":       (14.2014, 120.8826),
    # Alumni house — east-central, between mall and dorms
    "bahay_alumni": (14.1968, 120.8830),
    # Northern leisure features
    "lagoon":     (14.2008, 120.8819),  # water feature, near CAS/library
    "saluysoy":   (14.2025, 120.8825),  # bridge/waterway, north
    "bano_resort":(14.2042, 120.8828),  # far northeast
    "star_farm":  (14.2050, 120.8815),  # far north tip
    # Gender & Development Research Center
    "gender_dev": (14.1995, 120.8821),
    # "main" is virtual — no fixed pin
}

for pid, (lat, lon) in estimated_latlon.items():
    matched[pid] = (project(lat, lon), (lat, lon))


# Output the new coordinate map
print("# Projected coordinates (paste into campus_places.py / campusMap.ts)")
print("NEW_COORDS = {")
for pid in sorted(matched.keys()):
    (x, y), (lat, lon) = matched[pid]
    src = "OSM" if pid in OSM_NAME and OSM_NAME[pid] in osm_by_name else "estimated"
    print(f"    {pid!r:20s}: ({x:4d}, {y:4d}),  # {lat:.5f}, {lon:.5f}  [{src}]")
print("}")
