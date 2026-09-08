/*
 * lpview.test.mjs — DOM-free smoke test for the pure helpers on
 * window.JamnLaunchpad._internals. Same pattern as kit.js's smoke tests:
 * load lpview.js in a minimal window/document shim, then assert the pure
 * grid-math / color / sequence-step-dot / trigger-opts helpers.
 *
 * Run:  node backend/static/lpview.test.mjs
 */
// Non-strict assert: values built inside the vm realm have that realm's
// Array/Object prototypes, so deepStrictEqual would reject them as
// "not reference-equal". Loose deepEqual compares structure only.
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "lpview.js"), "utf8");

// Minimal window/document shim — the helpers under test never touch the DOM,
// but the IIFE references `document` at parse time inside builder fns only,
// and assigns to `window`. A bare object is enough.
const sandbox = { window: {}, document: { createElement: () => ({}) } };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);

const LP = sandbox.window.JamnLaunchpad;
assert.ok(LP && LP._internals, "JamnLaunchpad._internals exported");
const I = LP._internals;

// ---- padIndex ---------------------------------------------------------
assert.equal(I.padIndex(0, 0), 0, "top-left is 0");
assert.equal(I.padIndex(0, 7), 7, "top-right is 7");
assert.equal(I.padIndex(1, 0), 8, "second row starts at 8");
assert.equal(I.padIndex(7, 7), 63, "bottom-right is 63");

// ---- colorFromHint ----------------------------------------------------
assert.equal(I.colorFromHint(0xff0000), "rgb(255,0,0)", "number → rgb");
assert.equal(I.colorFromHint("0x00ff00"), "rgb(0,255,0)", "0x string → rgb");
assert.equal(I.colorFromHint("#123456"), "#123456", "hex passthrough");
assert.equal(I.colorFromHint("255,0,0"), "rgb(255,0,0)", "csv → rgb");
assert.equal(I.colorFromHint("teal"), "teal", "css name passthrough");
assert.equal(I.colorFromHint(null), null, "null → null");
assert.equal(I.colorFromHint(""), null, "empty → null");

// ---- categoryColor / categoryFor -------------------------------------
assert.equal(I.categoryColor("DRUMS"), "rgb(239,68,68)", "drums accent");
assert.equal(I.categoryColor("drums"), "rgb(239,68,68)", "case-insensitive");
assert.equal(I.categoryColor("nope"), null, "unknown category → null");
assert.equal(I.categoryFor("drums", null), "DRUMS", "stem overrides type");
assert.equal(I.categoryFor("bass", null), "BASS");
assert.equal(I.categoryFor("vocals", null), "VOCAL");
assert.equal(I.categoryFor("other", "chord_loop"), "CHORDS", "type → chords");
assert.equal(I.categoryFor("other", "one_shot"), "STAB");
assert.equal(I.categoryFor("other", "mystery"), "SAMPLE", "default sample");

// ---- padFill priority -------------------------------------------------
assert.equal(I.padFill(null), "rgb(91,107,140)", "empty → default");
assert.equal(I.padFill({ voice: true }), "rgb(155,77,255)", "voice → purple");
assert.equal(I.padFill({ sequence: true }), "rgb(153,51,204)", "sequence → purple");
assert.equal(I.padFill({ category: "DRUMS" }), "rgb(239,68,68)", "category wins");
assert.equal(
  I.padFill({ colorHint: 0x010203 }),
  "rgb(1,2,3)",
  "colorHint fallback when no category"
);

// ---- padLabel ---------------------------------------------------------
assert.equal(I.padLabel({ voice: true }), "Voice");
assert.equal(I.padLabel({ name: "Chorus Riff" }), "Chorus Riff");
assert.equal(I.padLabel({ sectionLabel: "Chorus" }), "Chorus");
assert.equal(I.padLabel({ kind: "kick" }), "Kick", "kind capitalized");
assert.equal(I.padLabel({ kind: "chord", chordSymbol: "Am" }), "Am", "chord kind → symbol");
assert.equal(I.padLabel({}), null);

// ---- sequenceStepFlags (defaultSequence wire form) --------------------
const kit = {
  defaultSequence: {
    tracks: [
      {
        chopRef: { type: "packPad", packId: "x", padIdx: 3 },
        steps: [{ velocity: 1 }, { velocity: 0 }, { velocity: 0.8 }, {}],
      },
      // legacy nested chopRef form + same pad (OR of both tracks)
      {
        chopRef: { packPad: { padIdx: 3 } },
        steps: [{ velocity: 0 }, { velocity: 1 }, { velocity: 0 }, { velocity: 0 }],
      },
    ],
  },
};
const flags = I.sequenceStepFlags(kit);
assert.deepEqual(flags[3], [true, true, true, false], "OR of two tracks on pad 3");
assert.equal(I.sequenceStepFlags({}), null, "no sequence → null");
assert.equal(I.sequenceStepFlags(null), null, "null kit → null");

// ---- sequenceStepDots layout -----------------------------------------
const dots = I.sequenceStepDots([true, false, true], 8);
assert.equal(dots.cols, 8);
assert.equal(dots.rows, 1);
assert.equal(dots.cells.length, 3);
assert.deepEqual(
  dots.cells.map((c) => c.on),
  [true, false, true]
);
const wide = I.sequenceStepDots(new Array(16).fill(true), 8);
assert.equal(wide.rows, 2, "16 flags / 8 cols → 2 rows");

// ---- buildTriggerOpts (Tap/Loop/Lock/Quantize) ------------------------
assert.deepEqual(
  I.buildTriggerOpts({ mode: "tap", lock: true, quantize: "bar" }),
  { loop: false, quantized: false },
  "Tap = one-shot, unquantized"
);
assert.deepEqual(
  I.buildTriggerOpts({ mode: "loop", lock: true, quantize: "bar" }),
  { loop: true, quantized: true, grid: "bar" },
  "Loop + Lock + bar = quantized loop"
);
assert.deepEqual(
  I.buildTriggerOpts({ mode: "loop", lock: false, quantize: "bar" }),
  { loop: true, quantized: false },
  "Loop + Lock OFF = immediate loop"
);
assert.deepEqual(
  I.buildTriggerOpts({ mode: "loop", lock: true, quantize: "off" }),
  { loop: true, quantized: false },
  "Loop + Quantize OFF = immediate loop"
);

// ---- pickInstantGroove (best per category) ---------------------------
const groove = I.pickInstantGroove([
  { padIdx: 0, category: "DRUMS", performanceScore: 0.2 },
  { padIdx: 1, category: "DRUMS", performanceScore: 0.9 },
  { padIdx: 2, category: "BASS", loopScore: 0.5 },
  { padIdx: 3, category: "SAMPLE", performanceScore: 1 }, // not a groove target
]);
assert.deepEqual(groove, [1, 2], "best DRUMS (pad 1) + BASS (pad 2); SAMPLE excluded");

// ---- deviceStatusLabel ------------------------------------------------
assert.equal(I.deviceStatusLabel(null), "No device");
assert.equal(I.deviceStatusLabel("Launchpad Pro MK3"), "Launchpad Pro MK3");
assert.equal(I.deviceStatusLabel({ connected: true, name: "LP MK3" }), "LP MK3");
assert.equal(I.deviceStatusLabel({ connected: false }), "No device");

console.log("lpview.test.mjs: all assertions passed");
