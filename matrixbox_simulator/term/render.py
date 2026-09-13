"""Draws a Frame to the terminal using half-block characters ('▀'): each
glyph's foreground/background carries one pixel each, doubling vertical
resolution relative to one-pixel-per-cell. Framed in a white border so the
panel's edges are visible against an equally black terminal background,
with a stats line and a static controls reference underneath.

Each frame is composed entirely off-screen: one Text buffer, with adjacent
same-color pixels merged into single runs rather than one styled character
each, then written to the terminal in a single pass (cursor home, then one
print()). This avoids rich.live.Live's own per-line clear-then-redraw,
which is what was actually causing flicker: that redraws line by line, and
a fresh style code per pixel makes each frame large enough that even a
single redraw can visibly tear.
"""

from rich.color import Color
from rich.console import Console
from rich.control import Control
from rich.style import Style
from rich.text import Text

from matrixbox_simulator.term.wire import Frame, Stats

Rgb = tuple[int, int, int]

_BORDER_STYLE = Style(color="white")
_STATS_STYLE = Style(color="white")
_TILE_BORDER_STYLE = Style(color="grey50")
_HEADER_STYLE = Style(color="white", bold=True)

# Mirrors run_app._button_listener()'s own key handling — this is a
# reference for a person watching the renderer, not something this
# process itself listens for, so it has to be kept in sync by hand
# rather than imported: the device process is a separate `matrixbox app`
# invocation, very often in an entirely different terminal.
_APP_CONTROLS = (
    ("n", "toggle wifi on/off"),
    ("s", "short button press"),
    ("l", "long button press (usually exits the app)"),
    ("r", "reload (restarts)"),
    ("+/-", "refresh-fps"),
    ("[/]", "gamma"),
    ("z", "cycle panel size"),
)


class TerminalRenderer:
    def __init__(self) -> None:
        # force_terminal + explicit truecolor: this tool always writes to an
        # interactive terminal (never meant to be piped), so there's no
        # need to risk ANSI capability autodetection guessing wrong.
        self.console = Console(
            highlight=False,
            soft_wrap=True,
            force_terminal=True,
            color_system="truecolor",
        )
        self.console.show_cursor(False)

        # Off by default: the seams between a panel's physical tiles
        # (e.g. matrixbox's X is 2 side by side, XL is 3) aren't visible
        # on the real device, so showing them isn't reproducing hardware,
        # it's a design aid for noticing content that'll straddle a seam.
        self.show_tile_borders = False

    def toggle_tile_borders(self) -> bool:
        self.show_tile_borders = not self.show_tile_borders

        return self.show_tile_borders

    def render(
        self,
        frame: Frame,
        stats: Stats | None,
        *,
        waiting_message: str = "waiting for a connected app...",
    ) -> None:
        text = Text()

        horizontal_border = "─" * frame.width
        text.append("┌" + horizontal_border + "┐\n", style=_BORDER_STYLE)

        for y in range(0, frame.height, 2):
            text.append("│", style=_BORDER_STYLE)
            text.append_text(_render_row(frame, y, self.show_tile_borders))
            text.append("│\n", style=_BORDER_STYLE)

        text.append("└" + horizontal_border + "┘\n", style=_BORDER_STYLE)

        # Padded to the box's own width: content here (app name, fps
        # digits) varies in length frame to frame, and with no per-line
        # clear between redraws, a shorter line than last frame's would
        # otherwise leave stray leftover characters with nothing to
        # overwrite them.
        stats_line = _format_stats_line(stats, waiting_message)
        text.append(
            stats_line.ljust(len(horizontal_border) + 2) + "\n", style=_STATS_STYLE
        )

        # Always rendered, not just printed once at startup: this is the
        # one screen region under this process's own full control every
        # frame, so it's the one place a controls reminder can stay
        # genuinely static instead of scrolling off under whatever this
        # window's arbitrary log/print output does next. Same reasoning
        # for including the *other* window's controls here too, not just
        # this window's own 't' — a person watching the panel shouldn't
        # have to go find that other terminal just to remember them.
        text.append("\n")
        text.append_text(_format_controls_block(self.show_tile_borders))

        self.console.control(Control.home())
        self.console.print(text, end="")

    def clear(self) -> None:
        self.console.clear()

    def stop(self) -> None:
        self.console.show_cursor(True)


def _render_row(frame: Frame, y: int, show_tile_borders: bool) -> Text:
    row = Text()
    boundaries = _tile_boundaries(frame.width, frame.tiles) if show_tile_borders else ()

    def emit(colors: tuple[Rgb, Rgb], length: int) -> None:
        top, bottom = colors
        row.append("▀" * length, style=Style(color=_rgb(top), bgcolor=_rgb(bottom)))

    run_colors: tuple[Rgb, Rgb] | None = None
    run_length = 0

    for x in range(frame.width):
        if x in boundaries:
            if run_colors is not None:
                emit(run_colors, run_length)
                run_colors = None
                run_length = 0

            row.append("│", style=_TILE_BORDER_STYLE)
            continue

        colors = (frame.pixel(x, y) or (0, 0, 0), frame.pixel(x, y + 1) or (0, 0, 0))

        if colors == run_colors:
            run_length += 1
        else:
            if run_colors is not None:
                emit(run_colors, run_length)

            run_colors = colors
            run_length = 1

    if run_colors is not None:
        emit(run_colors, run_length)

    return row


def _tile_boundaries(width: int, tiles: int) -> frozenset[int]:
    if tiles <= 1:
        return frozenset()

    tile_width = width // tiles

    return frozenset(tile_width * i for i in range(1, tiles))


def _rgb(rgb: Rgb) -> Color:
    r, g, b = rgb

    return Color.from_rgb(r, g, b)


def _format_stats_line(stats: Stats | None, waiting_message: str) -> str:
    if stats is None:
        return waiting_message

    cpu = f"{stats.cpu_percent:.0f}%" if stats.cpu_percent is not None else "n/a"
    rss = f"{stats.rss_kb / 1024:.1f} MB" if stats.rss_kb is not None else "n/a"

    return f"app: {stats.app}  fps: {stats.fps:.1f}  cpu: {cpu}  sim rss (peak): {rss}"


# Shared by both lists in _format_controls_block, so "t" and every app
# control key line up in the same column instead of the single-entry
# simulator list getting its own, different spacing.
_KEY_WIDTH = max(len("t"), max(len(key) for key, _ in _APP_CONTROLS))


def _format_controls_block(show_tile_borders: bool) -> Text:
    text = Text()
    text.append("simulator controls (this window):\n", style=_HEADER_STYLE)
    # Padded like the stats line above, for the same reason: "on" is
    # shorter than "off", and this is the one line in an otherwise fully
    # static block whose length can actually change between redraws.
    state = "on" if show_tile_borders else "off"
    border_line = f"  {'t'.ljust(_KEY_WIDTH)}  toggle borders [{state}]"
    text.append(
        border_line.ljust(len(f"  {'t'.ljust(_KEY_WIDTH)}  toggle borders [off]"))
        + "\n",
        style=_STATS_STYLE,
    )

    text.append("\napp controls (the `matrixbox app` window):\n", style=_HEADER_STYLE)
    lines = [
        f"  {key.ljust(_KEY_WIDTH)}  {description}"
        for key, description in _APP_CONTROLS
    ]
    # No trailing newline on the last one: render() prints this whole
    # buffer with no newline of its own either, so the cursor is left at
    # the end of this text, matching every other status message that
    # follows it with its own leading "\n" rather than a blank one baked
    # in here.
    text.append("\n".join(lines), style=_STATS_STYLE)

    return text
