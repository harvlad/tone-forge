// stage.test.mjs — pure-logic tests for stage.js (window.JamnStage._internals).
// Run in plain node (no browser, no deps):  node backend/static/stage.test.mjs
// stage.js is a classic script; we evaluate it in this realm (same-realm
// objects keep assert.deepEqual happy) with a minimal window stub —
// nothing else is required because the module touches the DOM only
// inside mount().

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

const src = readFileSync(new URL("./stage.js", import.meta.url), "utf8");
const windowStub = { devicePixelRatio: 1 };
const run = new Function(
  "window", "performance", "requestAnimationFrame", "cancelAnimationFrame", "localStorage",
  src
);
run(
  windowStub,
  { now: () => 0 },
  () => 0,
  () => {},
  { getItem: () => null, setItem: () => {} }
);
const I = windowStub.JamnStage._internals;

// ---------------------------------------------------------------------
// normalizeSymbol
// ---------------------------------------------------------------------
test("normalizeSymbol parses roots and qualities", () => {
  assert.deepEqual(I.normalizeSymbol("C"), { root: "C", rootPc: 0, quality: "maj" });
  assert.deepEqual(I.normalizeSymbol("Bm7"), { root: "B", rootPc: 11, quality: "m7" });
  assert.deepEqual(I.normalizeSymbol("F#m7"), { root: "F#", rootPc: 6, quality: "m7" });
  assert.deepEqual(I.normalizeSymbol("Am"), { root: "A", rootPc: 9, quality: "min" });
  assert.deepEqual(I.normalizeSymbol("Cmaj7"), { root: "C", rootPc: 0, quality: "maj7" });
  assert.deepEqual(I.normalizeSymbol("Eb"), { root: "Eb", rootPc: 3, quality: "maj" });
  assert.equal(I.normalizeSymbol("H"), null);
  assert.equal(I.normalizeSymbol(""), null);
  assert.equal(I.normalizeSymbol("Cweird"), null);
});

// ---------------------------------------------------------------------
// lookupShape: curated then algorithmic
// ---------------------------------------------------------------------
test("lookupShape returns curated open shapes", () => {
  const am = I.lookupShape("Am", null);
  assert.deepEqual(am.frets, [-1, 0, 2, 2, 1, 0]);
  const bm7 = I.lookupShape("Bm7", null);
  assert.deepEqual(bm7.frets, [-1, 2, 4, 2, 3, 2]);
  assert.deepEqual(bm7.barre, { fret: 2, from_string: 1, to_string: 5 });
});

test("lookupShape falls back to a movable barre for uncurated symbols", () => {
  // C#m has no embedded entry: A-shape at fret 4 beats E-shape at fret 9.
  const shape = I.lookupShape("C#m", null);
  assert.deepEqual(shape.frets, [-1, 4, 6, 6, 5, 4]);
  assert.deepEqual(shape.barre, { fret: 4, from_string: 1, to_string: 5 });
  assert.equal(shape.fingers, null);
});

test("lookupShape honours an explicit registry over the embedded table", () => {
  const registry = { shapes: { "A:min": { frets: [5, 7, 7, 5, 5, 5], fingers: null, barre: null } } };
  assert.deepEqual(I.lookupShape("Am", registry).frets, [5, 7, 7, 5, 5, 5]);
});

test("lookupShape returns null for unparseable symbols", () => {
  assert.equal(I.lookupShape("N.C.", null), null);
});

// ---------------------------------------------------------------------
// fingeringForShape
// ---------------------------------------------------------------------
test("fingeringForShape uses curated fingers and folds barre contacts into the bar", () => {
  const f = I.fingeringForShape(I.lookupShape("F", null)); // full barre F
  assert.deepEqual(f.barre, { fret: 1, lo: 0, hi: 5 });
  // Strings 0/4/5 are covered by the barre; remaining contacts get their fingers.
  assert.deepEqual(f.fingers, [
    { finger: 2, string: 3, fret: 2 },
    { finger: 3, string: 1, fret: 3 },
    { finger: 4, string: 2, fret: 3 },
  ]);
});

test("fingeringForShape heuristic infers an index barre on algorithmic shapes", () => {
  const f = I.fingeringForShape({ frets: [-1, 4, 6, 6, 5, 4], fingers: null, barre: null });
  assert.deepEqual(f.barre, { fret: 4, lo: 1, hi: 5 });
  assert.equal(f.fingers.length, 3);
  assert.deepEqual(f.fingers.map((c) => c.finger), [2, 3, 4]);
});

test("fingeringForShape keeps curated finger identities on open shapes", () => {
  // Em curated fingering is middle+ring ([0,2,3,0,0,0]), not 1..n.
  const f = I.fingeringForShape(I.lookupShape("Em", null)); // frets [0,2,2,0,0,0]
  assert.equal(f.barre, null);
  assert.deepEqual(f.fingers, [
    { finger: 2, string: 1, fret: 2 },
    { finger: 3, string: 2, fret: 2 },
  ]);
});

test("fingeringForShape heuristic assigns distinct fingers 1..n when no curated fingers", () => {
  const f = I.fingeringForShape({ frets: [0, 2, 2, 0, 0, 0], fingers: null, barre: null });
  assert.equal(f.barre, null); // 2 dots at min fret but nothing higher → no barre
  assert.deepEqual(f.fingers, [
    { finger: 1, string: 1, fret: 2 },
    { finger: 2, string: 2, fret: 2 },
  ]);
});

test("fingeringForShape handles empty/invalid shapes", () => {
  assert.deepEqual(I.fingeringForShape(null), { barre: null, fingers: [] });
  assert.deepEqual(I.fingeringForShape({ frets: [0, 0, 0, 0, 0, 0] }), { barre: null, fingers: [] });
});

// ---------------------------------------------------------------------
// activeIndexAt (time → chord/section lookup)
// ---------------------------------------------------------------------
test("activeIndexAt finds the last event started at or before t", () => {
  const evts = [
    { start_s: 0.0, end_s: 2.0, symbol: "Bm7" },
    { start_s: 2.0, end_s: 4.0, symbol: "F#m7" },
    { start_s: 4.0, end_s: 8.0, symbol: "D" },
  ];
  assert.equal(I.activeIndexAt(evts, -1), 0);   // before the first: clamps to 0
  assert.equal(I.activeIndexAt(evts, 0), 0);
  assert.equal(I.activeIndexAt(evts, 1.99), 0);
  assert.equal(I.activeIndexAt(evts, 2.0), 1);  // boundary belongs to the next chord
  assert.equal(I.activeIndexAt(evts, 3.5), 1);
  assert.equal(I.activeIndexAt(evts, 100), 2);  // after the last: clamps to last
  assert.equal(I.activeIndexAt([], 1), -1);
  assert.equal(I.activeIndexAt(null, 1), -1);
});

// ---------------------------------------------------------------------
// midiToFret
// ---------------------------------------------------------------------
test("midiToFret picks the lowest playable fret", () => {
  assert.deepEqual(I.midiToFret(40), { string: 0, fret: 0 }); // open low E
  assert.deepEqual(I.midiToFret(45), { string: 1, fret: 0 }); // open A beats E fret 5
  assert.deepEqual(I.midiToFret(47), { string: 1, fret: 2 });
  assert.deepEqual(I.midiToFret(64), { string: 5, fret: 0 }); // open high E
  assert.deepEqual(I.midiToFret(76), { string: 5, fret: 12 });
  assert.equal(I.midiToFret(39), null);                       // below range
  assert.equal(I.midiToFret(120), null);                      // above range
});

// ---------------------------------------------------------------------
// extractTimeline (bundle → stage data)
// ---------------------------------------------------------------------
const miniBundle = {
  audio: { duration_s: 100, source_title: "Song.mp3" },
  understanding: {
    tempo_bpm: 95.4,
    key: "C# minor",
    beats_s: [0.5, 1.0],
    chords: [{ start_s: 0, end_s: 3, symbol: "Bm7", confidence: 1 }],
    chords_beat_snapped: [
      { start_s: 0, end_s: 4, symbol: "Bm7", confidence: 1 },
      { start_s: 4, end_s: 8, symbol: "F#m7", confidence: 1 },
    ],
    sections: [
      { start_s: 0, end_s: 30, label: "chorus", confidence: 0.7 },
      { start_s: 30, end_s: 100, label: "verse", confidence: 0.7 },
    ],
  },
  user_midi: {
    role: "guitar",
    notes: [
      { pitch: 73, start: 0.7, end: 1.0, velocity: 82, role: "melody" },
      { pitch: 20, start: 1.2, end: 1.4, velocity: 60, role: "melody" }, // unmappable
    ],
  },
};

test("extractTimeline prefers beat-snapped chords and maps MIDI to tab positions", () => {
  const tl = I.extractTimeline(miniBundle, { duration: 90 });
  assert.equal(tl.chords.length, 2);
  assert.equal(tl.chords[1].symbol, "F#m7");
  assert.equal(tl.sections.length, 2);
  assert.equal(tl.duration, 100);           // audio.duration_s wins over entry
  assert.equal(tl.bpm, 95.4);
  assert.equal(tl.key, "C# minor");
  assert.equal(tl.tabNotes.length, 1);      // pitch 20 is unmappable and dropped
  assert.deepEqual(tl.tabNotes[0], { start: 0.7, end: 1.0, pitch: 73, string: 5, fret: 9 });
});

test("extractTimeline falls back to plain chords, then legacy key/bpm", () => {
  const b = {
    audio: { duration_s: 50 },
    understanding: { chords: [{ start_s: 0, end_s: 5, symbol: "C" }], sections: [] },
    legacy_tempo_bpm: 120,
    legacy_detected_key: "A minor",
  };
  const tl = I.extractTimeline(b, null);
  assert.equal(tl.chords[0].symbol, "C");
  assert.equal(tl.bpm, 120);
  assert.equal(tl.key, "A minor");
});

test("extractTimeline survives a null bundle (entry-only fallback)", () => {
  const tl = I.extractTimeline(null, { duration: 42 });
  assert.equal(tl.chords.length, 0);
  assert.equal(tl.duration, 42);
  assert.equal(tl.tabNotes.length, 0);
});

// ---------------------------------------------------------------------
// stemRows (mixer)
// ---------------------------------------------------------------------
test("stemRows orders main stems then extras with sanitized roles", () => {
  const rows = I.stemRows({
    stems: {
      drums: { audio_url: "u", display_name: "Drums" },
      bass: { audio_url: "u", display_name: "Bass" },
      vocals: { audio_url: "u", display_name: "Vocals" },
      other: { audio_url: "u", display_name: "Other" },
      guitar_left: null,
      guitar_right: null,
      extras: [{ id: "legacy.guitar", audio_url: "u", display_name: "Guitar" }],
    },
  });
  assert.deepEqual(rows.map((r) => r.role), ["drums", "bass", "vocals", "other", "guitar"]);
  assert.equal(rows[4].label, "Guitar");
});

test("stemRows skips stems without audio and handles a null bundle", () => {
  assert.deepEqual(I.stemRows(null), []);
  assert.deepEqual(
    I.stemRows({ stems: { drums: { audio_url: null }, extras: [] } }),
    []
  );
});

// ---------------------------------------------------------------------
// toneSummary
// ---------------------------------------------------------------------
test("toneSummary reads legacy_tone fallback naming", () => {
  const t = I.toneSummary({
    tone: { tier: null, chosen: null, alternates: [], rationale: null },
    legacy_tone: {
      tier: "low",
      rationale: "No confident match — pick by ear.",
      apply: { chain_id: "tfc.classic_rock", action: "connect.apply_chain" },
      match: null,
      fallback: { chain_id: "tfc.classic_rock", display_name: "Classic Rock" },
    },
  });
  assert.equal(t.name, "Classic Rock");
  assert.equal(t.chainId, "tfc.classic_rock");
  assert.equal(t.tier, "low");
  assert.match(t.rationale, /pick by ear/);
});

test("toneSummary returns null when there is nothing to show", () => {
  assert.equal(I.toneSummary(null), null);
  assert.equal(I.toneSummary({}), null);
});

// ---------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------
test("fmtTime formats mm:ss", () => {
  assert.equal(I.fmtTime(0), "0:00");
  assert.equal(I.fmtTime(65), "1:05");
  assert.equal(I.fmtTime(298.05), "4:58");
  assert.equal(I.fmtTime(-3), "0:00");
});

test("easeIO is a clamped smootherstep", () => {
  assert.equal(I.easeIO(-1), 0);
  assert.equal(I.easeIO(0), 0);
  assert.equal(I.easeIO(1), 1);
  assert.equal(I.easeIO(2), 1);
  assert.equal(I.easeIO(0.5), 0.5);
});

test("fret geometry is monotonic with the nut at 0", () => {
  assert.equal(I.wirePos(0), 0);
  assert.ok(I.wirePos(12) > I.wirePos(11));
  assert.ok(Math.abs(I.wirePos(12) - 0.5) < 1e-9); // octave = half the scale
  assert.ok(I.fingerPos(1) > 0 && I.fingerPos(1) < I.wirePos(1));
});

// ---------------------------------------------------------------------
// Board aspect / layout (skewed-neck fix): one px/unit for both axes.
// ---------------------------------------------------------------------
test("stringGapUnits tapers nut→saddle and clamps", () => {
  assert.ok(Math.abs(I.stringGapUnits(0) - (35 / 648) / 5) < 1e-12); // nut span / 5 gaps
  assert.ok(Math.abs(I.stringGapUnits(1) - (52 / 648) / 5) < 1e-12); // saddle span / 5 gaps
  assert.ok(I.stringGapUnits(0.5) > I.stringGapUnits(0));            // widens toward the body
  assert.equal(I.stringGapUnits(2), I.stringGapUnits(1));            // clamped at the saddle
  assert.equal(I.stringGapUnits(-1), I.stringGapUnits(0));           // clamped at the nut
});

test("boardAspect is the physical neck ratio — wide, not stretched", () => {
  const a9 = I.boardAspect(9);
  const a15 = I.boardAspect(15);
  assert.ok(Math.abs(a9 - 5.694) < 0.01, `9-fret aspect ${a9}`);
  assert.ok(Math.abs(a15 - 7.838) < 0.01, `15-fret aspect ${a15}`);
  assert.ok(a15 > a9); // more frets visible → wider board
});

test("boardGeom exposes span and tapered gap for a fret window", () => {
  const g9 = I.boardGeom(9);
  assert.equal(g9.loU, 0);
  assert.ok(Math.abs(g9.span - I.wirePos(9)) < 1e-12);
  // span/(6*gapU) is exactly the physical aspect for that many frets.
  assert.ok(Math.abs(g9.span / (6 * g9.gapU) - I.boardAspect(9)) < 1e-12);
});

test("computeBoardLayout uses ONE px/unit for both axes (no skew)", () => {
  // Width binds at the floor (9): board fills the width, small vertical slack.
  const lay = I.computeBoardLayout(1000, 320, 9);
  assert.equal(lay.maxFret, 9);                              // floor kept, width-bound
  // Rendered ratio == the pure physical ratio for the chosen fret count.
  assert.ok(Math.abs(lay.boardW / lay.boardH - I.boardAspect(lay.maxFret)) < 1e-9);
  // gap (vertical) and the horizontal scale share the single pxPerUnit.
  assert.ok(Math.abs(lay.gap - lay.gapU * lay.pxPerUnit) < 1e-12);
  // Wide container: the board fills to padL (=14), centered horizontally.
  assert.ok(Math.abs(lay.left - 14) < 1e-6);
  assert.ok(Math.abs(lay.boardW - 946.0) < 1.0, `boardW ${lay.boardW}`);
});

test("computeBoardLayout never shows fewer frets than the floor", () => {
  // Tall & narrow: even the floor's aspect is wider than the panel, so the
  // width binds and the board can't fill the height — but it must still show
  // all `floor` frets (fingerings stay on screen), never fewer.
  const lay = I.computeBoardLayout(700, 500, 6);
  assert.equal(lay.maxFret, 6);
  assert.ok(lay.boardW <= 700 - 40 - 14 + 1e-6);            // fits the width
  assert.ok(lay.boardH < 500 - 28 - 44);                    // leaves vertical slack
  assert.ok(Math.abs(lay.boardW / lay.boardH - I.boardAspect(6)) < 1e-9); // undistorted
});

test("computeBoardLayout centers the neck in leftover vertical budget", () => {
  const H = 500, top0 = 28, bottom = 44;
  const lay = I.computeBoardLayout(700, H, 6);              // width-bound, slack
  assert.ok(lay.top > top0, `top ${lay.top}`);             // pushed down to center
  // Matting above the slab == matting below it (symmetric, not a bottom void).
  const above = lay.top - top0;
  const below = (H - bottom) - lay.bot;
  assert.ok(Math.abs(above - below) < 1e-6, `above ${above} below ${below}`);
});

test("computeBoardLayout adds frets to fill a wide desktop panel", () => {
  // 1000×260 stage panel with an open-chord floor of 6: filling the height
  // pulls the visible window WIDER than the floor so the width fills too.
  const lay = I.computeBoardLayout(1000, 260, 6);
  assert.ok(lay.maxFret > 6, `maxFret ${lay.maxFret}`);
  assert.equal(lay.maxFret, 8);                            // best worst-axis fill
  assert.ok(Math.abs(lay.boardW / lay.boardH - I.boardAspect(8)) < 1e-9); // still true neck
  // Near-full fill on both axes (no big black band).
  const availW = 1000 - 40 - 14, vBudget = 260 - 28 - 44;
  assert.ok(lay.boardW / availW > 0.95, `wFill ${lay.boardW / availW}`);
  assert.ok(lay.boardH / vBudget > 0.9, `hFill ${lay.boardH / vBudget}`);
});

test("computeBoardLayout fills the height and centers a wide-short panel", () => {
  // Very wide, short: the height binds, the window opens to the cap (15) to
  // eat the width, and the board is centered (nut no longer pinned to padR).
  const W = 2000, H = 150, padR = 40;
  const lay = I.computeBoardLayout(W, H, 9);
  const vBudget = H - 28 - 44;
  assert.equal(lay.maxFret, 15);                            // widened to the cap
  assert.ok(Math.abs(lay.boardH - vBudget) < 1e-6);         // height fully filled
  assert.ok(lay.boardW <= W - padR - 14 + 1e-6);            // fits the width
  assert.ok(lay.right < W - padR - 1);                      // centered, not pinned right
  assert.ok(Math.abs(lay.boardW / lay.boardH - I.boardAspect(15)) < 1e-9); // undistorted
});
