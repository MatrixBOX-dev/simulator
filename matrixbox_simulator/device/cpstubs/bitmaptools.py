"""Stand-in for the `bitmaptools` native module: pure-Python implementations
of the handful of pixel operations matrixbox's Canvas relies on.
"""

import math

import displayio
from _colorspace import rgb565_swapped_to_rgb888, rgb888_to_rgb565_swapped


def fill_region(
    bitmap: displayio.Bitmap, x0: int, y0: int, x1: int, y1: int, value: int
) -> None:
    for y in range(y0, y1):
        for x in range(x0, x1):
            bitmap[x, y] = value


def draw_line(
    bitmap: displayio.Bitmap, x0: int, y0: int, x1: int, y1: int, value: int
) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    x, y = x0, y0
    while True:
        if 0 <= x < bitmap.width and 0 <= y < bitmap.height:
            bitmap[x, y] = value

        if x == x1 and y == y1:
            break

        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx

        if e2 <= dx:
            err += dx
            y += sy


def blit(
    dest: displayio.Bitmap,
    source: displayio.Bitmap,
    x: int,
    y: int,
    *,
    x1: int = 0,
    y1: int = 0,
    x2: int | None = None,
    y2: int | None = None,
    skip_source_index: int | None = None,
) -> None:
    x2 = source.width if x2 is None else x2
    y2 = source.height if y2 is None else y2

    for sy in range(y1, y2):
        dy = y + (sy - y1)
        if dy < 0 or dy >= dest.height:
            continue

        for sx in range(x1, x2):
            dx = x + (sx - x1)
            if dx < 0 or dx >= dest.width:
                continue

            value = source[sx, sy]
            if skip_source_index is not None and value == skip_source_index:
                continue

            dest[dx, dy] = value


def rotozoom(
    dest: displayio.Bitmap,
    source: displayio.Bitmap,
    *,
    ox: int,
    oy: int,
    px: int,
    py: int,
    angle: float = 0.0,
    scale: float = 1.0,
    skip_index: int | None = None,
) -> None:
    # Nearest-neighbor inverse mapping: for every candidate dest pixel, find
    # the source pixel that lands on it and sample that instead of walking
    # source -> dest (which would leave gaps at scale > 1).
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    half_w = int(source.width * scale) + 1
    half_h = int(source.height * scale) + 1

    x_min = max(0, ox - half_w)
    x_max = min(dest.width, ox + half_w)
    y_min = max(0, oy - half_h)
    y_max = min(dest.height, oy + half_h)

    for dy in range(y_min, y_max):
        for dx in range(x_min, x_max):
            rel_x = (dx - ox) / scale
            rel_y = (dy - oy) / scale
            sx = cos_a * rel_x + sin_a * rel_y + px
            sy = -sin_a * rel_x + cos_a * rel_y + py

            isx, isy = int(sx), int(sy)
            if 0 <= isx < source.width and 0 <= isy < source.height:
                value = source[isx, isy]
                if skip_index is not None and value == skip_index:
                    continue

                dest[dx, dy] = value


def alphablend(
    dest: displayio.Bitmap,
    source1: displayio.Bitmap,
    source2: displayio.Bitmap,
    colorspace: str,
    factor_1: float = 0.5,
    factor_2: float | None = None,
) -> None:
    if colorspace != displayio.Colorspace.RGB565_SWAPPED:
        raise NotImplementedError(
            f"bitmaptools.alphablend colorspace {colorspace!r} isn't implemented "
            "in the matrixbox-simulator yet"
        )

    if factor_2 is None:
        factor_2 = 1.0 - factor_1

    for y in range(dest.height):
        for x in range(dest.width):
            r1, g1, b1 = rgb565_swapped_to_rgb888(source1[x, y])
            r2, g2, b2 = rgb565_swapped_to_rgb888(source2[x, y])
            r = min(255, max(0, round(r1 * factor_1 + r2 * factor_2)))
            g = min(255, max(0, round(g1 * factor_1 + g2 * factor_2)))
            b = min(255, max(0, round(b1 * factor_1 + b2 * factor_2)))
            dest[x, y] = rgb888_to_rgb565_swapped(r, g, b)
