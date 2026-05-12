"""Snap every entry in data/waypoints_override.json onto the nearest road
segment defined in SeviWeb/app/lib/campusMap.ts (ROADS).

The seed_waypoints.py script puts each waypoint at its building's centre,
which is fine for "which building does this wp represent" but bad for
routing — the rendered path zigzags through building footprints. After
this script, every waypoint sits on a road, so the polyline between any
two waypoints follows the actual road network.

Run:  python scripts/snap_waypoints_to_roads.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TS_PATH = ROOT.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"
WAYPOINTS_PATH = ROOT / "data" / "waypoints_override.json"


def parse_roads(text: str) -> list[list[tuple[float, float]]]:
    """Extract every `points: [...]` array inside the ROADS = [...] literal."""
    start = text.find("export const ROADS")
    if start < 0:
        raise SystemExit("ROADS export not found in campusMap.ts")
    # The type annotation `Road[]` lives between the declaration and the
    # actual array literal — skip past the `=` first.
    eq = text.find("=", start)
    open_idx = text.find("[", eq)
    depth = 0
    end_idx = open_idx
    for i, ch in enumerate(text[open_idx:], start=open_idx):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                end_idx = i
                break
    block = text[open_idx : end_idx + 1]

    roads: list[list[tuple[float, float]]] = []
    for m in re.finditer(r"points\s*:\s*\[(.*?)\]", block, flags=re.DOTALL):
        pts_raw = m.group(1)
        coords = re.findall(r"x\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*:\s*(-?\d+(?:\.\d+)?)", pts_raw)
        if len(coords) >= 2:
            roads.append([(float(x), float(y)) for x, y in coords])
    return roads


def project_point_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> tuple[float, float, float]:
    """Return (closest_x, closest_y, squared_distance) of (px,py) onto seg AB."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        cx, cy = ax, ay
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
    ddx, ddy = px - cx, py - cy
    return cx, cy, ddx * ddx + ddy * ddy


def snap_to_roads(
    point: tuple[float, float], roads: list[list[tuple[float, float]]]
) -> tuple[int, int, float]:
    px, py = point
    best = (px, py, float("inf"))
    for poly in roads:
        for i in range(len(poly) - 1):
            ax, ay = poly[i]
            bx, by = poly[i + 1]
            cx, cy, d2 = project_point_to_segment(px, py, ax, ay, bx, by)
            if d2 < best[2]:
                best = (cx, cy, d2)
    cx, cy, d2 = best
    return int(round(cx)), int(round(cy)), d2 ** 0.5


def main() -> int:
    text = TS_PATH.read_text(encoding="utf-8")
    roads = parse_roads(text)
    if not roads:
        raise SystemExit("No road polylines parsed.")
    print(f"Parsed {len(roads)} road polylines, "
          f"{sum(len(p) - 1 for p in roads)} segments.")

    if not WAYPOINTS_PATH.exists():
        raise SystemExit(f"Missing {WAYPOINTS_PATH}")
    waypoints = json.loads(WAYPOINTS_PATH.read_text(encoding="utf-8"))

    moved = []
    snapped: dict[str, dict[str, int]] = {}
    for wp_id, c in waypoints.items():
        x, y = int(c["x"]), int(c["y"])
        nx, ny, dist = snap_to_roads((x, y), roads)
        snapped[wp_id] = {"x": nx, "y": ny}
        if (nx, ny) != (x, y):
            moved.append((wp_id, (x, y), (nx, ny), round(dist, 1)))

    WAYPOINTS_PATH.write_text(
        json.dumps(snapped, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Snapped {len(moved)} of {len(waypoints)} waypoints onto road segments.")
    moved.sort(key=lambda r: -r[3])
    for wp_id, before, after, dist in moved[:15]:
        print(f"  {wp_id:<18} {before!s:<14} -> {after!s:<14}  (moved {dist}px)")
    if len(moved) > 15:
        print(f"  ... {len(moved) - 15} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
