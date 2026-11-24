"""Render a few parameter combinations and report timing (development helper)."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from molpov import sdl  # noqa: E402
from molpov.render import Renderer, preview_quality  # noqa: E402
from molpov.structure import read_structure  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(name, structure, params, quality, renderer, outdir):
    started = time.perf_counter()
    outcome = renderer.render(structure, params, quality)
    total = (time.perf_counter() - started) * 1000
    if not outcome.ok:
        print(f"{name:28s} FAILED {outcome.error}")
        return
    path = os.path.join(outdir, f"bench_{name}.png")
    outcome.save(path)
    print(f"{name:28s} {quality.width}x{quality.height}  "
          f"render {outcome.elapsed_ms:7.0f} ms  total {total:7.0f} ms  -> {path}")


def main() -> None:
    structure = read_structure(os.path.join(ROOT, "samples", "caffeine.sdf"))
    renderer = Renderer(os.path.join(ROOT, "molpov", "wsl_server.py"))
    outdir = os.path.join(ROOT, "_out")
    os.makedirs(outdir, exist_ok=True)
    aspect = 4 / 3
    base = sdl.SceneParams().for_style()

    def q(width, extra=(), radiosity=False):
        quality = preview_quality(width, aspect)
        quality.radiosity = radiosity
        quality.extra = tuple(extra)
        return quality

    run("a_640_base", structure, base, q(640), renderer, outdir)
    run("b_640_dither_off", structure, base, q(640, ["Dither=off"]), renderer, outdir)
    no_jitter = sdl.replace(base, jitter=False)
    run("c_640_nojitter", structure, no_jitter, q(640), renderer, outdir)
    run("d_640_5x5", structure, sdl.replace(base, area_samples=5),
        q(640), renderer, outdir)
    run("e_480_base", structure, base, q(480), renderer, outdir)
    renderer.close()


if __name__ == "__main__":
    main()
