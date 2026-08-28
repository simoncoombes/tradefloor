"""Build the site's icons from the pretium mark.

    python tools/docs/learn/make_icons.py

The old `docs/favicon.svg` is a bar-chart glyph in rust orange. It belongs
to the previous identity: the learning path's mark is a stylised P and its
accent is a desaturated teal, so the tab icon and the page it opens were
showing two different products.

This traces `mark-pretium.png` - the mark as the design ships it - into a
vector outline, sets it on the brand's accent the way the old icon set its
glyph, and writes the four files a browser asks for:

    favicon.svg          what modern browsers use
    favicon.ico          16, 32 and 48, for the ones that ask for /favicon.ico
    apple-touch-icon.png 180, for an iOS home screen
    icon-512.png         for a web app manifest

The trace is marching squares over the alpha channel, then Ramer-Douglas-
Peucker to take the staircase off. The mark is straight lines at 45 and 90
degrees plus one bowl curve, so a polyline at sub-pixel tolerance is exact
where it matters and close enough on the curve at any size a tab icon is
ever drawn.

Rasterising is done by Chrome, because it is the renderer the icons will be
seen through and it is already a dependency of `verify.cjs`. The PNG writer
and the ICO container are here rather than in a library so the tool has no
dependencies beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
MARK = HERE / "handoff" / "mark-pretium.png"

#: The accent and the ground, from the design's token set. The icon is the
#: mark knocked out of a rounded square in the accent - the shape the old
#: favicon used, which is what keeps a tab strip legible at 16 pixels.
ACCENT = "#0E6B65"
GROUND = "#FAFBFA"

#: Fraction of the icon the mark occupies. Below about 0.6 the P reads as a
#: dot at 16px; above about 0.75 it touches the corner radius.
INSET = 0.66
RADIUS = 0.22          # of the icon's width


# ------------------------------------------------------------------ PNG codec

def read_png(path: pathlib.Path):
    data = path.read_bytes()
    at, idat, width, height, colour = 8, [], 0, 0, 0
    while at < len(data):
        length = struct.unpack(">I", data[at:at + 4])[0]
        kind = data[at + 4:at + 8].decode("ascii")
        chunk = data[at + 8:at + 8 + length]
        if kind == "IHDR":
            width, height = struct.unpack(">II", chunk[:8])
            if chunk[8] != 8:
                sys.exit("the mark is not 8 bits per channel")
            colour = chunk[9]
        elif kind == "IDAT":
            idat.append(chunk)
        elif kind == "IEND":
            break
        at += 12 + length

    channels = {6: 4, 2: 3, 0: 1}.get(colour)
    if channels is None:
        sys.exit(f"unsupported PNG colour type {colour}")

    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    out = bytearray(height * stride)
    pos = 0
    for y in range(height):
        filt = raw[pos]
        pos += 1
        line = raw[pos:pos + stride]
        pos += stride
        row = out[y * stride:(y + 1) * stride]
        prev = out[(y - 1) * stride:y * stride] if y else bytes(stride)
        for i in range(stride):
            a = row[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if (y and i >= channels) else 0
            v = line[i]
            if filt == 1:
                v += a
            elif filt == 2:
                v += b
            elif filt == 3:
                v += (a + b) >> 1
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                v += a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
            row[i] = v & 0xFF
        out[y * stride:(y + 1) * stride] = row
    return width, height, channels, out


# ----------------------------------------------------------------- the trace

def alpha_channel(width, height, channels, pixels) -> list[float]:
    """The mark's alpha, one value per pixel, softened.

    The mark ships hard-edged: two alpha values, 0 and 255, and nothing in
    between. Interpolating a crossing against those gives back the pixel
    midpoint, which is a staircase - invisible at the 15 by 22 the design
    draws it at, and plainly wrong on a 512-pixel app icon.

    A small blur puts the edge back. Where the source steps by one pixel, the
    blurred values cross the threshold along a line, and the interpolation
    finds it. A true corner rounds by about a pixel in 296, which is below
    what any of these sizes can show.
    """
    if channels != 4:
        sys.exit("the mark has no alpha channel to trace")
    flat = [float(pixels[i * channels + 3]) for i in range(width * height)]
    return _blur(flat, width, height)


#: A five-tap Gaussian, applied separably. Wide enough to bridge a one-pixel
#: step, narrow enough to leave a 45-degree edge straight.
_KERNEL = (1.0, 4.0, 6.0, 4.0, 1.0)


def _blur(values, width, height):
    total = sum(_KERNEL)
    half = len(_KERNEL) // 2

    pass_one = [0.0] * (width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            acc = 0.0
            for k, weight in enumerate(_KERNEL):
                xx = min(width - 1, max(0, x + k - half))
                acc += values[row + xx] * weight
            pass_one[row + x] = acc / total

    out = [0.0] * (width * height)
    for y in range(height):
        for x in range(width):
            acc = 0.0
            for k, weight in enumerate(_KERNEL):
                yy = min(height - 1, max(0, y + k - half))
                acc += pass_one[yy * width + x] * weight
            out[y * width + x] = acc / total
    return out


#: Marching squares.
#:
#: The cell's four corners are bits: 8 top-left, 4 top-right, 2 bottom-right,
#: 1 bottom-left. Its four edges are 0 top, 1 right, 2 bottom, 3 left. An
#: edge is crossed when the two corners it joins disagree, and the crossings
#: are joined in pairs - so the table is derived rather than remembered, and
#: is built here rather than written out, because writing it out by hand is
#: how it comes to have seven of its fifteen cases wrong.
_CORNERS = {0: (8, 4), 1: (4, 2), 2: (2, 1), 3: (1, 8)}

#: Which two cell corners each edge runs between, as (dx, dy) offsets.
_ENDS = {
    0: ((0, 0), (1, 0)),   # top:    top-left  -> top-right
    1: ((1, 0), (1, 1)),   # right:  top-right -> bottom-right
    2: ((1, 1), (0, 1)),   # bottom: bottom-right -> bottom-left
    3: ((0, 1), (0, 0)),   # left:   bottom-left  -> top-left
}


def _edge_table():
    table = {}
    for code in range(16):
        crossed = [e for e, (a, b) in _CORNERS.items()
                   if bool(code & a) != bool(code & b)]
        if len(crossed) == 2:
            table[code] = [tuple(crossed)]
        elif len(crossed) == 4:
            # The saddle: two corners set on one diagonal. Either pairing is
            # a valid contour; the mark has no one-pixel isthmus, so the
            # choice cannot separate anything that should stay joined.
            table[code] = [(0, 1), (2, 3)] if code == 5 else [(0, 3), (1, 2)]
    return table


_EDGES = _edge_table()


def contours(alpha, width, height, threshold=128.0):
    """Closed outlines of the shape, at sub-pixel accuracy.

    The crossing on each cell edge is interpolated against the alpha values
    at its two ends rather than taken at the midpoint. The mark is drawn
    anti-aliased, so those values carry where the edge really falls; snapping
    to midpoints instead throws that away and leaves the bowl of the P
    visibly stepped at any size above a tab icon.

    The segment graph is undirected. Marching squares can be made to emit
    consistently wound edges, but only by getting the two saddle cases and
    the corner conventions exactly right; walking an undirected graph and
    refusing to step straight back where you came from gives the same loops
    without depending on any of that.
    """
    def at(x, y):
        if 0 <= x < width and 0 <= y < height:
            return alpha[y * width + x]
        return 0.0

    def crossing(edge, x, y):
        (ax, ay), (bx, by) = _ENDS[edge]
        va, vb = at(x + ax, y + ay), at(x + bx, y + by)
        t = 0.5 if va == vb else (threshold - va) / (vb - va)
        t = min(1.0, max(0.0, t))
        return (round(x + ax + (bx - ax) * t, 4),
                round(y + ay + (by - ay) * t, 4))

    adjacent: dict[tuple, list] = {}

    def link(a, b):
        adjacent.setdefault(a, []).append(b)
        adjacent.setdefault(b, []).append(a)

    for y in range(-1, height):
        for x in range(-1, width):
            code = (8 if at(x, y) > threshold else 0) | \
                   (4 if at(x + 1, y) > threshold else 0) | \
                   (2 if at(x + 1, y + 1) > threshold else 0) | \
                   (1 if at(x, y + 1) > threshold else 0)
            for a, b in _EDGES.get(code, []):
                link(crossing(a, x, y), crossing(b, x, y))

    def unlink(a, b):
        if b in adjacent.get(a, []):
            adjacent[a].remove(b)
        if a in adjacent.get(b, []):
            adjacent[b].remove(a)
        for point in (a, b):
            if not adjacent.get(point):
                adjacent.pop(point, None)

    loops = []
    while adjacent:
        start = next(iter(adjacent))
        loop = [start]
        here, came_from = start, None
        while True:
            options = [p for p in adjacent.get(here, []) if p != came_from]
            if not options:
                break
            step = options[0]
            unlink(here, step)
            if step == start:
                break
            loop.append(step)
            came_from, here = here, step
        if len(loop) > 8:
            loops.append(loop)
    return loops


def _rdp(points, tolerance):
    """Ramer-Douglas-Peucker on an open chain, iteratively so a long outline
    cannot recurse deeper than the interpreter allows."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        worst, at = 0.0, None
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            else:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            if d > worst:
                worst, at = d, i
        if at is not None and worst > tolerance:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [p for p, k in zip(points, keep) if k]


def simplify(loop, tolerance):
    """The same, for a closed loop.

    A closed loop cannot be simplified in one pass: its first and last
    points are the same, the line between them has no direction, and every
    point measures zero distance from it - so the whole outline collapses to
    its start. Splitting at the point furthest from the start gives two open
    chains, each of which has a direction.
    """
    if len(loop) < 4:
        return list(loop)
    ax, ay = loop[0]
    far = max(range(1, len(loop)),
              key=lambda i: (loop[i][0] - ax) ** 2 + (loop[i][1] - ay) ** 2)
    first = _rdp(loop[:far + 1], tolerance)
    second = _rdp(loop[far:] + [loop[0]], tolerance)
    return first + second[1:-1]


def outline_path(tolerance=0.12):
    """The mark as one SVG path, in its own pixel space."""
    width, height, channels, pixels = read_png(MARK)
    alpha = alpha_channel(width, height, channels, pixels)
    parts = []
    for loop in contours(alpha, width, height):
        loop = simplify(loop, tolerance)
        if len(loop) < 4:
            continue
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in loop)
        parts.append("M" + pts.replace(" ", "L", 1).replace(" ", " L") + "Z")
    return width, height, " ".join(parts)


# ------------------------------------------------------------------- the SVG

def svg(size=32) -> str:
    width, height, path = outline_path()
    # Fit the mark's height into the inset box and centre it.
    scale = (size * INSET) / height
    tx = (size - width * scale) / 2
    ty = (size - height * scale) / 2
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="pretium">\n'
        f'  <!-- Traced from tools/docs/learn/handoff/mark-pretium.png by\n'
        f'       tools/docs/learn/make_icons.py. Do not edit: regenerate. -->\n'
        f'  <rect width="{size}" height="{size}" rx="{size * RADIUS:.2f}" fill="{ACCENT}"/>\n'
        f'  <g transform="translate({tx:.3f} {ty:.3f}) scale({scale:.5f})">\n'
        f'    <path d="{path}" fill="{GROUND}" fill-rule="evenodd"/>\n'
        f'  </g>\n'
        f'</svg>\n'
    )


# ----------------------------------------------------------- rasterising, ICO

def write_png(path: pathlib.Path, width, height, rgba: bytes) -> None:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload +
                struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def render(chrome: str, source: pathlib.Path, size: int, out: pathlib.Path) -> None:
    """Screenshot the SVG at an exact pixel size.

    Chrome is the renderer these icons will be seen through, so it is the
    one that should draw them: a rasteriser that disagreed with it about the
    corner radius or the curve would be a difference nobody would find until
    the icon looked wrong in a tab.
    """
    with tempfile.TemporaryDirectory() as tmp:
        page = pathlib.Path(tmp) / "icon.html"
        page.write_text(
            "<!doctype html><meta charset=utf-8>"
            "<style>html,body{margin:0;padding:0;background:transparent}"
            f"img{{display:block;width:{size}px;height:{size}px}}</style>"
            f'<img src="{source.name}">', encoding="utf-8")
        shutil.copy(source, pathlib.Path(tmp) / source.name)
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--default-background-color=00000000",
             f"--window-size={size},{size}", "--virtual-time-budget=2000",
             f"--screenshot={out}", str(page)],
            check=True, capture_output=True,
        )


def write_ico(path: pathlib.Path, pngs: list[tuple[int, bytes]]) -> None:
    """An ICO is a directory of images; since Vista each may be a PNG, which
    is what every browser that still asks for favicon.ico accepts."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, blob in pngs:
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0,
                               size if size < 256 else 0, 0, 0, 1, 32,
                               len(blob), offset)
        blobs += blob
        offset += len(blob)
    path.write_bytes(header + entries + blobs)


#: The card a link to this site unfurls into on Slack, on a social site, or
#: in a chat window. Without one those all fall back to a bare URL, which is
#: the least persuasive form a link to a documentation site can take.
CARD = """<!doctype html><meta charset=utf-8>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:opsz,wght@8..60,400;8..60,600&family=Spline+Sans+Mono:wght@400;500&display=swap">
<style>
html,body{{margin:0;width:1200px;height:630px;background:{ground};overflow:hidden}}
main{{box-sizing:border-box;width:100%;height:100%;padding:88px 96px;
  display:flex;flex-direction:column;justify-content:space-between}}
.row{{display:flex;align-items:center;gap:20px}}
.row img{{width:44px;height:63px;display:block}}
.mark{{font-family:'Spline Sans Mono',monospace;font-size:40px;font-weight:500;
  letter-spacing:-0.01em;color:{ink}}}
h1{{font-family:'Source Serif 4',Georgia,serif;font-weight:600;font-size:78px;
  line-height:1.06;letter-spacing:-0.02em;color:{ink};margin:0;max-width:15ch}}
p{{font-family:'Spline Sans Mono',monospace;font-size:23px;letter-spacing:0.04em;
  color:{accent};margin:0}}
.rule{{height:10px;background:{accent};width:150px;border-radius:5px}}
</style>
<main>
  <div class="row"><img src="mark.png"><span class="mark">pretium</span></div>
  <h1>{headline}</h1>
  <div>
    <div class="rule" style="margin-bottom:26px"></div>
    <p>{strapline}</p>
  </div>
</main>
"""

HEADLINE = "A market you can run a strategy against"
STRAPLINE = "DETERMINISTIC \u00b7 A REAL ORDER BOOK \u00b7 MEASURED AGAINST REAL MARKETS"


def write_card(chrome: str, out: pathlib.Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        (work / "card.html").write_text(
            CARD.format(ground=GROUND, ink="#101A18", accent=ACCENT,
                        headline=HEADLINE, strapline=STRAPLINE),
            encoding="utf-8")
        shutil.copy(MARK, work / "mark.png")
        target = work / "card.png"
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--window-size=1200,630",
             "--virtual-time-budget=6000", f"--screenshot={target}",
             str(work / "card.html")],
            check=True, capture_output=True)
        shutil.copy(target, out / "og-card.png")
        print(f"wrote {out / 'og-card.png'} (1200x630)")


def write_manifest(out: pathlib.Path) -> None:
    """A web app manifest, so the icons have somewhere to be declared and an
    installed copy gets the right name rather than the page title."""
    (out / "site.webmanifest").write_text(json.dumps({
        "name": "pretium documentation",
        "short_name": "pretium",
        "start_url": "index.html",
        "display": "minimal-ui",
        "background_color": GROUND,
        "theme_color": ACCENT,
        "icons": [
            {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "any"},
            {"src": "apple-touch-icon.png", "sizes": "180x180", "type": "image/png"},
        ],
    }, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out / 'site.webmanifest'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(ROOT / "docs2" / "v2"),
                    help="where to write the icons")
    ap.add_argument("--chrome", default=os.environ.get(
        "CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    source = out / "favicon.svg"
    source.write_text(svg(), encoding="utf-8")
    print(f"wrote {source} ({len(source.read_text())} bytes)")

    with tempfile.TemporaryDirectory() as tmp:
        blobs = []
        for size in (16, 32, 48):
            png = pathlib.Path(tmp) / f"{size}.png"
            render(args.chrome, source, size, png)
            blobs.append((size, png.read_bytes()))
        write_ico(out / "favicon.ico", blobs)
        print(f"wrote {out / 'favicon.ico'} (16, 32, 48)")

        for size, name in ((180, "apple-touch-icon.png"), (512, "icon-512.png")):
            png = pathlib.Path(tmp) / f"{name}"
            render(args.chrome, source, size, png)
            shutil.copy(png, out / name)
            print(f"wrote {out / name} ({size}px)")

    write_card(args.chrome, out)
    write_manifest(out)


if __name__ == "__main__":
    main()
