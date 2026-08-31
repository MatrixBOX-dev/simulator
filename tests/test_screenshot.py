"""Integration tests for `matrixbox screenshot`, run as real subprocesses
against small fixture apps under the package-kernel layout (no main.py).
The command's own staging does enough process-global monkeypatching
(sys.modules, builtins.open, os.chdir) that driving it in-process would
mean fighting that instead of testing it, so a subprocess is the only
way to see it the way a CI job actually would.
"""

import json
import subprocess
import sys
from pathlib import Path

from PIL import Image

_SOLID_FRAME_APP = """
import json

import displayio
import framebufferio
import rgbmatrix

try:
    with open("/settings.txt") as f:
        settings = json.loads(f.read())
except OSError:
    settings = {}

width = settings.get("width", 64)
height = settings.get("height", 32)
color = 0x00FF00 if settings.get("theme") == "green" else 0xFF0000

matrix = rgbmatrix.RGBMatrix(width=width, height=height)
display = framebufferio.FramebufferDisplay(matrix)

bitmap = displayio.Bitmap(width, height, 1)
palette = displayio.Palette(1)
palette[0] = color
tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
group = displayio.Group()
group.append(tile_grid)
display.root_group = group
display.refresh()

while True:
    pass
"""

_MULTI_FRAME_APP = """
import time

import displayio
import framebufferio
import rgbmatrix

matrix = rgbmatrix.RGBMatrix(width=64, height=32)
display = framebufferio.FramebufferDisplay(matrix)

bitmap = displayio.Bitmap(64, 32, 2)
palette = displayio.Palette(2)
palette[0] = 0x000000
palette[1] = 0x0000FF
tile_grid = displayio.TileGrid(bitmap, pixel_shader=palette)
group = displayio.Group()
group.append(tile_grid)
display.root_group = group

for i in range(5):
    bitmap[0, 0] = i % 2
    display.refresh()
    time.sleep(0.05)

while True:
    time.sleep(0.1)
"""

_NEVER_DRAWS_APP = """
import time

while True:
    time.sleep(0.1)
"""

_CRASHES_APP = 'raise RuntimeError("boom")\n'


def _make_app(tmp_path: Path, name: str, code: str) -> Path:
    # <root>/apps/<name> is the layout _framework_root_for() recognizes;
    # anything else falls back to the real ../matrixbox sibling checkout,
    # which won't exist in CI.
    app_dir = tmp_path / "fakefw" / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / "code.py").write_text(code)

    return app_dir


def _run_screenshot(
    *args: str, cwd: Path | None = None, timeout: float = 15.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "matrixbox_simulator.cli", "screenshot", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=timeout,
    )


def test_captures_a_drawn_frame_with_default_settings(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "solid", _SOLID_FRAME_APP)
    output = tmp_path / "out.png"

    result = _run_screenshot(str(app_dir), "-o", str(output))

    assert result.returncode == 0, result.stderr
    image = Image.open(output)
    assert image.size == (128 * 8, 32 * 8)  # default panel size, default 8x scale
    assert image.getpixel((0, 0)) == (255, 0, 0)  # no --settings: app's own default


def test_settings_file_is_resolved_inside_the_app_directory(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "solid", _SOLID_FRAME_APP)
    (app_dir / "ci.json").write_text(json.dumps({"theme": "green"}))
    output = tmp_path / "out.png"

    result = _run_screenshot(str(app_dir), "--settings", "ci.json", "-o", str(output))

    assert result.returncode == 0, result.stderr
    assert Image.open(output).getpixel((0, 0)) == (0, 255, 0)


def test_missing_settings_file_fails_fast(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "solid", _SOLID_FRAME_APP)

    result = _run_screenshot(
        str(app_dir), "--settings", "nope.json", "-o", str(tmp_path / "out.png")
    )

    assert result.returncode != 0
    assert "no such settings file" in result.stderr


def test_invalid_settings_json_fails_fast(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "solid", _SOLID_FRAME_APP)
    (app_dir / "ci.json").write_text("{not json")

    result = _run_screenshot(
        str(app_dir), "--settings", "ci.json", "-o", str(tmp_path / "out.png")
    )

    assert result.returncode != 0
    assert "invalid settings file" in result.stderr


def test_relative_output_path_resolves_against_the_launch_directory(
    tmp_path: Path,
) -> None:
    # Regression test: staging os.chdir()s into the app's own sandbox
    # before the frame is written, so a relative --output must be
    # resolved against the caller's cwd *before* that happens, not
    # whatever the sandbox's cwd is by the time the file gets written.
    app_dir = _make_app(tmp_path, "solid", _SOLID_FRAME_APP)
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    result = _run_screenshot(str(app_dir), "-o", "out.png", cwd=workdir)

    assert result.returncode == 0, result.stderr
    assert (workdir / "out.png").is_file()


def test_after_frames_caps_how_many_frames_are_waited_for(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "multi", _MULTI_FRAME_APP)

    result = _run_screenshot(
        str(app_dir), "--after-frames", "3", "-o", str(tmp_path / "out.png")
    )

    assert result.returncode == 0, result.stderr
    assert "3 frames drawn" in result.stdout


def test_an_app_that_never_draws_times_out_with_a_nonzero_exit(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "never", _NEVER_DRAWS_APP)

    result = _run_screenshot(
        str(app_dir), "--timeout", "1", "-o", str(tmp_path / "out.png")
    )

    assert result.returncode != 0
    assert "never drew a frame" in result.stderr


def test_a_crashing_app_exits_nonzero_with_the_error_surfaced(tmp_path: Path) -> None:
    app_dir = _make_app(tmp_path, "crashy", _CRASHES_APP)

    result = _run_screenshot(str(app_dir), "-o", str(tmp_path / "out.png"))

    assert result.returncode != 0
    assert "raised while rendering" in result.stderr
    assert "boom" in result.stderr
