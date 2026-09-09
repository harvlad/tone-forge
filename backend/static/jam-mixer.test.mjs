// jam-mixer.test.mjs — pure-logic tests for jam.js's solo resolver.
// Run in plain node (no browser, no deps):  node backend/static/jam-mixer.test.mjs
//
// jam.js is a single DOM-touching IIFE, so we can't evaluate the whole
// file the way stage.test.mjs does. Instead we slice `_resolveSoloMutes`
// out of the real source and evaluate that — the test still fails if
// someone edits the shipped function.

import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import test from "node:test";

const src = readFileSync(new URL("./jam.js", import.meta.url), "utf8");
const start = src.indexOf("function _resolveSoloMutes(");
assert.ok(start > 0, "_resolveSoloMutes not found in jam.js");
// The function body is a single top-level block; match to the closing
// brace at its own indentation (two spaces, module-level in the IIFE).
const end = src.indexOf("\n  }\n", start);
assert.ok(end > start, "could not delimit _resolveSoloMutes");
const resolveSoloMutes = new Function(
  `${src.slice(start, end + 4)}\nreturn _resolveSoloMutes;`,
)();

const STEMS = ["drums", "bass", "legacy.other"];
const plan = (over = {}) => resolveSoloMutes({
  stemNames: STEMS,
  soloedStems: new Set(),
  songSoloed: false,
  guitarSoloed: false,
  ...over,
});

test("no solo anywhere leaves every channel open", () => {
  const p = plan();
  assert.deepEqual([...p.stems.values()], [false, false, false]);
  assert.equal(p.song, false);
  assert.equal(p.guitar, false);
});

test("soloing a stem keeps the song master bus OPEN", () => {
  // Regression: every stem.gainNode feeds state.masterGain, which the
  // Song row mutes. Muting it on a stem solo silenced the soloed stem
  // too — the "can't hear stems" report.
  const p = plan({ soloedStems: new Set(["bass"]) });
  assert.equal(p.song, false, "stem solo must not mute the sum bus");
  assert.equal(p.stems.get("bass"), false);
  assert.equal(p.stems.get("drums"), true);
  assert.equal(p.stems.get("legacy.other"), true);
  assert.equal(p.guitar, true, "monitor is a real peer and does duck");
});

test("multiple stem solos stack", () => {
  const p = plan({ soloedStems: new Set(["bass", "drums"]) });
  assert.equal(p.song, false);
  assert.equal(p.stems.get("bass"), false);
  assert.equal(p.stems.get("drums"), false);
  assert.equal(p.stems.get("legacy.other"), true);
});

test("soloing the song mix keeps every stem audible", () => {
  const p = plan({ songSoloed: true });
  assert.deepEqual([...p.stems.values()], [false, false, false]);
  assert.equal(p.song, false);
  assert.equal(p.guitar, true);
});

test("a stem solo narrows a song solo", () => {
  const p = plan({ songSoloed: true, soloedStems: new Set(["drums"]) });
  assert.equal(p.song, false);
  assert.equal(p.stems.get("drums"), false);
  assert.equal(p.stems.get("bass"), true);
});

test("soloing the guitar input alone ducks the whole song bus", () => {
  const p = plan({ guitarSoloed: true });
  assert.equal(p.guitar, false);
  assert.equal(p.song, true, "no song-side solo — the master ducks");
  assert.deepEqual([...p.stems.values()], [true, true, true]);
});

test("guitar + stem solo: both are audible", () => {
  const p = plan({ guitarSoloed: true, soloedStems: new Set(["bass"]) });
  assert.equal(p.guitar, false);
  assert.equal(p.song, false, "the soloed stem still needs the bus");
  assert.equal(p.stems.get("bass"), false);
  assert.equal(p.stems.get("drums"), true);
});
