"""Render a QR code for the DIWA tunnel URL with a green "DIWA" badge in
the middle. High error-correction (H) lets us cover ~25% of modules without
breaking scannability.
"""

from __future__ import annotations

from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.constants import ERROR_CORRECT_H

URL = "https://dev.godwincreates.net/"
OUT_PATH = Path(__file__).resolve().parent.parent / "data" / "diwa_qr.png"

GREEN = (22, 101, 52)        # tailwind green-800-ish (matches DIWA branding)
GREEN_RING = (5, 46, 22)     # darker outer ring
WHITE = (255, 255, 255)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    """Pick a sensible bold font across Windows / *nix without bundling one."""
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",     # Segoe UI Bold
        "C:/Windows/Fonts/arialbd.ttf",      # Arial Bold
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    qr = qrcode.QRCode(
        version=None,                 # auto-fit
        error_correction=ERROR_CORRECT_H,
        box_size=20,
        border=2,
    )
    qr.add_data(URL)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=(15, 23, 42), back_color=WHITE).convert("RGBA")

    w, h = qr_img.size
    badge_d = int(min(w, h) * 0.28)         # badge diameter (~25% of side)
    cx, cy = w // 2, h // 2

    # White punch-out behind the badge so the QR pixels under it are masked.
    draw = ImageDraw.Draw(qr_img)
    pad = int(badge_d * 0.10)
    draw.ellipse(
        [cx - badge_d // 2 - pad, cy - badge_d // 2 - pad,
         cx + badge_d // 2 + pad, cy + badge_d // 2 + pad],
        fill=WHITE,
    )

    # Outer ring + green disc.
    draw.ellipse(
        [cx - badge_d // 2, cy - badge_d // 2,
         cx + badge_d // 2, cy + badge_d // 2],
        fill=GREEN,
        outline=GREEN_RING,
        width=max(4, badge_d // 30),
    )

    # Fit "DIWA" inside the disc — start big and shrink until it fits.
    text = "DIWA"
    max_text_w = int(badge_d * 0.72)
    font_size = int(badge_d * 0.42)
    font = _load_font(font_size)
    while font_size > 12:
        bbox = draw.textbbox((0, 0), text, font=font)
        if (bbox[2] - bbox[0]) <= max_text_w:
            break
        font_size -= 2
        font = _load_font(font_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        (cx - tw // 2 - bbox[0], cy - th // 2 - bbox[1]),
        text,
        fill=WHITE,
        font=font,
    )

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    qr_img.save(OUT_PATH, "PNG")
    print(f"QR saved to {OUT_PATH} ({w}x{h})")
    print(f"URL: {URL}")


if __name__ == "__main__":
    main()
