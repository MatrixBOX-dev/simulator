"""Stand-in for CircuitPython's `board`. There's no real hardware to name
pins for, so any attribute access (board.IO1, board.TX, ...) just returns a
sentinel identifying which pin was asked for.
"""


class _Pin:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return f"<sim pin {self.name}>"


def __getattr__(name: str) -> _Pin:
    return _Pin(name)
