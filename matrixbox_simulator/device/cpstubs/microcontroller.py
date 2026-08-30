"""Stand-in for CircuitPython's `microcontroller`. Only the bits matrixbox
touches: setting the CPU frequency (a no-op without real hardware), and
reset(), which restarts the sim process the same way a real reboot would."""


class _Cpu:
    frequency: int = 0
    # No real chip to read from, but a plausible constant beats an
    # AttributeError for code that just reports it (e.g. telemetry headers).
    temperature: float = 25.0


class _Watchdog:
    timeout: int = 0
    mode: str | None = None

    def feed(self) -> None:
        pass


cpu = _Cpu()
watchdog = _Watchdog()


def reset() -> None:
    # Real hardware reboots, re-reading settings from scratch — most
    # notably panel geometry, which only ever applies at boot. Settings
    # persist across restart here too, so nothing an app saved is lost.
    from matrixbox_simulator.device import run_app

    run_app.restart_process()
