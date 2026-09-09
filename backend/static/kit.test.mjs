// kit.test.mjs — DOM-free smoke test for kit.js. Run: node kit.test.mjs
// Stubs window/document just enough to evaluate the classic script and
// exercise the pure helpers + the no-root mount/unmount no-throw paths.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./kit.js", import.meta.url), "utf8");
const window = { location: { origin: "https://jamn.app" } };
const document = { getElementById: () => null };
new Function("window", "document", src)(window, document);

const K = window.JamnKit;
assert.equal(typeof K.mount, "function");
assert.equal(typeof K.unmount, "function");

const { resolveStemUrl, parseColor } = K._internals;
assert.equal(resolveStemUrl("/api/stems/x.wav"), "https://jamn.app/api/stems/x.wav");
assert.equal(resolveStemUrl("https://r2.example.com/a.wav?sig=1"), "https://r2.example.com/a.wav?sig=1");
assert.equal(resolveStemUrl(null), null);

assert.deepEqual(parseColor("#3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor("3B82F6"), { r: 59, g: 130, b: 246 });
assert.deepEqual(parseColor(0xff0000), { r: 255, g: 0, b: 0 });
assert.deepEqual(parseColor("garbage"), { r: 139, g: 92, b: 246 }); // accent fallback
assert.deepEqual(parseColor(undefined), { r: 139, g: 92, b: 246 });

// Instant Groove picker (pure): best performanceScore||loopScore per core
// category, in fixed category order; non-target categories ignored.
const { pickInstantGroove, fmtTime } = K._internals;
assert.deepEqual(pickInstantGroove([]), []);
assert.deepEqual(pickInstantGroove(null), []);
assert.deepEqual(
  pickInstantGroove([
    { padIdx: 0, category: "DRUMS", performanceScore: 0.4 },
    { padIdx: 1, category: "DRUMS", performanceScore: 0.9 }, // best drums
    { padIdx: 2, category: "BASS", loopScore: 0.7 }, // loopScore fallback
    { padIdx: 3, category: "bass", performanceScore: 0.2 }, // case-insensitive, loses
    { padIdx: 4, category: "VOCAL", performanceScore: 1.0 }, // not a groove target
    { padIdx: 5, category: "LEAD" }, // no scores → 0, still the only lead
    { padIdx: 6 }, // no category → skipped
    { category: "TEXTURE", performanceScore: 0.8 }, // no padIdx → skipped
  ]),
  [1, 2, 5] // drums, bass, lead — category order, not score order
);
// performanceScore wins over a higher loopScore (native `??` chain).
assert.deepEqual(
  pickInstantGroove([
    { padIdx: 7, category: "CHORDS", performanceScore: 0.1, loopScore: 0.9 },
    { padIdx: 8, category: "CHORDS", performanceScore: 0.3, loopScore: 0.0 },
  ]),
  [8]
);

// Pad-count preference: URL ?pads= wins, else stored, else 16; only 16/64
// are real layouts, anything else falls back.
const { resolvePadCount } = K._internals;
assert.equal(resolvePadCount("?pads=64", null), 64);
assert.equal(resolvePadCount("?song=x&pads=64", "16"), 64);
assert.equal(resolvePadCount("?pads=16", "64"), 16); // URL beats storage
assert.equal(resolvePadCount("", "64"), 64);
assert.equal(resolvePadCount("", "16"), 16);
assert.equal(resolvePadCount("?pads=32", null), 16); // not a real layout
assert.equal(resolvePadCount(null, "banana"), 16);
assert.equal(resolvePadCount(undefined, undefined), 16);

// Layer picker: categories present in fixed order; members best
// performanceScore first with loopScore fallback (native pads(in:)).
const { layerCategories, padsInCategory } = K._internals;
const layerPads = [
  { padIdx: 0, category: "LEAD", performanceScore: 0.5 },
  { padIdx: 1, category: "DRUMS", performanceScore: 0.4 },
  { padIdx: 2, category: "drums", performanceScore: 0.9 }, // case-insensitive
  { padIdx: 3, category: "DRUMS", loopScore: 0.7 }, // loopScore fallback
  { padIdx: 4, category: "VOCAL" },
  { padIdx: 5 }, // no category → dropped
  { category: "BASS", performanceScore: 1 }, // no padIdx → dropped
];
assert.deepEqual(layerCategories(layerPads), ["DRUMS", "LEAD", "VOCAL"]);
assert.deepEqual(layerCategories([]), []);
assert.deepEqual(layerCategories(null), []);
assert.deepEqual(
  padsInCategory(layerPads, "DRUMS").map((p) => p.padIdx),
  [2, 3, 1] // 0.9 > loopScore 0.7 > 0.4
);
assert.deepEqual(padsInCategory(layerPads, "drums").map((p) => p.padIdx), [2, 3, 1]);
assert.deepEqual(padsInCategory(layerPads, "BASS"), []); // padIdx-less dropped
// performanceScore wins over a higher loopScore (native `??` chain).
assert.deepEqual(
  padsInCategory(
    [
      { padIdx: 7, category: "CHORDS", performanceScore: 0.1, loopScore: 0.9 },
      { padIdx: 8, category: "CHORDS", performanceScore: 0.3, loopScore: 0.0 },
    ],
    "CHORDS"
  ).map((p) => p.padIdx),
  [8, 7]
);

// Sequence step flags: kit-level defaultSequence (kind=flip wire format)
// → padIdx → boolean steps; two tracks on one pad OR together; velocity 0
// = rest. Kits without a sequence (kind=auto/drums) → null.
const { padStepFlags } = K._internals;
assert.equal(padStepFlags({ pads: [] }), null);
assert.equal(padStepFlags(null), null);
assert.equal(padStepFlags({ defaultSequence: { tracks: [] } }), null);
const v = (x) => ({ velocity: x, probability: 1 });
assert.deepEqual(
  padStepFlags({
    defaultSequence: {
      tracks: [
        { chopRef: { packPad: { padIdx: 0 } }, steps: [v(1), v(0), v(0.5), v(0)] },
        { chopRef: { packPad: { padIdx: 0 } }, steps: [v(0), v(0.7), v(0), v(0)] },
        { chopRef: { packPad: { padIdx: 3 } }, steps: [v(0), v(0), v(0), v(0.9)] },
        { steps: [v(1)] }, // no chopRef → dropped
        { chopRef: { packPad: { padIdx: 5 } } }, // no steps → dropped
      ],
    },
  }),
  { 0: [true, true, true, false], 3: [false, false, false, true] }
);

// Feedback queue: payload rows match the native poster shape
// ({assetId, kind}); invalid rows dropped; cap drops the OLDEST.
const { pushPadEvent } = K._internals;
{
  const q = [];
  pushPadEvent(q, "asset-1", "play");
  pushPadEvent(q, "asset-1", "skip");
  pushPadEvent(q, "", "play"); // empty assetId dropped
  pushPadEvent(q, null, "play"); // non-string dropped
  pushPadEvent(q, "asset-2", "held"); // unknown kind dropped
  assert.deepEqual(q, [
    { assetId: "asset-1", kind: "play" },
    { assetId: "asset-1", kind: "skip" },
  ]);
  const capped = [];
  for (let i = 0; i < 10; i++) pushPadEvent(capped, "a" + i, "play", 3);
  assert.deepEqual(
    capped.map((e) => e.assetId),
    ["a7", "a8", "a9"]
  );
}

assert.equal(fmtTime(0), "0:00");
assert.equal(fmtTime(65.7), "1:05");
assert.equal(fmtTime(NaN), "0:00");
assert.equal(fmtTime(-3), "0:00");

// Engine↔song transport wiring: setTransport gets a live host bridge only
// when JamnKitHost + tempo exist; closures track the CURRENT host; losing
// the host detaches (null) so the engine falls back to its free-run grid.
const { syncEngineTransport } = K._internals;
{
  const calls = [];
  const s = {
    engine: { setTransport: (t) => calls.push(t) },
    entry: { result: { tempo_bpm: 120 } },
    engineTransport: null,
  };
  // No host yet → nothing wired, no call.
  syncEngineTransport(s);
  assert.equal(calls.length, 0);
  assert.equal(s.engineTransport, null);

  // Host appears → wired once with tempo + bar anchor 0.
  window.JamnKitHost = { isPlaying: () => true, getTime: () => 3.25 };
  syncEngineTransport(s);
  assert.equal(calls.length, 1);
  const t = calls[0];
  assert.equal(t.tempoBpm, 120);
  assert.equal(t.barAnchorSongTime, 0);
  assert.equal(t.isPlaying(), true);
  assert.equal(t.getSongTime(), 3.25);

  // Idempotent while wired — no churn on the 2 s re-check.
  syncEngineTransport(s);
  assert.equal(calls.length, 1);

  // Closures read the LIVE host: a replaced JamnKitHost tracks without
  // re-wiring, and a throwing host degrades (stopped / NaN), never throws.
  window.JamnKitHost = { isPlaying: () => false, getTime: () => 7 };
  assert.equal(t.isPlaying(), false);
  assert.equal(t.getSongTime(), 7);
  window.JamnKitHost = {
    isPlaying: () => { throw new Error("boom"); },
    getTime: () => { throw new Error("boom"); },
  };
  assert.equal(t.isPlaying(), false);
  assert.ok(Number.isNaN(t.getSongTime()));

  // Host gone → detach: engine returns to free-run quantize.
  delete window.JamnKitHost;
  assert.equal(t.isPlaying(), false); // safe even while detached
  syncEngineTransport(s);
  assert.equal(calls.length, 2);
  assert.equal(calls[1], null);
  assert.equal(s.engineTransport, null);
  syncEngineTransport(s); // detach is idempotent too
  assert.equal(calls.length, 2);
}
{
  // No usable tempo → never wires (the engine would build a garbage grid).
  window.JamnKitHost = { isPlaying: () => true, getTime: () => 1 };
  const s = {
    engine: { setTransport: () => { throw new Error("must not wire without tempo"); } },
    entry: { result: {} },
    engineTransport: null,
  };
  syncEngineTransport(s);
  s.entry.result.tempo_bpm = 0;
  syncEngineTransport(s);
  s.entry = null; // pack mounts have no entry at all
  syncEngineTransport(s);
  // Engine without setTransport (older twin) → silent no-op.
  syncEngineTransport({ engine: {}, entry: { result: { tempo_bpm: 120 } } });
  delete window.JamnKitHost;
}

// Radial wheel geometry: 92 px floor for small wheels (the tuned 4-button
// look); larger rings grow to keep ≥78 px of arc per 54 px button; the
// unifying disc tracks the radius.
const { radialGeometry } = K._internals;
assert.deepEqual(radialGeometry(4), { radius: 92, disc: 252 });
assert.deepEqual(radialGeometry(1), { radius: 92, disc: 252 });
assert.deepEqual(radialGeometry(9), { radius: 112, disc: 292 }); // full parity ring
assert.ok(radialGeometry(12).radius > radialGeometry(9).radius); // monotonic growth
for (const n of [7, 8, 9, 12]) {
  const g = radialGeometry(n);
  assert.ok((2 * Math.PI * g.radius) / n >= 77.5, "arc spacing holds at " + n);
}

// Pie-wheel geometry (desktop PadRadialMenu parity): equal 360/count
// wedges, wedge 0 CENTERED at the top, proceeding clockwise; a solid hub.
const { wedgeGeometry, wedgeAngles, wedgePath, wedgeLabelPoint } = K._internals;

// Wedge 0 straddles the top (−90°); each slice is 360/count wide.
assert.deepEqual(wedgeAngles(0, 4), { start: -135, end: -45, mid: -90 });
assert.deepEqual(wedgeAngles(1, 4), { start: -45, end: 45, mid: 0 });
assert.deepEqual(wedgeAngles(2, 4), { start: 45, end: 135, mid: 90 });
assert.deepEqual(wedgeAngles(3, 4), { start: 135, end: 225, mid: 180 });

// Wedges tile the full circle with no overlap or gap: each end meets the
// next start, and every slice is exactly 360/count wide.
for (const n of [3, 7, 10]) {
  for (let i = 0; i < n; i++) {
    const a = wedgeAngles(i, n);
    assert.ok(Math.abs(a.end - a.start - 360 / n) < 1e-9, "slice width " + n);
    const nxt = wedgeAngles((i + 1) % n, n);
    const gap = (((nxt.start - a.end) % 360) + 360) % 360;
    assert.ok(gap < 1e-9 || Math.abs(gap - 360) < 1e-9, "wedges tile " + n + "/" + i);
  }
}

// Ring grows so the outer arc per wedge stays legible (≥66 px); the SVG
// box tracks the outer radius; hub floor holds at the desktop-ish 126.
{
  const g = wedgeGeometry(10);
  assert.ok(g.outer >= 126 && g.inner > 0);
  assert.equal(g.size, (g.outer + 6) * 2);
  assert.ok((2 * Math.PI * g.outer) / 10 >= 66, "outer arc per wedge");
  assert.equal(wedgeGeometry(4).outer, 126); // small ring pinned to the floor
}

// wedgePath is a closed donut segment: moveto, outer arc, inner arc, close.
{
  const d = wedgePath(0, 8, 50, 120, 128, 128);
  assert.equal(typeof d, "string");
  assert.ok(d[0] === "M" && /Z$/.test(d));
  assert.equal((d.match(/A/g) || []).length, 2); // outer + inner arc
}

// Label point rides the mid-angle at mid-radius: wedge 0 sits straight up.
{
  const lp = wedgeLabelPoint(0, 4, 50, 120, 100, 100);
  assert.ok(Math.abs(lp.x - 100) < 1e-6, "wedge0 label centered on x");
  assert.ok(lp.y < 100, "wedge0 label above center"); // top of the wheel
}

// FX store serialization: JSON {padIdx: fxDict}; garbage rows dropped
// (clamping is the engine's job at apply time); empty/corrupt → null so a
// bad blob degrades to "no FX", never a throw.
const { parsePadFxStore, serializePadFxStore } = K._internals;
assert.equal(parsePadFxStore(null), null);
assert.equal(parsePadFxStore("not json"), null);
assert.equal(parsePadFxStore("{}"), null);
assert.equal(parsePadFxStore("[1,2]"), null);
assert.equal(parsePadFxStore('"str"'), null);
assert.deepEqual(
  parsePadFxStore(
    JSON.stringify({
      0: { delayMix: 40, filterCutoffHz: 900 },
      3: { gain: 1.5 },
      "-1": { delayMix: 10 }, // negative index dropped
      "2.5": { delayMix: 10 }, // non-integer key dropped
      x: { delayMix: 10 }, // non-numeric key dropped
      5: "loud", // non-object value dropped
      6: [1, 2], // array value dropped
      7: null,
    })
  ),
  { 0: { delayMix: 40, filterCutoffHz: 900 }, 3: { gain: 1.5 } }
);
// All rows invalid → null (caller removes the storage key).
assert.equal(parsePadFxStore(JSON.stringify({ x: 1, 5: "junk" })), null);
assert.equal(serializePadFxStore(null), null);
assert.equal(serializePadFxStore({}), null);
{
  const map = { 2: { delayMix: 30 } };
  const json = serializePadFxStore(map);
  assert.deepEqual(parsePadFxStore(json), map); // round-trips
}

// Reset-state math: which overrides a pad carries (drives the radial
// Reset enabled state). loopOverride false (forced one-shot) still counts.
const { padOverrides } = K._internals;
assert.deepEqual(padOverrides(null, null, null, null), {
  region: false, gate: false, loop: false, fx: false, any: false,
});
assert.equal(padOverrides({ startSec: 0, endSec: 1 }, null, null, null).any, true);
assert.equal(padOverrides(null, { startSec: 0.5, endSec: 1 }, null, null).gate, true);
assert.equal(padOverrides(null, null, false, null).loop, true); // forced one-shot
assert.equal(padOverrides(null, null, true, null).any, true);
assert.equal(padOverrides(null, null, null, { delayMix: 20 }).fx, true);
{
  const all = padOverrides({ s: 1 }, { s: 2 }, true, { gain: 2 });
  assert.deepEqual(all, { region: true, gate: true, loop: true, fx: true, any: true });
}

// Log cutoff slider mapping: endpoints exact, round-trip within a step.
const { cutoffFromSlider, sliderFromCutoff } = K._internals;
assert.equal(cutoffFromSlider(0), 100);
assert.equal(cutoffFromSlider(1), 20000);
assert.equal(cutoffFromSlider(-2), 100); // clamped
assert.equal(cutoffFromSlider(9), 20000);
assert.ok(Math.abs(sliderFromCutoff(20000) - 1) < 1e-9);
assert.ok(Math.abs(sliderFromCutoff(100) - 0) < 1e-9);
assert.ok(Math.abs(sliderFromCutoff(cutoffFromSlider(0.5)) - 0.5) < 0.001);
assert.equal(sliderFromCutoff(undefined), 1); // absent → open

// Live-capture arrangement (pure): section normalization tolerates every
// field shape the analyzer/bundle paths emit and drops undated/degenerate
// rows; sections come back ascending.
const { normSections, sectionIndexAtTime, sectionsWithPads, arrangedPads,
  parseArrangement, serializeArrangement } = K._internals;
assert.deepEqual(normSections(null), []);
assert.deepEqual(normSections({}), []);
assert.deepEqual(normSections({ sections: "nope" }), []);
assert.deepEqual(
  normSections({
    sections: [
      { type: "Chorus", start_time: 20, end_time: 40 },
      { name: "Intro", start_s: 0, end_s: 8 }, // out of order → resorted
      { label: "Verse", start: 8, end: 20 }, // {start,end} fallback
      { type: "Bridge", startSec: 40, endSec: 50 }, // camel fallback
      { type: "Junk", start_time: 5, end_time: 5 }, // zero-length → dropped
      { type: "NoDates" }, // undated → dropped
      { start_s: null, end_s: 10 }, // null start (Number(null)=0 trap) → dropped
    ],
  }),
  [
    { startSec: 0, endSec: 8, label: "Intro" },
    { startSec: 8, endSec: 20, label: "Verse" },
    { startSec: 20, endSec: 40, label: "Chorus" },
    { startSec: 40, endSec: 50, label: "Bridge" },
  ]
);
// Missing label → "Section"; a 0 start is real (not the null trap).
assert.deepEqual(
  normSections({ sections: [{ start_s: 0, end_s: 4 }] }),
  [{ startSec: 0, endSec: 4, label: "Section" }]
);

// sectionIndexAtTime: last section whose start ≤ t; -1 before the first
// start or on non-finite t; a gap/overrun holds on the prior/final section.
const secs = [
  { startSec: 0, endSec: 8, label: "Intro" },
  { startSec: 8, endSec: 20, label: "Verse" },
  { startSec: 22, endSec: 40, label: "Chorus" }, // 20..22 is a gap
];
assert.equal(sectionIndexAtTime(secs, -1), -1); // before the first start
assert.equal(sectionIndexAtTime(secs, 0), 0); // exactly the first start
assert.equal(sectionIndexAtTime(secs, 7.9), 0);
assert.equal(sectionIndexAtTime(secs, 8), 1); // boundary belongs to the new section
assert.equal(sectionIndexAtTime(secs, 21), 1); // gap holds on the prior section
assert.equal(sectionIndexAtTime(secs, 22), 2);
assert.equal(sectionIndexAtTime(secs, 1000), 2); // past the end holds on the last
assert.equal(sectionIndexAtTime(secs, NaN), -1);
assert.equal(sectionIndexAtTime([], 5), -1);
assert.equal(sectionIndexAtTime(null, 5), -1);

// sectionsWithPads: ascending indices of sections that captured ≥1 pad;
// empty lists + bad keys dropped.
assert.deepEqual(sectionsWithPads({ 2: [1, 3], 0: [4], 1: [], x: [9], "-1": [2] }), [0, 2]);
assert.deepEqual(sectionsWithPads({}), []);
assert.deepEqual(sectionsWithPads(null), []);

// arrangedPads: de-duped, sorted union of every pad across the arrangement.
assert.deepEqual(arrangedPads({ 0: [3, 1], 2: [1, 5], 3: [] }), [1, 3, 5]);
assert.deepEqual(arrangedPads({}), []);
assert.deepEqual(arrangedPads(null), []);

// Arrangement persistence: JSON {sectionIndex:[padIdx]}; garbage keys/values
// and non-integer pads dropped, lists de-duped + sorted; empty/corrupt → null
// so a bad blob degrades to "no arrangement", never a throw; round-trips.
assert.equal(parseArrangement(null), null);
assert.equal(parseArrangement("not json"), null);
assert.equal(parseArrangement("{}"), null);
assert.equal(parseArrangement("[1,2]"), null);
assert.deepEqual(
  parseArrangement(
    JSON.stringify({
      0: [2, 0, 2, 1], // de-dupe + sort → [0,1,2]
      3: [5],
      "-1": [1], // negative index dropped
      "2.5": [1], // non-integer key dropped
      x: [1], // non-numeric key dropped
      4: [], // empty list dropped
      5: "loud", // non-array dropped
      6: [1.5, -2, "z", 3], // non-integer/negative/non-number pads dropped → [3]
    })
  ),
  { 0: [0, 1, 2], 3: [5], 6: [3] }
);
assert.equal(parseArrangement(JSON.stringify({ 1: [], x: [1] })), null); // nothing usable
assert.equal(serializeArrangement(null), null);
assert.equal(serializeArrangement({}), null);
assert.equal(serializeArrangement({ 0: [] }), null); // empty list → nothing to store
{
  const map = { 0: [1, 3], 2: [4] };
  assert.deepEqual(parseArrangement(serializeArrangement(map)), map); // round-trips
}

// applyPadRegion with nothing mounted → false, never a throw.
assert.equal(K.applyPadRegion(0, { startSec: 0, endSec: 1 }), false);

K.mount(null); // no #kit-root in the stub → silent no-op, must not throw
K.unmount(); // nothing mounted → must not throw

console.log("kit.test.mjs: all assertions passed");
