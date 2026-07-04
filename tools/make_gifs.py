"""Dev-only: render a promo GIF of the architecture graph — a faithful preview
of the live HTML canvas (rotating wireframe dodecahedra, per-cluster bubbles,
glowing curved edges, flowing particles on a dark neural field). NOT a runtime
dependency and NOT a screen recording — it recreates the graph's look from a
small example structure (claudectl's own module names; no user data).

    py tools/make_gifs.py

Requires Pillow. Writes docs/graph.gif.
"""

import math
import os

from PIL import Image, ImageDraw, ImageFilter

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs')
W, H = 720, 400
FRAMES = 44
BG_TOP = (10, 16, 30)
BG_BOT = (3, 5, 11)

# example workspace: clusters (project modules) → nodes, sized by "importance"
CLUSTERS = [
    ('memory',      (200, 150), (125, 211, 252), [('recall', 15), ('lessons', 11),
                                                  ('graph', 9), ('digest', 7)]),
    ('connections', (500, 130), (138, 180, 248), [('hierarchy', 14), ('deps', 10),
                                                  ('render', 8)]),
    ('ui',          (250, 300), (167, 139, 250), [('menu', 12), ('hub', 9),
                                                  ('themes', 7), ('pager', 6)]),
    ('agents',      (520, 310), (94, 234, 212),  [('suggest', 10), ('library', 8),
                                                  ('hooks', 6)]),
]
# dependency edges between node labels (for glow curves + particles)
EDGES = [('recall', 'graph'), ('recall', 'lessons'), ('graph', 'hierarchy'),
         ('hierarchy', 'deps'), ('deps', 'render'), ('menu', 'hub'),
         ('hub', 'recall'), ('suggest', 'graph'), ('hub', 'themes'),
         ('library', 'hooks'), ('digest', 'recall'), ('menu', 'suggest')]

PHI = 1.6180339887
_DV = [(1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1),
       (-1, -1, 1), (-1, -1, -1), (0, 1 / PHI, PHI), (0, 1 / PHI, -PHI),
       (0, -1 / PHI, PHI), (0, -1 / PHI, -PHI), (1 / PHI, PHI, 0), (1 / PHI, -PHI, 0),
       (-1 / PHI, PHI, 0), (-1 / PHI, -PHI, 0), (PHI, 0, 1 / PHI), (PHI, 0, -1 / PHI),
       (-PHI, 0, 1 / PHI), (-PHI, 0, -1 / PHI)]
_DV = [tuple(c / math.sqrt(3) for c in v) for v in _DV]
_DE = []
_mn = min(math.dist(_DV[i], _DV[j]) for i in range(20) for j in range(i + 1, 20))
for i in range(20):
    for j in range(i + 1, 20):
        if math.dist(_DV[i], _DV[j]) < _mn * 1.1:
            _DE.append((i, j))


def _bg():
    img = Image.new('RGB', (W, H))
    px = img.load()
    cx, cy = W / 2, H / 2
    maxd = math.hypot(cx, cy)
    for y in range(H):
        for x in range(0, W, 2):
            t = math.hypot(x - cx, y - cy) / maxd
            c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
            px[x, y] = c
            if x + 1 < W:
                px[x + 1, y] = c
    return img


def _positions():
    pos = {}
    for _name, (cx, cy), col, nodes in CLUSTERS:
        n = len(nodes)
        for k, (lbl, imp) in enumerate(nodes):
            a = 2 * math.pi * k / n
            r = 46 + n * 4
            pos[lbl] = (cx + math.cos(a) * r, cy + math.sin(a) * r, imp, col)
    return pos


def _dodec(draw, x, y, rad, col, T, ph):
    ax, ay = T * 0.9 + ph, T * 0.66 + ph * 1.7
    ca, sa, cb, sb = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay)
    P = []
    for vx, vy, vz in _DV:
        x1 = vx * cb + vz * sb
        z1 = -vx * sb + vz * cb
        y2 = vy * ca - z1 * sa
        P.append((x + x1 * rad, y + y2 * rad))
    for i, j in _DE:
        draw.line([P[i], P[j]], fill=col, width=1)
    for pxp, pyp in P:
        draw.ellipse([pxp - 1.4, pyp - 1.4, pxp + 1.4, pyp + 1.4], fill=col)


def _qpt(a, b, t):
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    nx, ny = -(b[1] - a[1]), (b[0] - a[0])
    L = math.hypot(nx, ny) or 1
    cx, cy = mx + nx / L * 26, my + ny / L * 26
    u = 1 - t
    return (u * u * a[0] + 2 * u * t * cx + t * t * b[0],
            u * u * a[1] + 2 * u * t * cy + t * t * b[1])


def _frame(fi, base, pos):
    T = fi / FRAMES * 2 * math.pi
    img = base.copy()
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    # cluster bubbles
    for _name, (cx, cy), col, nodes in CLUSTERS:
        rr = 60 + len(nodes) * 8
        gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                   fill=(col[0], col[1], col[2], 16))
    # edges + node halos on the glow layer
    for a, b in EDGES:
        if a in pos and b in pos:
            pa, pb = pos[a][:2], pos[b][:2]
            pts = [_qpt(pa, pb, k / 12) for k in range(13)]
            gd.line(pts, fill=(150, 190, 255, 60), width=1)
    for lbl, (x, y, imp, col) in pos.items():
        r = 6 + imp * 1.6
        halo = int(r * 2.4)
        gd.ellipse([x - halo, y - halo, x + halo, y + halo],
                   fill=(col[0], col[1], col[2], 40))
    glow = glow.filter(ImageFilter.GaussianBlur(7))
    img = Image.alpha_composite(img.convert('RGBA'), glow)

    d = ImageDraw.Draw(img)
    # flow particles
    for ei, (a, b) in enumerate(EDGES):
        if a in pos and b in pos:
            t = ((fi / FRAMES) * 1.4 + ei * 0.13) % 1.0
            px_, py_ = _qpt(pos[a][:2], pos[b][:2], t)
            d.ellipse([px_ - 2, py_ - 2, px_ + 2, py_ + 2], fill=(210, 235, 255))
    # rotating dodecahedra + labels
    for lbl, (x, y, imp, col) in pos.items():
        r = 6 + imp * 1.5
        _dodec(d, x, y, r, col, T, hash(lbl) % 100 / 10.0)
        d.text((x + r + 3, y - 5), lbl, fill=(200, 214, 235))
    # title
    d.text((16, 14), "claudectl  architecture graph", fill=(150, 190, 255))
    return img.convert('RGB')


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    base = _bg()
    pos = _positions()
    frames = [_frame(i, base, pos) for i in range(FRAMES)]
    frames = [f.quantize(colors=128, method=Image.MEDIANCUT) for f in frames]
    out = os.path.join(OUT_DIR, 'graph.gif')
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=70, loop=0, optimize=True, disposal=2)
    print(f"wrote {out}  ({os.path.getsize(out) // 1024} KB, {FRAMES} frames)")


if __name__ == '__main__':
    main()
