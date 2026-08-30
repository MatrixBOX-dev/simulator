"""Stand-in for the `rgbmatrix` native module. No real HUB75 hardware in the
sim, so this only needs to remember the panel geometry that
framebufferio.FramebufferDisplay reads back.
"""


class RGBMatrix:
    def __init__(
        self, *, width: int, height: int, tile: int = 1, **_kwargs: object
    ) -> None:
        self.width = width
        self.height = height
        self.tile = tile
