"""Dev-only: generate the DESKTOP GUI app icon (distinct from the TUI's
navy-tile claudectl.ico — this is the GUI brand inverted: the app's
cyan→violet gradient tile with a dark "C"). Run manually to regenerate:

    py tools/make_gui_icon.py

Requires Pillow. Writes claudectl-gui.ico (multi-size) at the repo root.
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

from make_icon import _rounded_mask, SIZES

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'claudectl-gui.ico')
SS = 1024

CYAN = (125, 207, 255)       # #7dcfff — GUI gradient start
VIOLET = (138, 92, 246)      # #8a5cf6 — GUI gradient end
DARK = (13, 17, 23)          # #0d1117 — the GUI's --bg / on-accent ink
WHITE = (240, 246, 255)


def _diag_gradient(size, a, b):
    g = Image.new('RGB', (size, size))
    px = g.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return g


def draw_icon():
    pad = int(SS * 0.04)
    inner = SS - pad * 2
    radius = int(SS * 0.22)

    base = Image.new('RGBA', (SS, SS), (0, 0, 0, 0))
    tile = _diag_gradient(inner, CYAN, VIOLET).convert('RGBA')
    tile.putalpha(_rounded_mask(inner, radius))
    sheen = Image.new('RGBA', (inner, inner), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).rounded_rectangle([0, 0, inner - 1, int(inner * 0.5)],
                                            radius=radius, fill=(255, 255, 255, 34))
    tile = Image.alpha_composite(tile, sheen)
    base.alpha_composite(tile, (pad, pad))

    layer = Image.new('RGBA', (SS, SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cx = cy = SS / 2
    R = SS * 0.27
    w = int(SS * 0.13)
    box = [cx - R, cy - R, cx + R, cy + R]
    a0, a1 = 52, 308
    d.arc(box, a0, a1, fill=DARK, width=w)
    nodes = []
    for ang in (a0, a1, 180):
        nx = cx + R * math.cos(math.radians(ang))
        ny = cy + R * math.sin(math.radians(ang))
        nodes.append((nx, ny))
        d.ellipse([nx - w / 2, ny - w / 2, nx + w / 2, ny + w / 2], fill=DARK)
    nr = w * 0.38
    for (nx, ny) in nodes:
        d.ellipse([nx - nr, ny - nr, nx + nr, ny + nr], fill=WHITE)

    shadow = layer.filter(ImageFilter.GaussianBlur(SS * 0.015))
    base = Image.alpha_composite(base, shadow)
    base = Image.alpha_composite(base, layer)

    full_mask = Image.new('L', (SS, SS), 0)
    full_mask.paste(_rounded_mask(inner, radius), (pad, pad))
    base.putalpha(full_mask)

    big = base.resize((max(SIZES), max(SIZES)), Image.LANCZOS)
    big.save(OUT, format='ICO', sizes=[(s, s) for s in SIZES])
    print(f"wrote {OUT}  ({', '.join(str(s) for s in SIZES)})")


if __name__ == '__main__':
    draw_icon()
