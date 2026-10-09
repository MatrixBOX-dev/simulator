"""Single `matrixbox` entrypoint, with `app`, `simulator`, `screenshot`,
and `sandbox` as subcommands. `simulator` picks a renderer in turn, `web`
or `terminal`, defaulting to `web`."""

import argparse
import sys

from matrixbox_simulator.device import run_app, run_sandbox, run_screenshot
from matrixbox_simulator.term import run_simulator
from matrixbox_simulator.web import run_web

RENDERERS = ("web", "terminal")
DEFAULT_RENDERER = "web"


def main() -> None:
    parser = argparse.ArgumentParser(prog="matrixbox", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_app.build_parser(
        subparsers.add_parser(
            "app",
            help="run a real matrixbox app under a CircuitPython hardware shim",
            description=run_app.__doc__,
        )
    )
    simulator_parser = subparsers.add_parser(
        "simulator",
        help="renderer that connects to a running app (web by default)",
        description="Draws what a running `matrixbox app` shows, either in a "
        f"browser or the terminal. Defaults to `{DEFAULT_RENDERER}` when no "
        "renderer is named.",
    )
    renderers = simulator_parser.add_subparsers(dest="renderer", required=True)
    run_web.build_parser(
        renderers.add_parser(
            "web",
            help="browser renderer with glowing LEDs and a 3D device mockup",
            description=run_web.__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    )
    run_simulator.build_parser(
        renderers.add_parser(
            "terminal",
            help="terminal renderer using half-block characters",
            description=run_simulator.__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    )
    run_screenshot.build_parser(
        subparsers.add_parser(
            "screenshot",
            help="boot an app headlessly and save one rendered frame to a PNG",
            description=run_screenshot.__doc__,
        )
    )
    run_sandbox.build_parser(
        subparsers.add_parser(
            "sandbox",
            help="inspect or clear the simulator's staged sandbox",
            description=run_sandbox.__doc__,
        )
    )

    args = parser.parse_args(_with_default_renderer(sys.argv[1:]))
    if args.command == "app":
        run_app.run(args)
    elif args.command == "screenshot":
        run_screenshot.run(args)
    elif args.command == "simulator" and args.renderer == "web":
        run_web.run(args)
    elif args.command == "simulator" and args.renderer == "terminal":
        run_simulator.run(args)
    elif args.command == "sandbox":
        run_sandbox.run(args)
    else:
        raise AssertionError(f"unhandled command: {args.command!r}")


def _with_default_renderer(argv: list[str]) -> list[str]:
    # argparse has no default subcommand, so a bare `simulator` (or one
    # followed straight by options) gets the default renderer spliced in.
    if not argv or argv[0] != "simulator":
        return argv

    rest = argv[1:]
    if rest and (rest[0] in RENDERERS or rest[0] in ("-h", "--help")):
        return argv

    return ["simulator", DEFAULT_RENDERER, *rest]


if __name__ == "__main__":
    main()
