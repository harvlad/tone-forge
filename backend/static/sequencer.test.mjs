// sequencer.test.mjs — DOM-free test for sequencer.js. Run: node sequencer.test.mjs
// Evaluates the classic script with a stub window (kit.test.mjs pattern) and
// exercises the pure scheduler logic exported via _internals: step time
// computation (16th-note grid + swing), lookahead window selection, lock-grid
// aligned starts, and the persistence round-trip.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./sequencer.js", import.meta.url), "utf8");
const window = {};
const document = undefined; // pure paths must not touch the DOM
new Function("window", "document", src)(window, document);

const S = window.JamnSequencer;
assert.equal(typeof S.mount, "function");
assert.equal(typeof S.unmount, "function");

const {
  stepDurationSec,
  stepTimeSec,
  nextLockAlignedStart,
  collectDueSteps,
  resizeSteps,
  parseColor,
  emptyPattern,
  normalizePattern,
  normalizeStore,
  serializeStore,
  storageKey,
  LOOKAHEAD_SEC,
  LOCK_GRACE_SEC,
  SLOT_IDS,
  STEP_COUNTS,
} = S._internals;

// ---------- step duration: 16th notes, 60/bpm/4 (SequencerPattern) ----------

assert.equal(stepDurationSec(120), 0.125);
assert.equal(stepDurationSec(60), 0.25);
assert.ok(Math.abs(stepDurationSec(90) - 60 / 90 / 4) < 1e-12);
assert.equal(stepDurationSec(0), 0.125); // 120 BPM fallback, like SequencerClock
assert.equal(stepDurationSec(-5), 0.125);
assert.equal(stepDurationSec(undefined), 0.125);

// ---------- step time: grid + swing delays odd steps ----------

const dur = stepDurationSec(120); // 0.125
assert.equal(stepTimeSec(10, 0, dur, 0), 10);
assert.equal(stepTimeSec(10, 4, dur, 0), 10.5);
// swing 0.5 delays odd steps by half a step; even steps stay on the grid
assert.equal(stepTimeSec(10, 1, dur, 0.5), 10 + dur + 0.5 * dur);
assert.equal(stepTimeSec(10, 2, dur, 0.5), 10 + 2 * dur);
// swing clamps at 0.5 (native swing range)
assert.equal(stepTimeSec(0, 1, dur, 0.9), dur + 0.5 * dur);
// raw (unwrapped) parity: step 17 of a 16-step loop is odd → swung
assert.equal(stepTimeSec(0, 17, dur, 0.2), 17 * dur + 0.2 * dur);

// ---------- lock-grid aligned start (PadEngine._lockLaunchTime twin) ----------

// no anchor (no loop ever launched) → start now
assert.equal(nextLockAlignedStart(5.0, null, 8.0), 5.0);
assert.equal(nextLockAlignedStart(5.0, undefined, 8.0), 5.0);
// degenerate loop length → start now
assert.equal(nextLockAlignedStart(5.0, 1.0, 0), 5.0);
// mid-cycle → next boundary: anchor 2, L 8, now 7 → 10
assert.equal(nextLockAlignedStart(7.0, 2.0, 8.0), 10.0);
// within the grace window just after a boundary → immediate
assert.equal(nextLockAlignedStart(10.05, 2.0, 8.0, LOCK_GRACE_SEC), 10.05);
// just past the grace window → wait for the next boundary
assert.equal(nextLockAlignedStart(10.09, 2.0, 8.0, LOCK_GRACE_SEC), 18.0);
// exactly on a boundary (phase 0 <= grace) → immediate
assert.equal(nextLockAlignedStart(10.0, 2.0, 8.0), 10.0);
// anchor still in the future (loop armed but not sounding) → the anchor
assert.equal(nextLockAlignedStart(1.0, 2.0, 8.0), 2.0);

// ---------- lookahead window selection ----------

{
  // start 0, 120 BPM (0.125 s steps), horizon 0.30 → steps 0, 1, 2
  const res = collectDueSteps(
    { nextRaw: 0, startSec: 0, stepDur: 0.125, swing: 0, stepCount: 16 },
    0.30
  );
  assert.deepEqual(res.events.map((e) => e.raw), [0, 1, 2]);
  assert.deepEqual(res.events.map((e) => e.timeSec), [0, 0.125, 0.25]);
  assert.equal(res.nextRaw, 3);
}
{
  // swing pushes an odd step past the horizon: step 1 at 0.125+0.0625
  const res = collectDueSteps(
    { nextRaw: 1, startSec: 0, stepDur: 0.125, swing: 0.5, stepCount: 16 },
    0.18
  );
  assert.deepEqual(res.events, []); // 0.1875 >= 0.18 → nothing due yet
  assert.equal(res.nextRaw, 1); // counter must NOT advance past unscheduled steps
}
{
  // wrap: raw steps 15..17 of a 16-step pattern map to steps 15, 0, 1
  const res = collectDueSteps(
    { nextRaw: 15, startSec: 0, stepDur: 0.125, swing: 0, stepCount: 16 },
    15 * 0.125 + 0.3
  );
  assert.deepEqual(res.events.map((e) => e.step), [15, 0, 1]);
  assert.equal(res.nextRaw, 18);
}
{
  // empty window (next step beyond horizon) → no events, counter unchanged
  const res = collectDueSteps(
    { nextRaw: 4, startSec: 0, stepDur: 0.125, swing: 0, stepCount: 16 },
    0.4
  );
  assert.deepEqual(res.events, []);
  assert.equal(res.nextRaw, 4);
}
{
  // runaway guard: a huge window cannot spin forever in one tick
  const res = collectDueSteps(
    { nextRaw: 0, startSec: 0, stepDur: 0.001, swing: 0, stepCount: 16 },
    1e9
  );
  assert.ok(res.events.length <= 4097);
}
assert.ok(LOOKAHEAD_SEC > 0 && LOOKAHEAD_SEC <= 0.5);

// ---------- resize (SequencerTrack.resize semantics) ----------

assert.deepEqual(resizeSteps([1, 0, 1], 5), [1, 0, 1, 0, 0]);
assert.deepEqual(resizeSteps([1, 0, 1, 1], 2), [1, 0]);
assert.deepEqual(resizeSteps([], 4), [0, 0, 0, 0]);

// ---------- colorHint parsing ----------

assert.deepEqual(parseColor("#3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor("3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor(0xff0000), { r: 255, g: 0, b: 0 });
assert.deepEqual(parseColor("garbage"), { r: 139, g: 92, b: 246 }); // accent fallback
assert.deepEqual(parseColor(undefined), { r: 139, g: 92, b: 246 });

// ---------- persistence: versioned blob, A–D slots, corrupt-safe ----------

assert.equal(storageKey("abc123"), "jamn.seq.abc123");
assert.equal(storageKey(null), "jamn.seq.default");
assert.deepEqual(SLOT_IDS, ["A", "B", "C", "D"]);
assert.deepEqual(STEP_COUNTS, [16, 32]);

{
  const p = emptyPattern();
  assert.equal(p.stepCount, 16);
  assert.equal(p.swing, 0);
  assert.deepEqual(p.rows, {});
}

{
  // round trip: edit slot B, serialize, re-load
  const store = normalizeStore(null);
  assert.equal(store.activeSlot, "A");
  store.activeSlot = "B";
  store.slots.B.stepCount = 32;
  store.slots.B.swing = 0.25;
  store.slots.B.rows["3"] = resizeSteps([1, 0, 0, 0, 1], 32);
  store.slots.B.rows["7"] = resizeSteps([], 32); // all-off row must be dropped

  const json = serializeStore(store);
  const back = normalizeStore(json);
  assert.equal(back.activeSlot, "B");
  assert.equal(back.slots.B.stepCount, 32);
  assert.equal(back.slots.B.swing, 0.25);
  assert.equal(back.slots.B.rows["3"].length, 32);
  assert.equal(back.slots.B.rows["3"][0], 1);
  assert.equal(back.slots.B.rows["3"][4], 1);
  assert.equal(back.slots.B.rows["7"], undefined); // empty row pruned
  assert.equal(back.slots.A.stepCount, 16); // untouched slots stay default
  assert.ok(JSON.parse(json).v === 1); // versioned wire format
}

{
  // corrupt blobs are replaced with clean defaults, never thrown on
  for (const bad of ["not json", '{"slots": 5}', '{"activeSlot":"Z"}', "", null]) {
    const st = normalizeStore(bad);
    assert.equal(st.activeSlot, "A");
    for (const id of SLOT_IDS) assert.equal(st.slots[id].stepCount, 16);
  }
  // hostile pattern contents are clamped/dropped
  const p = normalizePattern({
    stepCount: 13, // not 16/32 → default
    swing: 9, // clamps to 0.5
    rows: { "-1": [1], x: [1], 2: "nope", 5: [2, -3, "1"] },
  });
  assert.equal(p.stepCount, 16);
  assert.equal(p.swing, 0.5);
  assert.equal(p.rows["-1"], undefined);
  assert.equal(p.rows.x, undefined);
  assert.equal(p.rows["2"], undefined);
  assert.deepEqual(p.rows["5"].slice(0, 3), [1, 0, 1]); // clamped to [0,1]
}

// ---------- mount is defensive without a DOM ----------

S.mount(null, null); // must not throw
S.unmount(); // nothing mounted → must not throw

console.log("sequencer.test.mjs: all assertions passed");
