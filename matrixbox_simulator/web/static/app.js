"use strict";

// Mirrors the `matrixbox app` process's own keys and control names, kept in
// sync by hand since that's a separate (Python) process. Each key here is
// sent on to the app, so it works the same as typing it in that terminal.
const APP_CONTROLS = [
  ["n", "toggle wifi on/off", { n: "toggle_wifi" }],
  ["s", "short button press", { s: "short_press" }],
  ["l", "long button press (usually exits the app)", { l: "long_press" }],
  ["r", "reload (restarts)", { r: "reload" }],
  ["+/-", "refresh-fps", { "+": "refresh_fps_up", "-": "refresh_fps_down" }],
  ["[/]", "gamma", { "[": "gamma_down", "]": "gamma_up" }],
  ["z", "cycle panel size", { z: "cycle_size" }],
];
const CONTROL_KEYS = Object.assign({}, ...APP_CONTROLS.map(([, , controls]) => controls));

const FRAME_MAGIC = 0xf3;
const STATS_MAGIC = 0xf4;
const WIRE_VERSION = 1;
const FRAME_HEADER_LENGTH = 8;
const STATS_HAS_CPU_PERCENT = 0b01;
const STATS_HAS_RSS_KB = 0b10;

// Look of the LEDs, taken from the departures-plus README renderer so both
// read the same: https://github.com/jnbp/matrixbox-departures-plus
const LIT_THRESHOLD = 24;
const GLOW_RADIUS_PER_LED = 0.7;
const GLOW_STRENGTH = 0.55;
const DOT_RADIUS_PER_LED = 0.36;
const FACE_COLOR = "rgb(7, 7, 9)";
const UNLIT_DOT_COLOR = "rgb(20, 20, 23)";
const HOUSING_COLOR = "rgb(26, 26, 30)";
const SEAM_COLOR = "rgba(150, 150, 170, 0.7)";

// Enclosure proportions in LEDs, eyeballed from product photos.
const PANEL_HOUSING_LEDS = 3;
const DEVICE_BEZEL_LEDS = 3.5;
const DEVICE_DEPTH_LEDS = 22;

const BUTTON_SEGMENTS = 32;
const BUTTON_COLLAR = { radius: 4.5, height: 0.6, lightness: 9 };
const BUTTON_CAP = { radius: 2.6, height: 1.8, lightness: 16 };
const PLUG_LENGTH_LEDS = 8;

const CABLE_POINTS = 28;
const CABLE_SLACK = 1.1;
const CABLE_GRAVITY = 0.6;
const CABLE_DAMPING = 0.96;
const CABLE_ITERATIONS = 24;
const CABLE_THICKNESS_LEDS = 1.9;

const DEFAULT_ROTATION = { x: -16, y: 24 };
const RECONNECT_DELAY_MS = 500;

class WireDecoder {
  decode(buffer) {
    const view = new DataView(buffer);
    const magic = view.getUint8(0);
    if (view.getUint8(1) !== WIRE_VERSION) {
      throw new Error(`unsupported protocol version ${view.getUint8(1)}`);
    }

    if (magic === FRAME_MAGIC) {
      return this.decodeFrame(view, buffer);
    }

    if (magic === STATS_MAGIC) {
      return this.decodeStats(view, buffer);
    }

    throw new Error(`bad magic byte ${magic}`);
  }

  decodeFrame(view, buffer) {
    const width = view.getUint16(2, true);
    const height = view.getUint16(4, true);
    const tiles = Math.max(1, view.getUint8(7));
    const pixels = new Uint8Array(buffer, FRAME_HEADER_LENGTH);
    if (pixels.length !== width * height * 3) {
      throw new Error(`expected ${width * height * 3} pixel bytes, got ${pixels.length}`);
    }

    return { type: "frame", width, height, tiles, pixels };
  }

  decodeStats(view, buffer) {
    const flags = view.getUint8(2);
    const appLength = view.getUint8(3);
    const app = new TextDecoder().decode(new Uint8Array(buffer, 4, appLength));
    let cursor = 4 + appLength;
    const fps = view.getFloat32(cursor, true);
    cursor += 4;

    let cpuPercent = null;
    if (flags & STATS_HAS_CPU_PERCENT) {
      cpuPercent = view.getFloat32(cursor, true);
      cursor += 4;
    }

    let rssKb = null;
    if (flags & STATS_HAS_RSS_KB) {
      rssKb = view.getUint32(cursor, true);
    }

    return { type: "stats", app, fps, cpuPercent, rssKb };
  }
}

class LedRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.pixelCanvas = document.createElement("canvas");
    this.litCanvas = document.createElement("canvas");
    this.dotMask = document.createElement("canvas");
    this.background = document.createElement("canvas");
    this.supportsFilter = typeof this.context.filter === "string";
    this.frame = null;
    this.housing = true;
    this.showSeams = false;
    this.ledSize = 0;
    this.layoutKey = "";
  }

  setFrame(frame) {
    this.frame = frame;
  }

  // Fits the panel into the given CSS box, picking a whole number of device
  // pixels per LED so every dot comes out the same shape.
  layout(maxCssWidth, maxCssHeight) {
    if (!this.frame) {
      return;
    }

    const pixelRatio = window.devicePixelRatio || 1;
    const padding = this.housing ? PANEL_HOUSING_LEDS * 2 : 0;
    const ledsWide = this.frame.width + padding;
    const ledsHigh = this.frame.height + padding;
    const ledSize = Math.max(
      2,
      Math.floor(
        Math.min(
          (maxCssWidth * pixelRatio) / ledsWide,
          (maxCssHeight * pixelRatio) / ledsHigh,
        ),
      ),
    );
    const key = [this.frame.width, this.frame.height, ledSize, this.housing, pixelRatio].join();
    if (key === this.layoutKey) {
      return;
    }

    this.layoutKey = key;
    this.ledSize = ledSize;
    this.canvas.width = ledsWide * ledSize;
    this.canvas.height = ledsHigh * ledSize;
    this.canvas.style.width = `${this.canvas.width / pixelRatio}px`;
    this.canvas.style.height = `${this.canvas.height / pixelRatio}px`;
    this.buildLayers();
  }

  cssLedSize() {
    return this.ledSize / (window.devicePixelRatio || 1);
  }

  buildLayers() {
    const { width, height } = this.frame;
    const size = this.ledSize;
    const faceOffset = this.faceOffset();

    this.pixelCanvas.width = width;
    this.pixelCanvas.height = height;
    this.litCanvas.width = width * size;
    this.litCanvas.height = height * size;
    this.dotMask.width = width * size;
    this.dotMask.height = height * size;
    this.background.width = this.canvas.width;
    this.background.height = this.canvas.height;

    const mask = this.dotMask.getContext("2d");
    mask.fillStyle = "#fff";
    this.traceDots(mask, width, height, size);
    mask.fill();

    const background = this.background.getContext("2d");
    if (this.housing) {
      background.fillStyle = HOUSING_COLOR;
      background.beginPath();
      background.roundRect(0, 0, this.background.width, this.background.height, size * 2);
      background.fill();
    }

    background.fillStyle = FACE_COLOR;
    background.fillRect(faceOffset, faceOffset, width * size, height * size);
    background.save();
    background.translate(faceOffset, faceOffset);
    background.fillStyle = UNLIT_DOT_COLOR;
    this.traceDots(background, width, height, size);
    background.fill();
    background.restore();
  }

  traceDots(context, width, height, size) {
    const radius = size * DOT_RADIUS_PER_LED;
    context.beginPath();
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const centerX = x * size + size / 2;
        const centerY = y * size + size / 2;
        context.moveTo(centerX + radius, centerY);
        context.arc(centerX, centerY, radius, 0, Math.PI * 2);
      }
    }
  }

  faceOffset() {
    return this.housing ? PANEL_HOUSING_LEDS * this.ledSize : 0;
  }

  draw() {
    if (!this.frame || !this.ledSize) {
      return;
    }

    const { width, height, pixels } = this.frame;
    const size = this.ledSize;
    const faceOffset = this.faceOffset();
    const faceWidth = width * size;
    const faceHeight = height * size;

    // Near-black is left fully dark, the same cut-off the reference uses,
    // so faint noise doesn't light up dots a real panel would show as off.
    const image = new ImageData(width, height);
    for (let source = 0, target = 0; source < pixels.length; source += 3, target += 4) {
      const red = pixels[source];
      const green = pixels[source + 1];
      const blue = pixels[source + 2];
      if (red + green + blue > LIT_THRESHOLD) {
        image.data[target] = red;
        image.data[target + 1] = green;
        image.data[target + 2] = blue;
      }

      image.data[target + 3] = 255;
    }

    this.pixelCanvas.getContext("2d").putImageData(image, 0, 0);

    const lit = this.litCanvas.getContext("2d");
    lit.globalCompositeOperation = "copy";
    lit.imageSmoothingEnabled = false;
    lit.drawImage(this.pixelCanvas, 0, 0, faceWidth, faceHeight);
    lit.globalCompositeOperation = "destination-in";
    lit.drawImage(this.dotMask, 0, 0);

    const context = this.context;
    context.save();
    context.globalCompositeOperation = "copy";
    context.drawImage(this.background, 0, 0);

    context.beginPath();
    context.rect(faceOffset, faceOffset, faceWidth, faceHeight);
    context.clip();

    context.globalCompositeOperation = "lighter";
    context.globalAlpha = GLOW_STRENGTH;
    if (this.supportsFilter) {
      context.filter = `blur(${size * GLOW_RADIUS_PER_LED}px)`;
      context.drawImage(this.litCanvas, faceOffset, faceOffset);
      context.filter = "none";
    } else {
      // Without canvas filters (older Safari), a smoothed upscale of the
      // raw pixels is a close enough stand-in for a blur.
      context.imageSmoothingEnabled = true;
      context.drawImage(this.pixelCanvas, faceOffset, faceOffset, faceWidth, faceHeight);
    }

    context.globalAlpha = 1;
    context.globalCompositeOperation = "lighten";
    context.drawImage(this.litCanvas, faceOffset, faceOffset);

    if (this.showSeams && this.frame.tiles > 1) {
      this.drawSeams(context, faceOffset, faceHeight);
    }

    context.restore();
  }

  drawSeams(context, faceOffset, faceHeight) {
    const tileWidth = Math.floor(this.frame.width / this.frame.tiles);
    context.globalCompositeOperation = "source-over";
    context.strokeStyle = SEAM_COLOR;
    context.lineWidth = Math.max(1, Math.round(this.ledSize / 4));
    context.setLineDash([this.ledSize, this.ledSize]);
    context.beginPath();
    for (let tile = 1; tile < this.frame.tiles; tile++) {
      const x = faceOffset + tile * tileWidth * this.ledSize;
      context.moveTo(x, faceOffset);
      context.lineTo(x, faceOffset + faceHeight);
    }

    context.stroke();
  }
}

class DeviceMockup {
  constructor(box, scene) {
    this.box = box;
    this.scene = scene;
    this.rotation = { ...DEFAULT_ROTATION };
    this.dragStart = null;
    this.ledCssSize = 0;
    this.onRotate = () => {};

    const button = document.getElementById("device-button");
    this.buttonCap = buildCylinder("cap", BUTTON_CAP, 0);
    this.buttonCapRest = this.buttonCap.style.transform;
    button.append(buildCylinder("collar", BUTTON_COLLAR, 0), this.buttonCap);
    this.onButton = () => {};
    this.bindButton(button);
    this.plug = buildPlug(document.getElementById("plug"));

    scene.addEventListener("pointerdown", (event) => this.startDrag(event));
    scene.addEventListener("pointermove", (event) => this.drag(event));
    scene.addEventListener("pointerup", () => this.endDrag());
    scene.addEventListener("pointercancel", () => this.endDrag());
    scene.addEventListener("dblclick", () => {
      this.rotation = { ...DEFAULT_ROTATION };
      this.applyRotation();
    });
    this.applyRotation();
  }

  // Sizes the enclosure around the screen, keeping bezel and depth in LED
  // units so every panel size gets a box of believable proportions.
  fit(screenCssWidth, screenCssHeight, ledCssSize) {
    const bezel = DEVICE_BEZEL_LEDS * ledCssSize;
    const style = this.box.style;
    this.ledCssSize = ledCssSize;
    style.setProperty("--box-width", `${screenCssWidth + bezel * 2}px`);
    style.setProperty("--box-height", `${screenCssHeight + bezel * 2}px`);
    style.setProperty("--box-depth", `${DEVICE_DEPTH_LEDS * ledCssSize}px`);
    style.setProperty("--bezel", `${bezel}px`);
    style.setProperty("--led", `${ledCssSize}px`);
  }

  // The button follows the real one: pressed for exactly as long as it's
  // held, so apps that react to hold length behave the same.
  bindButton(button) {
    const release = (event) => {
      if (!button.classList.contains("pressed")) {
        return;
      }

      event.stopPropagation();
      button.classList.remove("pressed");
      this.buttonCap.style.transform = this.buttonCapRest;
      this.onButton(false);
    };

    button.addEventListener("pointerdown", (event) => {
      // Keeps the press from also starting a rotation drag.
      event.stopPropagation();
      button.setPointerCapture(event.pointerId);
      button.classList.add("pressed");
      this.buttonCap.style.transform = `translateZ(calc(var(--led) * ${-BUTTON_CAP.height * 0.6}))`;
      this.onButton(true);
    });
    button.addEventListener("pointerup", release);
    button.addEventListener("pointercancel", release);
    button.addEventListener("dblclick", (event) => event.stopPropagation());
  }

  // Where the cable leaves the plug, projected to the screen, plus a point
  // a little further out so the cable starts off in the plug's direction.
  plugExit() {
    return { tip: centerOf(this.plug.tip), ahead: centerOf(this.plug.ahead) };
  }

  // The plug sits on the left side, which turns away from the viewer once
  // the box is rotated past straight on.
  plugFacesAway() {
    return this.rotation.y < 0;
  }

  startDrag(event) {
    this.dragStart = { x: event.clientX, y: event.clientY, rotation: { ...this.rotation } };
    this.scene.setPointerCapture(event.pointerId);
    this.scene.classList.add("dragging");
  }

  drag(event) {
    if (!this.dragStart) {
      return;
    }

    const deltaX = event.clientX - this.dragStart.x;
    const deltaY = event.clientY - this.dragStart.y;
    this.rotation = {
      x: clamp(this.dragStart.rotation.x - deltaY * 0.3, -50, 15),
      y: clamp(this.dragStart.rotation.y + deltaX * 0.3, -75, 75),
    };
    this.applyRotation();
  }

  endDrag() {
    this.dragStart = null;
    this.scene.classList.remove("dragging");
  }

  applyRotation() {
    this.box.style.setProperty("--rotate-x", `${this.rotation.x}deg`);
    this.box.style.setProperty("--rotate-y", `${this.rotation.y}deg`);
    this.onRotate();
  }
}

// A cable hanging from the plug to beyond the window's left edge, simulated
// as a verlet rope so it sags under gravity and swings as the device turns.
// See https://www.gamedeveloper.com/programming/advanced-character-physics
class CableRope {
  constructor(svg, device) {
    this.svg = svg;
    this.device = device;
    this.paths = {
      shade: svg.querySelector(".cable-shade"),
      body: svg.querySelector(".cable-body"),
      highlight: svg.querySelector(".cable-highlight"),
    };
    this.points = [];
    this.running = false;
    this.thickness = 0;
    this.endY = 0;
    this.restingFrames = 0;
  }

  start() {
    this.points = [];
    this.endY = 0;
    this.wake();
  }

  stop() {
    this.running = false;
  }

  wake() {
    this.restingFrames = 0;
    if (this.running) {
      return;
    }

    this.running = true;
    window.requestAnimationFrame(() => this.tick());
  }

  tick() {
    if (!this.running) {
      return;
    }

    const ends = this.measureEnds();
    if (ends) {
      this.thickness = ends.thickness;
      const moved = this.step(ends);
      this.draw();
      this.restingFrames = moved < 0.05 ? this.restingFrames + 1 : 0;
    }

    // Settled ropes stop simulating until the next rotation or resize wakes
    // them, so an idle page isn't burning a frame loop.
    if (this.restingFrames > 30) {
      this.running = false;
      return;
    }

    window.requestAnimationFrame(() => this.tick());
  }

  measureEnds() {
    const bounds = this.svg.getBoundingClientRect();
    if (!bounds.width) {
      return null;
    }

    const { tip, ahead } = this.device.plugExit();
    const thickness = CABLE_THICKNESS_LEDS * this.device.ledCssSize;
    if (!this.endY) {
      // Fixed once per layout, a little below where the plug starts out,
      // so the cable leaves the window close by instead of sweeping across it.
      this.endY = Math.min(bounds.height - thickness, tip.y - bounds.top + bounds.height * 0.12);
    }

    return {
      tip: { x: tip.x - bounds.left, y: tip.y - bounds.top },
      ahead: { x: ahead.x - bounds.left, y: ahead.y - bounds.top },
      end: { x: -thickness * 4, y: this.endY },
      thickness,
    };
  }

  step({ tip, ahead, end }) {
    const free = CABLE_POINTS - 1;
    if (this.points.length !== CABLE_POINTS) {
      this.points = Array.from({ length: CABLE_POINTS }, (_, index) => {
        const share = index / free;
        const x = ahead.x + (end.x - ahead.x) * share;
        const y = ahead.y + (end.y - ahead.y) * share;

        return { x, y, previousX: x, previousY: y };
      });
    }

    let moved = 0;
    for (const point of this.points) {
      const velocityX = (point.x - point.previousX) * CABLE_DAMPING;
      const velocityY = (point.y - point.previousY) * CABLE_DAMPING;
      point.previousX = point.x;
      point.previousY = point.y;
      point.x += velocityX;
      point.y += velocityY + CABLE_GRAVITY;
      moved = Math.max(moved, Math.abs(velocityX) + Math.abs(velocityY));
    }

    // Slack follows the current span, so turning the device never stretches
    // the cable taut or leaves metres of it piled up.
    const span = Math.hypot(end.x - ahead.x, end.y - ahead.y) + Math.hypot(ahead.x - tip.x, ahead.y - tip.y);
    const segmentLength = (span * CABLE_SLACK) / free;
    const last = this.points.length - 1;
    for (let iteration = 0; iteration < CABLE_ITERATIONS; iteration++) {
      this.pin(0, tip);
      this.pin(1, ahead);
      this.pin(last, end);
      for (let index = 1; index < last; index++) {
        this.constrain(this.points[index], this.points[index + 1], segmentLength, index === 1, index + 1 === last);
      }
    }

    this.pin(0, tip);
    this.pin(1, ahead);
    this.pin(last, end);

    return moved;
  }

  pin(index, position) {
    const point = this.points[index];
    point.x = position.x;
    point.y = position.y;
  }

  constrain(first, second, length, firstPinned, secondPinned) {
    const deltaX = second.x - first.x;
    const deltaY = second.y - first.y;
    const distance = Math.hypot(deltaX, deltaY) || 1;
    const correction = (distance - length) / distance;
    const firstShare = firstPinned ? 0 : secondPinned ? 1 : 0.5;
    const secondShare = 1 - firstShare;
    first.x += deltaX * correction * firstShare;
    first.y += deltaY * correction * firstShare;
    second.x -= deltaX * correction * secondShare;
    second.y -= deltaY * correction * secondShare;
  }

  draw() {
    const path = smoothPath(this.points);
    const thickness = this.thickness || 6;
    this.svg.classList.toggle("behind", this.device.plugFacesAway());
    for (const element of Object.values(this.paths)) {
      element.setAttribute("d", path);
    }

    this.paths.shade.style.strokeWidth = `${thickness}px`;
    this.paths.body.style.strokeWidth = `${thickness * 0.8}px`;
    this.paths.highlight.style.strokeWidth = `${thickness * 0.28}px`;
    this.paths.highlight.setAttribute("transform", `translate(0 ${-thickness * 0.16})`);
  }
}

// Builds a solid cylinder standing up out of its parent face: a ring of
// thin wall strips, shaded by angle as if lit from the front left, plus a
// top disc. Sizes are in LEDs so it scales with the enclosure.
function buildCylinder(className, { radius, height, lightness }, base) {
  const cylinder = document.createElement("div");
  cylinder.className = `cylinder ${className}`;
  cylinder.style.transform = `translateZ(calc(var(--led) * ${base}))`;
  const stripWidth = ((2 * Math.PI * radius) / BUTTON_SEGMENTS) * 1.08;

  for (let segment = 0; segment < BUTTON_SEGMENTS; segment++) {
    const angle = (360 * segment) / BUTTON_SEGMENTS;
    const shade = lightness + 9 * Math.cos(((angle - 45) * Math.PI) / 180);
    const wall = document.createElement("div");
    wall.className = "cylinder-wall";
    wall.style.left = `calc(var(--led) * ${-stripWidth / 2})`;
    wall.style.width = `calc(var(--led) * ${stripWidth})`;
    wall.style.height = `calc(var(--led) * ${height})`;
    wall.style.background = `hsl(240 4% ${shade}%)`;
    wall.style.transform = `rotateZ(${angle}deg) translateY(calc(var(--led) * ${radius})) rotateX(90deg)`;
    cylinder.append(wall);
  }

  const top = document.createElement("div");
  top.className = "cylinder-top";
  top.style.left = `calc(var(--led) * ${-radius})`;
  top.style.top = `calc(var(--led) * ${-radius})`;
  top.style.width = `calc(var(--led) * ${radius * 2})`;
  top.style.height = `calc(var(--led) * ${radius * 2})`;
  top.style.transform = `translateZ(calc(var(--led) * ${height}))`;
  cylinder.append(top);

  return cylinder;
}

// Builds the plug as a closed box sticking straight out of the side face,
// with two invisible markers the cable hangs from.
function buildPlug(plug) {
  const length = `calc(var(--led) * ${PLUG_LENGTH_LEDS})`;
  const sides = [
    ["end", { inset: "0", transform: `translateZ(${length})` }],
    ["upper", { left: "0", top: `calc(${length} * -1)`, width: "100%", height: length, transformOrigin: "bottom", transform: "rotateX(-90deg)" }],
    ["lower", { left: "0", top: "100%", width: "100%", height: length, transformOrigin: "top", transform: "rotateX(90deg)" }],
    ["back", { left: `calc(${length} * -1)`, top: "0", width: length, height: "100%", transformOrigin: "right", transform: "rotateY(90deg)" }],
    ["front", { left: "100%", top: "0", width: length, height: "100%", transformOrigin: "left", transform: "rotateY(-90deg)" }],
  ];

  for (const [name, style] of sides) {
    const side = document.createElement("div");
    side.className = `plug-side ${name}`;
    Object.assign(side.style, style);
    plug.append(side);
  }

  const tip = document.createElement("div");
  tip.className = "cable-anchor";
  tip.style.transform = `translateZ(${length})`;
  const ahead = document.createElement("div");
  ahead.className = "cable-anchor";
  ahead.style.transform = `translateZ(calc(var(--led) * ${PLUG_LENGTH_LEDS + 5}))`;
  plug.append(tip, ahead);

  return { tip, ahead };
}

function centerOf(element) {
  const rect = element.getBoundingClientRect();

  return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

// Curves through the midpoints between points, so the rope reads as one
// smooth cable rather than a chain of straight segments.
function smoothPath(points) {
  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 1; index < points.length - 1; index++) {
    const midX = (points[index].x + points[index + 1].x) / 2;
    const midY = (points[index].y + points[index + 1].y) / 2;
    path += ` Q ${points[index].x} ${points[index].y} ${midX} ${midY}`;
  }

  const last = points[points.length - 1];

  return `${path} L ${last.x} ${last.y}`;
}

class Preferences {
  read(key, fallback) {
    try {
      return window.localStorage.getItem(key) ?? fallback;
    } catch {
      return fallback;
    }
  }

  write(key, value) {
    try {
      window.localStorage.setItem(key, value);
    } catch {
      // Private windows can refuse storage; the page still works without it.
    }
  }
}

class SimulatorPage {
  constructor() {
    this.decoder = new WireDecoder();
    this.preferences = new Preferences();
    this.renderer = new LedRenderer(document.getElementById("leds"));
    this.device = new DeviceMockup(document.getElementById("box"), document.getElementById("scene"));
    this.cable = new CableRope(document.getElementById("cable"), this.device);
    this.device.onRotate = () => this.cable.wake();
    this.device.onButton = (pressed) => this.sendControl(pressed ? "button_down" : "button_up");
    this.socket = null;
    this.stage = document.getElementById("stage");
    this.panelView = document.getElementById("panel-view");
    this.deviceView = document.getElementById("device-view");
    this.deviceScreen = document.getElementById("device-screen");
    this.connection = document.getElementById("connection");
    this.stats = document.getElementById("stats");
    this.seamsToggle = document.getElementById("seams");
    this.viewButtons = document.querySelectorAll("[data-view]");
    this.drawPending = false;
    this.lastFrameSize = "";
    this.view =
      new URLSearchParams(window.location.search).get("view") ?? this.preferences.read("view", "panel");

    this.renderer.showSeams = this.preferences.read("seams", "off") === "on";
    this.seamsToggle.checked = this.renderer.showSeams;
    this.renderAppControls();
    this.bindControls();
    this.setView(this.view);
    this.connect();
  }

  renderAppControls() {
    const list = document.getElementById("app-controls");
    for (const [key, description] of APP_CONTROLS) {
      const term = document.createElement("dt");
      term.textContent = key;
      const detail = document.createElement("dd");
      detail.textContent = description;
      list.append(term, detail);
    }
  }

  bindControls() {
    for (const button of this.viewButtons) {
      button.addEventListener("click", () => this.setView(button.dataset.view));
    }

    this.seamsToggle.addEventListener("change", () => this.setSeams(this.seamsToggle.checked));
    window.addEventListener("resize", () => this.relayout());
    window.addEventListener("keydown", (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }

      if (event.key === "v") {
        this.setView(this.view === "panel" ? "device" : "panel");
      } else if (event.key === "t") {
        this.setSeams(!this.renderer.showSeams);
      } else if (event.key in CONTROL_KEYS && !event.repeat) {
        this.sendControl(CONTROL_KEYS[event.key]);
      }
    });
  }

  setView(view) {
    this.view = view === "device" ? "device" : "panel";
    this.preferences.write("view", this.view);
    for (const button of this.viewButtons) {
      button.setAttribute("aria-pressed", String(button.dataset.view === this.view));
    }

    const isDevice = this.view === "device";
    this.deviceView.hidden = !isDevice;
    this.panelView.hidden = isDevice;
    (isDevice ? this.deviceScreen : this.panelView).append(this.renderer.canvas);
    this.renderer.housing = !isDevice;
    this.relayout();
    if (isDevice) {
      this.cable.start();
    } else {
      this.cable.stop();
    }
  }

  setSeams(enabled) {
    this.renderer.showSeams = enabled;
    this.seamsToggle.checked = enabled;
    this.preferences.write("seams", enabled ? "on" : "off");
    this.scheduleDraw();
  }

  relayout() {
    const frame = this.renderer.frame;
    if (!frame) {
      return;
    }

    const bounds = this.stage.getBoundingClientRect();
    if (this.view === "device") {
      // Leaves room for the bezel, and for the box's depth once rotated.
      const enclosureLeds = DEVICE_BEZEL_LEDS * 2 + DEVICE_DEPTH_LEDS;
      const ledsWide = frame.width + enclosureLeds;
      const ledsHigh = frame.height + enclosureLeds;
      const ledCssSize = Math.min((bounds.width * 0.86) / ledsWide, (bounds.height * 0.8) / ledsHigh);
      // A hair over, so rounding down to whole device pixels lands on it.
      this.renderer.layout(frame.width * ledCssSize + 0.01, frame.height * ledCssSize + 0.01);
      const canvas = this.renderer.canvas;
      this.device.fit(
        parseFloat(canvas.style.width),
        parseFloat(canvas.style.height),
        this.renderer.cssLedSize(),
      );
      this.cable.start();
    } else {
      this.renderer.layout(bounds.width - 32, bounds.height - 32);
    }

    this.renderer.draw();
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${protocol}//${window.location.host}/ws`);
    socket.binaryType = "arraybuffer";
    this.socket = socket;
    socket.addEventListener("message", (event) => this.handleMessage(event.data));
    socket.addEventListener("close", () => {
      this.setConnection(false, "web server gone, retrying...");
      window.setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
    });
  }

  sendControl(control) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ control }));
    }
  }

  handleMessage(data) {
    if (typeof data === "string") {
      const status = JSON.parse(data);
      this.setConnection(status.connected, status.message);
      if (!status.connected) {
        this.stats.textContent = status.message;
      }

      return;
    }

    let message;
    try {
      message = this.decoder.decode(data);
    } catch (error) {
      console.warn("dropping malformed message", error);
      return;
    }

    if (message.type === "frame") {
      this.handleFrame(message);
    } else {
      this.stats.textContent = formatStats(message);
    }
  }

  handleFrame(frame) {
    this.renderer.setFrame(frame);
    const size = `${frame.width}x${frame.height}`;
    if (size !== this.lastFrameSize) {
      this.lastFrameSize = size;
      document.body.dataset.panelSize = size;
      this.relayout();
      return;
    }

    this.scheduleDraw();
  }

  // Frames can arrive faster than the screen refreshes; only the newest
  // one is worth drawing.
  scheduleDraw() {
    if (this.drawPending) {
      return;
    }

    this.drawPending = true;
    window.requestAnimationFrame(() => {
      this.drawPending = false;
      this.renderer.draw();
    });
  }

  setConnection(connected, message) {
    this.connection.dataset.connected = String(connected);
    this.connection.textContent = connected ? "live" : "offline";
    this.connection.title = message;
  }
}

function formatStats(stats) {
  const cpu = stats.cpuPercent === null ? "n/a" : `${stats.cpuPercent.toFixed(0)}%`;
  const rss = stats.rssKb === null ? "n/a" : `${(stats.rssKb / 1024).toFixed(1)} MB`;

  return `app: ${stats.app}  fps: ${stats.fps.toFixed(1)}  cpu: ${cpu}  sim rss (peak): ${rss}`;
}

function clamp(value, minimum, maximum) {
  return Math.min(maximum, Math.max(minimum, value));
}

new SimulatorPage();
