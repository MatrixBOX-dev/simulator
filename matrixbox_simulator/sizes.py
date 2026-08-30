"""Real product sizes: (width, height, panel count). Shared by the device
process's own size cycling and the renderer's device selection, so both
stay in sync.

Panel count isn't a real hardware setting — firmware always reports a
single tile regardless of size. It's a sim-only, display-side value for
the renderer's optional tile-boundary overlay, describing how many
64px-wide boards X/XL actually chain together. 2X's count is unconfirmed;
assumed 1 (a single matrix).
"""

SIZE_PRESETS: dict[str, tuple[int, int, int]] = {
    "XS": (64, 32, 1),
    "X": (128, 32, 2),
    "XL": (192, 32, 3),
    "2X": (128, 64, 1),
}
SIZE_CYCLE: list[str] = ["XS", "X", "XL", "2X"]


def size_name_for(width: int | None, height: int | None) -> str | None:
    return next(
        (
            name
            for name, (w, h, _panels) in SIZE_PRESETS.items()
            if (w, h) == (width, height)
        ),
        None,
    )


def panel_count_for(width: int | None, height: int | None) -> int:
    # An unrecognized size (a custom width/height) defaults to 1, no seams.
    name = size_name_for(width, height)

    return SIZE_PRESETS[name][2] if name is not None else 1


# XL's panel ships mounted rotated 180 degrees in its enclosure, so
# firmware defaults rotation to 180 for XL to compensate — meaningless on
# its own, it only reads upright combined with that physical twist. The
# sim has no enclosure to provide that twist for free, so the display
# compositor nets it back out at render time instead (see
# physical_mount_rotation_for). Same constant, read two ways: seeded
# forward into settings for real-hardware fidelity, netted back out for
# rendering.
ROTATION_OVERRIDES: dict[str, int] = {"XL": 180}


def physical_mount_rotation_for(width: int | None, height: int | None) -> int:
    name = size_name_for(width, height)

    return ROTATION_OVERRIDES.get(name, 0) if name is not None else 0
