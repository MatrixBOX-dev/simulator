import sys
import types
from pathlib import Path

import pytest

from matrixbox_simulator.device import run_app


class Restarted(Exception):
    pass


@pytest.fixture
def fake_execv(monkeypatch: pytest.MonkeyPatch) -> None:
    def execv(_path: str, _argv: list[str]) -> None:
        raise Restarted

    monkeypatch.setattr(run_app.os, "execv", execv)
    monkeypatch.delenv(run_app.RESUME_APP_ENV, raising=False)


def set_running_app(monkeypatch: pytest.MonkeyPatch, name: object) -> None:
    module = types.ModuleType("load_settings")
    module.app_running = name  # ty: ignore[unresolved-attribute]
    monkeypatch.setitem(sys.modules, "load_settings", module)


@pytest.mark.usefixtures("fake_execv")
def test_restart_resumes_the_running_app(monkeypatch: pytest.MonkeyPatch) -> None:
    set_running_app(monkeypatch, "clock")

    with pytest.raises(Restarted):
        run_app.restart_process()

    assert run_app.boot_app_name(None) == "clock"


@pytest.mark.usefixtures("fake_execv")
def test_restart_from_the_menu_boots_to_the_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(run_app.RESUME_APP_ENV, "stale")
    set_running_app(monkeypatch, False)

    with pytest.raises(Restarted):
        run_app.restart_process()

    assert run_app.boot_app_name(None) is None


@pytest.mark.usefixtures("fake_execv")
def test_resumed_app_wins_over_the_launched_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    set_running_app(monkeypatch, "weather")

    with pytest.raises(Restarted):
        run_app.restart_process()

    assert run_app.boot_app_name(Path("/apps/clock")) == "weather"
    # Consumed: a later boot without a restart falls back to the launch.
    assert run_app.boot_app_name(Path("/apps/clock")) == "clock"
