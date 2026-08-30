"""Decodes the binary wire messages the frame server sends: Frame (pixel
data) and Stats (app name, FPS, CPU load, memory).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_FRAME_MAGIC = 0xF3
_STATS_MAGIC = 0xF4
_VERSION = 1
_FRAME_HEADER_LEN = 8
_FORMAT_RGB888 = 0

_STATS_HAS_CPU_PERCENT = 0b01
_STATS_HAS_RSS_KB = 0b10


class DecodeError(Exception):
    pass


@dataclass
class Frame:
    width: int
    height: int
    pixels: bytes
    tiles: int = 1

    def pixel(self, x: int, y: int) -> tuple[int, int, int] | None:
        if x >= self.width or y >= self.height:
            return None

        i = (y * self.width + x) * 3

        return (self.pixels[i], self.pixels[i + 1], self.pixels[i + 2])

    @staticmethod
    def blank(width: int, height: int, tiles: int = 1) -> "Frame":
        return Frame(
            width=width, height=height, pixels=bytes(width * height * 3), tiles=tiles
        )


@dataclass
class Stats:
    app: str
    fps: float
    cpu_percent: float | None
    rss_kb: int | None


def decode(data: bytes) -> Frame | Stats:
    if not data:
        raise DecodeError("empty message")

    magic = data[0]
    if magic == _FRAME_MAGIC:
        return _decode_frame(data)
    if magic == _STATS_MAGIC:
        return _decode_stats(data)

    raise DecodeError(f"bad magic byte: {magic:#x}")


def _decode_frame(data: bytes) -> Frame:
    if len(data) < _FRAME_HEADER_LEN:
        raise DecodeError("frame shorter than its header")

    _magic, version, width, height, fmt, tiles = struct.unpack_from("<BBHHBB", data)
    if version != _VERSION:
        raise DecodeError(f"unsupported protocol version {version}")
    if fmt != _FORMAT_RGB888:
        raise DecodeError(f"unsupported pixel format {fmt}")

    pixels = data[_FRAME_HEADER_LEN:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise DecodeError(f"expected {expected} pixel bytes, got {len(pixels)}")

    return Frame(width=width, height=height, pixels=pixels, tiles=max(1, tiles))


def _decode_stats(data: bytes) -> Stats:
    if len(data) < 4:
        raise DecodeError("message shorter than its header")

    _magic, version, flags, app_len = struct.unpack_from("<BBBB", data)
    if version != _VERSION:
        raise DecodeError(f"unsupported protocol version {version}")

    cursor = 4
    app_end = cursor + app_len
    if len(data) < app_end + 4:
        raise DecodeError("message shorter than its header")

    app = data[cursor:app_end].decode("utf-8")
    cursor = app_end

    (fps,) = struct.unpack_from("<f", data, cursor)
    cursor += 4

    cpu_percent = None
    if flags & _STATS_HAS_CPU_PERCENT:
        if len(data) < cursor + 4:
            raise DecodeError("message shorter than its header")

        (cpu_percent,) = struct.unpack_from("<f", data, cursor)
        cursor += 4

    rss_kb = None
    if flags & _STATS_HAS_RSS_KB:
        if len(data) < cursor + 4:
            raise DecodeError("message shorter than its header")

        (rss_kb,) = struct.unpack_from("<I", data, cursor)

    return Stats(app=app, fps=fps, cpu_percent=cpu_percent, rss_kb=rss_kb)
