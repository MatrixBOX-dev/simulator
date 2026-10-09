from pathlib import Path

import pytest
from PIL import Image

from matrixbox_simulator.device import run_screenshot
from matrixbox_simulator.term import wire
from matrixbox_simulator.web.capture import WebCapture


def lit_frame(width: int, height: int) -> wire.Frame:
    pixels = bytearray(width * height * 3)
    for x in range(width):
        offset = ((height // 2) * width + x) * 3
        pixels[offset : offset + 3] = b"\xff\xa0\x20"

    return wire.Frame(width=width, height=height, pixels=bytes(pixels))


def capture(style: str, tmp_path: Path) -> Image.Image:
    output = tmp_path / f"{style}.png"
    try:
        WebCapture(lit_frame(64, 32), style, led_size=4).save(output)
    except SystemExit as exit_error:
        pytest.skip(f"no headless browser available: {exit_error}")

    return Image.open(output)


def test_panel_style_is_the_housing_on_a_transparent_background(
    tmp_path: Path,
) -> None:
    image = capture("panel", tmp_path)

    # 64x32 LEDs plus a 3 LED housing on every side, at 4 px per LED.
    assert image.size == ((64 + 6) * 4, (32 + 6) * 4)
    assert image.mode == "RGBA"
    corner_alpha = image.getchannel("A").crop((0, 0, 1, 1)).getextrema()
    assert corner_alpha == (0, 0)  # rounded corner, see-through


def test_device_style_crops_to_the_device(tmp_path: Path) -> None:
    image = capture("device", tmp_path)
    width, height = image.size

    assert 64 * 4 < width < (64 + 90) * 4
    assert 32 * 4 < height < (32 + 90) * 4


def test_screenshot_defaults_to_raw_pixels() -> None:
    args = run_screenshot.build_parser().parse_args(["clock"])

    assert args.style == "pixels"
