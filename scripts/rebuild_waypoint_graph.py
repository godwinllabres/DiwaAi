"""Rebuild the waypoint neighbor graph in SeviWeb/app/lib/campusMap.ts so
that edges follow the actual road network instead of an old hand-tuned
topology.

Algorithm:
  1. Parse ROADS (polylines) and WAYPOINTS from campusMap.ts. Apply the
     coordinates in data/waypoints_override.json on top.
  2. For each waypoint, find the road polyline whose nearest segment is
     closest, and record the position `t` (arc-length along that polyline).
  3. Per road polyline, sort waypoints by `t` and connect consecutive ones.
  4. Connect waypoints that snap to within JUNCTION_RADIUS of each other
     across different polylines — that's a road junction.
  5. Patch each `neighbors: [ ... ]` literal in campusMap.ts to reflect the
     new graph.

Idempotent. Run with --dry-run to preview without writing.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
from scipy.spatial import Delaunay

ROOT = Path(__file__).resolve().parent.parent
TS_PATH = ROOT.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"
WAYPOINTS_PATH = ROOT / "data" / "waypoints_override.json"

# Two waypoints whose snap-points are within this many pixels of each other
# (on different polylines) are treated as sitting at the same junction.
JUNCTION_RADIUS = 70

# Fallback: very-near waypoints that didn't get linked any other way.
NEAR_FALLBACK_RADIUS = 80


# ---------- parsing -----------------------------------------------------------

def parse_roads(text: str) -> list[list[Tuple[float, float]]]:
    start = text.find("export const ROADS")
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
    roads: list[list[Tuple[float, float]]] = []
    for m in re.finditer(r"points\s*:\s*\[(.*?)\]", block, flags=re.DOTALL):
        pts_raw = m.group(1)
        coords = re.findall(
            r"x\s*:\s*(-?\d+(?:\.\d+)?)\s*,\s*y\s*:\s*(-?\d+(?:\.\d+)?)", pts_raw
        )
        if len(coords) >= 2:
            roads.append([(float(x), float(y)) for x, y in coords])
    return roads


_WP_RE = re.compile(
    r"(?P<id>wp_[a-z0-9_]+)\s*:\s*\{\s*"
    r"id:\s*\"wp_[a-z0-9_]+\"\s*,\s*"
    r"x:\s*(?P<x>\d+)\s*,\s*"
    r"y:\s*(?P<y>\d+)\s*,\s*"
    r"neighbors:\s*\[(?P<n>[^\]]*)\]",
    re.DOTALL,
)


def parse_waypoints(text: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in _WP_RE.finditer(text):
        wp_id = m["id"]
        neighbors = re.findall(r"\"(wp_[a-z0-9_]+)\"", m["n"])
        out[wp_id] = {
            "x": int(m["x"]),
            "y": int(m["y"]),
            "neighbors": neighbors,
            "match_span": m.span(),
            "neighbors_span": m.span("n"),
        }
    return out


# ---------- geometry ----------------------------------------------------------

def project_to_segment(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> Tuple[float, float, float, float]:
    """Return (cx, cy, t_in_segment_units, squared_distance)."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        cx, cy, t = ax, ay, 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = max(0.0, min(1.0, t))
        cx, cy = ax + t * dx, ay + t * dy
    ddx, ddy = px - cx, py - cy
    return cx, cy, t, ddx * ddx + ddy * ddy


def snap_to_road_network(
    point: Tuple[float, float], roads: list[list[Tuple[float, float]]]
) -> Tuple[int, float, float, float]:
    """Return (road_idx, arc_length_along_polyline, snap_x, snap_y)."""
    px, py = point
    best = (-1, 0.0, px, py, float("inf"))
    for ri, poly in enumerate(roads):
        arc = 0.0
        for i in range(len(poly) - 1):
            ax, ay = poly[i]
            bx, by = poly[i + 1]
            cx, cy, t, d2 = project_to_segment(px, py, ax, ay, bx, by)
            if d2 < best[4]:
                seg_len = math.hypot(bx - ax, by - ay)
                best = (ri, arc + t * seg_len, cx, cy, d2)
            arc += math.hypot(bx - ax, by - ay)
    return best[0], best[1], best[2], best[3]


# ---------- build graph -------------------------------------------------------

def build_neighbors(
    waypoints: dict[str, dict],
    roads: list[list[Tuple[float, float]]],
    max_edge_px: float = 260,
) -> dict[str, list[str]]:
    """Construct neighbor graph by combining:
      a) Per-polyline consecutive waypoints (so edges follow roads when possible).
      b) Delaunay edges between waypoints (so the graph is planar + connected).
      c) Drop edges longer than `max_edge_px` (would cut across campus).
    """
    ids = list(waypoints.keys())
    coords = np.array([[waypoints[i]["x"], waypoints[i]["y"]] for i in ids], dtype=float)

    # 1. Per-road consecutive edges.
    snapped: dict[str, Tuple[int, float]] = {}
    for wp_id, w in waypoints.items():
        ri, arc, _, _ = snap_to_road_network((w["x"], w["y"]), roads)
        snapped[wp_id] = (ri, arc)
    edges: set[Tuple[str, str]] = set()
    by_road: dict[int, list[str]] = {}
    for wp_id, (ri, arc) in snapped.items():
        by_road.setdefault(ri, []).append(wp_id)
    for ri, road_ids in by_road.items():
        road_ids.sort(key=lambda i: snapped[i][1])
        for a, b in zip(road_ids, road_ids[1:]):
            edges.add(tuple(sorted([a, b])))

    # 2. Delaunay triangulation over all waypoints (catches junctions and
    #    cross-road shortcuts the per-polyline pass missed).
    if len(coords) >= 3:
        tri = Delaunay(coords)
        for simplex in tri.simplices:
            for i, j in ((0, 1), (1, 2), (2, 0)):
                a, b = ids[simplex[i]], ids[simplex[j]]
                edges.add(tuple(sorted([a, b])))

    # 3. Drop edges that span too much of the map — those almost always cut
    #    across buildings or grass.
    pruned: set[Tuple[str, str]] = set()
    for a, b in edges:
        ax, ay = waypoints[a]["x"], waypoints[a]["y"]
        bx, by = waypoints[b]["x"], waypoints[b]["y"]
        if math.hypot(ax - bx, ay - by) <= max_edge_px:
            pruned.add((a, b))

    adj: dict[str, set[str]] = {wp: set() for wp in waypoints}
    for a, b in pruned:
        adj[a].add(b)
        adj[b].add(a)

    # 4. Safety net — anything still orphan gets its nearest neighbor.
    for wp in ids:
        if adj[wp]:
            continue
        wx, wy = waypoints[wp]["x"], waypoints[wp]["y"]
        nearest = min(
            (o for o in ids if o != wp),
            key=lambda o: math.hypot(waypoints[o]["x"] - wx, waypoints[o]["y"] - wy),
        )
        adj[wp].add(nearest)
        adj[nearest].add(wp)

    return {wp: sorted(adj[wp]) for wp in waypoints}


# ---------- write back --------------------------------------------------------

def patch_neighbors_in_source(
    text: str, waypoints: dict[str, dict], new_neighbors: dict[str, list[str]]
) -> str:
    # Apply replacements from the end of the file backwards so the spans
    # of earlier matches stay valid.
    edits = []
    for wp_id, w in waypoints.items():
        old = w["neighbors"]
        new = new_neighbors[wp_id]
        if old == new:
            continue
        rendered = ", ".join(f'"{n}"' for n in new)
        span = w["neighbors_span"]
        edits.append((span, rendered))
    edits.sort(key=lambda e: e[0][0], reverse=True)
    out = text
    for (start, end), rendered in edits:
        out = out[:start] + rendered + out[end:]
    return out, len(edits)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    text = TS_PATH.read_text(encoding="utf-8")
    roads = parse_roads(text)
    waypoints = parse_waypoints(text)
    print(f"Parsed {len(roads)} road polylines, {len(waypoints)} waypoints.")

    # Overlay overrides so we use the snapped coordinates.
    if WAYPOINTS_PATH.exists():
        overrides = json.loads(WAYPOINTS_PATH.read_text(encoding="utf-8"))
        for wp_id, c in overrides.items():
            if wp_id in waypoints:
                waypoints[wp_id]["x"] = int(c["x"])
                waypoints[wp_id]["y"] = int(c["y"])
        print(f"Applied {len(overrides)} coordinate overrides.")

    new_neighbors = build_neighbors(waypoints, roads)

    deg_hist = sorted(
        (len(v), k) for k, v in new_neighbors.items()
    )
    print("\nDegree distribution (smallest first):")
    for d, wp in deg_hist[:8]:
        print(f"  {wp:<18} {d} neighbor(s) -> {new_neighbors[wp]}")
    print("  ...")
    for d, wp in deg_hist[-3:]:
        print(f"  {wp:<18} {d} neighbor(s) -> {new_neighbors[wp]}")

    # Highlight CEIT for the user (they reported the bad routing).
    print("\nKey waypoint changes:")
    for wp in ("wp_ceit", "wp_oval", "wp_admin", "wp_gate1", "wp_gate2", "wp_chapel"):
        if wp in waypoints:
            old = waypoints[wp]["neighbors"]
            new = new_neighbors[wp]
            tag = "" if sorted(old) == new else "  <- changed"
            print(f"  {wp:<18} {new}{tag}")

    if args.dry_run:
        print("\n--dry-run: not writing.")
        return 0

    out, n_edits = patch_neighbors_in_source(text, waypoints, new_neighbors)
    backup = TS_PATH.with_suffix(TS_PATH.suffix + ".bak_graph")
    backup.write_text(text, encoding="utf-8")
    TS_PATH.write_text(out, encoding="utf-8")
    print(f"\nPatched {n_edits} neighbor list(s) in {TS_PATH.name}.")
    print(f"Backup: {backup.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
