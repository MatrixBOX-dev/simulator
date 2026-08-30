"""Shared RGB565 <-> RGB888 conversion, matching CircuitPython's
Colorspace.RGB565_SWAPPED encoding (byte-swapped 16-bit 5-6-5 color). This
is the raw pixel format gifio's direct-color bitmaps use, decoded by
displayio.ColorConverter and blended by bitmaptools.alphablend.
"""


def rgb888_to_rgb565_swapped(r: int, g: int, b: int) -> int:
    packed = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)

    return ((packed & 0xFF) << 8) | (packed >> 8)


def rgb565_swapped_to_rgb888(value: int) -> tuple[int, int, int]:
    packed = ((value & 0xFF) << 8) | (value >> 8)
    r5 = (packed >> 11) & 0x1F
    g6 = (packed >> 5) & 0x3F
    b5 = packed & 0x1F

    return (r5 * 255 // 31, g6 * 255 // 63, b5 * 255 // 31)
