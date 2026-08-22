"""Regenerate assets/brand/og-preview.svg and .png.

Run from a throwaway venv with fontTools, brotli, resvg-py, and pillow --
never this project's own dev environment (this script is design tooling,
not shipped code).

Shares the Calibration palette and type vocabulary with the sibling
package tracegauge (token-efficiency-scorer's assets/brand/BRAND.md is
the canonical reference), but a distinct motif: adk-tracegauge gates on a
threshold, it doesn't report a graduated reading -- so no arc, no ticks,
just a track, a single threshold line, and a marker on the pass or fail
side.

Every character in the wordmark/tagline is a real path traced from this
file's own bundled font files via fontTools, never a live <text> element
-- this rasterization pipeline's SVG renderer does not do real
font-family matching (proven in tracegauge's AU1 rasterization: identical
font_extents regardless of the requested font name), so text is a shape
problem here, not a typography problem. The bundled font files are
subsetted to alphanumerics + space only (no punctuation at all) -- the
hyphen this design needs (adk-tracegauge's own name) is hand-drawn as a
trivial rectangle; every other character is a real glyph outline.

Usage:
    uv venv --python 3.11 C:/some-throwaway-path
    uv pip install fonttools brotli resvg-py pillow --python C:/some-throwaway-path/Scripts/python.exe
    C:/some-throwaway-path/Scripts/python.exe scripts/generate_og_preview.py
"""

from __future__ import annotations

import io
import struct
from pathlib import Path

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).parent.parent
FONT_DIR = ROOT / "assets" / "brand" / "fonts"
DISPLAY_FONT = str(FONT_DIR / "spacegrotesk-700.woff2")
BODY_FONT = str(FONT_DIR / "ibmplexsans-400.woff2")
OUT_SVG = ROOT / "assets" / "brand" / "og-preview.svg"
OUT_PNG = ROOT / "assets" / "brand" / "og-preview.png"

# Calibration palette (shared with tracegauge's assets/brand/BRAND.md)
INK = "#12140F"
PAPER = "#F0EDE4"
CALIBRATED = "#4F7A5C"
GRAPHITE = "#5B5D53"
TICK = "#A79F8C"

W, H = 1280, 640

_MANUAL_GLYPHS = {"-", "."}


def _manual_glyph_path(ch: str, font_size: float, cursor_x: float, y: float) -> tuple[str, float]:
    if ch == "-":
        w = font_size * 0.28
        h = font_size * 0.07
        gy = y - font_size * 0.32
        return f"M {cursor_x:.2f} {gy:.2f} h {w:.2f} v {h:.2f} h {-w:.2f} Z", font_size * 0.38
    if ch == ".":
        r = font_size * 0.045
        cx = cursor_x + r
        cy = y - r
        d = (
            f"M {cx - r:.2f} {cy:.2f} "
            f"a {r:.2f} {r:.2f} 0 1 0 {2 * r:.2f} 0 "
            f"a {r:.2f} {r:.2f} 0 1 0 {-2 * r:.2f} 0 Z"
        )
        return d, font_size * 0.22
    raise ValueError(f"no manual glyph for {ch!r}")


def text_to_path(
    font_path: str, text: str, font_size: float, x: float, y: float
) -> tuple[str, float]:
    font = TTFont(font_path)
    glyph_set = font.getGlyphSet()
    cmap = font.getBestCmap()
    scale = font_size / font["head"].unitsPerEm
    hmtx = font["hmtx"]
    path_parts = []
    cursor_x = x

    for ch in text:
        if ch in _MANUAL_GLYPHS:
            d, advance = _manual_glyph_path(ch, font_size, cursor_x, y)
        else:
            codepoint = ord(ch)
            if codepoint not in cmap:
                raise ValueError(f"glyph for {ch!r} not in font {font_path}")
            glyph_name = cmap[codepoint]
            advance = hmtx[glyph_name][0] * scale
            svg_pen = SVGPathPen(glyph_set)
            transform_pen = TransformPen(svg_pen, (scale, 0, 0, -scale, cursor_x, y))
            glyph_set[glyph_name].draw(transform_pen)
            d = svg_pen.getCommands()
        if d:
            path_parts.append(d)
        cursor_x += advance

    return " ".join(path_parts), cursor_x - x


def build_svg() -> str:
    track_x1, track_x2 = 120, 560
    track_y = 320
    gate_x = 400

    track = (
        f'<line x1="{track_x1}" y1="{track_y}" x2="{track_x2}" y2="{track_y}" '
        f'stroke="{GRAPHITE}" stroke-width="8" stroke-linecap="round"/>'
    )
    gate = (
        f'<line x1="{gate_x}" y1="{track_y - 90}" x2="{gate_x}" y2="{track_y + 90}" '
        f'stroke="{PAPER}" stroke-width="10" stroke-linecap="round"/>'
    )
    gate_flag = (
        f'<line x1="{gate_x - 18}" y1="{track_y - 90}" x2="{gate_x + 18}" y2="{track_y - 90}" '
        f'stroke="{PAPER}" stroke-width="6" stroke-linecap="round"/>'
    )
    marker_x = gate_x + 110
    marker = f'<circle cx="{marker_x}" cy="{track_y}" r="20" fill="{CALIBRATED}"/>'
    arrow_tail_x = gate_x - 140
    arrow = (
        f'<line x1="{arrow_tail_x}" y1="{track_y}" x2="{marker_x - 34}" y2="{track_y}" '
        f'stroke="{TICK}" stroke-width="3" stroke-dasharray="2 10" stroke-linecap="round"/>'
    )
    origin_dot = f'<circle cx="{arrow_tail_x}" cy="{track_y}" r="10" fill="{TICK}"/>'

    motif = track + arrow + origin_dot + gate + gate_flag + marker

    name_d, _ = text_to_path(DISPLAY_FONT, "adk-tracegauge", 72, 700, 300)
    tag_d, _ = text_to_path(BODY_FONT, "Gates on a threshold not a chart.", 26, 700, 356)
    text = f'<path d="{name_d}" fill="{PAPER}"/><path d="{tag_d}" fill="{TICK}"/>'

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}">
<rect width="{W}" height="{H}" fill="{INK}"/>
{motif}
{text}
</svg>'''


def main() -> None:
    svg = build_svg()
    OUT_SVG.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT_SVG}")

    import resvg_py

    png_data = resvg_py.svg_to_bytes(svg_path=str(OUT_SVG), width=W, height=H)
    img = Image.open(io.BytesIO(bytes(png_data))).convert("RGB")
    img.save(OUT_PNG, format="PNG")
    print(f"wrote {OUT_PNG}")

    data = OUT_PNG.read_bytes()
    pos = 8
    chunks = []
    while pos < len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunks.append(data[pos + 4 : pos + 8].decode("ascii"))
        pos += 8 + length + 4
    w, h, bitdepth, colortype = struct.unpack(">IIBB", data[16:26])
    print(
        f"verify: {w}x{h}, bitdepth={bitdepth}, colortype={colortype} (2=RGB truecolor), chunks={chunks}"
    )
    assert (w, h, bitdepth, colortype) == (W, H, 8, 2), "PNG constraint check failed"
    assert chunks == ["IHDR", "IDAT", "IEND"], "unexpected PNG chunks -- not maximally conservative"


if __name__ == "__main__":
    main()
