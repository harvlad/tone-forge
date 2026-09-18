// projects.test.mjs — DOM-free tests for the web Projects v1 pure seams
// (projects.js). Run: node projects.test.mjs
//
// Pins the cross-surface contract edges: the linear-grid ↔ iOS PadIndex
// coordinate mapping, preserve-don't-drop on snapshot round trips
// (native-only pad refs, sectionGates tri-state, unknown additive
// fields), content-addressed borrow matching (never response padIdx),
// and the sequencer wire export.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./projects.js", import.meta.url), "utf8");
const window = {};
const document = { createElement: () => ({}) };
new Function("window", "document", src)(window, document);

const P = window.JamnProjects;
assert.equal(typeof P.mount, "function");
assert.equal(typeof P.noteChange, "function");
const {
  gridToPadKey, padKeyToGrid, triggerModeToIOS, triggerModeFromIOS,
  toAppleSeconds, fromAppleSeconds, slotToNativePattern,
  borrowRefMatchesPad, buildSnapshot, workspaceFromSnapshot,
} = P._pure;

// ---- coordinate mapping: web linear (row-major, TOP-left) ↔ iOS
// PadIndex (row*10+col, row 1 = BOTTOM row). ----

// 8×8: web idx 0 = top-left = iOS "81"; web idx 63 = bottom-right = "18".
assert.equal(gridToPadKey(0, 64), "81");
assert.equal(gridToPadKey(7, 64), "88");
assert.equal(gridToPadKey(56, 64), "11");
assert.equal(gridToPadKey(63, 64), "18");
// 4×4 occupies rows 1..4 × cols 1..4.
assert.equal(gridToPadKey(0, 16), "41");
assert.equal(gridToPadKey(3, 16), "44");
assert.equal(gridToPadKey(12, 16), "11");
assert.equal(gridToPadKey(15, 16), "14");
// Round trip is identity across the whole grid, both sizes.
for (const n of [16, 64]) {
  for (let i = 0; i < n; i++) {
    assert.equal(padKeyToGrid(gridToPadKey(i, n), n), i, `idx ${i} pads ${n}`);
  }
}
// Out-of-grid keys/indices resolve to null, never a wrong pad.
assert.equal(gridToPadKey(16, 16), null);
assert.equal(gridToPadKey(-1, 64), null);
assert.equal(padKeyToGrid("71", 16), null); // row 7 has no 4×4 home
assert.equal(padKeyToGrid("19", 64), null); // col 9 never exists
assert.equal(padKeyToGrid("garbage", 64), null);

// ---- trigger-mode mapping ("one" ↔ "oneShot"; unknown → Follow). ----
assert.equal(triggerModeToIOS("one"), "oneShot");
assert.equal(triggerModeToIOS("latch"), "latch");
assert.equal(triggerModeToIOS("follow"), "follow");
assert.equal(triggerModeFromIOS("oneShot"), "one");
assert.equal(triggerModeFromIOS("latch"), "latch");
assert.equal(triggerModeFromIOS("whatIsThis"), "follow");

// ---- Apple reference dates (Swift JSONEncoder default). ----
assert.equal(toAppleSeconds(978307200 * 1000), 0); // 2001-01-01
assert.equal(fromAppleSeconds(0), 978307200 * 1000);
assert.equal(fromAppleSeconds(toAppleSeconds(1757894400000)), 1757894400000);

// ---- borrow matching: assetId OR span ±2ms + stemRole; NEVER padIdx. ----
const ref = {
  donorSongId: "d1", stemRole: "drums",
  loopStartSec: 12.5, loopEndSec: 20.5, transposeSemis: 0, targetPadIdx: 3,
};
assert.equal(borrowRefMatchesPad(ref, {
  source: "donor", stemRole: "drums",
  sourceLoopStartSec: 12.5001, sourceLoopEndSec: 20.4999, padIdx: 99,
}), true, "span match ignores padIdx");
assert.equal(borrowRefMatchesPad(ref, {
  source: "donor", stemRole: "drums",
  sourceLoopStartSec: 12.6, sourceLoopEndSec: 20.5,
}), false, "span off by 100ms");
assert.equal(borrowRefMatchesPad(ref, {
  source: "initial", stemRole: "drums",
  sourceLoopStartSec: 12.5, sourceLoopEndSec: 20.5,
}), false, "host pads are never borrow matches");
assert.equal(borrowRefMatchesPad(
  { ...ref, assetId: "a-1" },
  { source: "donor", assetId: "a-1", stemRole: "other" },
), true, "assetId match survives a re-cut span");

// ---- sequencer export: web slot → native SequencerPattern wire. ----
const pat = slotToNativePattern(
  { stepCount: 16, swing: 0.2, rows: { 2: [1, 0, 0.5, 0], 5: [0, 0, 0, 0] } },
  "ID-1", "Web Slot A", "packX",
);
assert.equal(pat.id, "ID-1");
assert.equal(pat.stepCount, 16);
assert.equal(pat.swing, 0.2);
assert.equal(pat.isLooping, true);
assert.equal(pat.tracks.length, 1, "all-zero rows are dropped");
assert.deepEqual(pat.tracks[0].chopRef, { type: "packPad", packId: "packX", padIdx: 2 });
assert.equal(pat.tracks[0].steps.length, 16);
assert.deepEqual(pat.tracks[0].steps[0], { velocity: 1, probability: 1 });
assert.deepEqual(pat.tracks[0].steps[2], { velocity: 0.5, probability: 1 });
assert.equal(slotToNativePattern({ stepCount: 16, swing: 0, rows: {} }, "x", "n", "p"), null);

// ---- snapshot build: preserve-don't-drop + web ownership rules. ----
const base = {
  schemaVersion: 1,
  padAssignments: {
    sample: {
      // iOS native-only refs at keys web isn't writing → must survive.
      "11": { ref: { type: "localSample", id: "AAAA" } },
      "12": { ref: { type: "sequence", patternId: "BBBB" } },
      // A stale iOS packPad at a key web no longer swaps → dropped
      // (web owns the sample-mode packPad grid).
      "13": { ref: { type: "packPad", packId: "old", padIdx: 9 } },
    },
    jamInKey: { "11": { ref: { type: "packPad", packId: "z", padIdx: 0 } } },
  },
  padFX: { "packA#3": { gain: 2 } },
  hiddenPads: ["packA#7"],
  sectionGates: [], // DENY ALL — [] and absent mean different things
  sequencerPatterns: [
    { id: "IOS-PAT", name: "iOS beat", stepCount: 16, tracks: [], swing: 0, isLooping: true },
  ],
  chopEdits: { harmonic: { edits: [] } },
  futureField: { anything: true }, // unknown additive field
};
const ws = {
  analysisId: "song1",
  surface: { type: "song", kind: "auto" },
  padCount: 16,
  triggerMode: "one",
  swaps: {
    0: { kind: "packPad", packId: "packA", padIdx: 5, url: "/u", name: "Kick", colorHint: "#fff" },
    1: { kind: "songPad", srcPadIdx: 7, name: "Copy" },
  },
  chopLoad: { stem: "drums", sliceMode: "beat" },
  padFx: { 0: { gain: 1.5 } },
  loopOverrides: { 2: true },
  hiddenPads: [9],
  arrangement: { "0": [1, 2] },
  borrows: [],
};
const seqIds = {};
const snap = buildSnapshot(base, ws, { A: { stepCount: 16, swing: 0, rows: { 3: [1] } } }, seqIds);

assert.equal(snap.schemaVersion, 1);
// Web's packPad swap landed at the translated key (grid 0 → "41" on 4×4)…
assert.deepEqual(snap.padAssignments.sample["41"],
  { ref: { type: "packPad", packId: "packA", padIdx: 5 } });
// …the songPad copy did NOT (web-only, no iOS slot type)…
assert.equal(Object.keys(snap.padAssignments.sample).filter(
  (k) => snap.padAssignments.sample[k].ref.type === "packPad").length, 1);
// …native-only refs survived untouched, the stale packPad didn't.
assert.deepEqual(snap.padAssignments.sample["11"], base.padAssignments.sample["11"]);
assert.deepEqual(snap.padAssignments.sample["12"], base.padAssignments.sample["12"]);
assert.equal(snap.padAssignments.sample["13"], undefined);
// Foreign modes untouched.
assert.deepEqual(snap.padAssignments.jamInKey, base.padAssignments.jamInKey);
// Native-keyed maps preserved verbatim; tri-state [] stays [].
assert.deepEqual(snap.padFX, base.padFX);
assert.deepEqual(snap.hiddenPads, base.hiddenPads);
assert.deepEqual(snap.sectionGates, []);
assert.deepEqual(snap.chopEdits, base.chopEdits);
assert.deepEqual(snap.futureField, base.futureField);
// iOS pattern preserved alongside the web slot export; slot id stable.
assert.equal(snap.sequencerPatterns.length, 2);
assert.ok(snap.sequencerPatterns.some((p) => p.id === "IOS-PAT"));
assert.ok(seqIds.A, "web slot got a stable id");
assert.equal(snap.launchpad.padCount, 16);
assert.equal(snap.launchpad.sampleTriggerMode, "oneShot");
assert.deepEqual(snap.arrangement, { "0": [1, 2] });
// Web lineage rode along in the additive extras field.
assert.deepEqual(snap.webExtras.chopLoad, { stem: "drums", sliceMode: "beat" });
assert.deepEqual(snap.webExtras.swaps["1"], ws.swaps[1]);
assert.deepEqual(snap.webExtras.hiddenGridPads, [9]);

// Tri-state: a base with NO sectionGates key must stay absent (writing
// [] would silently deny every section on iOS restore).
const snapNoGates = buildSnapshot({}, ws, null, {});
assert.equal("sectionGates" in snapNoGates, false);

// Re-exporting from the built snapshot keeps the same web slot id — the
// upsert-idempotence contract.
const seqIds2 = { A: seqIds.A };
const snap2 = buildSnapshot(snap, ws, { A: { stepCount: 16, swing: 0, rows: { 3: [1] } } }, seqIds2);
assert.equal(seqIds2.A, seqIds.A);
assert.equal(snap2.sequencerPatterns.filter((p) => p.id === seqIds.A).length, 1);

// ---- workspace extraction: web lineage wins, iOS-only refs counted. ----
const res = workspaceFromSnapshot(snap);
assert.equal(res.ws.padCount, 16);
assert.equal(res.ws.triggerMode, "one");
assert.deepEqual(res.ws.chopLoad, { stem: "drums", sliceMode: "beat" });
// Grid 0's swap keeps its full web lineage (url intact).
assert.equal(res.ws.swaps[0].url, "/u");
// The songPad copy came back from extras.
assert.equal(res.ws.swaps[1].kind, "songPad");
// localSample + sequence at "11"/"12" are unrepresentable on web.
assert.equal(res.unrepresentable, 2);

// An iOS-authored snapshot (no webExtras at all) still yields swaps —
// url-less packPads the loader resolves from the pack manifest.
const iosOnly = workspaceFromSnapshot({
  schemaVersion: 1,
  padAssignments: { sample: { "41": { ref: { type: "packPad", packId: "pk", padIdx: 2 } } } },
  launchpad: { padCount: 16, sampleTriggerMode: "latch" },
});
assert.equal(iosOnly.ws.triggerMode, "latch");
assert.deepEqual(iosOnly.ws.swaps[0], {
  kind: "packPad", packId: "pk", padIdx: 2, url: null, name: null, colorHint: null,
});

console.log("projects.test.mjs OK");
