import time

from matrixbox_simulator.device import button_input


def poll_after(seconds: float) -> bool:
    time.sleep(seconds)

    return button_input.is_pressed()


def test_click_shorter_than_the_poll_interval_is_still_seen() -> None:
    button_input.hold()
    button_input.release()

    assert poll_after(0.0) is True
    assert poll_after(0.06) is True
    assert poll_after(0.0) is False


def test_release_waits_until_two_polls_have_seen_the_press() -> None:
    button_input.press(0.01)

    assert poll_after(0.2) is True
    assert poll_after(0.0) is True  # too soon after the first poll to count
    assert poll_after(0.06) is True
    assert poll_after(0.0) is False


def test_hold_stays_pressed_until_released() -> None:
    button_input.hold()

    assert poll_after(0.0) is True
    assert poll_after(0.1) is True
    assert poll_after(0.1) is True

    button_input.release()
    assert poll_after(0.0) is False


def test_untouched_button_reads_released() -> None:
    button_input.hold()
    button_input.release()
    poll_after(0.0)
    poll_after(0.06)
    poll_after(0.0)

    assert button_input.is_pressed() is False
