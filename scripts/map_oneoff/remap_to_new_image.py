"""
remap_to_new_image.py — remap every building (x, y) from the old square
Matayuyon poster's 2000×2000 frame into the new portrait image's frame.

The new image is 667×1024 (saved at /public/cvsu-campus-map.png), letterboxed
inside the 2000×2000 SVG viewBox with `preserveAspectRatio="xMidYMid meet"`:

    image scale  = 2000 / 1024 = 1.953
    image width  = 1303 px (= 667 × 1.953)
    image x off  = (2000 − 1303) / 2 = 348

So a pixel (px, py) in the original new image lands at:
    (348 + px × 1.953, py × 1.953)  in the viewBox.

Strategy: take the campus bounding box of the OLD coordinates and map it onto
the campus polygon visible in the NEW image (excluding the title bar). The
result is approximate — individual markers may need a small per-pixel nudge
afterward via nudge_markers.py.
"""
import re
from pathlib import Path

SEVI_AI = Path(__file__).parent
CAMPUS_PY = SEVI_AI / "api" / "campus_places.py"
CAMPUS_TS = SEVI_AI.parent / "SeviWeb" / "app" / "lib" / "campusMap.ts"


# Old bounding box: extreme x/y of the 48 markers in the old square poster.
OLD_X_MIN, OLD_X_MAX = 225, 1120
OLD_Y_MIN, OLD_Y_MAX = 540, 1810

# New image — pixels inside the 667×1024 file that bound the campus polygon
# (just below the title bar to the bottom paved road).
NEW_PX_MIN_X, NEW_PX_MAX_X =  40, 640
NEW_PX_MIN_Y, NEW_PX_MAX_Y = 145, 985

# Image-to-viewBox transform (xMidYMid meet, 667×1024 → 2000×2000).
IMG_SCALE = 2000 / 1024
IMG_X_OFFSET = (2000 - 667 * IMG_SCALE) / 2

NEW_VB_MIN_X = IMG_X_OFFSET + NEW_PX_MIN_X * IMG_SCALE   # ≈ 426
NEW_VB_MAX_X = IMG_X_OFFSET + NEW_PX_MAX_X * IMG_SCALE   # ≈ 1598
NEW_VB_MIN_Y = 0           + NEW_PX_MIN_Y * IMG_SCALE    # ≈ 283
NEW_VB_MAX_Y = 0           + NEW_PX_MAX_Y * IMG_SCALE    # ≈ 1924

X_SCALE = (NEW_VB_MAX_X - NEW_VB_MIN_X) / (OLD_X_MAX - OLD_X_MIN)
Y_SCALE = (NEW_VB_MAX_Y - NEW_VB_MIN_Y) / (OLD_Y_MAX - OLD_Y_MIN)


def remap(x_old: int, y_old: int) -> tuple[int, int]:
    x_new = NEW_VB_MIN_X + (x_old - OLD_X_MIN) * X_SCALE
    y_new = NEW_VB_MIN_Y + (y_old - OLD_Y_MIN) * Y_SCALE
    return int(round(x_new)), int(round(y_new))


def patch_python(path: Path):
    text = path.read_text(encoding="utf-8")
    count = 0
    def repl(m):
        nonlocal count
        x = int(m.group(1))
        y = int(m.group(2))
        nx, ny = remap(x, y)
        count += 1
        return f'"x": {nx}, "y": {ny}'
    text = re.sub(r'"x":\s*(-?\d+),\s*"y":\s*(-?\d+)', repl, text)
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: remapped {count} (x,y) pairs")


def patch_typescript(path: Path):
    text = path.read_text(encoding="utf-8")
    count = 0
    def repl(m):
        nonlocal count
        x = int(m.group(1))
        y = int(m.group(2))
        nx, ny = remap(x, y)
        count += 1
        return f'x: {nx}, y: {ny}'
    text = re.sub(r'x:\s*(-?\d+),\s*y:\s*(-?\d+)', repl, text)
    path.write_text(text, encoding="utf-8")
    print(f"  {path.name}: remapped {count} (x,y) pairs")


if __name__ == "__main__":
    print(f"Transform: x_new = {NEW_VB_MIN_X:.0f} + (x_old − {OLD_X_MIN}) × {X_SCALE:.4f}")
    print(f"           y_new = {NEW_VB_MIN_Y:.0f} + (y_old − {OLD_Y_MIN}) × {Y_SCALE:.4f}")
    print()
    patch_python(CAMPUS_PY)
    patch_typescript(CAMPUS_TS)
