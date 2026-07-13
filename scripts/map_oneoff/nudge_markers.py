"""
nudge_markers.py — uniformly shift all 48 building markers by (dx, dy).

Usage:
    python3 nudge_markers.py --dx -20 --dy 0      # shift 20px left
    python3 nudge_markers.py --dx -25 --dy 0      # shift 25px left, etc.

Updates both api/campus_places.py and SeviWeb/app/lib/campusMap.ts, plus the
_GATE / MAIN_GATE constants.
"""
import argparse
import re
from pathlib import Path

SEVI_AI = Path(__file__).parent
CAMPUS_PY = SEVI_AI / "api" / "campus_places.py"
CAMPUS_TS = SEVI_AI.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"


def nudge_python(path: Path, dx: int, dy: int):
    text = path.read_text(encoding="utf-8")
    count = 0
    def repl(m):
        nonlocal count
        x = int(m.group(1)) + dx
        y = int(m.group(2)) + dy
        count += 1
        return f'"x": {x}, "y": {y}'
    text = re.sub(r'"x":\s*(-?\d+),\s*"y":\s*(-?\d+)', repl, text)
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: nudged {count} (x,y) pairs by ({dx:+d}, {dy:+d})")


def nudge_typescript(path: Path, dx: int, dy: int):
    text = path.read_text(encoding="utf-8")
    count = 0
    def repl(m):
        nonlocal count
        x = int(m.group(1)) + dx
        y = int(m.group(2)) + dy
        count += 1
        return f'x: {x}, y: {y}'
    # Match TS `x: NN, y: NN` but NOT inside type/interface declarations.
    # Buildings array uses this pattern on the same line.
    text = re.sub(r'x:\s*(-?\d+),\s*y:\s*(-?\d+)', repl, text)
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: nudged {count} (x,y) pairs by ({dx:+d}, {dy:+d})")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dx", type=int, required=True, help="x shift (negative = left)")
    p.add_argument("--dy", type=int, default=0, help="y shift (negative = up)")
    args = p.parse_args()

    nudge_python(CAMPUS_PY, args.dx, args.dy)
    nudge_typescript(CAMPUS_TS, args.dx, args.dy)
