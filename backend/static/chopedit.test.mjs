// chopedit.test.mjs — DOM-free smoke test for chopedit.js. Run: node chopedit.test.mjs
// Stubs window/document just enough to evaluate the classic script and
// exercise the pure helpers (bar math, clamps, snap, window) plus the
// no-open close() no-throw path.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./chopedit.js", import.meta.url), "utf8");
const window = { location: { origin: "https://jamn.app" } };
const document = {
  body: null, // open() with no body must be a silent no-op
  addEventListener: () => {},
  removeEventListener: () => {},
  createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }),
};
new Function("window", "document", src)(window, document);

const C = window.JamnChopEdit;
assert.equal(typeof C.open, "function");
assert.equal(typeof C.close, "function");

const {
  barSeconds,
  minLengthSec,
  wholeBars,
  clampStart,
  clampEnd,
  snapLengthToBars,
  computeWindow,
  initialRegion,
  lengthLabel,
  computePeaks,
} = C._internals;

// --- bar math ---
assert.equal(barSeconds(120), 2); // one 4/4 bar at 120 bpm
assert.equal(barSeconds(60), 4);
assert.equal(barSeconds(0), null);
assert.equal(barSeconds(null), null);
assert.equal(barSeconds(-90), null);

assert.equal(minLengthSec(120), 2); // 1 bar at known tempo
assert.equal(minLengthSec(null), 0.5); // 0.5 s fallback without tempo

assert.equal(wholeBars(6, 120), 3);
assert.equal(wholeBars(6.005, 120), 3); // within ±10 ms tolerance
assert.equal(wholeBars(6.02, 120), null); // outside tolerance
assert.equal(wholeBars(6, null), null); // no tempo → no bar display

// --- drag clamps (min length enforced, window bounds respected) ---
assert.equal(clampStart(-5, 0, 10, 2), 0); // window floor
assert.equal(clampStart(9.5, 0, 10, 2), 8); // min length to the end
assert.equal(clampStart(3, 0, 10, 2), 3); // free travel
assert.equal(clampEnd(99, 40, 10, 2), 40); // window ceiling
assert.equal(clampEnd(10.5, 40, 10, 2), 12); // min length from the start
assert.equal(clampEnd(20, 40, 10, 2), 20);

// --- bar snap on release ---
// End handle dragged (anchor=start): 7.4 s at 120 bpm → 4 bars = 8 s.
assert.deepEqual(snapLengthToBars(0, 7.4, 120, "start", 0, 100), {
  startSec: 0,
  endSec: 8,
});
// Snapped end would overrun the window → drop whole bars (never below 1).
assert.deepEqual(snapLengthToBars(0, 7.4, 120, "start", 0, 7.5), {
  startSec: 0,
  endSec: 6,
});
// Even one bar doesn't fit → hard clamp to the window edge.
assert.deepEqual(snapLengthToBars(0, 1.4, 120, "start", 0, 1.5), {
  startSec: 0,
  endSec: 1.5,
});
// Start handle dragged (anchor=end): start moves to the snapped length.
assert.deepEqual(snapLengthToBars(2.6, 10, 120, "end", 0, 100), {
  startSec: 2,
  endSec: 10,
});
// Already whole bars within ±10 ms → untouched (no re-snap drift).
assert.deepEqual(snapLengthToBars(0, 6.005, 120, "start", 0, 100), {
  startSec: 0,
  endSec: 6.005,
});
// No tempo → no snap.
assert.deepEqual(snapLengthToBars(0, 7.4, null, "start", 0, 100), {
  startSec: 0,
  endSec: 7.4,
});
// Sub-bar drag still snaps UP to the 1-bar minimum.
assert.deepEqual(snapLengthToBars(0, 0.8, 120, "start", 0, 100), {
  startSec: 0,
  endSec: 2,
});

// --- context window (±16 s, clamped to the stem) ---
assert.deepEqual(computeWindow(30, 37.56, 200), { startSec: 14, endSec: 53.56 });
assert.deepEqual(computeWindow(2, 6, 10), { startSec: 0, endSec: 10 }); // whole short stem
assert.deepEqual(computeWindow(0, 8, 300), { startSec: 0, endSec: 24 });

// --- initial region (analyzer loop bounds preferred over the raw slice) ---
assert.deepEqual(
  initialRegion({
    stemSlice: { stemRole: "drums", startSec: 10, endSec: 18 },
    loopStartSec: 10.5,
    loopEndSec: 17.5,
  }),
  { startSec: 10.5, endSec: 17.5 }
);
assert.deepEqual(
  initialRegion({ stemSlice: { stemRole: "drums", startSec: 10, endSec: 18 } }),
  { startSec: 10, endSec: 18 }
);
assert.equal(initialRegion({}), null);
assert.equal(initialRegion(null), null);

// --- readout (native "%.2fs long", plus bar count when exactly whole bars) ---
assert.equal(lengthLabel(0, 7.56, null), "7.56s long");
assert.equal(lengthLabel(0, 6, 120), "6.00s long · 3 bars");
assert.equal(lengthLabel(0, 2, 120), "2.00s long · 1 bar");
assert.equal(lengthLabel(0, 7.56, 120), "7.56s long");

// --- peaks (max |sample| per bin over the window) ---
const sr = 100;
const data = new Float32Array(sr * 4); // 4 s of silence…
data[sr * 1] = 0.5; // …with a spike at 1 s
data[sr * 3] = -0.9; // and a negative spike at 3 s
const peaks = computePeaks(data, sr, 0, 4, 4);
assert.equal(peaks.length, 4);
assert.equal(peaks[0], 0);
assert.equal(peaks[1], 0.5);
assert.ok(Math.abs(peaks[3] - 0.9) < 1e-6); // absolute value (float32 storage)
const empty = computePeaks(data, sr, 5, 6, 3); // window past the data
assert.deepEqual(empty, [0, 0, 0]);

// --- lifecycle no-throws in the stub environment ---
C.close(); // nothing open → must not throw
C.open({}); // no pad/region → silent no-op
C.open({ pad: { stemSlice: { stemRole: "drums", startSec: 0, endSec: 8 } } }); // no document.body → no-op
C.close();

console.log("chopedit.test.mjs: all assertions passed");
