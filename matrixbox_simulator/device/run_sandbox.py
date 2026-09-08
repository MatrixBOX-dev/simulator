"""Inspect or clear the simulator's entire staged sandbox: every staged
checkout and app, plus whatever settings.txt/wifi creds got saved along
the way. Nothing precious lives there — it's all re-derived from the
real checkout on the next `matrixbox app`/`screenshot` run.

Usage:

    matrixbox sandbox info
    matrixbox sandbox clean
"""

import argparse
import shutil
from pathlib import Path

from matrixbox_simulator.device import run_app


def build_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    subparsers = parser.add_subparsers(dest="sandbox_command", required=True)
    subparsers.add_parser("info", help="show the sandbox's location and on-disk size")
    subparsers.add_parser("clean", help="delete the sandbox entirely")

    return parser


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024

    raise AssertionError("unreachable")


def _run_info() -> None:
    if not run_app.SANDBOX_ROOT.exists():
        print(f"matrixbox-simulator: sandbox: {run_app.SANDBOX_ROOT} (doesn't exist)")
        return

    size = _format_size(_dir_size(run_app.SANDBOX_ROOT))
    print(f"matrixbox-simulator: sandbox: {run_app.SANDBOX_ROOT} ({size})")


def _run_clean() -> None:
    if not run_app.SANDBOX_ROOT.exists():
        print(f"matrixbox-simulator: nothing to clean at {run_app.SANDBOX_ROOT}")
        return

    shutil.rmtree(run_app.SANDBOX_ROOT)
    print(f"matrixbox-simulator: cleaned {run_app.SANDBOX_ROOT}")


def run(args: argparse.Namespace) -> None:
    if args.sandbox_command == "info":
        _run_info()
    elif args.sandbox_command == "clean":
        _run_clean()
    else:
        raise AssertionError(f"unhandled sandbox command: {args.sandbox_command!r}")


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
