import time

import pytest

from matrixbox_simulator.device import button_input, run_app


def test_every_terminal_key_maps_to_a_handled_control(
    capsys: pytest.CaptureFixture[str],
) -> None:
    unsafe = {"reload", "cycle_size"}  # these restart the process
    for control in set(run_app.CONTROL_KEYS.values()) - unsafe:
        run_app.handle_control(control)

    assert "unknown control" not in capsys.readouterr().out


def test_button_down_holds_until_button_up() -> None:
    run_app.handle_command('{"control": "button_down"}')
    assert button_input.is_pressed()
    time.sleep(0.06)
    assert button_input.is_pressed()

    run_app.handle_command('{"control": "button_up"}')
    assert not button_input.is_pressed()


def test_cycle_size_runs_the_given_callback() -> None:
    calls: list[str] = []
    run_app.handle_command('{"control": "cycle_size"}', lambda: calls.append("cycled"))

    assert calls == ["cycled"]


@pytest.mark.parametrize("message", ["not json", "[]", '{"other": 1}'])
def test_malformed_commands_are_ignored(
    message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    run_app.handle_command(message)

    assert "malformed command" in capsys.readouterr().out
