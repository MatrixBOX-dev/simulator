"""Web renderer for the matrixbox device simulator: draws the panel as
glowing LEDs in a browser, either flat or mounted in a mock enclosure.

Usage:

    matrixbox simulator

Then open http://127.0.0.1:8081/. Connects to ws://127.0.0.1:9191 by
default, same as the terminal renderer; --connect overrides that. No app
running? --demo draws an animated demo pattern instead:

    matrixbox simulator --demo
"""

import argparse
import signal
import sys
import time
import webbrowser
from collections.abc import Callable
from types import FrameType

from matrixbox_simulator.sizes import SIZE_PRESETS, panel_count_for
from matrixbox_simulator.term import wire
from matrixbox_simulator.term.demo import DemoSource
from matrixbox_simulator.term.run_simulator import (
    DEFAULT_CONNECT_URL,
    connect_with_retry,
)
from matrixbox_simulator.term.source import DISCONNECTED
from matrixbox_simulator.web.server import WebServer

DEFAULT_HOST = "127.0.0.1"
# One above the app's own settings page on 8080, so both can run at once.
DEFAULT_PORT = 8081


def build_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--connect",
        default=DEFAULT_CONNECT_URL,
        help=f"Frame server to connect to (default: {DEFAULT_CONNECT_URL}).",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Address to serve the page on (default: {DEFAULT_HOST}).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to serve the page on (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the page in the default browser once it's being served.",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Draw an animated demo pattern instead of connecting to a frame server.",
    )
    parser.add_argument(
        "--device",
        choices=sorted(SIZE_PRESETS),
        default=None,
        help="Real product size to assume for the demo pattern and placeholder "
        "panel. Errors if --width/--height are also given.",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Panel width in pixels for the demo pattern and placeholder panel.",
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
        width, height, _tiles = SIZE_PRESETS[args.device]
    else:
        width = args.width if args.width is not None else 128
        height = args.height if args.height is not None else 32

    tiles = panel_count_for(width, height)

    running = True

    def handle_sigint(_signum: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handle_sigint)

    def is_running() -> bool:
        return running

    try:
        server = WebServer(args.host, args.port)
    except OSError as err:
        raise SystemExit(f"can't serve on {args.host}:{args.port}: {err}") from err

    server.start()
    page_url = f"http://{args.host}:{server.port}/"
    print(f"matrixbox-simulator: serving {page_url}", file=sys.stderr)
    if args.open:
        webbrowser.open(page_url)

    try:
        if args.demo:
            run_demo(server, width, height, tiles, args.fps, is_running)
        else:
            placeholder = wire.Frame.blank(width, height, tiles=tiles)
            run_connected(server, args.connect, placeholder, is_running)
    finally:
        server.stop()


def run_demo(
    server: WebServer,
    width: int,
    height: int,
    tiles: int,
    fps: int,
    is_running: Callable[[], bool],
) -> None:
    source = DemoSource(width, height, tiles=tiles)
    frame_time = 1.0 / fps
    stats = wire.Stats(
        app="demo pattern", fps=float(fps), cpu_percent=None, rss_kb=None
    )
    server.publish_status(connected=True, message="demo pattern")

    while is_running():
        started = time.monotonic()
        server.publish(wire.encode_frame(source.next_frame()))
        server.publish(wire.encode_stats(stats))

        elapsed = time.monotonic() - started
        if elapsed < frame_time:
            time.sleep(frame_time - elapsed)


def run_connected(
    server: WebServer,
    url: str,
    placeholder: wire.Frame,
    is_running: Callable[[], bool],
) -> None:
    while is_running():
        server.publish(wire.encode_frame(placeholder))
        server.publish_status(
            connected=False, message=f"waiting for frame server at {url}..."
        )

        source = connect_with_retry(url, is_running)
        if source is None:
            break

        print(f"matrixbox-simulator: connected to {url}", file=sys.stderr)
        server.publish_status(connected=True, message=f"connected to {url}")
        server.on_control = source.send_text

        while is_running():
            message = source.next_message()
            if isinstance(message, bytes):
                try:
                    server.publish(message)
                except wire.DecodeError as err:
                    print(f"dropping malformed message: {err}", file=sys.stderr)
            elif message is DISCONNECTED:
                print(
                    "matrixbox-simulator: lost connection, waiting to reconnect...",
                    file=sys.stderr,
                )
                break

        server.on_control = None
        source.close()


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
