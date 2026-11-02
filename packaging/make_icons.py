"""Render the app icon and write desktop/build/icon.{png,icns,ico} (pure Python: no Pillow, sharp or iconutil).

The icon is drawn with signed-distance functions and analytic anti-aliasing at every target size, so small sizes
stay crisp instead of being downscaled from 1024 px.  Design: a deep indigo-violet tile with a luminous mark —
the signed distance to the mark gives the edge glow for free (exp(-d/k)), which is the one thing a flat SVG
mock-up cannot imitate.  The mark is the letter A drawn as a node graph: the product initial and a knowledge
graph in one shape.  Keep desktop/src/renderer/src/components/Logo.tsx in step with it.

    uv run python packaging/make_icons.py                        write the shipped icons
    uv run python packaging/make_icons.py --variant a-synapse    pick the mark
    uv run python packaging/make_icons.py --preview /tmp/x       render candidates without touching the build
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "desktop" / "build"

# The tile is deep enough to read as "dark" on a light desktop, with a violet lift behind the mark so it is
# not a flat black square.  The mark itself carries the indigo→violet gradient; nothing here is pure white.
TILE_TOP_LEFT = (18, 13, 40)  # #120D28
TILE_BOTTOM_RIGHT = (36, 23, 71)  # #241747
TILE_LIFT = (91, 33, 182)  # #5B21B6, radial, strongest behind the mark
MARK_TOP_LEFT = (224, 226, 254)  # #E0E2FE, barely-tinted indigo
MARK_BOTTOM_RIGHT = (252, 220, 254)  # #FCDCFE, barely-tinted fuchsia
GLOW_TOP_LEFT = (124, 92, 246)  # #7C5CF6 — the halo stays saturated so the core reads as light against it
GLOW_BOTTOM_RIGHT = (217, 70, 239)  # #D946EF
WHITE = (255, 255, 255)

MARK_SCALE = 1.08  # how much of the tile the mark fills; the marks below are drawn on a 0..100 grid
VARIANTS = ("node-a", "a-synapse", "synapse")
DEFAULT_VARIANT = "node-a"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def _mix(c1: tuple[float, float, float], c2: tuple[float, float, float], t: float) -> tuple[float, float, float]:
    return (c1[0] + (c2[0] - c1[0]) * t, c1[1] + (c2[1] - c1[1]) * t, c1[2] + (c2[2] - c1[2]) * t)


def _rounded_rect(px: float, py: float, cx: float, cy: float, half: float, radius: float) -> float:
    qx, qy = abs(px - cx) - half + radius, abs(py - cy) - half + radius
    outside = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return outside + min(max(qx, qy), 0.0) - radius


def _segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    abx, aby = bx - ax, by - ay
    t = _clamp(((px - ax) * abx + (py - ay) * aby) / (abx * abx + aby * aby))
    return math.hypot(px - ax - t * abx, py - ay - t * aby)


def _triangle(px: float, py: float, pts: list[tuple[float, float]]) -> float:
    """Signed distance to a triangle (negative inside)."""
    d = min(_segment(px, py, *pts[i], *pts[(i + 1) % 3]) for i in range(3))
    signs = []
    for i in range(3):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % 3]
        signs.append((bx - ax) * (py - ay) - (by - ay) * (px - ax) > 0)
    return -d if all(signs) or not any(signs) else d


def _arc(px: float, py: float, cx: float, cy: float, radius: float, start: float, end: float) -> float:
    """Distance to an arc of `radius` spanning [start, end] radians, with round caps."""
    ang = math.atan2(py - cy, px - cx)
    while ang < start:
        ang += 2 * math.pi
    if ang <= end:
        return abs(math.hypot(px - cx, py - cy) - radius)
    caps = [(cx + radius * math.cos(a), cy + radius * math.sin(a)) for a in (start, end)]
    return min(math.hypot(px - ax, py - ay) for ax, ay in caps)


def mark_distance(px: float, py: float, s: float, variant: str) -> float:
    """Signed distance to the mark, in pixels, for a canvas of `s` px.  Negative inside."""
    u = s / 100.0
    x = 50.0 + (px / u - 50.0) / MARK_SCALE
    y = 50.0 + (py / u - 50.0) / MARK_SCALE

    def legs(stroke: float, apex: tuple[float, float], left: tuple[float, float], right: tuple[float, float]) -> float:
        return min(_segment(x, y, *apex, *left), _segment(x, y, *apex, *right)) - stroke / 2

    if variant in ("node-a", "a-synapse"):
        apex, left, right = (50.0, 26.0), (29.0, 74.0), (71.0, 74.0)
        d = legs(6.0, apex, left, right)
        for cx, cy, r in ((50.0, 26.0, 9.0), (29.0, 74.0, 9.0), (71.0, 74.0, 9.0)):
            d = min(d, math.hypot(x - cx, y - cy) - r)
        if variant == "node-a":
            # a solid crossbar with a node at its middle: the letter plus a graph
            d = min(d, _segment(x, y, 35.8, 58.5, 64.2, 58.5) - 2.75)
            d = min(d, math.hypot(x - 50.0, y - 58.5) - 5.2)
        else:
            # the crossbar becomes three particles crossing the cleft between the legs
            for cx in (44.5, 50.0, 55.5):
                d = min(d, math.hypot(x - cx, y - 58.5) - 3.5)
        return d * u * MARK_SCALE

    # synapse: soma with two dendrites, particles crossing the cleft into a receptor cup
    d = min(_segment(x, y, 34.0, 50.0, 17.0, 36.0), _segment(x, y, 34.0, 50.0, 17.0, 64.0)) - 3.0
    d = min(d, math.hypot(x - 34.0, y - 50.0) - 12.0)
    d = min(d, _arc(x, y, 84.0, 50.0, 16.0, math.radians(125), math.radians(235)) - 4.0)
    for cx in (50.5, 55.5, 60.5):
        d = min(d, math.hypot(x - cx, y - 50.0) - 3.0)
    return d * u * MARK_SCALE


def render(size: int, variant: str = DEFAULT_VARIANT) -> bytes:
    """RGBA pixels, row-major."""
    s = float(size)
    # macOS icon grid: the tile covers ~80% of the canvas, and its corner radius is ~22.5% of the tile width.
    half, radius = 0.40 * s, 0.18 * s
    c = s / 2
    lift_cx, lift_cy, lift_r = c, c - 0.06 * s, 0.55 * s
    edge_k, halo_k = 0.022 * s, 0.075 * s
    highlight = 0.012 * s

    out = bytearray(size * size * 4)
    for yi in range(size):
        py = yi + 0.5
        for xi in range(size):
            px = xi + 0.5
            d_bg = _rounded_rect(px, py, c, c, half, radius)
            bg_a = _clamp(0.5 - d_bg)
            if bg_a <= 0.0:
                continue

            t = _clamp(((px - (c - half)) + (py - (c - half))) / (4 * half))
            r, g, b = _mix(TILE_TOP_LEFT, TILE_BOTTOM_RIGHT, t)
            # a soft violet lift behind the mark keeps the dark tile from reading as flat black
            lift = _clamp(1.0 - math.hypot(px - lift_cx, py - lift_cy) / lift_r)
            r, g, b = _mix((r, g, b), TILE_LIFT, 0.22 * lift * lift)
            # a hairline of light along the top inner edge: depth without skeuomorphism
            inner = _clamp((highlight + d_bg) / highlight) if d_bg < 0 else 0.0
            r, g, b = _mix((r, g, b), WHITE, 0.10 * inner * _clamp((c - py) / half))

            d = mark_distance(px, py, s, variant)
            mr, mg, mb = _mix(MARK_TOP_LEFT, MARK_BOTTOM_RIGHT, t)
            if d > 0.0:  # outside: a tight bright rim plus a wider, weaker halo, both in the saturated hue
                gr, gg, gb = _mix(GLOW_TOP_LEFT, GLOW_BOTTOM_RIGHT, t)
                glow = 0.62 * math.exp(-d / edge_k) + 0.20 * math.exp(-d / halo_k)
                r, g, b = _mix((r, g, b), (gr, gg, gb), _clamp(glow))
            fg_a = _clamp(0.5 - d)
            r, g, b = _mix((r, g, b), (mr, mg, mb), fg_a)

            i = (yi * size + xi) * 4
            out[i:i + 4] = bytes((int(r + 0.5), int(g + 0.5), int(b + 0.5), int(bg_a * 255 + 0.5)))
    return bytes(out)


def png_rgba(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    stride = width * 4
    raw = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride] for y in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def png(size: int, rgba: bytes) -> bytes:
    return png_rgba(size, size, rgba)


def icns(pngs: dict[int, bytes]) -> bytes:
    # PNG-backed ICNS element types (macOS 10.7+)
    types = [(b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
             (b"ic10", 1024), (b"ic11", 32), (b"ic12", 64), (b"ic13", 256), (b"ic14", 512)]
    body = b"".join(tag + struct.pack(">I", len(pngs[sz]) + 8) + pngs[sz] for tag, sz in types)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def ico(pngs: dict[int, bytes]) -> bytes:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries, data = b"", b""
    for sz in sizes:
        blob = pngs[sz]
        entries += struct.pack("<BBBBHHII", sz % 256, sz % 256, 0, 0, 1, 32, len(blob), offset + len(data))
        data += blob
    return header + entries + data


def sheet(variant: str, sizes: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256)) -> bytes:
    """Every size at its true scale on one strip, over a light and a dark band."""
    pad, gap = 18, 18
    width = pad * 2 + sum(sizes) + gap * (len(sizes) - 1)
    band = max(sizes) + pad * 2
    height = band * 2
    canvas = bytearray()
    rows: list[bytearray] = [bytearray(b"\x00" * (width * 4)) for _ in range(height)]
    for yi in range(height):
        shade = (238, 238, 234) if yi < band else (26, 26, 28)
        row = rows[yi]
        for xi in range(width):
            row[xi * 4:xi * 4 + 4] = bytes((*shade, 255))
    for repeat in range(2):
        top = repeat * band
        x0 = pad
        for sz in sizes:
            rgba = render(sz, variant)
            y0 = top + pad + (max(sizes) - sz) // 2
            for yi in range(sz):
                row = rows[y0 + yi]
                for xi in range(sz):
                    i = (yi * sz + xi) * 4
                    sr, sg, sb, sa = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
                    if not sa:
                        continue
                    j = (x0 + xi) * 4
                    a = sa / 255.0
                    for k, sc in enumerate((sr, sg, sb)):
                        row[j + k] = int(row[j + k] * (1 - a) + sc * a + 0.5)
            x0 += sz + gap
    for row in rows:
        canvas += row
    return png_rgba(width, height, bytes(canvas))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--variant", choices=VARIANTS, default=DEFAULT_VARIANT)
    ap.add_argument("--preview", metavar="DIR",
                    help="render candidates into DIR instead of writing the shipped icons")
    args = ap.parse_args()

    if args.preview:
        out = Path(args.preview).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.variant}-256.png").write_bytes(png(256, render(256, args.variant)))
        (out / f"{args.variant}-sheet.png").write_bytes(sheet(args.variant))
        print(f"wrote {out}/{args.variant}-256.png and -sheet.png")
        return

    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
    pngs = {}
    for sz in sizes:
        pngs[sz] = png(sz, render(sz, args.variant))
        print(f"rendered {sz}px", file=sys.stderr)
    (OUT / "icon.png").write_bytes(pngs[1024])
    (OUT / "icon.icns").write_bytes(icns(pngs))
    (OUT / "icon.ico").write_bytes(ico(pngs))
    (OUT / "icon-256.png").write_bytes(pngs[256])
    print(f"wrote {OUT}/icon.png, icon.icns, icon.ico")


if __name__ == "__main__":
    main()
