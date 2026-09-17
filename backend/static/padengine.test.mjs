// Regression tests for the padengine.js launch-timing + phase-lock logic.
// Run: node --test backend/static/padengine.test.mjs
// (CI: tests/test_padengine_js.py shells out here; skips when node is absent.)
//
// These pin the whole phase-lock saga — every one of these was a shipped bug:
//   - loop launches quantize to the BAR grid, never individual beats
//   - free-run "bar" snaps to a SINGLE bar, not the full loop cycle
//   - transport path snaps to the song's REAL downbeats
//   - padProgress includes the phase-lock start offset (playheads lied)
import { test } from "node:test";
import assert from "node:assert/strict";

const { PadEngine, quantizeWaitSec, nextGridTimeSec } = await import(
  new URL("./padengine.js", import.meta.url).href
);

function mockCtx() {
  let now = 0;
  const noop = () => ({
    connect() {},
    gain: { value: 1, setValueAtTime() {}, linearRampToValueAtTime() {} },
    start() {},
    stop() {},
  });
  return new Proxy(
    {},
    {
      get(_, k) {
        if (k === "currentTime") return now;
        if (k === "setNow") return (t) => { now = t; };
        if (k === "destination") return {};
        if (k === "sampleRate") return 44100;
        if (k === "state") return "running";
        return typeof k === "string" ? noop : undefined;
      },
    },
  );
}

const BPM = 128.2;
const BAR = (60 / BPM) * 4;

function engine(ctx) {
  const e = new PadEngine(ctx);
  e.setKit(
    { pads: [
      { padIdx: 0, loopable: true, loopStartSec: 0.9, loopEndSec: 0.9 + 4 * BAR },
      { padIdx: 1, loopable: true, loopStartSec: 0.9, loopEndSec: 0.9 + 4 * BAR },
    ] },
    { tempoBpm: BPM },
  );
  return e;
}

test("free-run bar grid snaps to a single bar, not the loop cycle", () => {
  const ctx = mockCtx();
  const e = engine(ctx);
  assert.ok(Math.abs(e.loopLengthSeconds - 4 * BAR) < 0.01);
  e._lockAnchor = 10.0;
  // Tapped 1.2s into the cycle: the old full-cycle snap waited ~6.3s.
  const start = e._lockLaunchTime(11.2, "bar");
  const wait = start - 11.2;
  assert.ok(wait <= BAR + 1e-6, `wait ${wait} exceeds one bar`);
  // …and lands ON the anchor's bar lattice.
  const phase = (start - 10.0) % BAR;
  assert.ok(Math.min(phase, BAR - phase) < 1e-6, `off-lattice by ${phase}`);
});

test("transport path snaps to the song's real downbeats", () => {
  const ctx = mockCtx();
  const e = engine(ctx);
  const downbeats = [0.9, 0.9 + BAR, 0.9 + 2 * BAR, 0.9 + 3 * BAR];
  let songNow = 1.2; // just past downbeat 1 → next boundary is 0.9+BAR
  e.setTransport({
    isPlaying: () => true,
    getSongTime: () => songNow,
    tempoBpm: BPM,
    downbeatTimesSec: downbeats,
    beatTimesSec: null,
    barAnchorSongTime: 0,
  });
  ctx.setNow(20.0);
  const start = e._transportLaunchTime(20.0, "bar");
  const expectedWait = 0.9 + BAR - songNow;
  assert.ok(Math.abs(start - (20.0 + expectedWait)) < 1e-6);
});

test("padProgress includes the phase-lock start offset", () => {
  const ctx = mockCtx();
  const e = engine(ctx);
  const body = 4 * BAR;
  const anchor = 10.0;
  // Voice B joined two bars late with the matching phase offset. Without the
  // phaseSec term the playheads diverged by exactly that phase (shipped bug:
  // audio locked, UI lied).
  const phaseB = (2 * BAR) % body;
  e._voices.set(0, { loop: true, bodySec: body, startTime: anchor, phaseSec: 0 });
  e._voices.set(1, { loop: true, bodySec: body, startTime: anchor + 2 * BAR, phaseSec: phaseB });
  for (const T of [20.0, 33.3, 61.07]) {
    ctx.setNow(T);
    const a = e.padProgress(0);
    const b = e.padProgress(1);
    assert.ok(Math.abs(a - b) < 1e-9, `playheads diverge at T=${T}: ${a} vs ${b}`);
  }
});

test("padProgress reports 0 while armed (not yet fired)", () => {
  const ctx = mockCtx();
  const e = engine(ctx);
  e._voices.set(0, { loop: true, bodySec: 4 * BAR, startTime: 50.0, phaseSec: 1.0 });
  ctx.setNow(49.0);
  assert.equal(e.padProgress(0), 0);
});

test("quantizeWaitSec grace window fires immediately just past a boundary", () => {
  assert.equal(quantizeWaitSec(10.05, 2.0, 10.0, 0.08), 0); // inside grace
  const w = quantizeWaitSec(10.5, 2.0, 10.0, 0.08); // past grace → next unit
  assert.ok(Math.abs(w - 1.5) < 1e-9);
});

test("nextGridTimeSec snaps to the first grid time at/after now-minus-grace", () => {
  const grid = [1.0, 3.0, 5.0];
  assert.equal(nextGridTimeSec(3.05, grid, 0.08), 3.0); // grace: just passed
  assert.equal(nextGridTimeSec(3.5, grid, 0.08), 5.0);
  assert.equal(nextGridTimeSec(9.0, grid, 0.08), null); // past the end
});

test("willLoopFor: Tap gate force-loops even a non-loopable pad", () => {
  // Normal loop: needs a baked loop buffer (a one-shot alone never loops).
  assert.equal(PadEngine.willLoopFor({ loop: true }, true, true), true);
  assert.equal(PadEngine.willLoopFor({ loop: true }, false, true), false);
  // Tap gate (forceLoop): loops a NON-loopable pad by wrapping its one-shot, so
  // the voice is a live, releasable gate instead of a one-shot that plays its
  // whole length (iOS loopOverride parity — the "tap plays the whole clip" bug).
  assert.equal(PadEngine.willLoopFor({ loop: true, forceLoop: true }, false, true), true);
  // …but only when the pad actually HAS a one-shot to loop.
  assert.equal(PadEngine.willLoopFor({ loop: true, forceLoop: true }, false, false), false);
  // A loopable pad under a Tap still loops (uses its baked loop buffer).
  assert.equal(PadEngine.willLoopFor({ loop: true, forceLoop: true }, true, true), true);
  // No loop requested → never loops, forceLoop or not.
  assert.equal(PadEngine.willLoopFor({ loop: false, forceLoop: true }, true, true), false);
  assert.equal(PadEngine.willLoopFor(null, true, true), false);
});
