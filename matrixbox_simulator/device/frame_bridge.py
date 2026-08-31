"""Bridges the shimmed framebufferio.FramebufferDisplay to the frame server:
broadcasts an already-composited RGB canvas in the wire format the
renderer decodes. Also tracks and broadcasts a lightweight stats sidecar
(app name, measured FPS, real CPU load if matrixbox.stats happens to
already be loaded, sim process peak RSS). This is purely a debugging aid;
real hardware would never send this.
"""

import struct
import sys
import time
from collections.abc import Callable

try:
    import resource
except ImportError:
    # POSIX-only: _read_rss_kb() just reports no RSS figure when unavailable.
    resource = None

from matrixbox_simulator.device import wsserver

_FPS_SMOOTHING = 0.25
_STATS_HAS_CPU_PERCENT = 0b01
_STATS_HAS_RSS_KB = 0b10

# Caps how often publish() actually broadcasts a *changed* frame, so a
# genuinely fast-updating app can't flood the terminal renderer with more
# frames than it can coherently draw. Identical frames are always skipped
# regardless of this — see the comment in publish() — so this only bounds
# real visual change, not how often an app happens to call refresh().
_MIN_PUBLISH_INTERVAL = 1.0 / 120


class FrameBridge:
    def __init__(self) -> None:
        self._server: wsserver.FrameServer | None = None
        self._app_name = ""
        self._tiles = 1
        self._last_publish_time: float | None = None
        self._last_broadcast_time: float | None = None
        self._last_rgb: bytes | None = None
        self._smoothed_interval: float | None = None

        # Set by headless callers (screenshot mode) that need a composited
        # frame directly, without standing up a real renderer to decode it
        # back off the wire.
        self.on_publish: Callable[[int, int, bytes], None] | None = None

    def start(self, host: str = "127.0.0.1", port: int = 9191) -> wsserver.FrameServer:
        if self._server is None:
            self._server = wsserver.FrameServer(host, port)

        return self._server

    def set_app_name(self, name: str) -> None:
        self._app_name = name

    def set_tiles(self, tiles: int) -> None:
        # How many equal-width physical panels make up the whole display
        # (e.g. matrixbox's X is 2, XL is 3), for the renderer's optional
        # tile-boundary overlay — not visible on the real device, purely a
        # design aid, off by default. Carried in the frame header's
        # previously-unused reserved byte rather than a separate message,
        # since every frame already needs to state it and it never changes
        # mid-run.
        self._tiles = tiles

    def publish_blank(self, width: int, height: int) -> None:
        # Real hardware shows black until something's drawn. Seed the same
        # placeholder here so a renderer connecting before (or during) an
        # app's slow on_start() sees a correctly-sized black panel, not
        # nothing.
        if self._server is None:
            return

        header = struct.pack("<BBHHBB", 0xF3, 1, width, height, 0, self._tiles)
        self._server.broadcast(header + bytes(width * height * 3), kind="frame")

    def publish(self, width: int, height: int, rgb: bytes) -> None:
        if self._server is None:
            return

        if rgb == self._last_rgb:
            # Nothing actually changed since the last broadcast: real
            # hardware wouldn't show anything different either. Some apps
            # call refresh() many times per logical step as their own
            # speed control, with identical content between calls —
            # broadcasting each one anyway means paying real socket-write
            # cost for frames carrying no new information.
            return

        self._last_rgb = rgb

        now = time.monotonic()
        if (
            self._last_broadcast_time is not None
            and now - self._last_broadcast_time < _MIN_PUBLISH_INTERVAL
        ):
            return  # dropped: too soon since the last actual broadcast

        self._last_broadcast_time = now

        header = struct.pack("<BBHHBB", 0xF3, 1, width, height, 0, self._tiles)
        self._server.broadcast(header + bytes(rgb), kind="frame")

        if self.on_publish is not None:
            self.on_publish(width, height, rgb)

        self._publish_stats()

    def _publish_stats(self) -> None:
        if self._server is None:
            return

        now = time.monotonic()
        if self._last_publish_time is not None:
            interval = now - self._last_publish_time
            if interval > 0:
                self._smoothed_interval = (
                    interval
                    if self._smoothed_interval is None
                    else self._smoothed_interval
                    + (interval - self._smoothed_interval) * _FPS_SMOOTHING
                )
        self._last_publish_time = now

        fps = 1.0 / self._smoothed_interval if self._smoothed_interval else 0.0
        cpu_percent = _read_cpu_percent()
        rss_kb = _read_rss_kb()

        flags = 0
        body = bytearray(struct.pack("<f", fps))
        if cpu_percent is not None:
            flags |= _STATS_HAS_CPU_PERCENT
            body += struct.pack("<f", cpu_percent)

        if rss_kb is not None:
            flags |= _STATS_HAS_RSS_KB
            body += struct.pack("<I", rss_kb)

        app_bytes = self._app_name.encode("utf-8")[:255]
        header = struct.pack("<BBBB", 0xF4, 1, flags, len(app_bytes))
        self._server.broadcast(header + app_bytes + bytes(body), kind="stats")


def _read_cpu_percent() -> float | None:
    # Only read it if the running app already imported it (every matrixbox
    # app does, transitively, via matrixbox.app). Never trigger an import
    # ourselves, since that would be reaching into a framework this sim
    # doesn't otherwise assume is present.
    stats_module = sys.modules.get("matrixbox.stats")
    if stats_module is None:
        return None

    try:
        return float(stats_module.cpu_percent())
    except Exception:
        return None


def _read_rss_kb() -> int | None:
    if resource is None:
        return None

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux, so normalize to KB.
    return int(peak / 1024) if sys.platform == "darwin" else int(peak)


# One frame bridge for the whole process, matching real hardware: there's
# only ever one display, one WS server, one app running at a time. The
# display object is instantiated by app code, so it can't have a bridge
# injected into its constructor without diverging from the real
# CircuitPython API it mimics — both it and the device process's own
# startup reach this the same way, through one shared instance.
bridge = FrameBridge()
