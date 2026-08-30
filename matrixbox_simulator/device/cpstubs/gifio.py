"""Stand-in for CircuitPython's `gifio`. The real firmware decodes GIFs
natively in C; here Pillow (this sim's one real dependency) does the actual
decoding, and each frame is re-encoded into the same RGB565_SWAPPED raw
pixel format gifio.OnDiskGif exposes on real hardware, so downstream code
(displayio.ColorConverter, bitmaptools.alphablend) doesn't need to know the
difference.
"""

import os

import displayio
from _colorspace import rgb888_to_rgb565_swapped
from PIL import Image


class OnDiskGif:
    def __init__(self, file_or_path: str | os.PathLike) -> None:
        self._image = Image.open(file_or_path)
        self.width, self.height = self._image.size
        self.bitmap = displayio.Bitmap(self.width, self.height, 65536)
        self.frame_count = getattr(self._image, "n_frames", 1)
        self.min_delay = 0.0
        self.max_delay = 0.0

        self._frame_index = 0
        self.duration = self._decode_frame(0)

    def next_frame(self) -> float:
        self._frame_index = (self._frame_index + 1) % self.frame_count
        self.duration = self._decode_frame(self._frame_index)

        return self.duration

    def _decode_frame(self, index: int) -> float:
        self._image.seek(index)
        # .tobytes() over .getpixel() per pixel: unambiguously bytes, with
        # no per-mode return-type ambiguity to fight the type checker over,
        # and it avoids width*height individual Pillow calls.
        raw = self._image.convert("RGB").tobytes()

        for y in range(self.height):
            row = y * self.width * 3
            for x in range(self.width):
                i = row + x * 3
                self.bitmap[x, y] = rgb888_to_rgb565_swapped(
                    raw[i], raw[i + 1], raw[i + 2]
                )

        # GIF frame duration defaults to 100ms when unset, matching
        # browsers' de facto handling of a zero/missing delay.
        milliseconds = self._image.info.get("duration") or 100

        return milliseconds / 1000.0
