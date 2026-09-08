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

// --- runner ----------------------------------------------------------------

let failed = 0;
for (const [name, fn] of tests) {
  try {
    fn();
    console.log(`ok   ${name}`);
  } catch (err) {
    failed++;
    console.error(`FAIL ${name}`);
    console.error(`     ${err.message}`);
  }
}
console.log(`\n${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
