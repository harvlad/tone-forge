/* artwork.js — best-effort album art for the web Jam surface.
 *
 * Web parity for the native cover-art path:
 *   jam-desktop Sources/JamDesktopCore/Artwork/RemoteArtworkFetcher.swift
 *               Sources/JamDesktopCore/Artwork/ArtworkStore.swift
 *   mobile-ios  RemoteArtworkFetcher / ArtworkStore
 *
 * Same idea, browser-shaped: query the iTunes Search API by a cleaned
 * song title, read results[0].artworkUrl100, upsize the thumbnail URL,
 * and cache the resolved URL (hits AND misses) so repeat loads never
 * refetch. The endpoint answers with `access-control-allow-origin: *`,
 * so a plain fetch from jamn.app works with no proxy.
 *
 * Public API (window.JamnArtwork):
 *   get(entry)     -> Promise<string|null>   resolved (upsized) art URL, or null
 *   thumbEl(entry) -> HTMLElement            ♪ placeholder that swaps to the art
 *
 * `entry` is a /api/history row (or any object carrying `.id` + `.name`).
 * The id keys the localStorage cache and the in-flight dedupe; the name
 * is cleaned into the search term. Misses are cached too — a song that
 * iTunes can't match should not hammer the API on every render.
 *
 * The caller owns the fallback: get() returns null on no-match / network
 * failure, and thumbEl() simply keeps its tinted ♪ tile.
 */
(function () {
  'use strict';

  var ITUNES = 'https://itunes.apple.com/search';
  // v2 namespace: the v1 cache permanently stored `null` misses, so any
  // song whose art was probed before the fetch reliably worked (or during
  // a transient failure) stayed blank forever. Bumping the prefix orphans
  // those entries and forces a clean re-probe.
  var CACHE_PREFIX = 'jamn:art:v2:';
  // Hits are cached forever (art URLs are stable); misses only for a day,
  // so a transient network/API hiccup never blanks a song permanently.
  var MISS_TTL_MS = 24 * 60 * 60 * 1000;

  // --------------------------------------------------------------- query
  // Strip the noise a human wouldn't type into a music search: YouTube
  // "- Topic" channels, "(Official Music Video)" / "[HD]" tags, bare
  // "official audio" phrases, and file extensions from uploads. What's
  // left is close to "artist title" or just "title", which is what the
  // iTunes term field matches best.
  function cleanQuery(name) {
    if (!name || typeof name !== 'string') return '';
    var q = name;
    // Drop a trailing audio-file extension (uploaded filenames).
    q = q.replace(/\.(mp3|wav|m4a|flac|aac|ogg|oga|aiff?|opus|wma)$/i, '');
    // Bracketed [...] chunks are almost always tags ("[HD]", "[4K]").
    q = q.replace(/\[[^\]]*\]/g, ' ');
    // Parentheticals that carry noise keywords ("(Official Video)",
    // "(Lyric Video)", "(Remastered 2011)"). "(feat. …)" survives —
    // it has no noise keyword and genuinely helps matching.
    q = q.replace(
      /\([^)]*\b(official|video|audio|lyrics?|lyric\s+video|visuali[sz]er|hd|hq|4k|1080p|720p|remaster(?:ed)?|explicit|clean|mv|m\/v|full\s+album|live|audio\s+only)\b[^)]*\)/gi,
      ' '
    );
    // YouTube auto-generated artist channel suffix.
    q = q.replace(/\s*[-–—]\s*topic\b/gi, ' ');
    // Bare (un-parenthesised) noise phrases.
    q = q.replace(
      /\bofficial\s+(?:music\s+)?(?:video|audio|lyric\s+video|visuali[sz]er)\b/gi,
      ' '
    );
    q = q.replace(
      /\b(?:official\s+video|official\s+audio|lyric\s+video|lyrics\s+video|music\s+video|audio\s+only|full\s+video)\b/gi,
      ' '
    );
    q = q.replace(/\b(?:hd|hq|4k|1080p|720p)\b/gi, ' ');
    // A *spaced* dash is the "Artist - Title" separator — collapse it to
    // a space (that's what a music search receives). The spaces are the
    // tell: hyphenated words like "Anti-Hero" have no surrounding space
    // and are left intact.
    q = q.replace(/\s+[-–—|]\s+/g, ' ');
    // Collapse whitespace, then shave stray leading/trailing separators
    // left behind by the removals ("Song - " → "Song").
    q = q.replace(/\s+/g, ' ').trim();
    q = q.replace(/^[\s\-–—|:·]+|[\s\-–—|:·]+$/g, '').trim();
    return q;
  }

  // The iTunes thumbnail URL embeds its dimensions ("…/100x100bb.jpg").
  // Swapping the token upsizes the served asset — no extra request to
  // discover the hi-res variant. Desktop jumps to 600; the web tiles
  // are small, so 300 is plenty and lighter.
  function upsizeUrl(url) {
    if (!url || typeof url !== 'string') return null;
    return url.replace(/100x100/g, '300x300');
  }

  // --------------------------------------------------------------- cache
  function cacheKey(id) { return CACHE_PREFIX + id; }

  // Returns: undefined (never fetched), null (cached miss), or a string
  // (cached hit). The three states let get() distinguish "unknown" from
  // "known to have no art", so a miss is honoured instead of retried.
  function readCache(store, id) {
    if (!store || !id) return undefined;
    var raw;
    try { raw = store.getItem(cacheKey(id)); } catch (_) { return undefined; }
    if (raw == null) return undefined;
    try {
      var obj = JSON.parse(raw);
      if (obj && typeof obj === 'object' && 'u' in obj) {
        // Hit (string) is permanent. Miss (null) expires after MISS_TTL_MS
        // so a transient failure self-heals on the next render past the TTL.
        if (typeof obj.u === 'string') return obj.u;
        var t = typeof obj.t === 'number' ? obj.t : 0;
        var now = (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
        if (now && t && (now - t) < MISS_TTL_MS) return null; // honour fresh miss
        return undefined; // stale/legacy miss — refetch
      }
    } catch (_) { /* corrupt entry — treat as unknown */ }
    return undefined;
  }

  function writeCache(store, id, url) {
    if (!store || !id) return;
    var now = (typeof Date !== 'undefined' && Date.now) ? Date.now() : 0;
    try { store.setItem(cacheKey(id), JSON.stringify({ u: url == null ? null : url, t: now })); }
    catch (_) { /* quota / disabled storage — degrade to no cache */ }
  }

  // --------------------------------------------------------------- fetch
  function fetchArtwork(query) {
    var f = (typeof window !== 'undefined' && window.fetch) || (typeof fetch !== 'undefined' ? fetch : null);
    if (!f || !query) return Promise.resolve(null);
    var url = ITUNES + '?term=' + encodeURIComponent(query) + '&entity=song&limit=1';
    return f(url)
      .then(function (r) { return (r && r.ok) ? r.json() : null; })
      .then(function (data) {
        var results = data && Array.isArray(data.results) ? data.results : null;
        var first = results && results.length ? results[0] : null;
        // artworkUrl100 is optional in the schema — guard its absence.
        var art = first && first.artworkUrl100;
        return (art && typeof art === 'string') ? upsizeUrl(art) : null;
      })
      .catch(function () { return null; });
  }

  // Same-origin backend proxy (tone_forge_api /api/artwork). We used to
  // fetch iTunes + mzstatic directly from the browser, but privacy/content
  // blockers routinely block apple.com/mzstatic, so every lookup silently
  // returned null. The proxy fetches server-side and streams the JPEG bytes
  // back from our own origin, so no blocker/CORS is in the path. It also
  // caches, so the client no longer needs its own network cache — get()
  // just hands back the proxy URL and the <img>/error path is the fallback.
  function get(entry) {
    // Mirror the on-screen label: name, then filename, then title.
    var label = entry && (entry.name || entry.filename || entry.title);
    var query = cleanQuery(label);
    if (!query) return Promise.resolve(null);
    return Promise.resolve(artworkProxyUrl(query));
  }

  function artworkProxyUrl(query) {
    return '/api/artwork?title=' + encodeURIComponent(query);
  }

  function _safeStore() {
    try { return window.localStorage || null; } catch (_) { return null; }
  }

  // ----------------------------------------------------------- thumbnail
  // Deterministic tint so each placeholder is stable per song and the
  // list doesn't look like one flat block of identical tiles.
  function _tint(seed) {
    var s = String(seed || '');
    var h = 0;
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return 'hsl(' + h + ', 42%, 32%)';
  }

  var _styleInjected = false;
  function _ensureStyle() {
    if (_styleInjected || typeof document === 'undefined' || !document.head) return;
    _styleInjected = true;
    var css =
      '.jamn-art{display:inline-flex;align-items:center;justify-content:center;' +
      'width:40px;height:40px;flex:0 0 40px;border-radius:8px;overflow:hidden;' +
      'font-size:18px;line-height:1;color:rgba(255,255,255,.7);' +
      'background:rgba(255,255,255,.08);user-select:none;}' +
      '.jamn-art--img{background:transparent;}' +
      '.jamn-art img{width:100%;height:100%;object-fit:cover;display:block;border-radius:8px;}';
    var el = document.createElement('style');
    el.setAttribute('data-jamn-artwork', '');
    el.textContent = css;
    document.head.appendChild(el);
  }

  function thumbEl(entry) {
    _ensureStyle();
    var el = document.createElement('span');
    el.className = 'jamn-art jamn-art--ph';
    el.textContent = '♪'; // ♪
    el.setAttribute('aria-hidden', 'true');
    el.style.background = _tint(entry && (entry.id || entry.name));

    get(entry).then(function (url) {
      if (!url) return; // keep the ♪ placeholder — caller's fallback
      var img = document.createElement('img');
      img.alt = '';
      img.decoding = 'async';
      img.loading = 'lazy';
      // Swap only once the bytes actually decode, so a 404/broken URL
      // leaves the placeholder intact rather than flashing a broken img.
      img.addEventListener('load', function () {
        el.textContent = '';
        el.style.background = '';
        el.classList.remove('jamn-art--ph');
        el.classList.add('jamn-art--img');
        el.appendChild(img);
      });
      img.addEventListener('error', function () { /* keep placeholder */ });
      img.src = url;
    }).catch(function () { /* keep placeholder */ });

    return el;
  }

  var api = {
    get: get,
    thumbEl: thumbEl,
    // Pure helpers exposed for the DOM-free test harness.
    _internals: {
      cleanQuery: cleanQuery,
      upsizeUrl: upsizeUrl,
      cacheKey: cacheKey,
      readCache: readCache,
      writeCache: writeCache,
      fetchArtwork: fetchArtwork,
    },
  };

  if (typeof window !== 'undefined') window.JamnArtwork = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
