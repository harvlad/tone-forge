// songs.test.mjs — DOM-free tests for the web Songs page pure seams
// (songs.js). Run: node songs.test.mjs
//
// Pins the cross-platform contract edges that a UI bug can't hide:
//   * every /api/library/search MUST carry scope=mine (owner gate) plus a
//     server-side sort + opaque cursor (never an offset);
//   * the live-queue overlay collapses a completing job into its history
//     row and prepends brand-new processing rows — byte-for-byte the
//     desktop SongsModel.merged rules;
//   * the virtualization window only ever spans the viewport + overscan.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./songs.js", import.meta.url), "utf8");
const window = {};
new Function("window", "module", src)(window, undefined);

const S = window.JamnSongs;
assert.equal(typeof S.mount, "function");
assert.equal(typeof S.enter, "function");
const {
  mergeKey, liveKey, liveStatus, mergeLive, processingCount,
  progressFraction, formatDuration, buildSearchQuery, computeWindow,
  hasMetadataFilter, filtersEmpty,
} = S._pure;

// ---- buildSearchQuery: scope=mine is non-negotiable ----
{
  const qs = buildSearchQuery({ source: "library", query: "  wave ", sort: "tempo", limit: 50 });
  const p = new URLSearchParams(qs);
  assert.equal(p.get("scope"), "mine", "scope=mine MUST be present (owner gate)");
  assert.equal(p.get("source"), "library");
  assert.equal(p.get("sort"), "tempo");
  assert.equal(p.get("limit"), "50");
  assert.equal(p.get("q"), "wave", "query is trimmed");
  assert.equal(p.get("cursor"), null, "no cursor on first page");
}
// Filters map to the exact backend param names; empties are dropped.
{
  const qs = buildSearchQuery({
    filters: { genre: "rock", key: "", mood: null, status: "processing",
               tags: ["loud", "live"], tempoMin: 90, tempoMax: 140 },
    cursor: "opaque123",
  });
  const p = new URLSearchParams(qs);
  assert.equal(p.get("scope"), "mine");
  assert.equal(p.get("genre"), "rock");
  assert.equal(p.has("key"), false, "empty facet is omitted");
  assert.equal(p.has("mood"), false);
  assert.equal(p.get("status"), "processing");
  assert.equal(p.get("tags"), "loud,live", "tags comma-joined");
  assert.equal(p.get("tempo_min"), "90");
  assert.equal(p.get("tempo_max"), "140");
  assert.equal(p.get("cursor"), "opaque123", "opaque cursor echoed, never an offset");
}
// tempo_min=0 is a real bound, not a falsy drop.
{
  const p = new URLSearchParams(buildSearchQuery({ filters: { tempoMin: 0 } }));
  assert.equal(p.get("tempo_min"), "0");
}

// ---- merge keys + live keys ----
assert.equal(mergeKey({ history_id: "h1", source_ref: "job9" }), "h1", "history id wins");
assert.equal(mergeKey({ source_ref: "job9" }), "job9", "falls back to source ref");
assert.equal(liveKey({ status: "done", historyId: "h1", jobId: "job9" }), "h1", "done keys on history id");
assert.equal(liveKey({ status: "running", jobId: "job9" }), "job9", "active keys on job id");
assert.equal(liveKey({ status: "queued", id: "local5" }), "local5", "falls back to local id");

// ---- liveStatus normalization (0..100 -> [0,1]) ----
assert.deepEqual(liveStatus("queued", null), ["queued", null]);
assert.deepEqual(liveStatus("running", 40), ["running", 0.4]);
assert.deepEqual(liveStatus("running", 130), ["running", 1], "percent is clamped");
assert.deepEqual(liveStatus("done", null), ["done", 1]);
assert.deepEqual(liveStatus("error", 55), ["error", null]);

// ---- progressFraction accepts either convention ----
assert.equal(progressFraction({ progress: 0.5 }), 0.5);
assert.equal(progressFraction({ progress: 75 }), 0.75, "0..100 percent normalizes");
assert.equal(progressFraction({ progress: null }), null);

// ---- formatDuration ----
assert.equal(formatDuration(0), "—");
assert.equal(formatDuration(null), "—");
assert.equal(formatDuration(5), "0:05");
assert.equal(formatDuration(125), "2:05");

// ---- mergeLive: collapse an active job into its server row ----
{
  const server = [
    { history_id: "h1", source_ref: "h1", title: "Song One", status: "done", progress: 1 },
    { history_id: "h2", source_ref: "h2", title: "Song Two", status: "done", progress: 1 },
  ];
  // A running job whose id == a server row's source_ref overlays it fresh.
  const live = [{ id: "l", jobId: "h1", status: "running", percent: 60, title: "Song One" }];
  const out = mergeLive(server, live, {});
  assert.equal(out.length, 2, "no synthetic row — the job already has a server row");
  const h1 = out.find((t) => mergeKey(t) === "h1");
  assert.equal(h1.status, "running", "server row adopts the fresher live status");
  assert.equal(h1.progress, 0.6);
}
// A brand-new upload (no server row yet) is prepended as a processing row.
{
  const server = [{ history_id: "h1", source_ref: "h1", title: "Old", status: "done", progress: 1 }];
  const live = [{ id: "l", jobId: "job42", status: "queued", title: "Fresh Upload" }];
  const out = mergeLive(server, live, {});
  assert.equal(out.length, 2);
  assert.equal(mergeKey(out[0]), "job42", "synthetic processing row is prepended (newest first)");
  assert.equal(out[0].status, "queued");
  assert.equal(out[1].title, "Old");
}
// A metadata filter hides the metadata-less synthetic row (server would too).
{
  const server = [{ history_id: "h1", source_ref: "h1", title: "Old", genre: "rock", status: "done" }];
  const live = [{ id: "l", jobId: "job42", status: "running", percent: 10, title: "Fresh" }];
  const out = mergeLive(server, live, { genre: "rock" });
  assert.equal(out.length, 1, "no synthetic prepend while a metadata facet is active");
  assert.equal(mergeKey(out[0]), "h1");
}
// A done/error status filter must not resurrect processing rows.
{
  const server = [{ history_id: "h1", source_ref: "h1", title: "Done Song", status: "done" }];
  const live = [{ id: "l", jobId: "job42", status: "running", percent: 10, title: "Fresh" }];
  const out = mergeLive(server, live, { status: "done" });
  assert.equal(out.length, 1, "status=done excludes the live processing row");
}
// status=processing DOES surface it.
{
  const out = mergeLive([], [{ id: "l", jobId: "j", status: "running", percent: 5, title: "X" }], { status: "processing" });
  assert.equal(out.length, 1);
  assert.equal(out[0].status, "running");
}

// ---- processingCount is filter-independent + deduped ----
{
  const server = [
    { history_id: "h1", source_ref: "h1", status: "running" },
    { history_id: "h2", source_ref: "h2", status: "done" },
  ];
  const live = [
    { id: "a", jobId: "h1", status: "running" },      // same key as server row -> not double-counted
    { id: "b", jobId: "job9", status: "queued" },      // new
  ];
  assert.equal(processingCount(server, live), 2, "one running server row + one new queued job");
}

// ---- filter predicates ----
assert.equal(hasMetadataFilter({ genre: "rock" }), true);
assert.equal(hasMetadataFilter({ status: "done" }), false, "status is NOT a metadata filter");
assert.equal(hasMetadataFilter({ tempoMin: 0 }), true);
assert.equal(filtersEmpty({ tags: [] }), true);
assert.equal(filtersEmpty({ status: "processing" }), false);

// ---- computeWindow: only the viewport + overscan is ever built ----
{
  // 1000 rows, 56px each, 560px viewport (=10 rows), overscan 6.
  const w = computeWindow(0, 560, 56, 1000, 6);
  assert.equal(w.start, 0);
  assert.equal(w.totalHeight, 56000, "vport is sized to the full row count");
  assert.equal(w.padTop, 0);
  assert.ok(w.end <= 6 + 10 + 6 + 1, "window ~= viewport + 2*overscan");
  // Scrolled halfway.
  const w2 = computeWindow(56 * 100, 560, 56, 1000, 6);
  assert.equal(w2.start, 94, "start = floor(scrollTop/rowH) - overscan");
  assert.equal(w2.padTop, 94 * 56);
  assert.ok(w2.end - w2.start <= 6 + 10 + 6 + 1, "never renders the whole 1000-row library");
  // Clamp at the end.
  const w3 = computeWindow(56 * 995, 560, 56, 1000, 6);
  assert.equal(w3.end, 1000);
  // Empty.
  const w4 = computeWindow(0, 560, 56, 0, 6);
  assert.deepEqual(w4, { start: 0, end: 0, padTop: 0, totalHeight: 0 });
}

console.log("songs.test.mjs: all assertions passed");
