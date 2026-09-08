// padengine.test.mjs — node tests for the pure DSP in padengine.js.
// Ports of the native SeamlessLoopTests (mobile-ios) plus the scheduler's
// normalize/crossfade constants. Run: node padengine.test.mjs
//
// The DSP functions operate on Float32Array + sampleRate, so no WebAudio
// (and no deps) is needed here; the PadEngine class merely wraps them.

import assert from "node:assert/strict";
import {
  DEFAULT_LOOP_CROSSFADE_MS,
  NORMALIZE_TARGET_PEAK,
  LOOP_CONTINUATION_SEC,
  applyEdgeFades,
  normalizePeak,
  onsetAlignedShift,
  exactCrossfaded,
  chooseCrossfadeMs,
  armedWatchdogDelayMs,
  ARMED_WATCHDOG_GRACE_SEC,
  PadEngine,
} from "./padengine.js";

const SR = 48000;
const tests = [];
function test(name, fn) {
  tests.push([name, fn]);
}

/** Mono sine with integer `period` (frames), Float32 like the native fixture. */
function sineBuffer(frames, period, amp = 1.0) {
  const d = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    d[i] = Math.fround(amp * Math.sin((2 * Math.PI * i) / period));
  }
  return d;
}

function flatBuffer(frames, value = 1.0) {
  return new Float32Array(frames).fill(value);
}

function maxAbs(d) {
  let m = 0;
  for (const v of d) if (Math.abs(v) > m) m = Math.abs(v);
  return m;
}

// --- exact-length seam bake ------------------------------------------------

test("exactCrossfaded returns exactly loopFrames (long + short continuation)", () => {
  const n = 48000;
  // Continuation longer than the fade.
  const out = exactCrossfaded([sineBuffer(n + 512, 128)], SR, n, 12);
  assert.equal(out[0].length, n);
  // Continuation SHORTER than the fade: the fade clamps to what's
  // available, the length must still be exact.
  const out2 = exactCrossfaded([sineBuffer(n + 40, 128)], SR, n, 12);
  assert.equal(out2[0].length, n);
});

test("seam continuity on a misaligned sine: wrap step tiny vs raw cut", () => {
  // Period 127 does NOT divide n, so a raw hard cut has a large wrap
  // discontinuity; the baked head is rebuilt from continuation audio that
  // genuinely follows the tail in the source, so its wrap step is a normal
  // sine step.
  const n = 48000;
  const p = 127;
  const s = sineBuffer(n + 512, p);
  const out = exactCrossfaded([s], SR, n, 4);
  const d = out[0];
  assert.equal(d.length, n);

  const maxSineStep = (2 * Math.PI) / p;
  const bakedStep = Math.abs(d[0] - d[n - 1]);
  const rawStep = Math.abs(s[0] - s[n - 1]); // hard cut, no bake
  assert.ok(bakedStep <= maxSineStep * 1.5, `baked wrap step ${bakedStep} too large`);
  assert.ok(rawStep > 0.3, `fixture broken: raw cut step ${rawStep} should be large`);
  assert.ok(bakedStep < rawStep / 3, `baked ${bakedStep} not clearly smaller than raw ${rawStep}`);

  // Grid lock: past the blend region the body is copied verbatim, so
  // pass-2 playback matches the ideal s[t mod n].
  const x = Math.trunc((4 / 1000) * SR); // 192
  let errNew = 0;
  for (let j = x; j < x + p; j++) errNew = Math.max(errNew, Math.abs(d[j] - s[j]));
  assert.ok(errNew < 0.02, `grid-lock error ${errNew}`);
});

test("exactCrossfaded fallback without continuation: exact length, edge ramps", () => {
  const n = 9600;
  const out = exactCrossfaded([flatBuffer(n)], SR, n, 12);
  const d = out[0];
  assert.equal(d.length, n);
  assert.ok(Math.abs(d[0]) < 1e-5, "head fades in from 0");
  assert.ok(Math.abs(d[n - 1]) < 0.02, "tail fades out to ~0");
  assert.ok(Math.abs(d[n >> 1] - 1.0) < 1e-6, "body untouched");
});

test("transient head caps fade at 3 ms", () => {
  // Loud attack in the first 10 ms (RMS > 2x the 10-60 ms RMS and > 1e-4)
  // must cap the requested 12 ms fade at 3 ms so the attack isn't played at
  // reduced gain every pass.
  const n = 9600;
  const cont = 512;
  const s = new Float32Array(n + cont);
  const loud = Math.trunc(0.010 * SR); // 480
  for (let i = 0; i < loud; i++) s[i] = Math.fround(0.8 * Math.sin((2 * Math.PI * i) / 32));
  for (let i = loud; i < n; i++) s[i] = Math.fround(0.05 * Math.sin((2 * Math.PI * i) / 128));
  for (let i = n; i < n + cont; i++) s[i] = 0.5; // continuation, clearly distinct
  const d = exactCrossfaded([s], SR, n, 12)[0];
  assert.equal(d.length, n);

  const x3 = Math.trunc(0.003 * SR); // 144
  const x12 = Math.trunc(0.012 * SR); // 576
  // Blend confined to the first 3 ms: frame 0 is pure continuation...
  assert.equal(d[0], s[n]);
  // ...and everything from 3 ms to the un-capped 12 ms is verbatim body.
  for (let i = x3; i < x12; i++) {
    assert.equal(d[i], s[i], `frame ${i} was blended despite the 3 ms cap`);
  }

  // Contrast: a sustained head keeps the full 12 ms fade (frame 300 is a
  // blend, not verbatim, when the continuation differs from the head).
  const s2 = sineBuffer(n + cont, 128, 0.5);
  for (let i = n; i < n + cont; i++) s2[i] = Math.fround(-s2[i - n]); // half-period shift
  const d2 = exactCrossfaded([s2], SR, n, 12)[0];
  let blended = false;
  for (let i = x3; i < x12; i++) {
    if (d2[i] !== s2[i]) {
      blended = true;
      break;
    }
  }
  assert.ok(blended, "sustained head should keep the full requested fade");
});

// --- onset-aligned shift ---------------------------------------------------

test("onsetAlignedShift finds attack 30 ms before center (lands ~5 ms pre-attack)", () => {
  // Silence with a sharp attack 30 ms BEFORE the nominal cut point — the
  // grid-late-downbeat case. The shift must move the cut to just ahead of
  // the attack (5 ms preroll), keeping it inside the loop.
  const n = 9600; // 200 ms scan region
  const d = new Float32Array(n);
  const center = 4800; // nominal cut at 100 ms
  const attack = center - Math.trunc(0.030 * SR); // real kick at 70 ms
  for (let i = attack; i < Math.min(n, attack + 2000); i++) {
    d[i] = Math.fround(0.8 * Math.exp(-(i - attack) / 400.0));
  }
  const shift = onsetAlignedShift(d, SR, center, Math.trunc(0.060 * SR), Math.trunc(0.005 * SR));
  const newCut = center + shift;
  assert.ok(newCut < attack + 100, "cut must land before the attack");
  assert.ok(newCut > attack - Math.trunc(0.015 * SR), "cut must stay near the attack, not run away");
});

test("onsetAlignedShift is zero for sustained material", () => {
  // A steady sine has no clear transient — the cut must not move.
  const shift = onsetAlignedShift(sineBuffer(9600, 128), SR, 4800, 2880, 240);
  assert.equal(shift, 0);
});

// --- normalize -------------------------------------------------------------

test("normalizePeak boosts to -4 dBFS and caps at 4x", () => {
  // Normal case: peak 0.9 pulled down to the 0.63 target.
  const loud = sineBuffer(4800, 128, 0.9);
  normalizePeak([loud]);
  assert.ok(Math.abs(maxAbs(loud) - NORMALIZE_TARGET_PEAK) < 1e-3, `peak ${maxAbs(loud)}`);

  // Boost CAP (+12 dB): a whisper-quiet slice is raised by at most 4x, not
  // dragged to target ("fuzzy samples" guard).
  const quiet = sineBuffer(4800, 128, 0.001);
  normalizePeak([quiet]);
  assert.ok(Math.abs(maxAbs(quiet) - 0.004) < 1e-5, `capped peak ${maxAbs(quiet)}`);

  // Silence guard: peak <= 1e-4 left untouched.
  const silent = sineBuffer(4800, 128, 5e-5);
  const before = maxAbs(silent);
  normalizePeak([silent]);
  assert.equal(maxAbs(silent), before);

  // Near-unity gain skipped (already at target).
  const atTarget = flatBuffer(100, NORMALIZE_TARGET_PEAK);
  normalizePeak([atTarget]);
  assert.equal(atTarget[50], Math.fround(NORMALIZE_TARGET_PEAK));
});

// --- edge fades ------------------------------------------------------------

test("applyEdgeFades ramps start and tail to zero, body untouched", () => {
  const n = 4800; // 100 ms
  const d = flatBuffer(n);
  applyEdgeFades([d], SR, 3, 5);
  assert.ok(Math.abs(d[0]) < 1e-6);
  assert.ok(Math.abs(d[n - 1]) < 1e-6);
  assert.ok(Math.abs(d[n >> 1] - 1.0) < 1e-6);
  // Ramps are monotonic up from the head and up toward the tail.
  const a = Math.trunc(0.003 * SR);
  assert.ok(d[1] < d[a - 1]);
  assert.ok(d[a + 1] > d[0]);
});

test("applyEdgeFades no-op on tiny buffer", () => {
  const d = flatBuffer(8);
  applyEdgeFades([d], SR);
  for (let i = 0; i < 8; i++) assert.equal(d[i], 1.0);
});

// --- crossfade choice ------------------------------------------------------

test("chooseCrossfadeMs: measured > loopScore map > 12 ms floor, clamped 8..30", () => {
  assert.equal(chooseCrossfadeMs({ crossfadeMs: 20 }), 20);
  assert.equal(chooseCrossfadeMs({ crossfadeMs: 50 }), 30);
  assert.equal(chooseCrossfadeMs({ crossfadeMs: 2 }), 8);
  // (1 - loopScore) * 45, clamped.
  assert.ok(Math.abs(chooseCrossfadeMs({ loopScore: 0.8 }) - 9) < 1e-9);
  assert.equal(chooseCrossfadeMs({ loopScore: 0.0 }), 30);
  assert.equal(chooseCrossfadeMs({ loopScore: 1.0 }), 8);
  assert.equal(chooseCrossfadeMs({}), DEFAULT_LOOP_CROSSFADE_MS);
});

test("constants match native", () => {
  assert.equal(DEFAULT_LOOP_CROSSFADE_MS, 12.0);
  assert.equal(NORMALIZE_TARGET_PEAK, 0.63);
  assert.equal(LOOP_CONTINUATION_SEC, 0.035);
});

// --- PadEngine scheduling (fake WebAudio) ----------------------------------
//
// The engine's scheduling/takeover logic is pure arithmetic over
// ctx.currentTime + node bookkeeping, so a minimal fake AudioContext is
// enough to test it in node — no real audio graph.

const FAKE_SR = 8000; // small so the bake stays fast

class FakeParam {
  constructor() {
    this.value = 1;
  }
  setValueAtTime() {}
  linearRampToValueAtTime() {}
}

class FakeAudioContext {
  constructor() {
    this.currentTime = 0;
    this.destination = { name: "ctx-destination" };
  }
  createBuffer(numCh, length, sampleRate) {
    const data = Array.from({ length: numCh }, () => new Float32Array(length));
    return {
      numberOfChannels: numCh,
      length,
      sampleRate,
      duration: length / sampleRate,
      getChannelData: (c) => data[c],
      copyToChannel(src, c) {
        data[c].set(src);
      },
    };
  }
  createBufferSource() {
    return {
      buffer: null,
      loop: false,
      loopStart: 0,
      loopEnd: 0,
      onended: null,
      connect() {},
      start(t) {
        this.startedAt = t;
      },
      stop() {},
    };
  }
  createGain() {
    return { gain: new FakeParam(), connect() {} };
  }
}

/** Engine with two loopable pads on `roles` over 3 s flat stems. */
function makeEngine(roles = ["drums", "drums"], tempoBpm = 120) {
  const ctx = new FakeAudioContext();
  const engine = new PadEngine(ctx, { name: "host-bus" });
  const stems = {};
  for (const role of roles) {
    if (!stems[role]) {
      const buf = ctx.createBuffer(1, 3 * FAKE_SR, FAKE_SR);
      buf.getChannelData(0).fill(0.5); // sustained → no onset shift
      stems[role] = buf;
    }
  }
  engine.setStems(stems);
  engine.setKit(
    {
      pads: roles.map((role, i) => ({
        padIdx: i,
        loopable: true,
        stemSlice: { stemRole: role, startSec: 0, endSec: 2 },
      })),
    },
    { tempoBpm }
  );
  engine.prepare();
  return { engine, ctx };
}

test("transport-aligned launch lands on the next song bar", () => {
  const { engine, ctx } = makeEngine();
  // 120 bpm → 2 s bars anchored at song time 0.
  let songNow = 3.25;
  engine.setTransport({
    isPlaying: () => true,
    getSongTime: () => songNow,
    tempoBpm: 120,
    barAnchorSongTime: 0,
  });
  ctx.currentTime = 10;
  // Next bar at song 4.0 → 0.75 s away → ctx time 10.75 (rate 1: the
  // launchTime = now + (nextBarSongTime - songTimeNow) identity).
  const r = engine.trigger(0, { loop: true, quantized: true });
  assert.ok(Math.abs(r.startTime - 10.75) < 1e-9, `startTime ${r.startTime}`);

  // Boundary grace: a press just past a bar line fires immediately.
  songNow = 4.05;
  ctx.currentTime = 20;
  const r2 = engine.trigger(1, { loop: true, quantized: true });
  assert.equal(r2.startTime, 20);

  // Non-zero bar anchor shifts the grid.
  songNow = 3.25;
  engine.setTransport({
    isPlaying: () => true,
    getSongTime: () => songNow,
    tempoBpm: 120,
    barAnchorSongTime: 0.5, // bars at 0.5, 2.5, 4.5, ...
  });
  ctx.currentTime = 30;
  const r3 = engine.trigger(0, { loop: true, quantized: true });
  assert.ok(Math.abs(r3.startTime - 31.25) < 1e-9, `startTime ${r3.startTime}`);
});

test("stopped transport falls back to the free-run lock grid", () => {
  const { engine, ctx } = makeEngine();
  engine.setTransport({
    isPlaying: () => false,
    getSongTime: () => 3.25,
    tempoBpm: 120,
  });
  // First loop launch anchors the free-run grid at NOW, ignoring the song
  // grid (song 3.25 would have queued 0.75 s out were the transport rolling).
  ctx.currentTime = 5;
  const r = engine.trigger(0, { loop: true, quantized: true });
  assert.equal(r.startTime, 5);
  assert.equal(engine.lockInfo().anchor, 5);
  // Second pad queues to the free-run cycle (bar 2 s → 4 bars fit 8 s).
  assert.equal(engine.lockInfo().cycle, 8);
  ctx.currentTime = 6;
  const r2 = engine.trigger(1, { loop: true, quantized: true });
  assert.equal(r2.startTime, 13); // anchor 5 + one 8 s cycle
});

test("takeover: two loops one role = one activation, both released = one deactivation", () => {
  const { engine, ctx } = makeEngine(["drums", "drums", "bass"]);
  const events = [];
  engine.ontakeover = (role, active) => events.push([role, active]);

  engine.trigger(0, { loop: true });
  engine.trigger(1, { loop: true });
  assert.deepEqual(events, [["drums", true]]); // second voice: count 1→2, silent

  engine.release(0);
  assert.deepEqual(events, [["drums", true]]); // count 2→1, silent
  engine.release(1);
  assert.deepEqual(events, [["drums", true], ["drums", false]]);

  // Independent roles duck independently; stopAll restores everything.
  events.length = 0;
  engine.trigger(0, { loop: true });
  engine.trigger(2, { loop: true });
  assert.deepEqual(events, [["drums", true], ["bass", true]]);
  engine.stopAll();
  assert.deepEqual(events, [
    ["drums", true],
    ["bass", true],
    ["drums", false],
    ["bass", false],
  ]);

  // Same-stem retrigger keeps the duck: no deactivate/activate blip
  // (ChopPlayer's takeoverStem transfer).
  events.length = 0;
  engine.trigger(0, { loop: true });
  engine.trigger(0, { loop: true });
  assert.deepEqual(events, [["drums", true]]);
  engine.release(0);
  assert.deepEqual(events, [["drums", true], ["drums", false]]);

  // One-shots duck too (ChopPlayer takes over on ANY chop voice) and
  // restore on their NATURAL end.
  events.length = 0;
  engine.trigger(0, {});
  assert.deepEqual(events, [["drums", true]]);
  engine._voices.get(0).source.onended(); // buffer played through
  assert.deepEqual(events, [["drums", true], ["drums", false]]);
  // Late onended after an explicit stop must not double-decrement.
  events.length = 0;
  engine.trigger(0, {});
  const src = engine._voices.get(0).source;
  engine.release(0);
  src.onended();
  assert.deepEqual(events, [["drums", true], ["drums", false]]);
  void ctx;
});

test("setRate scales the free-run lock cycle spacing", () => {
  const { engine, ctx } = makeEngine();
  engine.setRate(2.0); // double-speed practice → cycles half as long
  assert.equal(engine.lockInfo().cycle, 4); // 8 s grid / 2
  ctx.currentTime = 0;
  const r = engine.trigger(0, { loop: true, quantized: true });
  assert.equal(r.startTime, 0); // first launch anchors
  ctx.currentTime = 1;
  const r2 = engine.trigger(1, { loop: true, quantized: true });
  assert.equal(r2.startTime, 4); // next rate-scaled boundary, not 8

  // Transport path: song-time delta converts at the practice rate too
  // (desktop divides launch delays by tempoPct).
  engine.setTransport({
    isPlaying: () => true,
    getSongTime: () => 3.0, // next bar at 4.0 → 1 s of song → 0.5 s real
    tempoBpm: 120,
  });
  ctx.currentTime = 10;
  const r3 = engine.trigger(0, { loop: true, quantized: true });
  assert.ok(Math.abs(r3.startTime - 10.5) < 1e-9, `startTime ${r3.startTime}`);

  // Invalid rates reset to 1.
  engine.setRate(0);
  assert.equal(engine.lockInfo().cycle, 8);
});

// --- armed watchdog + suspended-context guard --------------------------------

test("armedWatchdogDelayMs: scheduled wait + grace, bounded by cycle + grace", () => {
  // Free-run example: launch queued 7 s out → check 8 s later (7 + 1 grace).
  assert.equal(armedWatchdogDelayMs(13, 6), 8000);
  // Immediate launch: only the grace second.
  assert.equal(armedWatchdogDelayMs(5, 5), ARMED_WATCHDOG_GRACE_SEC * 1000);
  // Launch already in the past never yields a negative delay.
  assert.equal(armedWatchdogDelayMs(3, 5), 0);
  // The wait is at most one lock cycle, so the check always lands within
  // cycle + grace — the "armed forever must be impossible" bound.
  const cycle = 8;
  for (const phase of [0, 0.1, 4, 7.99]) {
    const delay = armedWatchdogDelayMs(10 + (cycle - phase), 10);
    assert.ok(delay <= (cycle + ARMED_WATCHDOG_GRACE_SEC) * 1000, `phase ${phase}: ${delay}`);
  }
  // Custom grace plumbs through.
  assert.equal(armedWatchdogDelayMs(12, 10, 0.5), 2500);
});

test("suspended context: trigger resumes FIRST, computes launch after", async () => {
  const { engine, ctx } = makeEngine();
  ctx.currentTime = 10; // frozen, stale — must NOT be used for launch math
  ctx.state = "suspended";
  ctx.resume = () => {
    ctx.state = "running";
    ctx.currentTime = 50; // the live clock the resumed context reports
    return Promise.resolve();
  };
  const r = engine.trigger(0, { loop: true, quantized: true });
  assert.equal(r.deferred, true);
  assert.equal(r.startTime, null);
  assert.equal(engine._voices.get(0), undefined, "no voice until resume lands");
  await new Promise((res) => setTimeout(res, 0));
  const v = engine._voices.get(0);
  assert.ok(v, "voice starts once the clock is live");
  assert.ok(v.startTime >= 50, `launch ${v.startTime} must use the post-resume clock, not 10`);
  assert.equal(v.source.startedAt, v.startTime);
});

test("release during a deferred trigger cancels the pending voice", async () => {
  const { engine, ctx } = makeEngine();
  ctx.state = "suspended";
  ctx.resume = () => {
    ctx.state = "running";
    return Promise.resolve();
  };
  const r = engine.trigger(0, { loop: true, quantized: true });
  assert.equal(r.deferred, true);
  engine.release(0); // user let go before the resume landed
  await new Promise((res) => setTimeout(res, 0));
  assert.equal(engine._voices.get(0), undefined, "released pad must not start late");

  // A retrigger during the resume supersedes the first pending voice —
  // exactly one voice, from the second trigger. Like a real context, the
  // fake stays "suspended" until the resume promise settles.
  ctx.state = "suspended";
  let resumes = 0;
  ctx.resume = () => {
    resumes++;
    return Promise.resolve().then(() => {
      ctx.state = "running";
      ctx.currentTime = 5;
    });
  };
  engine.trigger(0, { loop: true, quantized: true });
  engine.trigger(0, { loop: true, quantized: true });
  await new Promise((res) => setTimeout(res, 0));
  assert.ok(engine._voices.get(0), "superseding trigger still starts");
  assert.equal(resumes, 2);
});

test("armed watchdog force-starts a voice the clock never launched", () => {
  const { engine, ctx } = makeEngine();
  ctx.currentTime = 0;
  engine.trigger(0, { loop: true, quantized: true }); // anchors the lock grid at 0
  ctx.currentTime = 1;
  const r = engine.trigger(1, { loop: true, quantized: true });
  assert.equal(r.startTime, 8); // queued to the next 8 s cycle boundary
  const v = engine._voices.get(1);
  assert.ok(v.watchdog != null, "scheduled launch must carry a watchdog");

  // Healthy clock: by check time the clock passed the launch — no-op.
  ctx.currentTime = 9.1;
  engine._watchdogCheck(1, v);
  assert.equal(engine._voices.get(1), v, "on-time voice left alone");

  // Stalled clock: real time elapsed but ctx.currentTime never reached the
  // launch — warn + force an immediate retrigger so armed can't be forever.
  ctx.currentTime = 1.5;
  const warnings = [];
  const origWarn = console.warn;
  console.warn = (m) => warnings.push(String(m));
  let v2;
  try {
    engine._watchdogCheck(1, v);
    v2 = engine._voices.get(1);
  } finally {
    console.warn = origWarn;
  }
  assert.ok(v2 && v2 !== v, "a fresh voice replaces the stuck one");
  assert.equal(v2.startTime, 1.5); // immediate, not re-quantized
  assert.equal(v2.loop, true);
  assert.equal(warnings.length, 1);
  assert.ok(/force-starting/.test(warnings[0]), warnings[0]);
  if (v2.watchdog) clearTimeout(v2.watchdog);

  // A released voice is never force-started (late timer after release).
  engine.release(1);
  const before = engine._voices.get(1);
  engine._watchdogCheck(1, v2);
  assert.equal(engine._voices.get(1), before);
});

// --- runner ----------------------------------------------------------------

let failed = 0;
for (const [name, fn] of tests) {
  try {
    await fn(); // async tests (deferred-resume paths) await; sync ones no-op
    console.log(`ok   ${name}`);
  } catch (err) {
    failed++;
    console.error(`FAIL ${name}`);
    console.error(`     ${err.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
