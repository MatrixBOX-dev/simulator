# matrixbox-simulator

A desktop simulator for [matrixbox][matrixbox], the [CircuitPython][circuitpython]
app that drives an LED matrix on a Waveshare ESP32-S3-Zero. It runs an
app's real, unmodified code on your desktop Python interpreter instead of
reimplementing it, and streams what it draws to a terminal renderer so you
can see it without touching hardware.

![demo](./asset/sim-image.png)

It's two halves that talk over a local WebSocket:

- **`matrixbox_simulator.device`**: a Python package that stands in for the
  CircuitPython modules matrixbox needs (`displayio`, `rgbmatrix`,
  `bitmaptools`, `wifi`, and so on), backed by plain Python instead of real
  hardware. It runs an app's actual code, and every `display.refresh()`
  gets pushed out as a frame. An app runs from a staged copy of its own
  code, kept in a gitignored folder so settings it saves (brightness,
  Wi-Fi, whatever it writes to `settings.txt`) survive between runs, the
  same way they'd survive a reflash on real hardware.
- **`matrixbox_simulator.term`**: a terminal renderer, built on `rich`, that
  connects, decodes those frames, and draws them with Unicode half-blocks
  and truecolor. Framed in a white border, with a stats line underneath
  (app name, measured FPS, real CPU load from matrixbox's own tick-load
  tracker, and the simulator's peak memory use, which is not the device's
  real heap, just a rough signal for whether an app is leaking).

## Requirements

- [`uv`][uv]
- A local checkout of [matrixbox][matrixbox-source]. By default it's expected as
  a sibling directory (`../matrixbox`), but any app directory can be pointed at
  explicitly (see below).

## Quick start

Terminal 1, run an app:

```sh
uv run matrixbox app clock
```

Terminal 2, watch it:

```sh
uv run matrixbox simulator --connect ws://127.0.0.1:9191
```

The renderer waits for the simulator if it isn't up yet, and reconnects
automatically if you stop it to switch apps, so you can just leave it
running.

You can point it at an app anywhere, not just `../matrixbox/apps/*`.
Whatever directory you give it gets staged and launched automatically.

Point it at a whole checkout's root instead of one app directory and it
boots the checkout as a system, not a single app: no app starts
automatically, so you land on its own home menu and can switch between
apps from there, the same way you would on the real device.

While an app is running, its real settings page is served too:

```sh
http://127.0.0.1:8080/
```

Port 80 is what the device would use, but unprivileged desktop processes
can't bind it, so it's remapped. Override with `MATRIXBOX_SIMULATOR_HTTP_PORT`
if you need a different one.

No app running yet? The renderer draws an animated demo pattern on its own
when run without `--connect`, which is a quick way to check it's working:

```sh
uv run matrixbox simulator
```

## Panel sizes

`uv run matrixbox app --size {XS,X,XL,2X}` picks a real product size
(width and height together). `uv run matrixbox simulator --device
{XS,X,XL,2X}` sizes the
demo pattern and the placeholder box to match, for watching a specific
size without a device connected; combining it with `--width`/`--height`
is an error, since they'd conflict.

| Size | Dimensions | Panels |
| ---- | ---------- | ------ |
| `XS` | 64x32      | 1      |
| `X`  | 128x32     | 2      |
| `XL` | 192x32     | 3      |
| `2X` | 128x64     | 1      |

X and XL aren't one wide panel, they're 2 or 3 separate 64x32 boards side
by side (`2X`'s own panel count isn't confirmed; assumed a single matrix
for now). That panel count is purely a display-side idea for the
renderer's tile-boundary overlay below, not a real setting: real
firmware's own size-preset endpoint sets `settings["tiles"]` to `1`
regardless of size, so this sim does too. `--width`/`--height` still work
directly for anything else, with no seam overlay available.

Real hardware only reads its panel geometry from `settings.txt` at boot,
and reboots whenever it changes there, since the wiring can't be
reconfigured live. This sim does the same: changing width/height from
the real settings UI reboots the sim process the same way, and `z`
cycles through the four sizes live from the terminal running `uv run
app`, also via a real restart. Either way, whatever an app already saved
survives the restart, same as a real reboot.

Selecting XL also rotates the panel 180 degrees, on real hardware and
here. That's not a bug, it's what the real firmware's own preset
endpoint does too, presumably compensating for how that specific
enclosure is physically wired. It also doesn't reset rotation back when
you switch away from XL to something else, apparently never addressed on
real firmware either, so this mirrors that as-is.

## Controls

Typed into whichever terminal is running `uv run matrixbox app` (it
needs a real terminal, not a redirected or piped one):

- `s` / `l`: short or long front-panel button press. Long usually exits
  the app, same as holding the real button.
- `r`: reload the running app, only offered when booted at a checkout's
  root (not a single app). Copies that app's code fresh from your
  checkout, then triggers the same exit a long press would, so it comes
  back running your changes without a full restart.
- `R`: restart the whole process, picking up a core code change (not
  just an app's own) rather than requiring a manual stop and rerun.
- `+` / `-`: adjust refresh pacing live. See "Animation speed" below.
- `[` / `]`: adjust color gamma live. See "Colors" below.
- `z`: cycle through panel sizes live. See "Panel sizes" above.

Typed into whichever terminal is running `uv run matrixbox simulator`
instead:

- `t`: toggle a guide line at each panel seam (see "Panel sizes"). Off by
  default, since it isn't something the real device shows, just a design
  aid for content that might straddle a seam. Current state always shows
  in the stats line underneath the panel.

## Animation speed

Real hardware paces itself: a `display.refresh()` call takes real time to
bit-bang pixels out over GPIO, and CPU-heavier apps do more work between
calls. This sim's `refresh()` is close to free, so how an app was written
determines whether that matters:

- Apps that pace themselves against elapsed real time (`time.monotonic()`
  deltas, explicit `time.sleep()`) run correctly regardless, since
  they're not depending on refresh() cost for anything. `lastfm` is like
  this.
- Apps with no pacing of their own, just state advanced once per loop
  iteration, run however fast this sim's loop can spin, which is usually
  much faster than real hardware. `screensaver`'s built-in savers are
  like this.
- Apps that call `refresh()` many times per logical step as their own
  deliberate speed control (more calls, more real time, slower) lose
  that control entirely once refresh() is free. `departures`' scroll
  pacing is like this.

`--refresh-fps <n>` (and live `+`/`-`) gives `refresh()` a small,
roughly-constant per-call cost, approximating real hardware's own
bit-banging time, so an app that depends on that cost for pacing gets it
back. Off (0) by default. There's no way to know or guess the right
value without testing against the real device: how CPU-heavy a given
app's own logic is between `refresh()` calls varies per app, so this is
one knob standing in for something that's actually app-specific, not a
universal setting. Expect to tune it per app, and expect some apps (like
`lastfm`) to need it left alone entirely.

Separately, the sim caps how often it actually broadcasts a _changed_
frame to the renderer (currently 120fps). That's pure safety, protecting
the renderer from a flood of frames it can't coherently draw, not an
attempt to model real timing. It doesn't push back on `--refresh-fps`,
and it never delays a frame that hasn't actually changed.

Values found to look right against real hardware so far:

| App                       | `--refresh-fps` |
| ------------------------- | --------------- |
| `departures`              | 120             |
| `screensaver` (aquarium)  | 20              |
| `screensaver` (fireworks) | 60              |
| `screensaver` (rain)      | 24              |
| `screensaver` (space)     | 90-100          |
| `screensaver` (starcloud) | 90-100          |

`screensaver` picks which saver runs from its own settings, so the right
value depends on which one is active. Everything else, `lastfm` included,
looks right left alone (0, off).

## Colors

Some apps render noticeably dimmer here than they look in person. The
likely cause: a raw PWM-driven LED at a given RGB value reads brighter to
the eye than the same value does on an LCD or terminal, and colors were
tuned by eye against the real panel, not a monitor.

`stocks` is a clean example: its own code sets its "white" text color to
`(50,50,50)` directly, no per-app setting involved at all, so there's
nothing to tune except the color itself being a genuinely low RGB value.
`scroller` instead reaches for the framework's own shared display
library's "white", which is `(20,20,20)` there (`(100,100,100)` for
"brightwhite"). Different apps, different actual values, same underlying
gap between what looks right on an LED panel and what looks right on a
screen.

`departures` looked like a different problem at first, since it has its
own `brightness` setting that visibly helps when turned up. But the real
device's own `settings.txt` has that setting at 0 (unscaled, no boost)
and still looks right in person, which means the dimness at 0 isn't
about departures' setting at all, it's the same LED-vs-monitor gap as
stocks and scroller. Turning brightness up in the sim was papering over
that gap with an app setting that happened to be available, not fixing
the actual cause.

`--gamma <n>` (and live `[`/`]`) corrects for this by boosting dim values
more than bright ones before they're drawn. Off (1.0) by default.

In practice this ends up per-app rather than one fixed panel constant:
different apps lean on different parts of the color range, and gamma is
a curve, not a flat offset, so how much correction looks right shifts
with it. Values found to look right so far:

| App                      | `--gamma` |
| ------------------------ | --------- |
| `departures`             | 3.0       |
| `screensaver` (aquarium) | 2.4       |

As a rule of thumb, `stocks`' own `(50,50,50)` "white" needs roughly
`--gamma 5.0` to actually read as white here rather than grey. Values
like this are deliberately low, not `(255,255,255)`, since a raw LED at
full brightness would be blinding in person; a color that's already
`(255,255,255)` looks white in the sim with no gamma correction at all
(there's no headroom left to boost), which is exactly why apps don't use
it. Start around 5.0 for a similarly low "should read as white" value
and adjust live with `[`/`]` for anything not listed.

## Useful flags

`uv run matrixbox app`: `--size` or `--width` / `--height` for panel
size (default 128x32, see "Panel sizes" above), `--ws-host` / `--ws-port`
(default `127.0.0.1:9191`), `--reset` to wipe this app's saved settings
and start fresh, `--refresh-fps` / `--gamma` (see above).

`uv run matrixbox simulator`: `--connect <url>` (omit for demo mode),
`--device` or `--width` / `--height` for the demo/placeholder size (see
"Panel sizes" above), `--fps` (demo mode only).

## Limitations

- `Group(scale=...)` is accepted but not honored: nothing renders
  upscaled. Position (`x`/`y`), visibility (`hidden`), and palette
  transparency (`make_transparent`) are all composited correctly, at any
  nesting depth.
- `bitmaptools.alphablend` only supports the `RGB565_SWAPPED` colorspace,
  which is what the `gif` app uses. Other colorspaces raise
  `NotImplementedError`.

## Development

```sh
uv run ruff format . && uv run ruff check . && uv run ty check .
```

[matrixbox]: https://www.matrixbox.app
[matrixbox-source]: https://github.com/MatrixBOX-dev/matrixbox
[circuitpython]: https://circuitpython.org/
[uv]: https://docs.astral.sh/uv/
