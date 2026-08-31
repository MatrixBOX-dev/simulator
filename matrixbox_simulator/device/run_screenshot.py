"""Boots a single matrixbox app headlessly and saves one rendered frame to
a PNG, for CI smoke tests and PR previews rather than interactive use.

Usage:

    matrixbox screenshot clock --settings ci.json -o clock.png

Reuses `run_app`'s own staging (kernel detection, path sandboxing,
settings seeding) — screenshot mode differs only in what happens after
staging: no terminal, no button listener, no web UI, just wait for a
frame and write it out.
"""

import argparse
import io
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from PIL import Image

from matrixbox_simulator.device import frame_bridge, run_app
from matrixbox_simulator.sizes import ROTATION_OVERRIDES, SIZE_PRESETS


def build_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "app",
        help=(
            "app to screenshot: a directory name under matrixbox/apps (e.g. "
            "clock), or an absolute/relative path to any app directory"
        ),
    )
    parser.add_argument(
        "--settings",
        default=None,
        help=(
            "name of a settings file to seed with, resolved inside the "
            "app's own directory (e.g. --settings ci.json for "
            "<app>/ci.json). Optional: omitted, the app boots with plain "
            "defaults"
        ),
    )
    parser.add_argument(
        "--size", choices=sorted(SIZE_PRESETS), default=None, help="see `matrixbox app`"
    )
    parser.add_argument("--width", type=int, default=None, help="see `matrixbox app`")
    parser.add_argument("--height", type=int, default=None, help="see `matrixbox app`")
    parser.add_argument(
        "--after-frames",
        type=int,
        default=1,
        help="capture as soon as this many distinct frames have been drawn (default 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="give up waiting for --after-frames after this many seconds, "
        "capturing whatever the last drawn frame was instead (default 5.0)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=8,
        help="pixel scale factor for the output PNG: each device pixel is "
        "drawn as an NxN block (default 8)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="output PNG path (default: <app-name>.png in the current directory)",
    )
    parser.add_argument(
        "--refresh-fps", type=float, default=0.0, help="see `matrixbox app`"
    )
    parser.add_argument("--gamma", type=float, default=1.0, help="see `matrixbox app`")

    return parser


def _stage_for_screenshot(
    app_dir: Path,
    framework_root: Path,
    settings_src: Path | None,
    args: argparse.Namespace,
) -> tuple[str, Path]:
    """Stages `app_dir` fresh (whichever kernel style it uses) and seeds its
    settings.txt, either from `settings_src` or plain defaults. Returns the
    exec-ready (source, path) for the app's own entry point, ready for
    `run_app._exec_as_main`. Mirrors run_app's own
    _run_main_kernel/_run_package_kernel split, minus everything that's
    interactive-only or web-UI-only."""
    if run_app._is_monolithic_kernel(framework_root):
        return _stage_monolithic_app_for_screenshot(
            app_dir, framework_root, settings_src, args
        )

    return _stage_package_app_for_screenshot(
        app_dir, framework_root, settings_src, args
    )


def _stage_monolithic_app_for_screenshot(
    app_dir: Path,
    framework_root: Path,
    settings_src: Path | None,
    args: argparse.Namespace,
) -> tuple[str, Path]:
    staged_root = run_app._stage_checkout(framework_root, reset=True)
    run_app._install_path_sandbox(staged_root)
    run_app._install_chdir_path_tracking()
    run_app._install_lenient_bytes_import_hook(staged_root)

    settings_path = staged_root / "settings.txt"
    if settings_src is not None:
        settings_path.write_text(settings_src.read_text())

    run_app._seed_monolithic_settings(
        staged_root,
        args.width,
        args.height,
        app_name=app_dir.name,
        overwrite=args.geometry_explicit,
        rotation=args.rotation_override,
    )

    sys.path.insert(0, str(run_app.REPO_ROOT))
    sys.path.insert(0, str(staged_root))
    sys.path.insert(0, str(staged_root / "lib"))
    sys.path.insert(0, str(run_app.STUB_DIR))

    entry_path = staged_root / "main.py"
    os.chdir(staged_root)  # goes through tracked_chdir, seeds sys.path[0]

    return entry_path.read_text(), entry_path


def _stage_package_app_for_screenshot(
    app_dir: Path,
    framework_root: Path,
    settings_src: Path | None,
    args: argparse.Namespace,
) -> tuple[str, Path]:
    if not (app_dir / "code.py").exists():
        raise SystemExit(f"{app_dir} doesn't look like an app (no code.py)")

    staged_app_dir = run_app._stage_app(app_dir, reset=True)
    run_app._install_path_sandbox(run_app.SANDBOX_ROOT)

    # SANDBOX_ROOT (not staged_app_dir) is where a package-kernel app's
    # settings.txt actually lives, matching real hardware's single
    # flash-root settings file — see run_app._seed_settings. reset=True on
    # _stage_app above only wipes this app's own staged code, so drop any
    # leftover settings.txt from an earlier, unrelated run by hand:
    # screenshot mode always starts from a clean, known state.
    settings_path = run_app.SANDBOX_ROOT / "settings.txt"
    settings_path.unlink(missing_ok=True)
    if settings_src is not None:
        settings_path.write_text(settings_src.read_text())

    run_app._seed_settings(
        args.width,
        args.height,
        overwrite=args.geometry_explicit,
        rotation=args.rotation_override,
    )

    sys.path.insert(0, str(run_app.REPO_ROOT))
    sys.path.insert(0, str(framework_root))
    sys.path.insert(0, str(framework_root / "lib"))
    sys.path.insert(0, str(run_app.STUB_DIR))

    entry_path = staged_app_dir / "code.py"
    os.chdir(staged_app_dir)
    sys.path.insert(0, str(staged_app_dir))

    return entry_path.read_text(), entry_path


def _write_screenshot(
    path: Path, width: int, height: int, rgb: bytes, *, scale: int
) -> None:
    image = Image.frombytes("RGB", (width, height), rgb)
    if scale > 1:
        image = image.resize((width * scale, height * scale), Image.Resampling.NEAREST)

    # Image.save() reaches for builtins.open directly, which
    # _install_path_sandbox has redirected into the app's own sandboxed
    # filesystem — but this output path is a real one on the host, not
    # something the app itself wrote. Route around that the same way
    # settings.txt writes elsewhere do: through pathlib's own io.open,
    # which the sandbox patch never touches.
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buffer.getvalue())


def run(args: argparse.Namespace) -> None:
    args.geometry_explicit = (
        args.size is not None or args.width is not None or args.height is not None
    )
    args.rotation_override = (
        ROTATION_OVERRIDES.get(args.size, 0) if args.size is not None else None
    )

    if args.size is not None:
        args.width, args.height, _panels = SIZE_PRESETS[args.size]
    else:
        args.width = args.width if args.width is not None else 128
        args.height = args.height if args.height is not None else 32

    os.environ["MATRIXBOX_SIMULATOR_REFRESH_FPS"] = str(args.refresh_fps)
    os.environ["MATRIXBOX_SIMULATOR_GAMMA"] = str(args.gamma)

    app_dir = run_app._resolve_app_dir(args.app)
    framework_root = run_app._framework_root_for(app_dir)
    if not framework_root.exists():
        raise SystemExit(f"expected a matrixbox-style checkout at {framework_root}")

    settings_src = None
    if args.settings is not None:
        settings_src = app_dir / args.settings
        if not settings_src.is_file():
            raise SystemExit(f"no such settings file: {settings_src}")

        # Validated up front rather than left to run_app._seed_settings'
        # own _read_json: that swallows a parse error and falls back to
        # {}, reasonable for on-disk runtime state that might get
        # corrupted, but not for a file the caller explicitly asked to
        # seed with — a typo here should fail the CI job, not silently
        # boot with plain defaults instead.
        try:
            json.loads(settings_src.read_text())
        except (OSError, ValueError) as exc:
            raise SystemExit(f"invalid settings file {settings_src}: {exc}") from exc

    # Resolved against the real launch directory, before staging below
    # os.chdir()s into the sandbox — a relative --output would otherwise
    # land inside it instead of where the caller actually meant.
    output = (
        Path(args.output) if args.output is not None else Path(f"{app_dir.name}.png")
    ).resolve()

    run_app._patch_stdlib()
    source, entry_path = _stage_for_screenshot(
        app_dir, framework_root, settings_src, args
    )

    # Port 0: nothing ever connects to this server, it's only running so
    # framebufferio's refresh() (which bails out early with no server
    # started) has somewhere to publish to — the callback below is what
    # actually captures the frame. Letting the OS pick a free port avoids
    # colliding with a real `matrixbox app` or another screenshot run
    # already using the default 9191.
    frame_bridge.bridge.start("127.0.0.1", 0)

    capture_lock = threading.Lock()
    frame_ready = threading.Event()
    captured: dict[str, Any] = {"count": 0, "width": 0, "height": 0, "rgb": None}

    def on_publish(width: int, height: int, rgb: bytes) -> None:
        with capture_lock:
            captured["count"] += 1
            captured["width"] = width
            captured["height"] = height
            captured["rgb"] = rgb

        frame_ready.set()

    frame_bridge.bridge.on_publish = on_publish

    crashed: dict[str, BaseException | None] = {"error": None}

    def run_app_code() -> None:
        try:
            run_app._exec_as_main(source, entry_path)
        except SystemExit:
            pass
        except BaseException as exc:  # noqa: BLE001 - surfaced to the CI caller below
            crashed["error"] = exc

    app_thread = threading.Thread(target=run_app_code, daemon=True)
    app_thread.start()

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if crashed["error"] is not None:
            break

        with capture_lock:
            if captured["count"] >= args.after_frames:
                break

        frame_ready.wait(timeout=0.05)
        frame_ready.clear()

    frame_bridge.bridge.on_publish = None

    if crashed["error"] is not None:
        traceback.print_exception(crashed["error"])
        raise SystemExit(f"{app_dir.name} raised while rendering: {crashed['error']!r}")

    with capture_lock:
        count, width, height, rgb = (
            captured["count"],
            captured["width"],
            captured["height"],
            captured["rgb"],
        )

    if rgb is None:
        raise SystemExit(
            f"{app_dir.name} never drew a frame within {args.timeout:.1f}s"
        )

    _write_screenshot(output, width, height, rgb, scale=args.scale)
    print(
        f"matrixbox-simulator: wrote {output} ({width}x{height}, "
        f"{count} frame{'s' if count != 1 else ''} drawn, {args.scale}x scale)"
    )


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
