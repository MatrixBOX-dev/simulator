"""Stand-in for CircuitPython's `digitalio`. Used here only for the front
panel button; every pin reads as idle-high (pulled up, not pressed) except
the button's own pin, which reflects the simulated button press triggered
from the keyboard.
"""

from matrixbox_simulator.device import button_input


class Direction:
    INPUT = "INPUT"
    OUTPUT = "OUTPUT"


class Pull:
    UP = "UP"
    DOWN = "DOWN"


class DigitalInOut:
    def __init__(self, pin: object) -> None:
        self.pin = pin
        self.direction: str | None = None
        self.pull: str | None = None

    @property
    def value(self) -> bool:
        if getattr(self.pin, "name", None) == "RX":
            return not button_input.is_pressed()

        return True
