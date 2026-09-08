"""Runs a real matrixbox app, unmodified, under a CircuitPython hardware
shim, streaming its display output to any connected renderer over a local
WebSocket.

Usage:

    matrixbox app clock

Then, in another terminal:

    matrixbox simulator --connect ws://127.0.0.1:9191
"""

import argparse
import ast
import binascii
import builtins
import collections
import contextlib
import gc
import importlib.machinery
import json
import os
import select
import shutil
import signal
import sys
import threading
import traceback
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from types import FrameType
from typing import Any, NoReturn, TextIO

from matrixbox_simulator.device import button_input, frame_bridge
from matrixbox_simulator.sizes import (
    ROTATION_OVERRIDES,
    SIZE_CYCLE,
    SIZE_PRESETS,
    panel_count_for,
    size_name_for,
)

try:
    import termios
    import tty
except ImportError:
    # POSIX-only; the button simulation just won't work without them.
    termios = None  # ty: ignore[invalid-assignment]
    tty = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MATRIXBOX_ROOT = REPO_ROOT.parent / "matrixbox"
STUB_DIR = Path(__file__).resolve().parent / "cpstubs"  # bundled, read-only


def _default_sandbox_root() -> Path:
    if xdg_cache := os.environ.get("XDG_CACHE_HOME"):
        cache_root = Path(xdg_cache)
    elif sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches"
    elif sys.platform == "win32" and (local_app_data := os.environ.get("LOCALAPPDATA")):
        cache_root = Path(local_app_data)
    else:
        cache_root = Path.home() / ".cache"

    return cache_root / "matrixbox-simulator" / "sandbox_fs"


SANDBOX_ROOT = _default_sandbox_root()

# The matrixbox device profile this sim pretends to be. Only the board
# name has to match matrixbox's own board-detection logic; the pins it
# resolves to are never touched by real hardware here.
SIM_MACHINE = "Waveshare ESP32-S3-Zero with ESP32S3"


def _patch_stdlib() -> None:
    # CircuitPython-only gc extensions matrixbox's own tick-load tracking
    # relies on.
    gc.mem_free = lambda: 200_000  # ty: ignore[unresolved-attribute]
    gc.mem_alloc = lambda: 50_000  # ty: ignore[unresolved-attribute]

    uname_result = collections.namedtuple(
        "uname_result", "sysname nodename release version machine"
    )
    os.uname = lambda: uname_result(  # ty: ignore[invalid-assignment]
        "circuitpython-sim", "matrixbox-simulator", "10.1.4", "sim", SIM_MACHINE
    )

    # CircuitPython/MicroPython-only; matrixbox's own request handler uses
    # it as a last line of defense around each request.
    def print_exception(exc: BaseException, file: TextIO = sys.stderr) -> None:
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=file)

    sys.print_exception = print_exception  # ty: ignore[unresolved-attribute]

    # CircuitPython's binascii functions accept a bare str (implicitly
    # UTF-8-encoding it) where CPython's require real bytes-like input.
    # Patch the functions, not bytes/bytearray themselves — those are
    # fundamental types checked via isinstance() throughout the
    # interpreter and stdlib, and patching them breaks pathlib internals.
    def _lenient(fn: Callable[..., object]) -> Callable[..., object]:
        def wrapper(data: bytes | str, *args: object, **kwargs: object) -> object:
            if isinstance(data, str):
                data = data.encode("utf-8")

            return fn(data, *args, **kwargs)

        return wrapper

    binascii.hexlify = _lenient(binascii.hexlify)  # ty: ignore[invalid-assignment]
    binascii.unhexlify = _lenient(binascii.unhexlify)  # ty: ignore[invalid-assignment]
    binascii.a2b_base64 = _lenient(binascii.a2b_base64)  # ty: ignore[invalid-assignment]
    binascii.b2a_base64 = _lenient(binascii.b2a_base64)  # ty: ignore[invalid-assignment]


def _install_path_sandbox(root: Path) -> None:
    # App code reads/writes absolute paths (e.g. "/settings.txt",
    # os.listdir("/")) assuming they're the device's flash root. Redirect
    # any absolute path under `root` instead, so app code can't touch the
    # real machine's filesystem. `root` is the staged checkout, whose own
    # directory listing must see all its apps together.
    root.mkdir(parents=True, exist_ok=True)
    root_str = str(root)
    real_open = builtins.open
    real_rename = os.rename
    real_listdir = os.listdir
    real_remove = os.remove
    real_chdir = os.chdir

    def resolve(path: Any) -> Any:
        # Idempotent: a path already inside root (e.g. a real filesystem
        # path our own code builds, not the simulated app's) is left
        # alone instead of getting root prepended a second time.
        if (
            isinstance(path, str)
            and path.startswith("/")
            and not path.startswith(root_str)
        ):
            return str(root / path.lstrip("/"))

        return path

    def sandboxed_open(path: Any, *args: Any, **kwargs: Any) -> object:
        return real_open(resolve(path), *args, **kwargs)

    def sandboxed_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        return real_rename(resolve(src), resolve(dst), *args, **kwargs)

    def sandboxed_listdir(path: Any = ".") -> list[str]:
        return real_listdir(resolve(path))

    def sandboxed_remove(path: Any, *args: Any, **kwargs: Any) -> None:
        return real_remove(resolve(path), *args, **kwargs)

    def sandboxed_chdir(path: Any) -> None:
        return real_chdir(resolve(path))

    builtins.open = sandboxed_open  # ty: ignore[invalid-assignment]
    os.rename = sandboxed_rename  # ty: ignore[invalid-assignment]
    os.listdir = sandboxed_listdir  # ty: ignore[invalid-assignment]
    os.remove = sandboxed_remove  # ty: ignore[invalid-assignment]
    os.chdir = sandboxed_chdir  # ty: ignore[invalid-assignment]


class _BytesLiteralWrapper(ast.NodeTransformer):
    """Rewrites every bytes literal (b"...") into a call wrapping it in a
    bytes subclass that tolerates being concatenated with a str, since
    plain bytes can't be patched to allow that."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if isinstance(node.value, bytes):
            call = ast.Call(
                func=ast.Attribute(
                    value=ast.Name(
                        id="__matrixbox_simulator_lenient_bytes__", ctx=ast.Load()
                    ),
                    attr="wrap",
                    ctx=ast.Load(),
                ),
                args=[node],
                keywords=[],
            )
            return ast.copy_location(call, node)

        return node


def _compile_with_lenient_bytes(source: str, path: str) -> types.CodeType:
    tree = ast.parse(source, filename=path)
    tree = _BytesLiteralWrapper().visit(tree)

    import_stmt = ast.Import(
        names=[
            ast.alias(
                name="matrixbox_simulator.device.lenient_bytes",
                asname="__matrixbox_simulator_lenient_bytes__",
            )
        ]
    )
    import_stmt.lineno = 1
    import_stmt.col_offset = 0
    tree.body.insert(0, import_stmt)
    ast.fix_missing_locations(tree)

    return compile(tree, path, "exec")


def _exec_as_main(source: str, path: Path) -> None:
    # Some app code does `from __main__ import *`, which resolves via
    # sys.modules["__main__"], a real module object, not whatever dict
    # exec() happens to use as globals. Swap in a real module for the
    # duration of the run so that pattern actually sees what this exec()
    # populates, matching how CircuitPython's own __main__ works.
    module = types.ModuleType("__main__")
    module.__file__ = str(path)
    previous = sys.modules.get("__main__")
    sys.modules["__main__"] = module

    try:
        exec(_compile_with_lenient_bytes(source, str(path)), module.__dict__)
    finally:
        if previous is not None:
            sys.modules["__main__"] = previous


class _LenientBytesLoader(importlib.machinery.SourceFileLoader):
    # Narrower than the real signature (which also accepts a readable
    # buffer or a pre-parsed ast module/expression) on purpose: this hook
    # only ever sees actual file contents, decoded, never a pre-parsed AST.
    def source_to_code(  # ty: ignore[invalid-method-override]
        self, data: bytes | bytearray | str, path: str, *, _optimize: int = -1
    ) -> types.CodeType:
        source = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else data

        return _compile_with_lenient_bytes(source, path)


def _install_lenient_bytes_import_hook(root: Path) -> None:
    # Every .py file imported (not just exec'd) from within the staged
    # checkout goes through the same bytes-literal rewrite, since app files
    # reach the broken concat via a normal `import`, not the top-level exec
    # _exec_as_main handles. Scoped to `root` only: everything else (this
    # sim's own code, stdlib, and so on) imports completely normally.
    root_str = str(root)

    def hook(path: str) -> importlib.machinery.FileFinder:
        if path != root_str and not path.startswith(root_str + os.sep):
            raise ImportError("outside the sim's checkout sandbox")

        return importlib.machinery.FileFinder(
            path, (_LenientBytesLoader, importlib.machinery.SOURCE_SUFFIXES)
        )

    sys.path_hooks.insert(0, hook)
    sys.path_importer_cache.clear()


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _resolve_app_dir(app_arg: str) -> Path:
    # Accept either a bare name under matrixbox/apps (the common case) or an
    # absolute or relative path to any app directory elsewhere, such as a
    # scratch checkout that doesn't live next to this repo.
    given = Path(app_arg).expanduser()
    if given.is_dir():
        return given.resolve()

    by_name = MATRIXBOX_ROOT / "apps" / app_arg
    if by_name.is_dir():
        return by_name

    raise SystemExit(
        f"no such app directory: {app_arg!r} "
        f"(tried it as a path, and as a name under {MATRIXBOX_ROOT / 'apps'})"
    )


def _framework_root_for(app_dir: Path) -> Path:
    # Convention every checkout so far follows: <framework_root>/apps/<name>.
    # Walk up from the app directory rather than assuming a fixed sibling
    # checkout, so an app from a different checkout pulls in its own lib/
    # and package instead of matrixbox's.
    if app_dir.parent.name == "apps":
        return app_dir.parent.parent

    return MATRIXBOX_ROOT


def _is_os_root(path: Path) -> bool:
    # Any checkout with its own main.py can be booted as a whole system
    # instead of one specific app: its home menu, installed-apps list, and
    # in-process app switching all become reachable, not just autostarted
    # straight into one app.
    return (path / "main.py").exists()


_SOURCE_FILE_SUFFIXES = {".py", ".mpy", ".html"}


def _sync_tree(src: Path, dst: Path) -> None:
    # dirs_exist_ok merges instead of wiping: files present in src are
    # refreshed (so source edits still show up), but anything that only
    # exists in dst, like a settings.txt an app wrote at runtime, is left
    # alone. Matches real hardware, where reflashing code doesn't erase
    # flash-resident settings.
    #
    # That merge is one-directional, though: a file you delete or rename
    # in src has no way to take its old copy in dst down with it, so it
    # would otherwise sit there forever, stale, still importable by
    # anything that references its old name. Only code files are swept up
    # here, not "anything dst has that src doesn't" generally, since the
    # whole reason settings.txt survives a sync at all is that exact
    # asymmetry — sweeping broadly would delete it right back out again.
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", ".git"),
        dirs_exist_ok=True,
    )

    # The kernel flattens apps/<name> to a top-level sibling on every
    # boot, but src's own apps/ layout never changes to match. From
    # the second boot on, dst has a flattened copy the copytree above
    # can't see at all, and the generic prune pass below would otherwise
    # find no source counterpart for its files and delete the whole thing
    # outright. Re-sync each already-flattened app directly against its
    # real source first, and have the generic pass skip what this
    # already covered.
    already_synced = set()
    apps_dir = src / "apps"
    if apps_dir.is_dir():
        for app_src in apps_dir.iterdir():
            flattened_dst = dst / app_src.name
            if app_src.is_dir() and flattened_dst.is_dir():
                _sync_tree(app_src, flattened_dst)
                already_synced.add(flattened_dst)

    for path in dst.rglob("*"):
        if path.is_dir() or path.suffix not in _SOURCE_FILE_SUFFIXES:
            continue

        if any(parent in already_synced for parent in path.parents):
            continue

        if not (src / path.relative_to(dst)).exists():
            path.unlink()


def _stage_checkout(framework_root: Path, *, reset: bool = False) -> Path:
    # The whole checkout, not just one app: booting expects apps reachable
    # relative to itself, flattened as siblings once the kernel picks one
    # to run, mirroring real firmware.
    dst = SANDBOX_ROOT / "system" / framework_root.name
    if reset and dst.exists():
        shutil.rmtree(dst)

    _sync_tree(framework_root, dst)

    return dst


def _seed_settings(
    staged_root: Path,
    width: int,
    height: int,
    *,
    app_name: str | None = None,
    overwrite: bool = False,
    rotation: int | None = None,
) -> None:
    # Merge, don't overwrite: the app may have saved other keys here
    # (ssid, brightness, ...) on a previous run, and those carry over the
    # same way they would on real hardware.
    #
    # app_name is only set when launched with a single app to autostart.
    # Launched against a full checkout instead, autostart is cleared: a
    # plain root boot always lands on the home menu, regardless of
    # whatever an earlier single-app launch (or a previous root boot's
    # own in-UI app pick) left saved here — unlike real firmware, where
    # autostart is a sticky user preference, this is a dev sandbox and a
    # stale autostart from a different, unrelated launch shouldn't leak
    # into the next one.
    #
    # width/height are defaults, applied only when missing, unless the
    # caller explicitly picked a size this time (overwrite=True) — a human
    # picking a size right now means *this*, not whatever an earlier run
    # left in this sandbox.
    #
    # tiles is always 1, matching real firmware, which never tracks
    # multiple physical boards as a distinct setting. See sizes.py for
    # the sim-only panel count this displaces.
    settings_path = staged_root / "settings.txt"
    settings = _read_json(settings_path)
    settings["autostart"] = app_name if app_name is not None else 0

    if overwrite:
        settings["width"] = width
        settings["height"] = height
        if rotation is not None:
            settings["rotation"] = rotation
    else:
        settings.setdefault("width", width)
        settings.setdefault("height", height)

    settings.setdefault("tiles", 1)
    settings_path.write_text(json.dumps(settings))


def _install_chdir_path_tracking() -> None:
    # The kernel chdirs into whichever app it's launching, then does a
    # bare `import code` / `import __init__`. Real CircuitPython resolves
    # those against the current directory implicitly; desktop Python's
    # import system doesn't, so keep sys.path[0] in sync with cwd
    # ourselves on every chdir (it happens more than once: into an app,
    # then back to "/" on exit).
    real_chdir = os.chdir
    tracked_entry: str | None = None

    def tracked_chdir(path: Any) -> None:
        nonlocal tracked_entry
        real_chdir(path)
        if tracked_entry is not None:
            try:
                sys.path.remove(tracked_entry)
            except ValueError:
                pass

        tracked_entry = os.getcwd()
        sys.path.insert(0, tracked_entry)

    os.chdir = tracked_chdir  # ty: ignore[invalid-assignment]


_GEOMETRY_FLAGS = ("--size", "--width", "--height")


def _strip_geometry_flags(argv: list[str]) -> list[str]:
    # An internal restart (cycling size, a settings-UI change, 'r')
    # already wrote the new geometry to settings before restarting —
    # that's now authoritative. Replaying the original launch's own size/
    # width/height would fight that, since an explicit geometry value
    # always wins, overwriting the very change the restart exists to
    # apply.
    result = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue

        if token in _GEOMETRY_FLAGS:
            skip_next = True
            continue

        if any(token.startswith(flag + "=") for flag in _GEOMETRY_FLAGS):
            continue

        result.append(token)

    return result


def restart_process(reason: str = "to apply the new panel geometry") -> NoReturn:
    # Real hardware only ever reads panel geometry at boot, and reboots
    # whenever it changes — pins and tile count can't reconfigure live.
    # A reboot is also the only way to pick up a core code change, not
    # just an app's, since staging only happens once at process start.
    #
    # os.execv replaces this process outright rather than spawning a
    # child, so there's no port double-binding to worry about: every
    # socket this process opened is non-inheritable by default (PEP 446),
    # so exec() closes them before the new process opens fresh ones. Only
    # the CLI args get replayed, not argv[0], so this works the same
    # regardless of how the process was originally launched.
    #
    # Re-enter via cli.py, not this module directly: sys.argv[1:] already
    # starts with the "app" subcommand token cli.py's parser expects, which
    # this module's own parser would instead misread as the app argument.
    print(f"matrixbox-simulator: restarting {reason}...")
    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "matrixbox_simulator.cli",
            *_strip_geometry_flags(sys.argv[1:]),
        ],
    )


def _controls_hint() -> str:
    return (
        "'s'/'l' button, '+'/'-' refresh-fps, '['/']' gamma, 'z' cycle size, "
        "'r' reload (restarts)"
    )


def _cycle_size(settings_path: Path) -> NoReturn:
    # Matches by width/height rather than a saved "size" name, since real
    # settings have no such field either — just width/height/tiles. An
    # unrecognized combination (a custom size, or the very first boot)
    # starts the cycle from the top.
    settings = _read_json(settings_path)
    current_name = size_name_for(settings.get("width"), settings.get("height"))
    next_index = (
        (SIZE_CYCLE.index(current_name) + 1) % len(SIZE_CYCLE)
        if current_name in SIZE_CYCLE
        else 0
    )
    next_name = SIZE_CYCLE[next_index]
    width, height, _panels = SIZE_PRESETS[next_name]

    settings["width"] = width
    settings["height"] = height
    settings["tiles"] = 1  # always, matching real firmware — see _seed_settings
    # Always assert the rotation this size needs (180 for XL's mount, 0
    # otherwise), not just setdefault it — the display compositor
    # compensates for XL's mount specifically, so a rotation left over
    # from a previous XL visit would render every other size upside down.
    settings["rotation"] = ROTATION_OVERRIDES.get(next_name, 0)

    settings_path.write_text(json.dumps(settings))

    print(f"matrixbox-simulator: size now {next_name} ({width}x{height})")
    restart_process()


_REFRESH_FPS_STEP = 1.25
_REFRESH_FPS_START = 10.0  # what '+' picks the first time, going from off


def _bump_refresh_fps(direction: int) -> None:
    # Reaches into the same module the running app imported, the same
    # way the running-app lookup reaches into the kernel's own state, so
    # this always affects whichever display instance is actually live.
    module = sys.modules.get("framebufferio")
    if module is None:
        print("matrixbox-simulator: nothing running yet to adjust")
        return

    current = module.get_refresh_fps()
    if direction > 0:
        new = _REFRESH_FPS_START if current == 0 else current * _REFRESH_FPS_STEP
    else:
        new = current / _REFRESH_FPS_STEP
        if new < 1:
            new = 0.0

    module.set_refresh_fps(new)
    label = "off" if new == 0 else f"{new:.1f}"
    print(f"matrixbox-simulator: refresh-fps now {label}")


_GAMMA_STEP = 0.2


def _bump_gamma(direction: int) -> None:
    # Same lookup as the refresh-fps bump, same reasoning: this is the
    # exact module instance the running app's own colors resolve through.
    module = sys.modules.get("displayio")
    if module is None:
        print("matrixbox-simulator: nothing running yet to adjust")
        return

    new = max(1.0, module.get_gamma() + direction * _GAMMA_STEP)
    module.set_gamma(new)

    # Force a recomposite right away rather than waiting for the app's
    # own next refresh() call. Otherwise gamma changes are invisible
    # whenever the app is idle (sitting at a menu, waiting on
    # network/input), since refresh() skips recompositing entirely when
    # nothing else has touched displayio state.
    framebufferio_module = sys.modules.get("framebufferio")
    if framebufferio_module is not None:
        framebufferio_module.force_redraw()

    label = "off" if new == 1.0 else f"{new:.1f}"
    print(f"matrixbox-simulator: gamma now {label}")


def _run_kernel(
    framework_root: Path, args: argparse.Namespace, app_dir: Path | None = None
) -> None:
    # Runs the kernel entrypoint itself, not one app's code directly.
    #
    # app_dir set: autostart straight into that one app, for quick
    # iteration. app_dir None: boot the checkout as a whole system, no
    # forced autostart, so its own home menu and app switching are
    # reachable, like booting real firmware with no app configured yet.
    staged_root = _stage_checkout(framework_root, reset=args.reset)
    _install_path_sandbox(staged_root)
    _install_chdir_path_tracking()
    _install_lenient_bytes_import_hook(staged_root)
    _seed_settings(
        staged_root,
        args.width,
        args.height,
        app_name=app_dir.name if app_dir is not None else None,
        overwrite=args.geometry_explicit,
        rotation=args.rotation_override,
    )

    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(staged_root))
    sys.path.insert(0, str(staged_root / "lib"))
    sys.path.insert(0, str(STUB_DIR))

    frame_bridge.bridge.start(args.ws_host, args.ws_port)
    # Read back rather than trusting the launch values directly: after a
    # restart triggered by a settings-UI change or a size cycle, these
    # are whatever's actually now saved, which can differ from launch.
    final_settings = _read_json(staged_root / "settings.txt")
    final_width = final_settings.get("width", args.width)
    final_height = final_settings.get("height", args.height)
    frame_bridge.bridge.set_tiles(panel_count_for(final_width, final_height))
    frame_bridge.bridge.set_app_name(
        app_dir.name if app_dir is not None else framework_root.name
    )
    print(
        f"matrixbox-simulator: frame server listening on ws://{args.ws_host}:{args.ws_port}"
    )
    if app_dir is not None:
        print(
            f"matrixbox-simulator: running {app_dir} "
            f"({final_width}x{final_height}) via {framework_root.name}'s "
            "own kernel (autostart)"
        )
    else:
        print(
            f"matrixbox-simulator: running {framework_root} "
            f"({final_width}x{final_height}) via its own kernel "
            "(system menu, no forced autostart)"
        )

    http_port = int(os.environ.get("MATRIXBOX_SIMULATOR_HTTP_PORT", "8080"))
    print(f"matrixbox-simulator: web UI at http://127.0.0.1:{http_port}/")

    main_path = staged_root / "main.py"
    os.chdir(staged_root)  # goes through tracked_chdir, seeds sys.path[0]
    source = main_path.read_text()

    def cycle_size() -> NoReturn:
        _cycle_size(staged_root / "settings.txt")

    with _button_listener(cycle_size=cycle_size):
        try:
            _exec_as_main(source, main_path)
        except KeyboardInterrupt:
            print("\nmatrixbox-simulator: stopped")


# Set while stdin is in cbreak mode, so a SIGINT arriving mid-app can
# still put the terminal back the way it found it — the hard exit below
# can't rely on ordinary try/finally cleanup running.
_restore_terminal: Callable[[], None] | None = None


def _install_hard_sigint_handler() -> None:
    # Real app code is riddled with bare `except:` clauses — ordinary
    # embedded-firmware defensiveness against sensor/network errors — and
    # a bare except also catches KeyboardInterrupt, since it's "catch
    # anything at all", not an Exception subclass check. A plain SIGINT
    # can get silently swallowed by one of those, with the loop just
    # continuing, so Ctrl+C might never actually exit.
    #
    # A signal handler runs outside that exception machinery entirely:
    # it's invoked directly rather than raised into the app's own stack,
    # so nothing the app wrote can catch it. os._exit() (not sys.exit(),
    # which just raises a catchable SystemExit) skips the interpreter's
    # own cleanup too, so the terminal's cbreak mode is restored by hand
    # first.
    def handle_sigint(_signum: int, _frame: FrameType | None) -> None:
        if _restore_terminal is not None:
            _restore_terminal()

        print("\nmatrixbox-simulator: stopped")
        sys.stdout.flush()
        os._exit(0)

    signal.signal(signal.SIGINT, handle_sigint)


@contextlib.contextmanager
def _button_listener(
    *,
    cycle_size: Callable[[], None] | None = None,
) -> Iterator[None]:
    # Simulates the front-panel button from the terminal: 's' short press,
    # 'l' long press (usually exits the app). 'r' reloads by restarting
    # the whole process — staging always re-syncs the entire checkout
    # fresh on boot, so this alone picks up both app and core code
    # changes; no in-process reload path to keep in sync with it. No-ops
    # when stdin isn't a real terminal, or termios/tty aren't available
    # at all.
    if not sys.stdin.isatty() or termios is None:
        yield
        return

    global _restore_terminal

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setcbreak(fd)  # ty: ignore[unresolved-attribute]

    def restore() -> None:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    _restore_terminal = restore
    stop = threading.Event()

    def listen() -> None:
        while not stop.is_set():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not ready:
                continue

            char = sys.stdin.read(1)
            if char == "s":
                button_input.press(0.2)
                print("matrixbox-simulator: button, short press")
            elif char == "l":
                button_input.press(2.2)
                print("matrixbox-simulator: button, long press")
            elif char == "r":
                restart_process(reason="to reload app and core code")
            elif char == "+":
                _bump_refresh_fps(1)
            elif char == "-":
                _bump_refresh_fps(-1)
            elif char == "]":
                _bump_gamma(1)
            elif char == "[":
                _bump_gamma(-1)
            elif char == "z" and cycle_size is not None:
                cycle_size()

    thread = threading.Thread(target=listen, daemon=True)
    thread.start()
    print(f"matrixbox-simulator: controls: {_controls_hint()}")

    try:
        yield
    finally:
        stop.set()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        _restore_terminal = None


def build_parser(
    parser: argparse.ArgumentParser | None = None,
) -> argparse.ArgumentParser:
    if parser is None:
        parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "app",
        help=(
            "app to run: a directory name under matrixbox/apps (e.g. clock), "
            "an absolute/relative path to any app directory, or a path to a "
            "whole checkout's root to boot its own system menu instead of "
            "one specific app"
        ),
    )
    parser.add_argument(
        "--size",
        choices=sorted(SIZE_PRESETS),
        default=None,
        help=(
            "real product size to simulate (sets width/height/tile count "
            "together). Overrides --width/--height if both are given"
        ),
    )
    parser.add_argument(
        "--width", type=int, default=None, help="panel width in pixels (see --size)"
    )
    parser.add_argument(
        "--height", type=int, default=None, help="panel height in pixels (see --size)"
    )
    parser.add_argument("--ws-host", default="127.0.0.1")
    parser.add_argument("--ws-port", type=int, default=9191)
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "wipe this app's staged state (settings.txt and anything else "
            "it wrote) before running it, for a fresh-install test"
        ),
    )
    parser.add_argument(
        "--refresh-fps",
        type=float,
        default=0.0,
        help=(
            "give display.refresh() a small, roughly-constant per-call cost "
            "(1/this many seconds), approximating real hardware's own "
            "bit-banging cost. Off (0) by default: some apps rely on this "
            "cost for their own animation pacing and run too fast without "
            "it, but others call refresh() many times per logical step and "
            "get proportionally slower, so there's no one right value yet — "
            "this is an experimental knob, not a verified default"
        ),
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=1.0,
        help=(
            "gamma-correct every color an app draws, boosting dim values "
            "more than bright ones. Off (1.0) by default. matrixbox's "
            "colors are often tuned by eye against the real LED panel, not "
            "a monitor (its shared 'white' palette entry is literally "
            "(20,20,20), commented 'grey' in its own source) — a raw PWM-"
            "driven LED at low values reads brighter in person than the "
            "same RGB triplet does on a screen. No verified correct value; "
            "try 1.8-2.8 and adjust live with '['/']'"
        ),
    )
    return parser


def run(args: argparse.Namespace) -> None:
    _install_hard_sigint_handler()

    # Explicit right now beats whatever's already saved in this sandbox:
    # picking a size means *this*, not whatever an unrelated earlier run
    # left behind. An internal restart never reaches here carrying an
    # explicit size at all (see _strip_geometry_flags), so it falls back
    # to whatever's already on disk instead.
    args.geometry_explicit = (
        args.size is not None or args.width is not None or args.height is not None
    )
    # 0 by default, not None: picking a named size means "this is the
    # whole device now", so a leftover rotation from a previous, different
    # size has to be reset along with width/height. A bare width/height
    # doesn't name a real product, so there's no known-correct rotation
    # to assert — leave it alone.
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

    given = Path(args.app).expanduser()
    if given.is_dir() and _is_os_root(given.resolve()):
        _patch_stdlib()
        _run_kernel(given.resolve(), args)
        return

    app_dir = _resolve_app_dir(args.app)
    framework_root = _framework_root_for(app_dir)
    if not framework_root.exists():
        raise SystemExit(f"expected a matrixbox-style checkout at {framework_root}")

    _patch_stdlib()
    _run_kernel(framework_root, args, app_dir=app_dir)


def main() -> None:
    run(build_parser().parse_args())


if __name__ == "__main__":
    main()
