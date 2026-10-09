"""Simulated front-panel button state. A background keyboard listener calls
press(), a renderer's clickable button calls hold() and release(); the
digitalio pin stand-in calls is_pressed() on every button.poll() the same
way it'd read a real pin.

Apps only notice a press when two of their polls both see the pin down
(see matrixbox's own debounce), and a mouse click or a fixed-length key
press can come and go between two slow polls. So a release only takes
effect once the app has really observed the press.
"""

import threading
import time

# A renderer that vanishes mid-hold never sends its release, so a hold
# lets go by itself after this long rather than staying pressed forever.
MAX_HOLD_SECONDS = 10.0

# Just over matrixbox's own 40 ms noise filter, so an observed press is
# never thrown away as a bounce.
OBSERVED_SPAN_SECONDS = 0.05

_lock = threading.Lock()
_pressed = False
_release_at = 0.0
_first_read_at: float | None = None
_last_read_at: float | None = None


def press(duration_seconds: float) -> None:
    _start(time.monotonic() + duration_seconds)


def hold() -> None:
    press(MAX_HOLD_SECONDS)


def release() -> None:
    global _release_at
    with _lock:
        if _pressed:
            _release_at = min(_release_at, time.monotonic())


def is_pressed() -> bool:
    global _pressed, _first_read_at, _last_read_at
    with _lock:
        if not _pressed:
            return False

        now = time.monotonic()
        if now >= _release_at and _was_observed():
            _pressed = False
            return False

        _first_read_at = _first_read_at if _first_read_at is not None else now
        _last_read_at = now

        return True


def _start(release_at: float) -> None:
    global _pressed, _release_at, _first_read_at, _last_read_at
    with _lock:
        _pressed = True
        _release_at = release_at
        _first_read_at = None
        _last_read_at = None


def _was_observed() -> bool:
    if _first_read_at is None or _last_read_at is None:
        return False

    return _last_read_at - _first_read_at >= OBSERVED_SPAN_SECONDS
