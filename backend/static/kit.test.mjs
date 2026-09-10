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

// Borrow layout (pure): a borrow manifest carries BOTH songs' loop pads,
// tagged per pad by `source` ("initial" = current song, "donor" = borrowed).
// arrangeBorrowLayout re-lays them on the 8×8 (64) grid: current song on top,
// a BLANK divider row, donor below — additive (no pad dropped), never 16.
const { arrangeBorrowLayout } = K._internals;
{
  // 8 initial + 8 donor: initial fills row 0, row 1 is the blank divider,
  // donor fills row 2 (padIdx 16..23).
  const initialPads = Array.from({ length: 8 }, (_, i) => ({ padIdx: i, source: "initial" }));
  const donorPads = Array.from({ length: 8 }, (_, i) => ({ padIdx: 8 + i, source: "donor" }));
  const src = initialPads.concat(donorPads);
  const { placements, dividerRow } = arrangeBorrowLayout(src, 8);

  // (a) Additive — every source pad is placed, none replaced.
  assert.equal(placements.length, src.length, "all pads placed (additive)");
  src.forEach((p) => assert.ok(placements.some((pl) => pl.pad === p), "pad kept"));

  // The layout targets the 8-wide 64 grid, not a packed 16.
  const maxIdx = Math.max(...placements.map((pl) => pl.padIdx));
  assert.ok(maxIdx > 15, "borrow layout spills past a 16 grid (stays 64)");
  assert.ok(maxIdx < 64, "borrow layout fits the 64 grid");

  // Both songs present with their tags intact.
  const initPlaced = placements.filter((pl) => pl.source === "initial");
  const donorPlaced = placements.filter((pl) => pl.source === "donor");
  assert.equal(initPlaced.length, 8, "initial pads present");
  assert.equal(donorPlaced.length, 8, "donor pads present");

  // Initial pads occupy the top row(s), donor pads start below the divider.
  assert.deepEqual(initPlaced.map((pl) => pl.padIdx).sort((a, b) => a - b), [0, 1, 2, 3, 4, 5, 6, 7]);
  assert.equal(dividerRow, 1, "divider is the first full row after initial");

  // (c) The divider row is EMPTY — no placement maps into it.
  const rowOf = (idx) => Math.floor(idx / 8);
  assert.ok(!placements.some((pl) => rowOf(pl.padIdx) === dividerRow), "divider row is empty");
  // Donor starts on the first full row after the divider.
  assert.equal(Math.min(...donorPlaced.map((pl) => pl.padIdx)), (dividerRow + 1) * 8);

  // (d) Every placed pad resolves to a source-song label — the same mapping
  // mountPack applies (initial → current song, donor → borrowed song).
  const label = (pl) => (pl.source === "donor" ? "Donor Song" : "Host Song");
  placements.forEach((pl) => {
    const l = label(pl);
    assert.ok(typeof l === "string" && l.length > 0, "pad has a source-song label");
  });
  assert.deepEqual(
    Array.from(new Set(placements.map(label))).sort(),
    ["Donor Song", "Host Song"],
    "both song labels represented"
  );
}
{
  // Uneven initial block: a partial last initial row still leaves a full blank
  // divider, and donor starts on the next full row (never overlapping).
  const src = [];
  for (let i = 0; i < 5; i++) src.push({ padIdx: i, source: "initial" }); // row 0 partial
  for (let i = 0; i < 6; i++) src.push({ padIdx: 8 + i, source: "donor" });
  const { placements, dividerRow } = arrangeBorrowLayout(src, 8);
  assert.equal(placements.length, 11, "additive across an uneven split");
  assert.equal(dividerRow, 1, "divider after the single (partial) initial row");
  const rowOf = (idx) => Math.floor(idx / 8);
  assert.ok(!placements.some((pl) => rowOf(pl.padIdx) === dividerRow), "uneven divider empty");
  const donor = placements.filter((pl) => pl.source === "donor");
  assert.equal(Math.min(...donor.map((pl) => pl.padIdx)), 16, "donor on the next full row");
}
{
  // Full 4-stem borrow (32 + 32 = 64): no room for a divider, so it is dropped
  // (dividerRow -1) and EVERY pad is still placed inside the 64 grid.
  const src = [];
  for (let i = 0; i < 32; i++) src.push({ padIdx: i, source: "initial" });
  for (let i = 0; i < 32; i++) src.push({ padIdx: 100 + i, source: "donor" });
  const { placements, dividerRow } = arrangeBorrowLayout(src, 8);
  assert.equal(placements.length, 64, "all 64 pads placed");
  assert.equal(dividerRow, -1, "no divider when the grid is full");
  assert.ok(Math.max(...placements.map((pl) => pl.padIdx)) < 64, "nothing pushed off the grid");
  // No two pads collide on the same cell.
  assert.equal(new Set(placements.map((pl) => pl.padIdx)).size, 64, "no cell collisions");
}
{
  // COMPACT (16 = 4×4): the 16/64 toggle must RE-ARRANGE a borrow, not clip it.
  // best-of-both — top 8 initial + top 8 donor by score, NOT the top 16 rows
  // (which dropped the donor). 12 + 12 candidates → 8 + 8 selected.
  const initialPads = Array.from({ length: 12 }, (_, i) => ({
    padIdx: i, source: "initial", performanceScore: i / 100, // higher idx = better
  }));
  const donorPads = Array.from({ length: 12 }, (_, i) => ({
    padIdx: 100 + i, source: "donor", loopScore: (11 - i) / 100, // lower idx = better
  }));
  const src = initialPads.concat(donorPads);
  const { placements, dividerRow } = arrangeBorrowLayout(src, 4, 4);

  assert.equal(placements.length, 16, "compact fills the 16 grid");
  assert.equal(dividerRow, -1, "no divider at 16 (no room)");

  const initPlaced = placements.filter((pl) => pl.source === "initial");
  const donorPlaced = placements.filter((pl) => pl.source === "donor");
  // BOTH songs survive the shrink — the donor is no longer dropped.
  assert.equal(initPlaced.length, 8, "8 best initial kept");
  assert.equal(donorPlaced.length, 8, "8 best donor kept");

  // 8 initial land in idx 0..7, 8 donor in idx 8..15.
  assert.deepEqual(
    initPlaced.map((pl) => pl.padIdx).sort((a, b) => a - b),
    [0, 1, 2, 3, 4, 5, 6, 7], "initial in the top half (idx 0..7)");
  assert.deepEqual(
    donorPlaced.map((pl) => pl.padIdx).sort((a, b) => a - b),
    [8, 9, 10, 11, 12, 13, 14, 15], "donor in the bottom half (idx 8..15)");

  // best-by-score selection: highest-scored 8 initial (source padIdx 4..11)
  // and highest-scored 8 donor (source padIdx 100..107) survive.
  assert.deepEqual(
    initPlaced.map((pl) => pl.pad.padIdx).sort((a, b) => a - b),
    [4, 5, 6, 7, 8, 9, 10, 11], "the 8 highest-score initial pads selected");
  assert.deepEqual(
    donorPlaced.map((pl) => pl.pad.padIdx).sort((a, b) => a - b),
    [100, 101, 102, 103, 104, 105, 106, 107], "the 8 highest-score donor pads selected");

  // within-block SECTION order preserved (grid idx ascends with source padIdx).
  const initByGrid = initPlaced.slice().sort((a, b) => a.padIdx - b.padIdx);
  assert.deepEqual(
    initByGrid.map((pl) => pl.pad.padIdx),
    [4, 5, 6, 7, 8, 9, 10, 11], "initial keep section order within the block");
}
{
  // COMPACT underflow: one song short of its half → the other fills the rest by
  // score, and both stay present (grid never left emptier than needed).
  const src = [];
  for (let i = 0; i < 3; i++) src.push({ padIdx: i, source: "initial", loopScore: 0.5 });
  for (let i = 0; i < 20; i++) src.push({ padIdx: 100 + i, source: "donor", performanceScore: i / 100 });
  const { placements } = arrangeBorrowLayout(src, 4, 4);
  const initPlaced = placements.filter((pl) => pl.source === "initial");
  const donorPlaced = placements.filter((pl) => pl.source === "donor");
  assert.equal(placements.length, 16, "grid filled despite the short initial block");
  assert.equal(initPlaced.length, 3, "all 3 initial kept");
  assert.equal(donorPlaced.length, 13, "donor fills the 13 remaining slots");
  assert.deepEqual(
    initPlaced.map((pl) => pl.padIdx).sort((a, b) => a - b),
    [0, 1, 2], "initial occupy idx 0..2");
  assert.equal(Math.min(...donorPlaced.map((pl) => pl.padIdx)), 3, "donor starts right after initial");
}

// Live-capture arrangement (pure). collapseSections merges consecutive
// same-`type` sections into readable BLOCKS: the analyzer's fine-grained
// output (Doomsday = 23 sections, long intro/verse/chorus runs) becomes a
// handful of full-label segments, not 23 one-char slivers.
const { collapseSections, blockIndexAtTime, arrangementDiff, parseArrangement, serializeArrangement } =
  K._internals;

// The 23→N case: a realistic Doomsday-shaped section list (many consecutive
// same-type rows) collapses to 7 readable blocks with Title-case labels.
{
  const seq = [
    "intro", "intro", "intro",
    "verse", "verse", "verse", "verse",
    "chorus", "chorus",
    "verse", "verse", "verse", "verse",
    "chorus", "chorus",
    "bridge", "bridge", "bridge",
    "outro", "outro", "outro", "outro", "outro",
  ]; // 23 raw sections
  const raw = seq.map((type, i) => ({ type, start_time: i * 4, end_time: (i + 1) * 4 }));
  const blocks = collapseSections(raw);
  assert.equal(raw.length, 23);
  assert.deepEqual(
    blocks.map((b) => b.label),
    ["Intro", "Verse", "Chorus", "Verse", "Chorus", "Bridge", "Outro"]
  );
  assert.equal(blocks.length, 7); // 23 → 7 readable blocks
  // Each block spans firstStart..lastEnd of its merged run.
  assert.deepEqual(blocks[0], { type: "intro", label: "Intro", start: 0, end: 12 });
  assert.deepEqual(blocks[6], { type: "outro", label: "Outro", start: 72, end: 92 });
}

// start/end fallbacks, out-of-order input sorts, case-insensitive type merge.
assert.deepEqual(
  collapseSections([
    { type: "Chorus", start: 10, end: 20 },
    { type: "intro", start: 0, end: 5 },
    { type: "INTRO", start: 5, end: 10 }, // merges with intro (case-insensitive)
  ]),
  [
    { type: "intro", label: "Intro", start: 0, end: 10 },
    { type: "Chorus", label: "Chorus", start: 10, end: 20 },
  ]
);

// Empty / no-sections / undated → [] (caller hides the strip).
assert.deepEqual(collapseSections([]), []);
assert.deepEqual(collapseSections(null), []);
assert.deepEqual(collapseSections(undefined), []);
assert.deepEqual(collapseSections("nope"), []);
assert.deepEqual(
  collapseSections([
    { start_time: 0, end_time: 4 }, // no type → dropped
    { type: "verse", start_time: 5, end_time: 5 }, // end<=start → dropped
    { type: "verse", start_time: 9, end_time: "x" }, // non-finite → dropped
    { type: "", start_time: 0, end_time: 4 }, // blank type → dropped
  ]),
  []
);

// blockIndexAtTime: [start,end) per block, last block owns its end; outside/
// gap/non-finite → -1.
{
  const blocks = collapseSections([
    { type: "intro", start_time: 0, end_time: 10 },
    { type: "verse", start_time: 10, end_time: 30 },
    { type: "chorus", start_time: 30, end_time: 40 },
  ]);
  assert.equal(blockIndexAtTime(blocks, 0), 0);
  assert.equal(blockIndexAtTime(blocks, 9.9), 0);
  assert.equal(blockIndexAtTime(blocks, 10), 1); // boundary → next block
  assert.equal(blockIndexAtTime(blocks, 29.99), 1);
  assert.equal(blockIndexAtTime(blocks, 30), 2);
  assert.equal(blockIndexAtTime(blocks, 40), 2); // last block owns its end
  assert.equal(blockIndexAtTime(blocks, 40.1), -1); // past the song
  assert.equal(blockIndexAtTime(blocks, -1), -1); // before the first
  assert.equal(blockIndexAtTime(blocks, NaN), -1);
  assert.equal(blockIndexAtTime([], 5), -1);
}

// Replay set math: arm the newly-wanted pads, release the dropped ones.
assert.deepEqual(arrangementDiff([], [1, 2]), { toArm: [1, 2], toRelease: [] });
assert.deepEqual(arrangementDiff([1, 2, 3], [2, 3, 4]), { toArm: [4], toRelease: [1] });
assert.deepEqual(arrangementDiff([1, 2], [1, 2]), { toArm: [], toRelease: [] });
assert.deepEqual(arrangementDiff([1, 2], []), { toArm: [], toRelease: [1, 2] });
assert.deepEqual(arrangementDiff(null, null), { toArm: [], toRelease: [] });

// Persistence round-trip: {blockIndex → [padIdx…]}; garbage rows dropped;
// empty/all-invalid → null so the caller removes the key.
{
  const map = { 0: [3, 0, 5], 2: [1] };
  const json = serializeArrangement(map);
  // Serialized pads are sorted + de-duped.
  assert.deepEqual(parseArrangement(json), { 0: [0, 3, 5], 2: [1] });
  assert.deepEqual(parseArrangement(serializeArrangement({ 1: [4, 4, 4] })), { 1: [4] });
  assert.equal(serializeArrangement(null), null);
  assert.equal(serializeArrangement({}), null);
  assert.equal(serializeArrangement({ 0: [] }), null); // no pads anywhere
  assert.equal(parseArrangement(null), null);
  assert.equal(parseArrangement("not json"), null);
  assert.equal(parseArrangement("[1,2]"), null); // array, not a map
  assert.equal(parseArrangement("{}"), null);
  assert.deepEqual(
    parseArrangement(
      JSON.stringify({
        0: [1, 2],
        "-1": [3], // negative block dropped
        "1.5": [3], // non-integer block dropped
        x: [3], // non-numeric block dropped
        2: [1, -1, 2.5, "q", 3], // only valid pad indices survive
        3: "loud", // non-array dropped
        4: [], // empty dropped
      })
    ),
    { 0: [1, 2], 2: [1, 3] }
  );
  // Full capture round-trip: record into a block, persist, reload.
  const captured = {};
  [2, 0, 2, 5].forEach((padIdx) => {
    const cur = captured[1] || (captured[1] = []);
    if (cur.indexOf(padIdx) === -1) cur.push(padIdx);
  });
  assert.deepEqual(parseArrangement(serializeArrangement(captured)), { 1: [0, 2, 5] });
}

// applyPadRegion with nothing mounted → false, never a throw.
assert.equal(K.applyPadRegion(0, { startSec: 0, endSec: 1 }), false);

K.mount(null); // no #kit-root in the stub → silent no-op, must not throw
K.unmount(); // nothing mounted → must not throw

console.log("kit.test.mjs: all assertions passed");
