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

// applyPadRegion with nothing mounted → false, never a throw.
assert.equal(K.applyPadRegion(0, { startSec: 0, endSec: 1 }), false);

K.mount(null); // no #kit-root in the stub → silent no-op, must not throw
K.unmount(); // nothing mounted → must not throw

console.log("kit.test.mjs: all assertions passed");
