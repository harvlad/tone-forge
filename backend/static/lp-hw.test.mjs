// lp-hw.test.mjs — DOM-free tests for the hardware Launchpad mirror's
// FUNCTION-BUTTON surface (lp-hw.js). Run: node lp-hw.test.mjs
//
// Pins the web port of the desktop D-036 contract (jam-desktop
// LaunchpadControlSurface): the CC → function assignment table and the
// LED state transitions, so web and desktop can never silently diverge
// on what a physical button does or shows (parity rule 4). The desktop
// twin is LaunchpadControlSurfaceTests.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./lp-hw.js", import.meta.url), "utf8");
const window = {};
const document = { getElementById: () => null };
new Function("window", "document", src)(window, document);

const H = window.JamnLpHW;
assert.equal(typeof H.attach, "function");
assert.equal(typeof H.detach, "function");

const { ccFunction, controlFrame, LAYER_ROW, CTL_CCS, CTL, FLASH_MS } =
  H._internals;
assert.equal(typeof ccFunction, "function");
assert.equal(typeof controlFrame, "function");

// ---------- the D-036 assignment table, button by button ----------

assert.deepEqual(ccFunction(20), { kind: "playPause" }); // ▷ Play
assert.deepEqual(ccFunction(10), { kind: "globalStop" }); // ○ Record/Capture
assert.deepEqual(ccFunction(30), { kind: "selectMode", mode: "one" }); // Fixed Length
assert.deepEqual(ccFunction(40), { kind: "selectMode", mode: "follow" }); // Quantise
assert.deepEqual(ccFunction(50), { kind: "selectMode", mode: "latch" }); // Duplicate
assert.deepEqual(ccFunction(60), { kind: "loopLockToggle" }); // Clear
assert.deepEqual(ccFunction(91), { kind: "gridSize", count: 16 }); // ◄
assert.deepEqual(ccFunction(92), { kind: "gridSize", count: 64 }); // ►
assert.deepEqual(ccFunction(93), { kind: "sequencerPanelToggle" }); // Session
assert.deepEqual(ccFunction(95), { kind: "instantGroove" }); // Chord
assert.deepEqual(ccFunction(1), { kind: "recordToggle" }); // Record Arm
assert.deepEqual(ccFunction(8), { kind: "stopAllPads" }); // Stop Clip
assert.deepEqual(ccFunction(89), { kind: "sequencerPlayStop" }); // > scene top

// Layer mutes CC 2–7 mirror the desktop layerRow order exactly.
assert.deepEqual(LAYER_ROW, ["DRUMS", "BASS", "CHORDS", "SYNTH", "LEAD", "TEXTURE"]);
for (let cc = 2; cc <= 7; cc++) {
  assert.deepEqual(ccFunction(cc), { kind: "layerToggle", category: LAYER_ROW[cc - 2] });
}

// Pattern select CC 101–108 → 0-based index.
for (let cc = 101; cc <= 108; cc++) {
  assert.deepEqual(ccFunction(cc), { kind: "patternSelect", index: cc - 101 });
}

// Section jumps: scene column top→bottom = song order. CC 79 (directly
// under the sequencer arrow 89) is block 0; CC 19 (bottom) is block 6.
assert.deepEqual(ccFunction(79), { kind: "sectionJump", index: 0 });
assert.deepEqual(ccFunction(69), { kind: "sectionJump", index: 1 });
assert.deepEqual(ccFunction(59), { kind: "sectionJump", index: 2 });
assert.deepEqual(ccFunction(49), { kind: "sectionJump", index: 3 });
assert.deepEqual(ccFunction(39), { kind: "sectionJump", index: 4 });
assert.deepEqual(ccFunction(29), { kind: "sectionJump", index: 5 });
assert.deepEqual(ccFunction(19), { kind: "sectionJump", index: 6 });

// Reserved / deliberately unmapped: Shift 90, Note 94, Custom 96,
// Sequencer 97, Projects 98, logo 99, ▲▼ 80/70 (jam.js octave arrows
// keep them off this surface; desktop leaves them unmapped), and any
// grid/junk address.
for (const cc of [90, 94, 96, 97, 98, 99, 80, 70, 0, 9, 11, 88, 100, 109, 127]) {
  assert.equal(ccFunction(cc), null, `cc ${cc} must be unmapped`);
}

// ---------- LED frame: state → colors (controlLightFrame twin) ----------

function baseState(over) {
  return Object.assign(
    {
      mode: "follow",
      loopLocked: false,
      padCount: 16,
      seqOpen: false,
      seqPlaying: false,
      slots: null,
      recording: false,
      layers: [null, null, null, null, null, null],
      sections: { count: 0, active: -1 },
      flash8: false,
      flash95: false,
    },
    over || {}
  );
}

// Global stop is an always-available dim red.
{
  const f = controlFrame(baseState(), 1);
  assert.deepEqual(f[10], CTL.redDim);
}

// Trigger-mode select: exactly the selected chip is bright.
{
  const f = controlFrame(baseState({ mode: "follow" }), 1);
  assert.deepEqual(f[40], CTL.bright);
  assert.deepEqual(f[30], CTL.dim);
  assert.deepEqual(f[50], CTL.dim);
  const g = controlFrame(baseState({ mode: "latch" }), 1);
  assert.deepEqual(g[50], CTL.bright);
  assert.deepEqual(g[40], CTL.dim);
  // No kit mounted (mode null): nothing selected, all dim.
  const h = controlFrame(baseState({ mode: null }), 1);
  assert.deepEqual(h[30], CTL.dim);
  assert.deepEqual(h[40], CTL.dim);
  assert.deepEqual(h[50], CTL.dim);
}

// Loop lock: amber on / dim amber off.
{
  assert.deepEqual(controlFrame(baseState({ loopLocked: true }), 1)[60], CTL.lockOn);
  assert.deepEqual(controlFrame(baseState({ loopLocked: false }), 1)[60], CTL.lockDim);
}

// Grid-size arrows: the active size's arrow is lit.
{
  const f16 = controlFrame(baseState({ padCount: 16 }), 1);
  assert.deepEqual(f16[91], CTL.bright);
  assert.deepEqual(f16[92], CTL.dim);
  const f64 = controlFrame(baseState({ padCount: 64 }), 1);
  assert.deepEqual(f64[91], CTL.dim);
  assert.deepEqual(f64[92], CTL.bright);
}

// Session = sequencer panel open/closed.
{
  assert.deepEqual(controlFrame(baseState({ seqOpen: true }), 1)[93], CTL.bright);
  assert.deepEqual(controlFrame(baseState({ seqOpen: false }), 1)[93], CTL.dim);
}

// Record Arm: dim red idle → red PULSE recording (pulse = scaled by k).
{
  assert.deepEqual(controlFrame(baseState(), 1)[1], CTL.redDim);
  assert.deepEqual(controlFrame(baseState({ recording: true }), 1)[1], CTL.red);
  const half = controlFrame(baseState({ recording: true }), 0.5)[1];
  assert.deepEqual(half, { r: 64, g: 0, b: 0 }); // breathing, not static
}

// Momentary press flashes: Stop Clip + Chord jump to bright amber.
{
  assert.deepEqual(controlFrame(baseState(), 1)[8], CTL.amberDim);
  assert.deepEqual(controlFrame(baseState({ flash8: true }), 1)[8], CTL.amber);
  assert.deepEqual(controlFrame(baseState(), 1)[95], CTL.amberDim);
  assert.deepEqual(controlFrame(baseState({ flash95: true }), 1)[95], CTL.amber);
  assert.ok(FLASH_MS > 0 && FLASH_MS < 1000);
}

// Layer LEDs: active pulses the category accent, available shows it
// dim (quarter), empty/no-kit is dark.
{
  const drums = { present: true, active: true, color: { r: 239, g: 68, b: 68 } };
  const bass = { present: true, active: false, color: { r: 34, g: 197, b: 94 } };
  const f = controlFrame(baseState({ layers: [drums, bass, null, null, null, null] }), 1);
  // Active DRUMS at k=1: the halved accent, full brightness.
  assert.deepEqual(f[2], { r: 119, g: 34, b: 34 });
  // Available BASS: quarter of the halved accent.
  assert.deepEqual(f[3], { r: 4, g: 24, b: 11 });
  // Empty categories dark.
  assert.deepEqual(f[4], CTL.off);
  assert.deepEqual(f[7], CTL.off);
  // Pulse actually breathes the active layer.
  const dimmer = controlFrame(baseState({ layers: [drums, null, null, null, null, null] }), 0.5);
  assert.deepEqual(dimmer[2], { r: 60, g: 17, b: 17 });
}

// Pattern select: pane closed (slots null) = all dark; open = stored
// slots dim, the active one bright, pulsing while the sequencer runs;
// web has 4 slots, so 105–108 stay dark (desktop's 8-pattern guard).
{
  const closed = controlFrame(baseState(), 1);
  for (let cc = 101; cc <= 108; cc++) assert.deepEqual(closed[cc], CTL.off);

  const slots = [
    { id: "A", hasContent: true, active: true },
    { id: "B", hasContent: true, active: false },
    { id: "C", hasContent: false, active: false },
    { id: "D", hasContent: false, active: false },
  ];
  const open = controlFrame(baseState({ slots }), 1);
  assert.deepEqual(open[101], CTL.bright); // active, not running
  assert.deepEqual(open[102], CTL.pattern); // stored
  assert.deepEqual(open[103], CTL.off); // empty
  assert.deepEqual(open[104], CTL.off);
  for (let cc = 105; cc <= 108; cc++) assert.deepEqual(open[cc], CTL.off);

  const running = controlFrame(baseState({ slots, seqPlaying: true }), 0.5);
  assert.deepEqual(running[101], { r: 64, g: 64, b: 64 }); // current + running pulses
  assert.deepEqual(running[102], CTL.pattern);
}

// Sequencer play/stop arrow: green pulse running / dim green stopped.
{
  assert.deepEqual(controlFrame(baseState(), 1)[89], CTL.greenDim);
  assert.deepEqual(controlFrame(baseState({ seqPlaying: true }), 1)[89], CTL.green);
  assert.deepEqual(
    controlFrame(baseState({ seqPlaying: true }), 0.5)[89],
    { r: 0, g: 64, b: 0 }
  );
}

// Section blocks: lit where a block exists, pulse on the active one,
// dark past the song's block count.
{
  const f = controlFrame(baseState({ sections: { count: 3, active: 1 } }), 1);
  assert.deepEqual(f[79], CTL.section); // block 0 exists
  assert.deepEqual(f[69], CTL.bright); // block 1 = active (pulses)
  assert.deepEqual(f[59], CTL.section); // block 2 exists
  assert.deepEqual(f[49], CTL.off); // no block 3
  assert.deepEqual(f[19], CTL.off); // no block 6
  const none = controlFrame(baseState(), 1);
  for (const cc of [79, 69, 59, 49, 39, 29, 19]) assert.deepEqual(none[cc], CTL.off);
}

// ---------- ownership boundaries of the LED frame ----------

// The frame must stay inside the blank/cache domain (CTL_CCS) and must
// NEVER address the driver-owned play LED (20), jam.js's octave arrows
// (70/80), or reserved buttons (90/94/96/97/98) — the play-LED spray
// stomping those addresses is the exact hazard this split fixes.
{
  const busy = controlFrame(
    baseState({
      loopLocked: true,
      seqOpen: true,
      seqPlaying: true,
      recording: true,
      flash8: true,
      flash95: true,
      slots: [
        { id: "A", hasContent: true, active: true },
        { id: "B", hasContent: true, active: false },
        { id: "C", hasContent: true, active: false },
        { id: "D", hasContent: true, active: false },
      ],
      layers: LAYER_ROW.map(() => ({
        present: true, active: true, color: { r: 255, g: 255, b: 255 },
      })),
      sections: { count: 7, active: 6 },
    }),
    1
  );
  const domain = new Set(CTL_CCS);
  for (const key of Object.keys(busy)) {
    assert.ok(domain.has(Number(key)), `frame addresses foreign cc ${key}`);
  }
  for (const forbidden of [20, 70, 80, 90, 94, 96, 97, 98, 99]) {
    assert.ok(!(forbidden in busy), `cc ${forbidden} must not be painted`);
    assert.ok(!domain.has(forbidden), `cc ${forbidden} must not be blanked`);
  }
  // Every color stays in the 0..127 SysEx range.
  for (const rgb of Object.values(busy)) {
    for (const ch of [rgb.r, rgb.g, rgb.b]) {
      assert.ok(Number.isInteger(ch) && ch >= 0 && ch <= 127, `channel ${ch}`);
    }
  }
}

// Determinism: identical state + pulse → identical frame (the per-CC
// diff cache depends on it).
{
  const a = controlFrame(baseState({ recording: true }), 0.7);
  const b = controlFrame(baseState({ recording: true }), 0.7);
  assert.deepEqual(a, b);
}

console.log("lp-hw.test.mjs: all assertions passed");
