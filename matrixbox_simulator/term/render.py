"""Draws a Frame to the terminal using half-block characters ('▀'): each
glyph's foreground/background carries one pixel each, doubling vertical
resolution relative to one-pixel-per-cell. Framed in a white border so the
panel's edges are visible against an equally black terminal background,
with an optional stats line underneath.

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
_STATS_STYLE = Style(color="bright_black")
_TILE_BORDER_STYLE = Style(color="grey50")


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

    def render(self, frame: Frame, stats: Stats | None) -> None:
        text = Text()

        horizontal_border = "─" * frame.width
        text.append("┌" + horizontal_border + "┐\n", style=_BORDER_STYLE)

        for y in range(0, frame.height, 2):
            text.append("│", style=_BORDER_STYLE)
            text.append_text(_render_row(frame, y, self.show_tile_borders))
            text.append("│\n", style=_BORDER_STYLE)

        text.append("└" + horizontal_border + "┘\n", style=_BORDER_STYLE)

        # Padded to the box's own width: this is the last thing printed
        # each frame, with no trailing newline, so a shorter line here
        # than last frame's (app name changed length, fps gained a digit,
        # the tile-border toggle flipped) would otherwise leave stray
        # leftover characters on screen with nothing to overwrite them.
        stats_line = _format_stats_line(stats, self.show_tile_borders)
        text.append(stats_line.ljust(len(horizontal_border) + 2), style=_STATS_STYLE)

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


def _format_stats_line(stats: Stats | None, show_tile_borders: bool) -> str:
    borders = f"borders (t): {'on' if show_tile_borders else 'off'}"
    if stats is None:
        return borders

    cpu = f"{stats.cpu_percent:.0f}%" if stats.cpu_percent is not None else "n/a"
    rss = f"{stats.rss_kb / 1024:.1f} MB" if stats.rss_kb is not None else "n/a"

    return (
        f"app: {stats.app}  fps: {stats.fps:.1f}  cpu: {cpu}  "
        f"sim rss (peak): {rss}  {borders}"
    )
