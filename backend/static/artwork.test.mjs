// artwork.test.mjs — DOM-free smoke test for artwork.js.
// Run: node artwork.test.mjs
// Evaluates the classic script with stubbed window/document, then
// exercises the pure helpers (query cleaning, URL upsize, cache
// round-trip) and a get() flow with stubbed fetch + localStorage.
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";

const src = readFileSync(new URL("./artwork.js", import.meta.url), "utf8");

// --- fake localStorage (Map-backed) ------------------------------------
function makeStore() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    _size: () => m.size,
    _dump: () => Object.fromEntries(m),
  };
}

// --- fetch stub: records calls, returns a scripted iTunes response -----
function makeFetch(responder) {
  const calls = [];
  const fetch = (url) => {
    calls.push(url);
    const body = responder(url);
    return Promise.resolve({
      ok: body !== null,
      json: () => Promise.resolve(body),
    });
  };
  fetch.calls = calls;
  return fetch;
}

const store = makeStore();
const window = { location: { origin: "https://jamn.app" }, localStorage: store };
// Minimal document — thumbEl isn't exercised here, but _ensureStyle guards
// on document.head, so leaving it null keeps the classic script inert.
const document = { head: null, createElement: () => ({ setAttribute() {}, appendChild() {} }) };

new Function("window", "document", "module", src)(window, document, undefined);

const A = window.JamnArtwork;
assert.equal(typeof A.get, "function", "get is exposed");
assert.equal(typeof A.thumbEl, "function", "thumbEl is exposed");

const { cleanQuery, upsizeUrl, cacheKey, readCache, writeCache } = A._internals;

// ------------------------------------------------- query cleaning rules
assert.equal(cleanQuery("M83 - Oblivion"), "M83 Oblivion");
assert.equal(
  cleanQuery("M83 - Oblivion (Official Music Video)"),
  "M83 Oblivion"
);
assert.equal(
  cleanQuery("Radiohead - Creep [HD] (Official Video)"),
  "Radiohead Creep"
);
assert.equal(cleanQuery("Some Song - Topic"), "Some Song");
assert.equal(
  cleanQuery("Artist - Track Official Audio"),
  "Artist Track"
);
assert.equal(
  cleanQuery("Nirvana - Come As You Are (Remastered 2011)"),
  "Nirvana Come As You Are"
);
// "(feat. …)" is signal, not noise — it must survive.
assert.equal(
  cleanQuery("M83 - Oblivion (feat. Susanne Sundfør)"),
  "M83 Oblivion (feat. Susanne Sundfør)"
);
// Spaced dash = artist/title separator (collapsed); in-word hyphen kept.
assert.equal(cleanQuery("Taylor Swift - Anti-Hero"), "Taylor Swift Anti-Hero");
// Uploaded filename: extension stripped.
assert.equal(cleanQuery("my_demo_take.mp3"), "my_demo_take");
// No usable name → empty string (get() turns this into a null/miss).
assert.equal(cleanQuery(""), "");
assert.equal(cleanQuery(null), "");
assert.equal(cleanQuery(undefined), "");
assert.equal(cleanQuery(42), "");

// ------------------------------------------------- URL upsize
assert.equal(
  upsizeUrl("https://is1-ssl.mzstatic.com/image/thumb/x/100x100bb.jpg"),
  "https://is1-ssl.mzstatic.com/image/thumb/x/300x300bb.jpg"
);
assert.equal(upsizeUrl(null), null);
assert.equal(upsizeUrl(""), null);
// URL without the dimension token is returned unchanged (never crashes).
assert.equal(upsizeUrl("https://x/art.jpg"), "https://x/art.jpg");

// ------------------------------------------------- cache round-trip
const c = makeStore();
assert.equal(cacheKey("abc"), "jamn:art:v2:abc");
assert.equal(readCache(c, "abc"), undefined, "unknown before write");
writeCache(c, "abc", "https://x/300x300bb.jpg");
assert.equal(readCache(c, "abc"), "https://x/300x300bb.jpg", "hit round-trips");
writeCache(c, "def", null);
assert.equal(readCache(c, "def"), null, "miss round-trips as null (not undefined)");
// Corrupt payload → treated as unknown, not a throw.
c.setItem("jamn:art:v2:bad", "{not json");
assert.equal(readCache(c, "bad"), undefined, "corrupt entry is unknown");
// No store / no id → safe no-ops.
assert.equal(readCache(null, "x"), undefined);
writeCache(null, "x", "y"); // must not throw

// ------------------------------------------------- get(): backend proxy URL
// get() now returns a same-origin /api/artwork proxy URL (no direct client
// fetch — blockers were killing the iTunes/mzstatic path). The <img> load /
// error path is the fallback; the backend does the iTunes lookup + caching.
await (async () => {
  const entry = { id: "song-1", name: "M83 - Oblivion (Official Music Video)" };
  const url = await A.get(entry);
  assert.ok(url && url.startsWith("/api/artwork?title="),
    "get() returns the same-origin proxy URL");
  // The cleaned query (noise stripped) rides in the title param.
  assert.ok(decodeURIComponent(url).includes("M83 Oblivion"),
    "proxy URL carries the cleaned term");
  assert.ok(!decodeURIComponent(url).toLowerCase().includes("official"),
    "noise phrases stripped from the term");
})();

// ------------------------------------------------- get(): name fallbacks
await (async () => {
  const byFilename = await A.get({ id: "s", filename: "Doomsday.mp3" });
  assert.ok(decodeURIComponent(byFilename).includes("Doomsday"),
    "filename used when name absent (extension stripped)");
  const byTitle = await A.get({ title: "ONUKA ZENIT" });
  assert.ok(byTitle.startsWith("/api/artwork?title="), "title used as last resort");
})();

// ------------------------------------------------- get(): no name → null
await (async () => {
  const r = await A.get({ id: "song-4" }); // no name/filename/title
  assert.equal(r, null, "no usable name → null");
})();

console.log("artwork.test.mjs: all assertions passed");
