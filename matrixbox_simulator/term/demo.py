"""Synthetic animated frame source, standing in for the real simulator while
the renderer is developed and verified on its own.
"""

import math

from matrixbox_simulator.term.wire import Frame


class DemoSource:
    def __init__(self, width: int, height: int, tiles: int = 1) -> None:
        self.width = width
        self.height = height
        self.tiles = tiles
        self.tick = 0.0

    def next_frame(self) -> Frame:
        pixels = bytearray(self.width * self.height * 3)
        i = 0
        for y in range(self.height):
            for x in range(self.width):
                r, g, b = _plasma(x, y, self.tick)
                pixels[i] = r
                pixels[i + 1] = g
                pixels[i + 2] = b
                i += 3

        self.tick += 0.08

        return Frame(
            width=self.width,
            height=self.height,
            pixels=bytes(pixels),
            tiles=self.tiles,
        )


def _plasma(x: int, y: int, t: float) -> tuple[int, int, int]:
    v = (
        math.sin(x * 0.2 + t)
        + math.sin(y * 0.2 + t * 0.7)
        + math.sin((x + y) * 0.15 + t * 1.3)
    )

    r = int((v * 0.5 + 0.5) * 255.0)
    g = int((math.sin(v + 2.094) * 0.5 + 0.5) * 255.0)
    b = int((math.sin(v + 4.188) * 0.5 + 0.5) * 255.0)

    return r & 0xFF, g & 0xFF, b & 0xFF
