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

// ------------------------------------------------- get(): fetch + cache
await (async () => {
  const s2 = makeStore();
  const art100 = "https://is1-ssl.mzstatic.com/image/thumb/z/100x100bb.jpg";
  const fetch = makeFetch((url) => {
    if (url.includes("itunes.apple.com")) {
      return { results: [{ artworkUrl100: art100, trackName: "Oblivion" }] };
    }
    return null;
  });
  window.localStorage = s2;
  window.fetch = fetch;

  const entry = { id: "song-1", name: "M83 - Oblivion (Official Music Video)" };
  const url = await A.get(entry);
  assert.equal(url, "https://is1-ssl.mzstatic.com/image/thumb/z/300x300bb.jpg",
    "get() returns the upsized art URL");
  assert.equal(fetch.calls.length, 1, "one network call");
  // The cleaned query rode into the request.
  assert.ok(decodeURIComponent(fetch.calls[0]).includes("M83 Oblivion"),
    "request carries the cleaned term");
  assert.ok(fetch.calls[0].includes("entity=song"), "entity=song param present");
  assert.ok(fetch.calls[0].includes("limit=1"), "limit=1 param present");

  // Second call for the same id is served from cache — no new fetch.
  const url2 = await A.get(entry);
  assert.equal(url2, url, "cached hit matches");
  assert.equal(fetch.calls.length, 1, "no refetch on cache hit");
})();

// ------------------------------------------------- get(): miss is cached
await (async () => {
  const s3 = makeStore();
  const fetch = makeFetch((url) =>
    url.includes("itunes.apple.com") ? { results: [] } : null);
  window.localStorage = s3;
  window.fetch = fetch;

  const entry = { id: "song-2", name: "Totally Unknown Bedroom Demo 7" };
  const r = await A.get(entry);
  assert.equal(r, null, "no results → null");
  assert.equal(fetch.calls.length, 1);
  const r2 = await A.get(entry);
  assert.equal(r2, null, "cached miss → null");
  assert.equal(fetch.calls.length, 1, "miss is cached, no refetch");
})();

// ------------------------------------------------- get(): absent artworkUrl100
await (async () => {
  const s4 = makeStore();
  const fetch = makeFetch((url) =>
    url.includes("itunes.apple.com") ? { results: [{ trackName: "X" }] } : null);
  window.localStorage = s4;
  window.fetch = fetch;
  const r = await A.get({ id: "song-3", name: "Song With No Art" });
  assert.equal(r, null, "missing artworkUrl100 field → null (no crash)");
})();

// ------------------------------------------------- get(): no name → null, no fetch
await (async () => {
  const fetch = makeFetch(() => ({ results: [{ artworkUrl100: "x/100x100bb.jpg" }] }));
  window.localStorage = makeStore();
  window.fetch = fetch;
  const r = await A.get({ id: "song-4" }); // no name/filename/title
  assert.equal(r, null, "no usable name → null");
  assert.equal(fetch.calls.length, 0, "no fetch when there's nothing to search");
})();

// ------------------------------------------------- get(): network error → null
await (async () => {
  window.localStorage = makeStore();
  window.fetch = () => Promise.reject(new Error("offline"));
  const r = await A.get({ id: "song-5", name: "Doomsday" });
  assert.equal(r, null, "network failure → null (caller shows fallback)");
})();

console.log("artwork.test.mjs: all assertions passed");
