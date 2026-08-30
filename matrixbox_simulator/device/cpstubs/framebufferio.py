"""Stand-in for CircuitPython's `framebufferio`. Instead of driving real
hardware, refresh() composites the whole root_group tree onto a canvas
sized to the panel's own fixed dimensions and hands it to the frame bridge
for broadcast to connected renderers.

Composited, not just "find a TileGrid and publish its bitmap": a Group can
nest arbitrarily deep, each layer (Group or TileGrid) can have its own x/y
offset and hidden flag, and a TileGrid's bitmap is very often larger than
the panel itself (e.g. a wide scrolling row, positioned via x so only part
of it falls within the visible area). Real hardware clips to the panel's
own bounds regardless of how large an individual bitmap is; publishing a
found bitmap's own size instead, unclipped, is what a much smaller "just
grab whatever's there" version of this used to do, and it doesn't match
what real hardware would actually show.
"""

import os
import time

import displayio
import rgbmatrix

from matrixbox_simulator.device import frame_bridge
from matrixbox_simulator.sizes import physical_mount_rotation_for

Node = displayio.Group | displayio.TileGrid

# A rough approximation of the real per-call cost of bit-banging pixels
# out over GPIO/DMA for a panel this size — not a target-fps floor. Some
# apps call refresh() many times in a row as their own speed control, and
# pacing every call to a fixed floor makes that pattern take whole
# seconds per pixel. A small, roughly-constant per-call cost gives every
# refresh()-heavy loop some real pacing without blowing that up. Starts
# at whatever refresh-fps value was launched with, but stays mutable
# since it's also adjustable live, per app, without a restart.
_refresh_fps = float(os.environ.get("MATRIXBOX_SIMULATOR_REFRESH_FPS", "0"))


def get_refresh_fps() -> float:
    return _refresh_fps


def set_refresh_fps(fps: float) -> None:
    global _refresh_fps
    _refresh_fps = max(0.0, fps)


# The most recently constructed display, so a live gamma change can
# force a fresh recomposite even when the app is idle and isn't calling
# refresh() on its own. Without this, a gamma change is invisible until
# something else touches display state, since refresh() otherwise skips
# recompositing when nothing's changed.
_active_display: "FramebufferDisplay | None" = None


def force_redraw() -> None:
    if _active_display is not None:
        _active_display._last_generation = None
        _active_display.refresh()


class FramebufferDisplay:
    def __init__(
        self,
        matrix: rgbmatrix.RGBMatrix,
        *,
        auto_refresh: bool = False,
        rotation: int = 0,
    ) -> None:
        self._matrix = matrix
        self.rotation = rotation
        self.root_group: displayio.Group | None = None
        self._last_generation: int | None = None

        global _active_display
        _active_display = self

        # Panel count for the renderer's tile-boundary overlay is set by
        # the device process itself, not here: the matrix's own tile
        # count is always 1 regardless of size, so it isn't the right
        # source for "how many physical boards" — this display doesn't
        # touch the frame bridge's tile count at all.

        # Physical dimensions, not self.width/height: refresh() always
        # publishes at the physical panel's own fixed size (rotation is
        # applied before publishing, not left for the renderer to guess
        # at), so the placeholder needs to match or the panel would
        # appear to resize once the first real frame arrives.
        frame_bridge.bridge.publish_blank(self._matrix.width, self._matrix.height)

    @property
    def width(self) -> int:
        # Real hardware swaps width/height for a 90/270 rotation, not a
        # multiple of 180 — app code (e.g. reading display.width once at
        # import time) sees the rotated, logical panel shape, not the
        # physical one. See _rotate_canvas for the pixel-level mapping
        # back to the physical panel this eventually publishes to.
        return self._matrix.height if self.rotation in (90, 270) else self._matrix.width

    @property
    def height(self) -> int:
        return self._matrix.width if self.rotation in (90, 270) else self._matrix.height

    def refresh(
        self, *, target_frames_per_second: int = 60, minimum_frames_per_second: int = 1
    ) -> None:
        group = self.root_group
        if group is None or getattr(group, "hidden", False):
            return

        # Skip everything, pacing sleep included, when nothing's changed
        # since the last one, matching real hardware's own dirty tracking.
        # Some apps call refresh() many times per logical step as their
        # own speed control, with nothing actually changing in between —
        # paying the per-call cost for those redundant calls too would
        # make that pattern take whole seconds per pixel again.
        generation = displayio.current_generation()
        if generation == self._last_generation:
            return

        self._last_generation = generation

        # Pacing hints for real hardware's auto-refresh, currently unused:
        # every refresh() call publishes immediately. See _refresh_fps for
        # the (opt-in, live-adjustable) per-call cost instead.
        if _refresh_fps > 0:
            time.sleep(1.0 / _refresh_fps)

        canvas = bytearray(self.width * self.height * 3)
        _composite(group, canvas, self.width, self.height, 0, 0)

        # Some sizes ship with their panel physically mounted rotated in
        # the enclosure, which real firmware's own rotation default
        # compensates for. The sim has no enclosure to provide that twist,
        # so it nets it back out here instead.
        mount_rotation = physical_mount_rotation_for(
            self._matrix.width, self._matrix.height
        )
        effective_rotation = (self.rotation - mount_rotation) % 360

        physical = _rotate_canvas(
            canvas,
            self.width,
            self.height,
            self._matrix.width,
            self._matrix.height,
            effective_rotation,
        )
        frame_bridge.bridge.publish(self._matrix.width, self._matrix.height, physical)


def _rotate_canvas(
    canvas: bytearray,
    logical_width: int,
    logical_height: int,
    physical_width: int,
    physical_height: int,
    rotation: int,
) -> bytes:
    # Maps the just-composited canvas (sized to the logical, possibly
    # rotated width/height app code sees) onto the physical panel's own
    # fixed layout, matching real hardware's own settings["rotation"]
    # (0/90/180/270, user-configurable from the device's own settings
    # UI). A 180 rotation, for instance, is what turns content anchored
    # near x=0 (the left edge as app code sees it) into something that
    # shows up on the right edge on the real device.
    if rotation == 0:
        return bytes(canvas)

    physical = bytearray(physical_width * physical_height * 3)
    for ly in range(logical_height):
        for lx in range(logical_width):
            if rotation == 180:
                px, py = physical_width - 1 - lx, physical_height - 1 - ly
            elif rotation == 90:
                px, py = physical_width - 1 - ly, lx
            elif rotation == 270:
                px, py = ly, physical_height - 1 - lx
            else:
                px, py = lx, ly  # unrecognized value: pass through unrotated

            src_i = (ly * logical_width + lx) * 3
            dst_i = (py * physical_width + px) * 3
            physical[dst_i : dst_i + 3] = canvas[src_i : src_i + 3]

    return bytes(physical)


def _composite(
    node: Node, canvas: bytearray, width: int, height: int, offset_x: int, offset_y: int
) -> None:
    if getattr(node, "hidden", False):
        return

    if isinstance(node, displayio.TileGrid):
        _blit(node, canvas, width, height, offset_x, offset_y)
        return

    for layer in getattr(node, "layers", []):
        _composite(
            layer,
            canvas,
            width,
            height,
            offset_x + getattr(layer, "x", 0),
            offset_y + getattr(layer, "y", 0),
        )


def _blit(
    tile: displayio.TileGrid,
    canvas: bytearray,
    width: int,
    height: int,
    offset_x: int,
    offset_y: int,
) -> None:
    bitmap = tile.bitmap
    shader = tile.pixel_shader

    # Clip to the intersection with the canvas up front. A source bitmap
    # can be much larger than the panel (e.g. a wide scrolling row, mostly
    # positioned off-canvas), and iterating its full extent just to throw
    # most pixels away per-pixel is real, wasted work happening inside
    # every refresh() call, which apps often pace their own animation
    # timing against.
    sy_start = max(0, -offset_y)
    sy_end = min(bitmap.height, height - offset_y)
    sx_start = max(0, -offset_x)
    sx_end = min(bitmap.width, width - offset_x)

    for sy in range(sy_start, sy_end):
        dy = offset_y + sy
        row = dy * width

        for sx in range(sx_start, sx_end):
            dx = offset_x + sx

            resolved = shader.resolve(bitmap[sx, sy])
            if resolved is None:
                continue  # transparent: leave whatever's already there

            i = (row + dx) * 3
            canvas[i], canvas[i + 1], canvas[i + 2] = resolved
