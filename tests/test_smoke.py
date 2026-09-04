"""Placeholder so `pytest` has something to collect (a bare `tests/`
directory exits nonzero, which CI treats as a failure). Delete once real
tests exist again.
"""

from matrixbox_simulator.device import run_app


def test_build_parser_accepts_the_app_argument() -> None:
    args = run_app.build_parser().parse_args(["clock"])

    assert args.app == "clock"
