---
description: "claudectl memory: Claude/tools"
globs: "tools/**"
---
# Claude/tools — Dev-only asset-generation scripts that render claudectl's promo GIFs (hand-drawn preview and real headless-browser capture of the architecture graph) and the multi-size app icon using Pillow and Playwright.
- tools (module) — Collection of dev-only scripts for generating documentation and branding assets; not runtime dependencies.
- make_gifs.py (component) — Recreates the architecture graph's look (dodecahedra nodes, glowing curved edges, particles) from a hardcoded example structure and writes docs/graph.gif.
- capture_graph_gif.py (component) — Drives headless Chromium over the real connections-graph.html, screenshots the animated canvas frame by frame, and assembles docs/graph-real.gif.
- make_icon.py (component) — Generates claudectl's multi-size .ico app icon: rounded navy gradient tile with a glowing cyan 'C' and node accents.
- Pillow (service) — Imaging library used for drawing frames, gradients, glow effects, quantizing, and saving GIF/ICO output.
- Playwright (service) — Browser automation library that launches headless Chromium to screenshot the live graph canvas.
- connections-graph.html (model) — Rendered interactive architecture-graph HTML file whose live canvas animation is captured to GIF.
- graph.gif (model) — Hand-drawn promo GIF preview of the architecture graph written to docs/.
- graph-real.gif (model) — Animated GIF assembled from screenshots of the real graph render, written to docs/.
- claudectl.ico (model) — Multi-size (16-256px) application icon written at the repo root.
- cluster layout (concept) — Example workspace structure of module clusters, importance-sized nodes, and dependency edges used to fake the graph preview.
Relations: tools contains make_gifs.py; tools contains capture_graph_gif.py; tools contains make_icon.py; make_gifs.py uses Pillow; make_gifs.py uses cluster layout; make_gifs.py implements dodecahedron wireframe; make_gifs.py contains graph.gif; capture_graph_gif.py uses Playwright; capture_graph_gif.py uses Pillow; capture_graph_gif.py depends_on connections-graph.html; capture_graph_gif.py contains graph-real.gif; capture_graph_gif.py implements GIF quantization; make_icon.py uses Pillow; make_icon.py contains claudectl.ico
