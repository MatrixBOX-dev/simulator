"""Renders one frame through the web renderer's own page in a headless
browser and saves it as a PNG, so screenshots look exactly like the live
view. Needs Playwright (`pip install 'matrixbox-simulator[screenshot]'`).

Run as its own process by `matrixbox screenshot --style panel|device`,
with the frame's raw RGB bytes on stdin: the screenshot process itself
has file access patched into the app's sandbox, which a browser driver
can't run under.
"""

import argparse
import sys
from pathlib import Path

from matrixbox_simulator.sizes import panel_count_for
from matrixbox_simulator.term import wire
from matrixbox_simulator.web.server import WebServer

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    # Optional: only the panel and device screenshot styles need it.
    PlaywrightError = Exception  # ty: ignore[invalid-assignment]
    sync_playwright = None

STYLES = ("panel", "device")
THEMES = ("dark", "light")
READY_TIMEOUT_MS = 15000

# Room around the panel or device; the page crops to it anyway, this
# only has to be big enough to never cut into it.
_VIEWPORT_MARGIN_LEDS = {"panel": 16, "device": 90}


class WebCapture:
    def __init__(
        self, frame: wire.Frame, style: str, led_size: int, theme: str = "dark"
    ) -> None:
        self.frame = frame
        self.style = style
        self.led_size = led_size
        self.theme = theme

    def save(self, output: Path) -> None:
        server = WebServer("127.0.0.1", 0)
        server.start()
        server.publish(wire.encode_frame(self.frame))
        server.publish_status(connected=True, message="screenshot")

        try:
            self._screenshot(f"http://127.0.0.1:{server.port}/", output)
        finally:
            server.stop()

    def viewport_size(self) -> tuple[int, int]:
        margin = _VIEWPORT_MARGIN_LEDS[self.style]

        return (
            (self.frame.width + margin) * self.led_size,
            (self.frame.height + margin) * self.led_size,
        )

    def _screenshot(self, base_url: str, output: Path) -> None:
        if sync_playwright is None:
            raise SystemExit(
                "--style panel/device needs Playwright: "
                "pip install 'matrixbox-simulator[screenshot]'"
            )

        with sync_playwright() as playwright:
            try:
                # The installed Chrome first, so nothing extra has to be
                # downloaded, then Playwright's own Chromium.
                browser = playwright.chromium.launch(channel="chrome")
            except PlaywrightError:
                try:
                    browser = playwright.chromium.launch()
                except PlaywrightError as err:
                    raise SystemExit(
                        "no browser for --style panel/device: install Chrome, "
                        "or run `playwright install chromium`"
                    ) from err

            try:
                width, height = self.viewport_size()
                page = browser.new_page(
                    viewport={"width": width, "height": height},
                    color_scheme="light" if self.theme == "light" else "dark",
                )
                page.goto(
                    f"{base_url}?view={self.style}&theme={self.theme}"
                    f"&capture=1&led={self.led_size}"
                )
                page.wait_for_selector("body[data-ready]", timeout=READY_TIMEOUT_MS)
                clip = page.evaluate("window.matrixboxCaptureBounds()")
                output.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(output),
                    clip=clip,
                    omit_background=self.style == "panel",
                )
            finally:
                browser.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--style", choices=STYLES, required=True)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--led-size", type=int, default=8)
    parser.add_argument("--theme", choices=THEMES, default="dark")
    parser.add_argument("-o", "--output", required=True)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    rgb = sys.stdin.buffer.read()
    if len(rgb) != args.width * args.height * 3:
        raise SystemExit(
            f"expected {args.width * args.height * 3} bytes of RGB, got {len(rgb)}"
        )

    frame = wire.Frame(
        width=args.width,
        height=args.height,
        pixels=rgb,
        tiles=panel_count_for(args.width, args.height),
    )
    WebCapture(frame, args.style, args.led_size, args.theme).save(Path(args.output))


if __name__ == "__main__":
    main()
