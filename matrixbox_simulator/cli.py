"""Single `matrixbox` entrypoint, with `app` and `simulator` as subcommands."""

import argparse

from matrixbox_simulator.device import run_app
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

    args = parser.parse_args()
    if args.command == "app":
        run_app.run(args)
    else:
        run_simulator.run(args)


if __name__ == "__main__":
    main()
