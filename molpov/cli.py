"""Command line front end: inspect, dump SDL and render without the GUI."""

from __future__ import annotations

import argparse
import os
import sys
import time

from . import sdl
from .render import (
    FINAL_WIDTH,
    Renderer,
    final_quality,
    full_command_quality,
    preview_quality,
)
from .structure import read_structure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="molpov",
        description="Read a molecular structure and render it with POV-Ray.",
    )
    parser.add_argument("structure", nargs="?",
                        help="input .xyz / .pdb / .mol / .sdf file")
    parser.add_argument("--pov", help="write the generated POV-Ray scene here")
    parser.add_argument("--render", help="render to this PNG file")
    parser.add_argument("--render-pov", metavar="SCENE.pov",
                        help="render an existing .pov with Avogadro's full "
                             "command line (+Q9 +A +AM2 +UA), into --render")
    parser.add_argument("--height", type=int, default=None,
                        help="output height (default: 3/4 of --width)")
    parser.add_argument("--width", type=int, default=FINAL_WIDTH,
                        help="output width in pixels (default: %(default)s)")
    parser.add_argument("--style", default="ball_stick",
                        choices=list(sdl.STYLES))
    parser.add_argument("--palette", default="cpk", choices=list(sdl.PALETTES))
    parser.add_argument("--preset", choices=["studio", "avogadro"],
                        help="coherent look preset (material + light + background)")
    parser.add_argument("--material", default=None, choices=list(sdl.MATERIALS))
    parser.add_argument("--background", default=None, choices=list(sdl.BACKGROUNDS))
    parser.add_argument("--no-floor", action="store_true")
    parser.add_argument("--no-hydrogens", action="store_true",
                        help="hide hydrogen atoms and their bonds")
    parser.add_argument("--no-radiosity", action="store_true")
    parser.add_argument("--transparency", type=float, default=0.0,
                        help="0 = opaque, 0.5 = half transparent (rgbt)")
    parser.add_argument("--no-union", action="store_true",
                        help="emit loose primitives instead of one union")
    parser.add_argument("--headlight", action="store_true",
                        help="add a parallel light fixed to the camera")
    parser.add_argument("--headlight-intensity", type=float, default=0.45)
    parser.add_argument("--color", action="append", default=[], metavar="SYM=RRGGBB",
                        help="override an element colour, e.g. --color C=ff8800")
    parser.add_argument("--preview", action="store_true",
                        help="fast preview quality (no antialiasing)")
    parser.add_argument("--full-command", action="store_true",
                        help="render with Avogadro's complete switch set "
                             "(+Q9 +A +AM2 +UA)")
    parser.add_argument("--backend", choices=["auto", "native", "wsl", "gui"],
                        default="auto")
    parser.add_argument("--povray", metavar="EXE",
                        help="explicit POV-Ray executable (povray.exe or pvengine64.exe)")
    parser.add_argument("--backend-info", action="store_true",
                        help="print the detected POV-Ray back end and exit")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    renderer = Renderer(os.path.join(here, "wsl_server.py"),
                        prefer=None if args.backend == "auto" else args.backend,
                        exe_path=args.povray)

    if args.backend_info:
        print(f"backend : {renderer.backend_name()}")
        print(f"detail  : {renderer.describe()}")
        print(f"native  : {renderer.native}")
        print(f"wsl     : {renderer.distro}")
        print(f"gui     : {renderer.gui}")
        return 0

    if args.render_pov:
        if not args.render:
            build_parser().error("--render-pov needs --render <out.png>")
        width = args.width
        height = args.height or int(round(width * 0.75))
        started = time.perf_counter()
        outcome = renderer.render_pov_file(args.render_pov, width, height)
        if not outcome.ok:
            print(f"render failed: {outcome.error}", file=sys.stderr)
            return 2
        outcome.save(args.render)
        print(f"  rendered {width}x{height} via {outcome.backend} "
              f"in {outcome.elapsed_ms:.0f} ms (total "
              f"{(time.perf_counter() - started) * 1000:.0f} ms)")
        print(f"  command: {outcome.log}")
        print(f"  wrote {args.render} ({len(outcome.png)} bytes)")
        renderer.close()
        return 0

    if not args.structure:
        build_parser().error("a structure file is required")

    structure = read_structure(args.structure)
    print(f"{os.path.basename(args.structure)}: {structure.summary()}")
    print(f"  centre {tuple(round(c, 2) for c in structure.center())}  "
          f"radius {structure.radius():.2f} A")

    params = sdl.SceneParams(
        style=args.style,
        palette=args.palette,
        radiosity=not args.no_radiosity,
        transparency=args.transparency,
        pack_scene=not args.no_union,
    )
    if args.preset:
        sdl.apply_preset(params, args.preset)
    if args.material:
        params.material = args.material
    if args.background:
        params.background = args.background
    if args.no_floor:
        params.floor = False
    if args.no_hydrogens:
        params.hide_hydrogens = True
    for spec in args.color:
        symbol, _, value = spec.partition("=")
        value = value.lstrip("#")
        if len(value) != 6 or not symbol:
            print(f"ignoring colour override {spec!r}", file=sys.stderr)
            continue
        params.custom_colors[symbol] = (
            int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    if params.custom_colors:
        params.palette = "custom"
    params = params.for_style()

    params.light.headlight = args.headlight
    params.light.headlight_intensity = args.headlight_intensity

    if args.pov:
        sdl.write_pov(structure, params, args.pov)
        print(f"  wrote {args.pov}")

    if args.render:
        if args.full_command:
            height = args.height or int(round(args.width * 0.75))
            quality = full_command_quality(args.width, height)
        elif args.preview:
            quality = preview_quality(args.width)
        else:
            quality = final_quality(args.width)
        started = time.perf_counter()
        outcome = renderer.render(structure, params, quality)
        total = (time.perf_counter() - started) * 1000
        if not outcome.ok:
            print(f"render failed: {outcome.error}", file=sys.stderr)
            if outcome.log:
                print(outcome.log[-2000:], file=sys.stderr)
            return 2
        outcome.save(args.render)
        print(f"  rendered {quality.width}x{quality.height} via {outcome.backend} "
              f"in {outcome.elapsed_ms:.0f} ms (total {total:.0f} ms)")
        print(f"  wrote {args.render} ({len(outcome.png)} bytes)")
    renderer.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
