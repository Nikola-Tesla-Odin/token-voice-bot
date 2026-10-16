"""Free quote cards: the post rendered as a 1600×900 image (fits X's 16:9 preview), made locally with Pillow.

Layout: the persona's portrait on the left (if `portrait:` is set in its YAML), the quote on the right in a
serif face sized to fit, then the signoff.
"""

from __future__ import annotations

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .images import Media
from .persona import Persona

W, H = 1600, 900
BG = (14, 17, 24)
INK = (238, 233, 222)
ACCENT = (110, 180, 255)  # electric blue
MUTED = (150, 158, 172)

# Bundled Gelasio (SIL Open Font License, a Georgia look-alike) so cards look the same on any machine.
FONTS = Path(__file__).parent / "fonts"
SERIF = str(FONTS / "Gelasio.ttf")
SERIF_ITALIC = str(FONTS / "Gelasio-Italic.ttf")


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default(size)


def _fit_text(draw: ImageDraw.ImageDraw, text: str, box_w: int, box_h: int):
    """Largest font size (and wrapping) at which the text fits the box."""
    for size in range(64, 27, -2):
        font = _font(SERIF, size)
        avg = draw.textlength("abcdefghijklmnopqrstuvwxyz", font=font) / 26
        lines = textwrap.wrap(text, width=max(10, int(box_w / avg)))
        line_h = int(size * 1.4)
        if len(lines) * line_h <= box_h and all(draw.textlength(l, font=font) <= box_w for l in lines):
            return font, lines, line_h
    return font, lines, line_h


def _portrait(path: Path, width: int) -> Image.Image:
    img = ImageOps.grayscale(Image.open(path)).convert("RGB")
    img = ImageOps.fit(img, (width, H), centering=(0.5, 0.3))
    # Fade the right 45% into the background so the portrait melts into the text side.
    mask = Image.new("L", (width, H), 255)
    fade_from = int(width * 0.55)
    mask_draw = ImageDraw.Draw(mask)
    for x in range(fade_from, width):
        mask_draw.line([(x, 0), (x, H)], fill=int(255 * (width - x) / (width - fade_from)))
    return Image.composite(img, Image.new("RGB", (width, H), BG), mask)


def render(persona: Persona, text: str) -> Media:
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)

    left = 90
    portrait = Path(persona.portrait).expanduser() if persona.portrait else None
    if portrait and portrait.exists():
        pw = 620
        canvas.paste(_portrait(portrait, pw), (0, 0))
        left = pw + 30

    right_margin, top, bottom = 100, 130, 200
    box_w, box_h = W - left - right_margin, H - top - bottom

    draw.text((left, top - 120), "“", font=_font(SERIF, 150), fill=ACCENT)
    font, lines, line_h = _fit_text(draw, text, box_w, box_h)
    y = top + (box_h - len(lines) * line_h) // 2
    for line in lines:
        draw.text((left, y), line, font=font, fill=INK)
        y += line_h

    signoff = persona.format.signoff or f"— {persona.name}"
    draw.line([(left, H - 150), (left + 80, H - 150)], fill=ACCENT, width=3)
    draw.text((left, H - 125), signoff, font=_font(SERIF_ITALIC, 38), fill=MUTED)

    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return Media(buf.getvalue(), "image/png")
