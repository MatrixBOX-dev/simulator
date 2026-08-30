"""Stand-in for CircuitPython's `displayio`, backed by plain Python objects
instead of real display hardware. Mirrors just the surface matrixbox uses:
Bitmap (a pixel grid, either palette-indexed or, for gifio's direct-color
frames, raw RGB565_SWAPPED values), Palette, ColorConverter, Group and
TileGrid.
"""

import array
import os
from collections.abc import Callable, Iterator
from typing import ParamSpec, TypeVar

from _colorspace import rgb565_swapped_to_rgb888

_P = ParamSpec("_P")
_T = TypeVar("_T")

# Colors in matrixbox apps are often tuned by eye against the real LED
# panel, not a monitor: a raw PWM-driven LED at a given value reads
# brighter in person than the same RGB triplet does on a screen. Gamma
# correction converts a color as an app wrote it into what it should
# look like on a monitor instead. 1.0 is off; real hardware has no such
# concept, so there's no correct default to bake in.
_gamma = float(os.environ.get("MATRIXBOX_SIMULATOR_GAMMA", "1.0"))

Rgb = tuple[int, int, int]


def get_gamma() -> float:
    return _gamma


def set_gamma(value: float) -> None:
    global _gamma
    _gamma = max(0.1, value)


def _apply_gamma(rgb: Rgb) -> Rgb:
    if _gamma == 1.0:
        return rgb

    exponent = 1.0 / _gamma
    r, g, b = rgb
    return (
        round(255 * (r / 255) ** exponent),
        round(255 * (g / 255) ** exponent),
        round(255 * (b / 255) ** exponent),
    )


# Bumped on every mutation that could change what's on screen (pixel
# writes, palette changes, x/y/hidden, adding/removing layers). Lets the
# display compositor skip a redraw entirely when nothing's changed since
# the last one, matching real hardware's own dirty tracking. Some apps
# call refresh() many times in a row as their own speed control with
# nothing actually changing in between; without this, that pattern pays
# full recompositing cost every time for no visual difference.
_generation = 0


def current_generation() -> int:
    return _generation


def bumps(method: Callable[_P, _T]) -> Callable[_P, _T]:
    """Marks a mutator as unconditionally bumping the generation counter
    once it's done. For a mutator that's often called with no real effect
    (make_transparent on an already-transparent index, x/y/hidden
    reassigned to their current value), use bumps_if_changed instead."""

    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        global _generation
        result = method(*args, **kwargs)
        _generation += 1
        return result

    return wrapper


def bumps_if_changed(method: Callable[_P, bool]) -> Callable[_P, None]:
    """Like bumps, but only bumps when the wrapped method reports an
    actual change by returning a truthy value. The wrapped method's own
    return value is consumed here, not passed through — every user of
    this decorator returns None either way, matching real CircuitPython's
    API for these."""

    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> None:
        global _generation
        if method(*args, **kwargs):
            _generation += 1

    return wrapper


class Bitmap:
    def __init__(self, width: int, height: int, value_count: int) -> None:
        self.width = width
        self.height = height
        self.value_count = value_count
        # Always word-sized regardless of value_count. That's simpler than
        # switching storage width, and value_count only ever means "index
        # into a small Palette" (<256) or "raw RGB565 value" (<65536) here.
        self._data = array.array("I", [0]) * (width * height)

    def __getitem__(self, key: tuple[int, int]) -> int:
        x, y = key
        return self._data[y * self.width + x]

    @bumps
    def __setitem__(self, key: tuple[int, int], value: int) -> None:
        x, y = key
        self._data[y * self.width + x] = value

    @bumps
    def fill(self, value: int = 0) -> None:
        self._data = array.array("I", [value]) * (self.width * self.height)


class Palette:
    def __init__(self, count: int, *, dither: bool = False) -> None:
        self._colors: list[Rgb] = [(0, 0, 0)] * count
        self._transparent: set[int] = set()

    def __len__(self) -> int:
        return len(self._colors)

    def __getitem__(self, index: int) -> Rgb:
        return self._colors[index]

    @bumps
    def __setitem__(self, index: int, value: int | Rgb) -> None:
        if isinstance(value, int):
            value = ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)

        self._colors[index] = (value[0] & 0xFF, value[1] & 0xFF, value[2] & 0xFF)

    def resolve(self, index: int) -> Rgb | None:
        # None means transparent: the compositor leaves whatever's already
        # on the canvas from earlier layers in place instead of overwriting
        # it with this pixel.
        if index in self._transparent:
            return None

        return _apply_gamma(self._colors[index])

    @bumps_if_changed
    def make_transparent(self, index: int) -> bool:
        if index in self._transparent:
            return False

        self._transparent.add(index)
        return True

    @bumps_if_changed
    def make_opaque(self, index: int) -> bool:
        if index not in self._transparent:
            return False

        self._transparent.discard(index)
        return True


class Colorspace:
    RGB565_SWAPPED: str = "RGB565_SWAPPED"


class ColorConverter:
    def __init__(
        self, *, input_colorspace: str = Colorspace.RGB565_SWAPPED, dither: bool = False
    ) -> None:
        if input_colorspace != Colorspace.RGB565_SWAPPED:
            raise NotImplementedError(
                f"displayio.Colorspace {input_colorspace!r} isn't implemented "
                "in the matrixbox-simulator yet"
            )

        self.input_colorspace = input_colorspace
        self.dither = dither

    def resolve(self, value: int) -> Rgb | None:
        return _apply_gamma(rgb565_swapped_to_rgb888(value))


def _dirty_attr(name: str) -> property:
    """A property backed by `_<name>` that only bumps the generation
    counter when the value actually changes, so reassigning the same x/y
    or re-hiding an already-hidden layer doesn't force a recomposite."""
    private_name = "_" + name

    def getter(self: object) -> object:
        return getattr(self, private_name)

    @bumps_if_changed
    def setter(self: object, value: object) -> bool:
        if value == getattr(self, private_name):
            return False

        setattr(self, private_name, value)
        return True

    return property(getter, setter)


class _DirtyPositionMixin:
    """x/y/hidden as properties that bump the shared generation counter on
    an actual change, so a redraw can tell a real move from a no-op one
    without walking the whole tree."""

    x = _dirty_attr("x")
    y = _dirty_attr("y")
    hidden = _dirty_attr("hidden")


PixelShader = Palette | ColorConverter


class TileGrid(_DirtyPositionMixin):
    def __init__(
        self,
        bitmap: Bitmap,
        *,
        pixel_shader: PixelShader,
        x: int = 0,
        y: int = 0,
        **_kwargs: object,
    ) -> None:
        # **_kwargs swallows width/height/tile_width/tile_height/default_tile:
        # real tiling/sub-region params the compositor doesn't need, since
        # every TileGrid here is used as a single untiled sprite, but the
        # constructor still needs to accept them.
        self.bitmap = bitmap
        self.pixel_shader = pixel_shader
        self._x = x
        self._y = y
        self._hidden = False


class Group(_DirtyPositionMixin):
    def __init__(
        self, *, scale: int = 1, x: int = 0, y: int = 0, **_kwargs: object
    ) -> None:
        # scale is accepted but not honored: nothing here renders upscaled.
        # x/y and hidden are honored by the compositor, at every nesting
        # level, not just the root.
        self.layers: list["Group | TileGrid"] = []
        self._hidden = False
        self.scale = scale
        self._x = x
        self._y = y

    @bumps
    def append(self, layer: "Group | TileGrid") -> None:
        self.layers.append(layer)

    @bumps
    def pop(self, index: int = -1) -> "Group | TileGrid":
        return self.layers.pop(index)

    def __len__(self) -> int:
        return len(self.layers)

    def __getitem__(self, index: int) -> "Group | TileGrid":
        return self.layers[index]

    def __iter__(self) -> Iterator["Group | TileGrid"]:
        return iter(self.layers)


def release_displays() -> None:
    pass
