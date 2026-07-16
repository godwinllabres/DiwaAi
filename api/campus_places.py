"""Canonical campus place data for the CvSU Don Severino delas Alas Campus (Indang).

Single source of truth for the chatbot's map feature. Both `app.py` (root) and
`api/app.py` import from this module so the 48 locations from the official
campus map (Matayuyon Crop Science Society, 2026) stay in lockstep across
entry points.

Pixel coordinates (x, y) are in the campus image's 2000 x 2000 reference frame
and match `SeviWeb/app/lib/campusMap.ts`. Keep the two files in sync when
moving markers.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Models exposed by the API
# ---------------------------------------------------------------------------

class MapData(BaseModel):
    place_id: str  # key into the campus map (matches frontend Building.id)
    label: str     # human-readable building/area name


class Directory(BaseModel):
    """Structured contact / location card returned alongside the chat text."""
    office: str                       # canonical office name
    location: Optional[str] = None    # human-readable directions
    place_id: Optional[str] = None    # link back into the campus map
    email: Optional[str] = None
    phone: Optional[str] = None
    hours: Optional[str] = None


class PlaceMeta(BaseModel):
    place_id: str
    label: str
    num: int                          # legend number on the official map (1..48)
    short: str
    full: str
    walk_minutes_from_gate: int
    direction_from_gate: str
    # Pin center on the 2000 x 2000 campus image. None for the full-campus view.
    x: Optional[int] = None
    y: Optional[int] = None


# ---------------------------------------------------------------------------
# Layout reference (must match SeviWeb/app/lib/campusMap.ts)
# ---------------------------------------------------------------------------

_GATE = {"x": 639, "y": 1610}  # Gate 1 — Main Gate (official Matayuyon map)
_CAMPUS_VIEWBOX = {"width": 2000, "height": 2000}


# ---------------------------------------------------------------------------
# Place metadata — 48 numbered locations + a virtual "main" full-campus view
# ---------------------------------------------------------------------------
# Each entry: place_id -> {num, short, full, walk_minutes_from_gate,
# direction_from_gate, x, y}
#
# `num` is the legend number on the official campus map. `direction_from_gate`
# is a concise one-line direction; richer step-by-step text lives on the
# frontend (`campusMap.ts`).

_PLACE_METADATA: Dict[str, Dict[str, Any]] = {
    "main": {"num": 0, "short": "Campus", "full": "CvSU Don Severino delas Alas Campus – Indang",
             "walk_minutes_from_gate": 0,
             "direction_from_gate": "Enter via Gate 1 (Main Gate). The campus has 48 numbered locations across 10+ colleges, dormitories, an oval, a library, and farms.",
             "x": None, "y": None},

    "plaza":          {"num": 1,  "short": "Plaza",         "full": "University Plaza (Laya't Diwa)",
                       "walk_minutes_from_gate": 1, "direction_from_gate": "Just inside Gate 1 on the south entrance.",
                       "x": 806, "y": 1715},
    "gate_1":         {"num": 2,  "short": "Gate 1",        "full": "Gate 1 (Main Gate)",
                       "walk_minutes_from_gate": 0, "direction_from_gate": "Gate 1 is the southern main entrance — you're here.",
                       "x": 868, "y": 1670},
    "softball":       {"num": 3,  "short": "Softball",      "full": "Softball Field",
                       "walk_minutes_from_gate": 2, "direction_from_gate": "From Gate 1, head west along the southern boundary.",
                       "x": 746, "y": 1586},
    "intl_house":     {"num": 4,  "short": "Intl House",    "full": "International House — Marketing",
                       "walk_minutes_from_gate": 1, "direction_from_gate": "From Gate 1, walk east a short distance.",
                       "x": 943, "y": 1705},
    "bleachers":      {"num": 5,  "short": "Bleachers",     "full": "Bleachers",
                       "walk_minutes_from_gate": 2, "direction_from_gate": "Walk north from Gate 1 toward the University Oval.",
                       "x": 985, "y": 1628},
    "open_court":     {"num": 6,  "short": "Open Court",    "full": "Open Court",
                       "walk_minutes_from_gate": 3, "direction_from_gate": "Walk north from Gate 1; the open court is east of the oval.",
                       "x": 953, "y": 1581},
    "oval":           {"num": 7,  "short": "Oval",          "full": "University Oval — Grandstand",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk straight north from Gate 1; the oval is the central track.",
                       "x": 900, "y": 1382},
    "mall":           {"num": 8,  "short": "Mall",          "full": "University Mall",
                       "walk_minutes_from_gate": 2, "direction_from_gate": "From Gate 1, walk east along the southern row.",
                       "x": 1124, "y": 1733},
    "osas":           {"num": 9,  "short": "OSAS",          "full": "Office of Student Affairs and Services (OSAS) — University Registrar",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "From Gate 1, walk north past the oval; OSAS / Registrar is on the east side.",
                       "x": 1122, "y": 1439},
    "gym":            {"num": 10, "short": "Gymnasium",     "full": "College of Sports and Physical Education and Recreation — University Gymnasium",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk north past the bleachers and open court; the gymnasium is on the east side.",
                       "x": 1154, "y": 1563},
    "boys_dorm":      {"num": 11, "short": "Boys Dorm",     "full": "Boy's Dormitory",
                       "walk_minutes_from_gate": 3, "direction_from_gate": "From Gate 1, walk east past the University Mall.",
                       "x": 1246, "y": 1708},
    "bahay_alumni":   {"num": 12, "short": "Bahay Alumni",  "full": "Bahay Alumni",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk east past the mall and gymnasium; near the east dorm cluster.",
                       "x": 1261, "y": 1601},
    "girls_dorm":     {"num": 13, "short": "Girls Dorm",    "full": "Girl's Dormitory",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk east along the southern row, past Bahay Alumni.",
                       "x": 1368, "y": 1695},
    "ncrdec":         {"num": 14, "short": "NCRDEC",        "full": "National Coffee Research, Development, and Extension Center (NCRDEC)",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk east to the far edge of campus; NCRDEC is in the eastern strip.",
                       "x": 1530, "y": 1568},
    "demo_farm":      {"num": 15, "short": "Demo Farm",     "full": "Technology Demonstration Farm",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk northeast past the gymnasium toward the agri zone.",
                       "x": 1495, "y": 1399},
    "processing":     {"num": 16, "short": "Processing",    "full": "Fruits and Vegetable Processing Center",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Northeast section, next to the demo farm.",
                       "x": 1368, "y": 1429},
    "cafenr":         {"num": 17, "short": "CAFENR",        "full": "College of Agriculture, Food, Environment, and Natural Resources (CAFENR)",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Walk north past Admin and the oval; CAFENR is in the central-east area.",
                       "x": 1306, "y": 1300},
    "dcs":            {"num": 18, "short": "DCS",           "full": "Department of Crop Science (DCS)",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Beside CAFENR in the central-east cluster.",
                       "x": 1251, "y": 1327},
    "cemds":          {"num": 19, "short": "CEMDS",         "full": "College of Economics, Management, and Development Studies (CEMDS)",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk north past Admin and CAFENR; CEMDS is north-central, east side.",
                       "x": 1468, "y": 1292},
    "research_center":{"num": 20, "short": "Research",      "full": "Research Center",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Just north of CEMDS in the north-central area.",
                       "x": 1443, "y": 1185},
    "cthm":           {"num": 21, "short": "CTHM",          "full": "College of Tourism and Hospitality Management (CTHM)",
                       "walk_minutes_from_gate": 7, "direction_from_gate": "Walk north along the eastern road past CEMDS.",
                       "x": 1420, "y": 1091},
    "cas":            {"num": 22, "short": "CAS",           "full": "College of Arts and Sciences (CAS)",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk north from Gate 1 past Admin and the oval; CAS is in the upper-central cluster.",
                       "x": 1189, "y": 1088},
    "old_cemds":      {"num": 23, "short": "Old CEMDS",     "full": "Old CEMDS Building",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Walk north from Gate 1 past Admin; the Old CEMDS is west of CAS.",
                       "x": 960, "y": 1088},
    "admin":          {"num": 24, "short": "Admin",         "full": "Administration Building — University Cashier",
                       "walk_minutes_from_gate": 3, "direction_from_gate": "Walk straight north from Gate 1 — the Administration Building is the first major structure.",
                       "x": 851, "y": 1223},
    "ceit":           {"num": 25, "short": "CEIT",          "full": "College of Engineering and Information Technology (CEIT)",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Walk north from Gate 1 then bear left past Admin; CEIT is near Gate 2.",
                       "x": 754, "y": 1173},
    "child_dev":      {"num": 26, "short": "Child Dev",     "full": "Child Development Center",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Enter via Gate 2 on the west, or walk north-west from Gate 1.",
                       "x": 622, "y": 1121},
    "gate_2":         {"num": 27, "short": "Gate 2",        "full": "Gate 2",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Gate 2 is the western entrance, on the side of CEIT and the Chapel.",
                       "x": 582, "y": 1213},
    "chapel":         {"num": 28, "short": "Chapel",        "full": "University Chapel",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Enter via Gate 2 or walk west from the central road past Admin.",
                       "x": 530, "y": 1354},
    "ccj":            {"num": 29, "short": "CCJ",           "full": "College of Criminal Justice (CCJ)",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "On the western edge, south of the Chapel.",
                       "x": 557, "y": 1439},
    "clinic":         {"num": 30, "short": "Clinic",        "full": "University Clinic (Infirmary)",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk west from Gate 1 along the southern road, then turn north.",
                       "x": 607, "y": 1561},
    "sci_hs":         {"num": 31, "short": "Sci HS",        "full": "Science Highschool",
                       "walk_minutes_from_gate": 4, "direction_from_gate": "Walk north from Gate 1, then west of the Administration Building.",
                       "x": 726, "y": 1466},
    "quadrangle":     {"num": 32, "short": "Quad",          "full": "Quadrangle",
                       "walk_minutes_from_gate": 3, "direction_from_gate": "The open area just south of the Administration Building.",
                       "x": 699, "y": 1374},
    "coed":           {"num": 33, "short": "CEd",           "full": "College of Education (CED)",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Walk north past Admin; CED is in the west-central cluster near CEIT.",
                       "x": 677, "y": 1277},
    "hostel":         {"num": 34, "short": "Hostel",        "full": "Hostel Tropicana",
                       "walk_minutes_from_gate": 8, "direction_from_gate": "Walk north past CEMDS and the Research Center; in the northeast cluster.",
                       "x": 1430, "y": 1001},
    "agri_eco":       {"num": 35, "short": "Agri-Eco",      "full": "CvSU Agri-Eco Park",
                       "walk_minutes_from_gate": 9, "direction_from_gate": "Walk north along the eastern road past CTHM and the hostel.",
                       "x": 1552, "y": 949},
    "saluysoy":       {"num": 36, "short": "Saluysoy",      "full": "Saluysoy",
                       "walk_minutes_from_gate": 9, "direction_from_gate": "Walk north past CTHM into the upper-east section.",
                       "x": 1423, "y": 884},
    "bano_resort":    {"num": 37, "short": "Baño Resort",   "full": "Baño De Señora Resort",
                       "walk_minutes_from_gate": 10, "direction_from_gate": "Walk north all the way past CTHM and the Agri-Eco area; the resort is at the northeast edge.",
                       "x": 1493, "y": 797},
    "icon":           {"num": 38, "short": "ICON",          "full": "International Convention Center (ICON)",
                       "walk_minutes_from_gate": 9, "direction_from_gate": "Walk far north past CAS and the library cluster.",
                       "x": 1159, "y": 840},
    "con":            {"num": 39, "short": "CON",           "full": "College of Nursing (CON)",
                       "walk_minutes_from_gate": 8, "direction_from_gate": "Walk north past Admin and the library; CON is in the north-central area near CAS.",
                       "x": 1211, "y": 1024},
    "cvmbs":          {"num": 40, "short": "CVMBS",         "full": "College of Veterinary Medicine and Biological Sciences (CVMBS)",
                       "walk_minutes_from_gate": 11, "direction_from_gate": "Walk far north past CAS, the library, and ICON; CVMBS is in the upper-north section.",
                       "x": 1124, "y": 703},
    "star_farm":      {"num": 41, "short": "Star Farm",     "full": "CvSU Star Farm",
                       "walk_minutes_from_gate": 13, "direction_from_gate": "All the way north past CVMBS — the Star Farm is at the far northern tip.",
                       "x": 1221, "y": 566},
    "das":            {"num": 42, "short": "DAS",           "full": "Department of Animal Science (DAS)",
                       "walk_minutes_from_gate": 11, "direction_from_gate": "Walk far north past CTHM and the Agri-Eco area; DAS is east of CVMBS.",
                       "x": 1311, "y": 700},
    "gate_3":         {"num": 43, "short": "Gate 3",        "full": "Gate 3",
                       "walk_minutes_from_gate": 2, "direction_from_gate": "Gate 3 is the third entrance, on the south side near the dorm row.",
                       "x": 1104, "y": 1650},
    "lagoon":         {"num": 44, "short": "Lagoon",        "full": "Lagoon",
                       "walk_minutes_from_gate": 9, "direction_from_gate": "Walk north past Admin, the library, and CAS — the lagoon is the water feature in the north-central area.",
                       "x": 1174, "y": 939},
    "library":        {"num": 45, "short": "Library",       "full": "Ladislao N. Diwa Memorial Library",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk north from Gate 1 past the Administration Building and CAFENR; the library is central.",
                       "x": 1256, "y": 1225},
    "gender_dev":     {"num": 46, "short": "GAD",           "full": "Gender and Development Research Center",
                       "walk_minutes_from_gate": 6, "direction_from_gate": "Walk north past Admin and the library; the GAD Research Center is in the central area near CAS.",
                       "x": 1174, "y": 1190},
    "grad":           {"num": 47, "short": "Grad School",   "full": "Graduate School",
                       "walk_minutes_from_gate": 5, "direction_from_gate": "Walk north from Gate 1 past Admin; Graduate School is in the central area near the library.",
                       "x": 1055, "y": 1233},
    "rolle_hall":     {"num": 48, "short": "Rolle Hall",    "full": "S.M Rolle Hall",
                       "walk_minutes_from_gate": 8, "direction_from_gate": "Walk north past CAS into the upper-central area.",
                       "x": 1055, "y": 912},
}

import json as _json
from pathlib import Path as _Path

# Legacy JSON paths — kept only for backward-compatible fallback reads.
_OVERRIDE_PATH = _Path(__file__).resolve().parent.parent / "data" / "coords_override.json"
_WAYPOINTS_PATH = _Path(__file__).resolve().parent.parent / "data" / "waypoints_override.json"
_CUSTOM_MARKERS_PATH = _Path(__file__).resolve().parent.parent / "data" / "custom_markers.json"


# ---------------------------------------------------------------------------
# DB connection helper (lazy import to avoid circular deps at module level)
# ---------------------------------------------------------------------------

def _get_conn():
    from .db import get_conn
    return get_conn()


# ---------------------------------------------------------------------------
# Campus places — DB-primary, hardcoded _PLACE_METADATA as fallback
# ---------------------------------------------------------------------------

def _load_places_from_db() -> Dict[str, Dict[str, Any]]:
    """Load campus places from the DB. Returns {} if unavailable."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT place_id, label, num, short, full, walk_minutes, direction, x, y"
                " FROM campus_places"
            ).fetchall()
    except Exception:
        return {}
    if not rows:
        return {}
    return {
        row["place_id"]: {
            "num": row["num"],
            "short": row["short"],
            "full": row["full"],
            "walk_minutes_from_gate": row["walk_minutes"],
            "direction_from_gate": row["direction"],
            "x": row["x"],
            "y": row["y"],
        }
        for row in rows
    }


def get_all_places() -> Dict[str, Dict[str, Any]]:
    """Return the live campus-places dict — DB-primary, hardcoded fallback."""
    return _load_places_from_db() or _PLACE_METADATA


def _place_labels() -> Dict[str, str]:
    """Derive place_id -> full-name mapping from the live places dict."""
    return {pid: meta["full"] for pid, meta in get_all_places().items()}


def save_place_to_db(place_id: str, meta: Dict[str, Any]) -> None:
    """Upsert a single place record into the campus_places table."""
    try:
        label = meta.get("full") or meta.get("short") or place_id
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO campus_places"
                " (place_id, label, num, short, full, walk_minutes, direction, x, y)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (place_id, label, meta.get("num"), meta.get("short"), meta.get("full"),
                 meta.get("walk_minutes_from_gate"), meta.get("direction_from_gate"),
                 meta.get("x"), meta.get("y")),
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Coordinate overrides — DB-primary
# ---------------------------------------------------------------------------

def list_coord_overrides() -> Dict[str, Dict[str, int]]:
    try:
        with _get_conn() as conn:
            rows = conn.execute("SELECT place_id, x, y FROM map_coords_overrides").fetchall()
        if rows:
            return {row["place_id"]: {"x": int(row["x"]), "y": int(row["y"])} for row in rows}
    except Exception:
        pass
    # Fallback: read legacy JSON
    if _OVERRIDE_PATH.exists():
        try:
            return _json.loads(_OVERRIDE_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, _json.JSONDecodeError):
            pass
    return {}


def save_coord_overrides(coords: Dict[str, Dict[str, int]]) -> int:
    places = get_all_places()
    applied = 0
    try:
        with _get_conn() as conn:
            for pid, c in coords.items():
                if pid not in places or not isinstance(c, dict):
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO map_coords_overrides (place_id, x, y) VALUES (?, ?, ?)",
                    (pid, int(c["x"]), int(c["y"])),
                )
                applied += 1
    except Exception:
        pass
    return applied


def reset_coord_overrides() -> None:
    try:
        with _get_conn() as conn:
            conn.execute("DELETE FROM map_coords_overrides")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Waypoint overrides — DB-primary
# ---------------------------------------------------------------------------

def list_waypoint_overrides() -> Dict[str, Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            rows = conn.execute("SELECT waypoint_id, x, y, neighbors FROM map_waypoints").fetchall()
        if rows:
            result: Dict[str, Any] = {}
            for row in rows:
                entry: Dict[str, Any] = {"x": int(row["x"]), "y": int(row["y"])}
                if row["neighbors"] and row["neighbors"] != "[]":
                    entry["neighbors"] = _json.loads(row["neighbors"])
                result[row["waypoint_id"]] = entry
            return result
    except Exception:
        pass
    # Fallback: read legacy JSON
    if _WAYPOINTS_PATH.exists():
        try:
            return _json.loads(_WAYPOINTS_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, _json.JSONDecodeError):
            pass
    return {}


def save_waypoint_overrides(coords: Dict[str, Any]) -> int:
    existing = list_waypoint_overrides()
    for wid, c in coords.items():
        if not isinstance(c, dict) or "x" not in c or "y" not in c:
            continue
        entry: Dict[str, Any] = {"x": int(c["x"]), "y": int(c["y"])}
        if isinstance(c.get("neighbors"), list):
            entry["neighbors"] = [str(n) for n in c["neighbors"] if isinstance(n, str)]
        existing[wid] = entry
    try:
        with _get_conn() as conn:
            for wid, e in existing.items():
                neighbors = e.get("neighbors", [])
                conn.execute(
                    "INSERT OR REPLACE INTO map_waypoints (waypoint_id, x, y, neighbors) VALUES (?, ?, ?, ?)",
                    (wid, float(e["x"]), float(e["y"]), _json.dumps(neighbors)),
                )
    except Exception:
        pass
    return len(existing)


def delete_waypoint_override(waypoint_id: str) -> bool:
    try:
        with _get_conn() as conn:
            cur = conn.execute("DELETE FROM map_waypoints WHERE waypoint_id = ?", (waypoint_id,))
            return cur.rowcount > 0
    except Exception:
        return False


def reset_waypoint_overrides() -> None:
    try:
        with _get_conn() as conn:
            conn.execute("DELETE FROM map_waypoints")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Custom markers — DB-primary
# ---------------------------------------------------------------------------

def list_custom_markers() -> Dict[str, Dict[str, Any]]:
    try:
        with _get_conn() as conn:
            rows = conn.execute("SELECT id, name, abbr, x, y, num FROM map_custom_markers").fetchall()
        if rows:
            result: Dict[str, Any] = {}
            for row in rows:
                entry: Dict[str, Any] = {
                    "id": row["id"], "name": row["name"], "abbr": row["abbr"],
                    "x": row["x"], "y": row["y"],
                }
                if row["num"] is not None:
                    entry["num"] = row["num"]
                result[row["id"]] = entry
            return result
    except Exception:
        pass
    # Fallback: read legacy JSON
    if _CUSTOM_MARKERS_PATH.exists():
        try:
            return _json.loads(_CUSTOM_MARKERS_PATH.read_text(encoding="utf-8")) or {}
        except (OSError, _json.JSONDecodeError):
            pass
    return {}


def upsert_custom_marker(marker: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(marker.get("id", "")).strip()
    name = str(marker.get("name", "")).strip()
    if not mid or not name:
        raise ValueError("custom marker requires id and name")
    try:
        x = int(marker["x"])
        y = int(marker["y"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("custom marker requires integer x, y")
    entry: Dict[str, Any] = {
        "id": mid, "name": name,
        "abbr": str(marker.get("abbr") or name)[:48],
        "x": x, "y": y,
    }
    num = marker.get("num")
    if num is not None:
        try:
            entry["num"] = int(num)
        except (TypeError, ValueError):
            pass
    try:
        with _get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO map_custom_markers (id, name, abbr, x, y, num)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (mid, name, entry["abbr"], x, y, entry.get("num")),
            )
    except Exception:
        pass
    return entry


def delete_custom_marker(marker_id: str) -> bool:
    try:
        with _get_conn() as conn:
            cur = conn.execute("DELETE FROM map_custom_markers WHERE id = ?", (marker_id,))
            return cur.rowcount > 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Keyword aliases per place. Ordering matters — more specific (longer, unique)
# names come first so they win over generic substrings. Matches use whole-word
# boundaries (see `_matches_term`), which prevents "cas" from triggering on
# "campus" or "ced" from triggering on "exceeded". Includes Tagalog aliases.
# ---------------------------------------------------------------------------

_PLACE_KEYWORDS: List[Tuple[str, List[str]]] = [
    # Unique multi-word phrases (longest, safest matches first)
    ("ncrdec",          ["ncrdec", "national coffee research", "coffee research", "extension center"]),
    ("intl_house",      ["international house", "marketing house"]),
    ("processing",      ["fruits and vegetable processing", "vegetable processing", "processing center", "fruits and vegetable"]),
    ("demo_farm",       ["technology demonstration farm", "demonstration farm", "demo farm", "tech demo"]),
    ("gender_dev",      ["gender and development", "gender development", "gad research", "gad center"]),
    ("agri_eco",        ["agri-eco", "agri eco", "eco park", "agri-eco park"]),
    ("bano_resort",     ["baño de señora", "bano de senora", "baño resort", "bano resort", "señora resort"]),
    ("research_center", ["research center"]),
    ("old_cemds",       ["old cemds"]),
    ("star_farm",       ["star farm", "cvsu star farm"]),
    ("bahay_alumni",    ["bahay alumni", "alumni house"]),
    ("hostel",          ["hostel tropicana", "tropicana"]),
    ("child_dev",       ["child development", "child dev", "daycare", "day care"]),
    ("rolle_hall",      ["rolle hall", "s.m rolle", "sm rolle", "rolle"]),
    ("sci_hs",          ["science highschool", "science high school", "high school", "shs"]),
    ("intl_house",      ["intl house"]),

    # Unique college abbreviations
    ("ceit",            ["ceit", "engineering", "information technology", "computer science", "computer engineering", "electronics engineering"]),
    ("cafenr",          ["cafenr", "agriculture", "food technology", "environment and natural", "natural resources", "agri"]),
    ("cemds",           ["cemds", "economics", "management studies", "development studies", "business administration", "accountancy"]),
    ("coed",            ["coed", "ced", "college of education", "ceduc", "teacher education", "education building"]),
    ("cvmbs",           ["cvmbs", "veterinary", "vet med", "biological sciences"]),
    ("cthm",            ["cthm", "tourism", "hospitality"]),
    ("cas",             ["cas", "arts and sciences", "arts & sciences", "liberal arts"]),
    ("ccj",             ["ccj", "criminal justice"]),
    ("con",             ["con", "nursing", "college of nursing"]),
    ("dcs",             ["dcs", "crop science", "department of crop"]),
    ("das",             ["das", "animal science", "department of animal"]),
    ("osas",            ["osas", "student affairs", "registrar", "university registrar"]),
    ("icon",            ["icon", "convention center", "international convention"]),
    ("grad",            ["graduate school", "grad school", "masteral", "doctoral", "post-graduate", "postgraduate"]),

    # Singular landmarks / facilities
    ("library",         ["library", "aklatan", "diwa library", "ladislao", "ladislao diwa"]),
    ("admin",           ["admin", "administration", "administration building", "admin building", "cashier", "university cashier", "pangasiwaan"]),
    ("chapel",          ["chapel", "simbahan", "kapilya", "university church"]),
    ("gym",             ["gym", "gymnasium", "himnasyo", "sports complex", "sports and physical"]),
    ("mall",            ["mall", "university mall", "canteen", "cafeteria", "kantina", "food court"]),
    ("clinic",          ["clinic", "infirmary", "university clinic", "ospital"]),
    ("oval",            ["oval", "track", "grandstand", "university oval"]),
    ("open_court",      ["open court", "basketball court"]),
    ("bleachers",       ["bleachers"]),
    ("softball",        ["softball", "softball field"]),
    ("quadrangle",      ["quadrangle", "quad"]),
    ("plaza",           ["plaza", "laya't diwa", "laya t diwa", "university plaza"]),
    ("lagoon",          ["lagoon"]),
    ("saluysoy",        ["saluysoy"]),
    ("boys_dorm",       ["boys dorm", "boy's dorm", "boy dormitory", "boys dormitory", "boy's dormitory"]),
    ("girls_dorm",      ["girls dorm", "girl's dorm", "girl dormitory", "girls dormitory", "girl's dormitory"]),

    # Gates
    ("gate_1",          ["gate 1", "main gate", "entrance"]),
    ("gate_2",          ["gate 2"]),
    ("gate_3",          ["gate 3"]),

    # Catch-all for general CvSU queries — must come last
    ("main",            ["cvsu", "main campus", "campus map", "the campus", "indang campus", "pamantasan", "unibersidad", "don severino"]),
]


_LOCATION_KEYWORDS = {
    "where", "location", "direction", "directions",
    "how to get", "how to find", "get to", "go to", "locate", "find",
    "building", "place", "address",
    "saan", "nasaan", "paano pumunta", "paano makakapunta",
    "papunta", "papuntang", "pupunta", "pupuntang", "pumunta",
}


_TERM_REGEX_CACHE: Dict[str, "re.Pattern[str]"] = {}


def _matches_term(msg: str, term: str) -> bool:
    """Whole-word match: \\bterm\\b against the lowercased message.

    Phrases (e.g. "old cemds", "paano pumunta") work because the word
    boundaries are anchored only at the start and end of the escaped term.
    """
    pat = _TERM_REGEX_CACHE.get(term)
    if pat is None:
        pat = re.compile(r"\b" + re.escape(term) + r"\b")
        _TERM_REGEX_CACHE[term] = pat
    return pat.search(msg) is not None


# ---------------------------------------------------------------------------
# Intent -> place / directory routing
# ---------------------------------------------------------------------------
# Every routable intent maps to a default destination. Keyword match in the
# message takes priority (so "where is the library" beats the intent default),
# but the intent fallback ensures the directory card is always populated for
# the user even when they didn't include a "where" keyword.

_INTENT_TO_PLACE: Dict[str, str] = {
    # Admissions & enrollment all happen at Admin / Registrar (OSAS)
    "admissions_requirements": "admin",
    "admissions_exam":         "admin",
    "enrollment_procedure":    "osas",
    "enrollment_schedule":     "osas",
    "enrollment":              "osas",
    "registrar":               "osas",
    "shifting":                "osas",
    "student_id":              "osas",
    "academic_policies":       "osas",
    "academic_calendar":       "osas",

    # Financial — University Cashier is in the Administration Building
    "tuition_fees":            "admin",
    "scholarship":             "osas",

    # Academic units / programs
    "courses_offered":         "main",
    "it_cs_courses":           "ceit",
    "graduate_programs":       "grad",
    "online_programs":         "main",
    "retention_policy":        "osas",
    "retention_policy_appeal": "osas",
    "retention_policy_grades": "osas",

    # Student life
    "student_organizations":   "osas",
    "career_opportunity":      "osas",
    "nstp":                    "osas",
    "alumni":                  "bahay_alumni",
    "international_students":  "intl_house",
    "dormitory":               "girls_dorm",
    "library":                 "library",

    # Catch-all / generic
    "campus_facilities":       "main",
    "campus_specific":         "main",
    "campus_branches":         "main",
    "about_cvsu":              "main",
    "vision_mission":          "main",
    "contact_info":            "admin",
    "directory":               "admin",
    "events":                  "main",
    "student_portal":          "ceit",
}


# Shared field values — avoid duplicating literal strings across entries
_OUR = "Office of the University Registrar"
_OUR_ENROLLMENT = "Office of the University Registrar (Enrollment)"
_REGISTRAR_EMAIL = "registrarmain@cvsu.edu.ph"
# CvSU trunkline + PABX locals (static CvSU Phone Directory). Callers dial the
# trunk number, then the office local. From campus phones, press 9 first for
# outside calls (e.g. 94150013).
_CVSU_TRUNK = "4839250"
_REGISTRAR_PHONE = f"{_CVSU_TRUNK} loc. 1026"
_OSAS_PHONE = f"{_CVSU_TRUNK} loc. 1025"
_OSAS_BUILDING = "OSAS Building"
_OSAS_EMAIL = "osasmain@cvsu.edu.ph"
_ADMIN_BUILDING = "Administration Building"
_INFO_EMAIL = "info@cvsu.edu.ph"
_OFFICE_HOURS = "Mon–Fri, 8:00 AM – 5:00 PM"

_INTENT_TO_DIRECTORY: Dict[str, Directory] = {
    "registrar": Directory(
        office=_OUR,
        location="OSAS Building, east of the University Oval",
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        phone=_REGISTRAR_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "enrollment_procedure": Directory(
        office=_OUR_ENROLLMENT,
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        phone=_REGISTRAR_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "enrollment_schedule": Directory(
        office=_OUR_ENROLLMENT,
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        phone=_REGISTRAR_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "enrollment": Directory(
        office=_OUR_ENROLLMENT,
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        phone=_REGISTRAR_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "shifting": Directory(
        office="Office of the University Registrar (Shifting)",
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        hours=_OFFICE_HOURS,
    ),
    "student_id": Directory(
        office="Office of Student Affairs and Services (OSAS)",
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_OSAS_EMAIL,
        phone=_OSAS_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "scholarship": Directory(
        office="OSAS — Scholarship and Financial Assistance",
        location=_OSAS_BUILDING,
        place_id="osas",
        email="scholarshipmain@cvsu.edu.ph",
        hours=_OFFICE_HOURS,
    ),
    "admissions_requirements": Directory(
        office="Office of Admissions",
        location=_ADMIN_BUILDING,
        place_id="admin",
        email="admissionmain@cvsu.edu.ph",
        phone=_CVSU_TRUNK,
        hours=_OFFICE_HOURS,
    ),
    "admissions_exam": Directory(
        office="Office of Admissions (CvSUAT)",
        location=_ADMIN_BUILDING,
        place_id="admin",
        email="admissionmain@cvsu.edu.ph",
        hours=_OFFICE_HOURS,
    ),
    "tuition_fees": Directory(
        office="University Cashier",
        location="Administration Building, ground floor",
        place_id="admin",
        email="cashiermain@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1015",
        hours=_OFFICE_HOURS,
    ),
    "academic_policies": Directory(
        office=_OUR,
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        hours=_OFFICE_HOURS,
    ),
    "academic_calendar": Directory(
        office=_OUR,
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_REGISTRAR_EMAIL,
        hours=_OFFICE_HOURS,
    ),
    "retention_policy": Directory(
        office="OSAS / Office of the University Registrar",
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_OSAS_EMAIL,
        hours=_OFFICE_HOURS,
    ),
    "retention_policy_appeal": Directory(
        office="OSAS — Student Appeals",
        location=_OSAS_BUILDING,
        place_id="osas",
        hours=_OFFICE_HOURS,
    ),
    "retention_policy_grades": Directory(
        office=_OUR,
        location=_OSAS_BUILDING,
        place_id="osas",
        hours=_OFFICE_HOURS,
    ),
    "library": Directory(
        office="Ladislao N. Diwa Memorial Library",
        location="Central campus, north of Administration Building",
        place_id="library",
        email="librarymain@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1038",
        hours="Mon–Fri, 8:00 AM – 7:00 PM; Sat 8:00 AM – 12:00 NN",
    ),
    "dormitory": Directory(
        office="University Dormitories (Boys & Girls)",
        location="South-east row, past the University Mall",
        place_id="girls_dorm",
        email=_OSAS_EMAIL,
        hours="24-hour residence; office Mon–Fri 8:00 AM – 5:00 PM",
    ),
    "graduate_programs": Directory(
        office="CvSU Graduate School",
        location="Central campus, near the library",
        place_id="grad",
        email="gradschoolmain@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1042",
        hours=_OFFICE_HOURS,
    ),
    "career_opportunity": Directory(
        office="OSAS — Career and Placement",
        location=_OSAS_BUILDING,
        place_id="osas",
        email=_OSAS_EMAIL,
        hours=_OFFICE_HOURS,
    ),
    "nstp": Directory(
        office="NSTP Coordinator (OSAS)",
        location=_OSAS_BUILDING,
        place_id="osas",
        hours=_OFFICE_HOURS,
    ),
    "alumni": Directory(
        office="Alumni Relations Office (Bahay Alumni)",
        location="Bahay Alumni, near the east dorm cluster",
        place_id="bahay_alumni",
        email="alumnimain@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1049",
        hours=_OFFICE_HOURS,
    ),
    "international_students": Directory(
        office="International House — Marketing & International Affairs",
        location="International House, near Gate 1",
        place_id="intl_house",
        email="internationalmain@cvsu.edu.ph",
        hours=_OFFICE_HOURS,
    ),
    "student_organizations": Directory(
        office="OSAS — Student Organizations",
        location=_OSAS_BUILDING,
        place_id="osas",
        hours=_OFFICE_HOURS,
    ),
    "contact_info": Directory(
        office="CvSU Main Switchboard",
        location=_ADMIN_BUILDING,
        place_id="admin",
        email=_INFO_EMAIL,
        phone=_CVSU_TRUNK,
        hours=_OFFICE_HOURS,
    ),
    "directory": Directory(
        office="CvSU Directory",
        location=_ADMIN_BUILDING,
        place_id="admin",
        email=_INFO_EMAIL,
        phone=_CVSU_TRUNK,
        hours=_OFFICE_HOURS,
    ),
    "it_cs_courses": Directory(
        office="College of Engineering and Information Technology (CEIT)",
        location="West side of campus, near Gate 2",
        place_id="ceit",
        email="ceit@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1055",
        hours=_OFFICE_HOURS,
    ),
    "student_portal": Directory(
        office="Management Information Systems (MIS)",
        location="CEIT Building",
        place_id="ceit",
        email="mis@cvsu.edu.ph",
        phone=f"{_CVSU_TRUNK} loc. 1054",
        hours=_OFFICE_HOURS,
    ),
    "campus_location": Directory(
        office="CvSU Main Campus (Don Severino delas Alas)",
        location="Indang, Cavite — enter via Gate 1",
        place_id="main",
        email=_INFO_EMAIL,
        hours="Open daily, offices Mon–Fri 8:00 AM – 5:00 PM",
    ),
    "campus_facilities": Directory(
        office="Office of Student Affairs and Services (OSAS)",
        location="OSAS Building, east of the oval",
        place_id="osas",
        email=_OSAS_EMAIL,
        phone=_OSAS_PHONE,
        hours=_OFFICE_HOURS,
    ),
    "events": Directory(
        office="OSAS — Events and Activities",
        location=_OSAS_BUILDING,
        place_id="osas",
        hours=_OFFICE_HOURS,
    ),
}


def resolve_map_data(message: str, intent: str) -> Optional[MapData]:
    """Return MapData for routable queries.

    Priority order:
      1. Specific place keyword in the message (e.g. "where is the library").
      2. Intent default place from `_INTENT_TO_PLACE`.
      3. None for chitchat / greeting / fallback intents.
    """
    msg = message.lower()
    has_location_kw = any(_matches_term(msg, kw) for kw in _LOCATION_KEYWORDS)

    labels = _place_labels()

    # 1. Keyword match in the message — most specific
    if intent == "campus_location" or has_location_kw:
        for pid, keywords in _PLACE_KEYWORDS:
            if any(_matches_term(msg, kw) for kw in keywords):
                if pid in labels:
                    return MapData(place_id=pid, label=labels[pid])

    # 2. Intent default
    pid = _INTENT_TO_PLACE.get(intent)
    if pid and pid in labels:
        return MapData(place_id=pid, label=labels[pid])

    # 3. Special case: explicit "where" without a recognised place
    if intent == "campus_location":
        return MapData(place_id="main", label=labels.get("main", "CvSU Campus"))

    return None


def resolve_directory(intent: str) -> Optional[Directory]:
    """Return the structured directory card for an intent, or None."""
    return _INTENT_TO_DIRECTORY.get(intent)


def build_place_meta(place_id: str) -> PlaceMeta:
    places = get_all_places()
    meta = places[place_id]
    labels = _place_labels()
    return PlaceMeta(
        place_id=place_id,
        label=labels[place_id],
        num=meta["num"],
        short=meta["short"],
        full=meta["full"],
        walk_minutes_from_gate=meta["walk_minutes_from_gate"],
        direction_from_gate=meta["direction_from_gate"],
        x=meta["x"], y=meta["y"],
    )


def campus_map_payload() -> Dict[str, Any]:
    places = get_all_places()
    return {
        "gate": _GATE,
        "viewbox": _CAMPUS_VIEWBOX,
        "places": [build_place_meta(pid) for pid in places.keys()],
    }


def has_place(place_id: str) -> bool:
    return place_id in get_all_places()


# Re-exported for convenience
__all__ = [
    "MapData", "PlaceMeta", "Directory",
    "resolve_map_data", "resolve_directory",
    "build_place_meta", "campus_map_payload", "has_place",
    "save_coord_overrides", "list_coord_overrides", "reset_coord_overrides",
    "save_waypoint_overrides", "list_waypoint_overrides", "reset_waypoint_overrides",
]
