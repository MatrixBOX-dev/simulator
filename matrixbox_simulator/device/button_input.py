"""Simulated front-panel button state. A background keyboard listener calls
press(); the digitalio pin stand-in reads pressed_until on every
button.poll() the same way it'd read a real pin.
"""

import time

pressed_until = 0.0


def press(duration_seconds: float) -> None:
    global pressed_until
    pressed_until = time.monotonic() + duration_seconds


def is_pressed() -> bool:
    return time.monotonic() < pressed_until
