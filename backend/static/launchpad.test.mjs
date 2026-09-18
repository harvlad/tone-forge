// launchpad.test.mjs — DOM-free tests for the Launchpad driver's pure
// seams (launchpad.js). Run: node launchpad.test.mjs
//
// Pins the Notes-surface overlay contract: the fourths-grid geometry
// (pad → MIDI) and the note → scale-membership → color mapping that the
// on-screen mirror, the hardware LEDs and the legend all share
// (_instrumentPadColor). Field regression this guards: with a song key
// loaded, EVERY grid cell must resolve to a defined role — root pads
// the distinct root color, in-key pads one clear uniform scale tint
// that IS the legend's 'Scale' chip color, out-of-key pads dark — and
// nothing may depend on a chord sounding or on which grid row the pad
// sits in.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./launchpad.js", import.meta.url), "utf8");
const window = {};
const document = { getElementById: () => null, createElement: () => ({}) };
new Function("window", "document", src)(window, document);

const L = window.Launchpad;
assert.equal(typeof L.setMode, "function");
const {
  midiForPad, scaleDegreeInKey, instrumentPadColor,
  OPEN_JAM_BASE_MIDI, ROOT_RGB, CHORD_TONE_RGB, SCALE_RGB, CHROMATIC_DIM_RGB,
} = L._internals;

function key(root, scale) {
  // Matches jam.js parseDetectedKey / launchpad.js _effectiveKey shape.
  const MAJOR = [0, 2, 4, 5, 7, 9, 11];
  const MINOR = [0, 2, 3, 5, 7, 8, 10];
  const iv = scale === "Minor" ? MINOR : MAJOR;
  return { root, scale, pitchClasses: new Set(iv.map((i) => (root + i) % 12)) };
}

// ---- fourths-grid geometry: row 0 col 0 = E2 (40), +5 per row ----
assert.equal(OPEN_JAM_BASE_MIDI, 40);
assert.equal(midiForPad(0, 0), 40); // E2 bottom-left
assert.equal(midiForPad(0, 7), 47);
assert.equal(midiForPad(7, 0), 75);
assert.equal(midiForPad(7, 7), 82); // A#5 top-right

// ---- scale membership in C# minor (the field song's key) ----
const cSharpMin = key(1, "Minor");
// Natural-minor pcs from C#: C# D# E F# G# A B.
const inKey = [1, 3, 4, 6, 8, 9, 11];
for (let pc = 0; pc < 12; pc++) {
  const deg = scaleDegreeInKey(pc, cSharpMin);
  if (inKey.includes(pc)) {
    assert.equal(deg, inKey.indexOf(pc) + 1, `pc ${pc} degree`);
  } else {
    assert.equal(deg, null, `pc ${pc} must be out of key`);
  }
}

// ---- per-pad color mapping (transport stopped: empty highlight) ----
const none = new Set();
// Root: distinct root color, static kind (never the pressed-glow
// 'active' treatment), with or without a sounding chord.
assert.deepEqual(instrumentPadColor(1, cSharpMin, none, "off"),
  { rgb: ROOT_RGB, kind: "static", role: "root" });
assert.deepEqual(instrumentPadColor(1, cSharpMin, new Set([1, 4, 8]), "off"),
  { rgb: ROOT_RGB, kind: "static", role: "root" });
// In-key non-root: the uniform scale tint — the SAME constant the
// legend's 'Scale' chip renders.
for (const pc of inKey.filter((p) => p !== 1)) {
  assert.deepEqual(instrumentPadColor(pc, cSharpMin, none, "off"),
    { rgb: SCALE_RGB, kind: "static", role: "scale" }, `pc ${pc} scale tint`);
}
// Out-of-key: dark when out-of-key pads are off, barely-lit chromatic
// otherwise — never a scale/root/chord color.
assert.deepEqual(instrumentPadColor(2, cSharpMin, none, "off"),
  { rgb: [0, 0, 0], kind: "off", role: "out" });
assert.deepEqual(instrumentPadColor(2, cSharpMin, none, "dim"),
  { rgb: CHROMATIC_DIM_RGB, kind: "static", role: "out" });

// ---- playback: chord tones boost ON TOP of the base scale coloring ----
const cSharpMinChord = new Set([1, 4, 8]); // C# E G#
assert.deepEqual(instrumentPadColor(4, cSharpMin, cSharpMinChord, "off"),
  { rgb: CHORD_TONE_RGB, kind: "static", role: "chord" });
assert.deepEqual(instrumentPadColor(8, cSharpMin, cSharpMinChord, "off"),
  { rgb: CHORD_TONE_RGB, kind: "static", role: "chord" });
// Non-chord in-key pads keep the scale tint while the chord sounds.
assert.deepEqual(instrumentPadColor(3, cSharpMin, cSharpMinChord, "off"),
  { rgb: SCALE_RGB, kind: "static", role: "scale" });

// ---- whole-grid coverage: all 64 cells resolve, no row holes ----
// (The field bug: the bass submode blanked rows 4..7; the Notes surface
// coerces to synth, whose painter walks every cell through this mapping.)
const roles = { root: 0, scale: 0, chord: 0, out: 0 };
for (let r = 0; r < 8; r++) {
  for (let c = 0; c < 8; c++) {
    const pc = ((midiForPad(r, c) % 12) + 12) % 12;
    const col = instrumentPadColor(pc, cSharpMin, none, "off");
    assert.ok(col && col.role in roles, `pad r${r}c${c} resolves`);
    roles[col.role]++;
  }
}
assert.equal(roles.root + roles.scale + roles.chord + roles.out, 64);
// Fourths layout repeats a pitch class at (r+1, c-5); C# lands on 4 of
// the 64 cells in the E2..A#5 window.
assert.ok(roles.root >= 3, "root pads present across the grid");
assert.ok(roles.scale >= 30, "in-key pads across the whole grid");
assert.ok(roles.out >= 10, "out-of-key pads stay dark");

// ---- legend/grid identity: the instrument legend's Scale chip is the
// grid's in-key constant, root chip the root constant ----
L.setMode("instrument-synth");
const legend = L.getLegendInfo();
const chip = (name) => legend.chips.find((c) => c.name === name);
assert.deepEqual(chip("Scale").rgb, SCALE_RGB);
assert.deepEqual(chip("Key root").rgb, ROOT_RGB);
assert.deepEqual(chip("Chord tone").rgb, CHORD_TONE_RGB);

console.log("launchpad.test.mjs: all assertions passed");
