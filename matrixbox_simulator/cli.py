"""Single `matrixbox` entrypoint, with `app`, `simulator`, `screenshot`,
and `sandbox` as subcommands."""

import argparse

from matrixbox_simulator.device import run_app, run_sandbox, run_screenshot
from matrixbox_simulator.term import run_simulator


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
    run_simulator.build_parser(
        subparsers.add_parser(
            "simulator",
            help="terminal renderer that connects to a running app",
            description=run_simulator.__doc__,
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

    args = parser.parse_args()
    if args.command == "app":
        run_app.run(args)
    elif args.command == "screenshot":
        run_screenshot.run(args)
    elif args.command == "simulator":
        run_simulator.run(args)
    elif args.command == "sandbox":
        run_sandbox.run(args)
    else:
        raise AssertionError(f"unhandled command: {args.command!r}")


if __name__ == "__main__":
    main()
