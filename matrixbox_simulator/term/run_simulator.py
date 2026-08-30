"""Terminal renderer for the matrixbox device simulator.

Usage:

    matrixbox simulator --connect ws://127.0.0.1:9191

No simulator running? Omit --connect to run an animated demo pattern
instead:

    matrixbox simulator
"""

import argparse
import contextlib
import select
import signal
import sys
import time
from collections.abc import Callable, Iterator
from types import FrameType

from matrixbox_simulator.sizes import SIZE_PRESETS, panel_count_for
from matrixbox_simulator.term.demo import DemoSource
from matrixbox_simulator.term.render import TerminalRenderer
from matrixbox_simulator.term.source import DISCONNECTED, IDLE, WsSource
from matrixbox_simulator.term.wire import Frame, Stats

try:
    import termios
    import tty
except ImportError:
    # POSIX-only: the tile-border toggle just doesn't work when
    # unavailable — _key_listener() checks for this.
    termios = None  # ty: ignore[invalid-assignment]
    tty = None

# How long to wait between connection attempts, whether the server hasn't
# started yet or the previous connection just dropped (for example the
# simulator restarting to switch apps).
RECONNECT_DELAY_SECONDS = 0.3


def build_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--connect",
        default=None,
        help="Frame server to connect to, e.g. ws://127.0.0.1:9191. Omit to run "
        "an animated demo pattern instead, useful for exercising the renderer "
        "without the simulator running.",
    )
    parser.add_argument(
        "--device",
        choices=sorted(SIZE_PRESETS),
        default=None,
        help="Real product size to assume for the demo pattern and placeholder "
        "box (see --width). Errors if --width/--height are also given.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Panel width in pixels: the demo pattern's size, and the size of "
        "the placeholder box shown before a real frame has arrived (or arrives "
        "again after a disconnect). See --device.",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=None,
        help="Panel height in pixels, see --width.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=20,
        help="Frames per second, demo mode only.",
    )

    return parser


def run(args: argparse.Namespace) -> None:
    if args.device is not None and (args.width is not None or args.height is not None):
        raise SystemExit("--device can't be combined with --width/--height")

    if args.device is not None:
        args.width, args.height, _tiles = SIZE_PRESETS[args.device]
    else:
        args.width = args.width if args.width is not None else 128
        args.height = args.height if args.height is not None else 32

    # Only meaningful for the demo pattern and the placeholder box: a real
    # connected sim always sends its own actual panel count over the
    # wire, which takes over the moment a real frame arrives.
    tiles = panel_count_for(args.width, args.height)

    running = True

    def handle_sigint(_signum: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sigint)

    def is_running() -> bool:
        return running

    renderer = TerminalRenderer()
    try:
        with _key_listener(renderer):
            if args.connect:
                run_connected(
                    args.connect, args.width, args.height, tiles, renderer, is_running
                )
            else:
                run_demo(args.width, args.height, tiles, args.fps, renderer, is_running)
    finally:
        renderer.stop()


def main() -> None:
    run(build_parser().parse_args())


@contextlib.contextmanager
def _key_listener(renderer: TerminalRenderer) -> Iterator[None]:
    # 't' toggles the tile-boundary overlay live. No-ops when stdin isn't
    # a real terminal (e.g. output redirected) or termios/tty aren't
    # available at all (non-POSIX platforms) — same guard the device
    # process's own button listener uses.
    if not sys.stdin.isatty() or termios is None:
        yield
        return

    import threading

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)  # ty: ignore[unresolved-attribute]
    stop = threading.Event()

    def listen() -> None:
        while not stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not ready:
                continue

            char = sys.stdin.read(1)
            if char == "t":
                # No separate announcement: it'd print outside the
                # renderer's own cursor-controlled redraw region, so it
                # can overlap or linger through later frames instead of
                # ever being cleanly overwritten. The stats line already
                # redrawn on every frame reflects the new state instead.
                renderer.toggle_tile_borders()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    print("matrixbox-simulator: press 't' to toggle tile borders", file=sys.stderr)

    try:
        yield
    finally:
        stop.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_demo(
    width: int,
    height: int,
    tiles: int,
    fps: int,
    renderer: TerminalRenderer,
    is_running: Callable[[], bool],
) -> None:
    source = DemoSource(width, height, tiles=tiles)
    frame_time = 1.0 / fps
    stats = Stats(app="demo pattern", fps=float(fps), cpu_percent=None, rss_kb=None)

    while is_running():
        started = time.monotonic()
        renderer.render(source.next_frame(), stats)

        elapsed = time.monotonic() - started
        if elapsed < frame_time:
            time.sleep(frame_time - elapsed)


def run_connected(
    url: str,
    width: int,
    height: int,
    tiles: int,
    renderer: TerminalRenderer,
    is_running: Callable[[], bool],
) -> None:
    # Shown before any real frame has arrived, including while there's no
    # sim running at all yet, so the panel's bounds are always visible
    # rather than depending on server round-trip timing.
    placeholder = Frame.blank(width, height, tiles=tiles)
    renderer.render(placeholder, None)

    while is_running():
        source = connect_with_retry(url, is_running)
        if source is None:
            break  # shut down while still waiting to connect

        # Leading newline: the renderer's last render() left the cursor at
        # the end of its own stats line with no trailing newline (so the
        # *next* frame can redraw in place without leaving a blank line
        # behind) — printing here without one would land this status
        # message stuck onto the end of that same line instead of its own.
        print(f"\nmatrixbox-simulator: connected to {url}", file=sys.stderr)
        renderer.clear()  # drop any stale frame from a differently-sized previous app
        latest_stats: Stats | None = None

        while is_running():
            message = source.next_frame()
            if isinstance(message, Frame):
                renderer.render(message, latest_stats)
            elif isinstance(message, Stats):
                latest_stats = message
            elif message is IDLE:
                pass
            elif message is DISCONNECTED:
                print(
                    "\nmatrixbox-simulator: lost connection, waiting to reconnect...",
                    file=sys.stderr,
                )
                break

        source.close()

        if is_running():
            renderer.clear()
            renderer.render(placeholder, None)


def connect_with_retry(url: str, is_running: Callable[[], bool]) -> WsSource | None:
    """Retries WsSource(url) until it succeeds or is_running() goes false.
    Covers both a server that hasn't started yet and one that restarted."""
    announced = False

    while is_running():
        try:
            return WsSource(url)
        except Exception:
            if not announced:
                print(
                    f"\nmatrixbox-simulator: waiting for frame server at {url}...",
                    file=sys.stderr,
                )
                announced = True

            time.sleep(RECONNECT_DELAY_SECONDS)

    return None


if __name__ == "__main__":
    main()
