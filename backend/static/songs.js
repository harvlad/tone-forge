// songs.js — the web "Songs" page.
//
// ONE full-screen searchable / filterable / sortable table that replaces
// the cramped Recent-Songs sidebar list AND the Band Room card-stack as
// the library destination. Port of the desktop SongsPageView + SongsModel
// (jam-desktop/.../Library/SongsPageView.swift, JamDesktopCore/Songs/): the
// rows are the UNION of finished analyses + in-flight jobs the backend
// already returns from GET /api/library/search, with the web's own live
// analysis queue (BandRoomQueue) overlaid so a just-submitted upload shows
// instantly and a completing job COLLAPSES into its history row. Band Room
// is now the "Processing" status filter + the Status column, not a
// separate destination.
//
// The collapse/merge/window math lives in pure functions (window.JamnSongs
// ._pure) so the cross-platform contract — scope=mine on every search, the
// live overlay, the virtualization window — is unit-tested (songs.test.mjs)
// with no DOM and no network. Per-song deep-open stays /api/history/{id}
// (the router's onOpen); this page only supersedes the LIST.

(function () {
  'use strict';

  // Fixed row height keeps the table windowed: we only ever build the
  // rows inside the scroll viewport (+ overscan), so a library of
  // hundreds of songs renders ~two dozen row elements, not all of them.
  var ROW_H = 56;
  var OVERSCAN = 6;          // rows above/below the viewport, both ways
  var DEBOUNCE_MS = 300;     // search debounce — desktop parity
  var POLL_MS = 4000;        // live refresh cadence while a job cooks
  var PAGE_LIMIT = 50;

  // Source tabs: My Library wired; the rest declared coming-soon so the
  // pluggable shell is visible before those sources land (desktop parity).
  var SOURCE_TABS = [
    { id: 'library', title: 'My Library', enabled: true },
    { id: 'crate', title: 'Vinyl Crate', enabled: false },
    { id: 'jamendo', title: 'Jamendo', enabled: false },
    { id: 'ccmixter', title: 'ccMixter', enabled: false },
  ];
  var SORTS = [
    { id: 'recent', label: 'Recent' },
    { id: 'title', label: 'Title' },
    { id: 'tempo', label: 'Tempo' },
    { id: 'key', label: 'Key' },
  ];

  // =====================================================================
  // Pure helpers (no DOM, no network) — the tested contract surface.
  // =====================================================================

  // A row's stable identity: the history id when the analysis has landed,
  // else the source ref (a job id while still cooking). This is what lets
  // a queued→running→done row keep its place and a done job COLLAPSE into
  // its history row (both converge on the same history id).
  function mergeKey(track) {
    return (track && (track.history_id || track.source_ref)) || '';
  }

  // A live queue item's merge key mirrors mergeKey: a completed item keys
  // on its produced history id (converging with the server's history row);
  // an active item keys on its job id (else its local id).
  function liveKey(item) {
    if (!item) return '';
    if (item.status === 'done' && item.historyId) return item.historyId;
    return item.jobId || item.id || '';
  }

  function isActiveStatus(s) { return s === 'queued' || s === 'running'; }

  // QueueItemStatus (+ 0..100 percent) -> [TrackStatus, [0,1] progress].
  function liveStatus(status, percent) {
    switch (status) {
      case 'queued': return ['queued', null];
      case 'running':
        return ['running', percent == null ? null : Math.min(1, Math.max(0, percent / 100))];
      case 'done': return ['done', 1];
      case 'error': return ['error', null];
      default: return [status || 'unknown', null];
    }
  }

  // True when any METADATA facet (not the lifecycle status) is set. A
  // live-only processing row has no genre/key/etc., so it must be hidden
  // while such a filter is active — the server filters it out too.
  function hasMetadataFilter(f) {
    if (!f) return false;
    return !!(f.genre || f.key || f.mood ||
      (f.tags && f.tags.length) || f.tempoMin != null || f.tempoMax != null);
  }

  function filtersEmpty(f) {
    if (!f) return true;
    return !hasMetadataFilter(f) && (f.status == null || f.status === '');
  }

  // Overlay the live analysis queue onto the server union — the same rules
  // as the desktop SongsModel.merged (kept identical on purpose, parity
  // rule 4):
  //   * an ACTIVE live item whose key matches a server row replaces that
  //     row's status/progress (fresher SSE) — the collapse point;
  //   * an ACTIVE live item with NO server row is prepended as a fresh
  //     processing row (instant upload feedback) — unless a metadata facet
  //     is active or the status filter excludes processing;
  //   * DONE/errored live items defer to the server row (full metadata).
  function mergeLive(server, live, filters) {
    server = server || [];
    live = live || [];
    filters = filters || {};

    var liveByKey = Object.create(null);
    for (var i = 0; i < live.length; i++) {
      if (isActiveStatus(live[i].status)) liveByKey[liveKey(live[i])] = live[i];
    }

    var serverKeys = Object.create(null);
    for (var s = 0; s < server.length; s++) serverKeys[mergeKey(server[s])] = true;

    var overlaid = server.map(function (t) {
      var it = liveByKey[mergeKey(t)];
      if (!it) return t;
      var st = liveStatus(it.status, it.percent);
      var copy = Object.assign({}, t);
      copy.status = st[0];
      copy.progress = st[1];
      return copy;
    });

    // A status filter that excludes processing (done/error) must not
    // resurrect live processing rows the server omitted.
    var statusAllowsProcessing =
      !filters.status || filters.status === '' || filters.status === 'processing';
    if (hasMetadataFilter(filters) || !statusAllowsProcessing) return overlaid;

    // Prepend brand-new processing rows the server hasn't surfaced yet,
    // newest-first (live is maintained newest-first).
    var synthetic = [];
    for (var j = 0; j < live.length; j++) {
      var item = live[j];
      if (!isActiveStatus(item.status)) continue;
      var key = liveKey(item);
      if (serverKeys[key]) continue;
      var st2 = liveStatus(item.status, item.percent);
      synthetic.push({
        source: 'library',
        source_ref: key,
        title: item.title || 'Processing…',
        artist: null, key: null, tempo_bpm: null, duration_s: null,
        genre: null, mood: null, tags: [],
        status: st2[0], progress: st2[1],
        history_id: item.historyId || null,
      });
    }
    return synthetic.concat(overlaid);
  }

  // How many analyses are still cooking, across BOTH the server union and
  // the local queue (deduped by merge key). Drives the "Processing (N)"
  // chip — independent of the current filter so the count doesn't vanish
  // when you filter to something else.
  function processingCount(server, live) {
    var keys = Object.create(null);
    var n = 0;
    function add(k) { if (k && !keys[k]) { keys[k] = true; n++; } }
    (server || []).forEach(function (t) { if (isActiveStatus(t.status)) add(mergeKey(t)); });
    (live || []).forEach(function (it) { if (isActiveStatus(it.status)) add(liveKey(it)); });
    return n;
  }

  // Progress as a [0,1] fraction regardless of whether the value arrived
  // as a fraction (server union: percent/100) or a 0–100 percent.
  function progressFraction(track) {
    var p = track && track.progress;
    if (p == null) return null;
    if (p > 1) return Math.min(1, p / 100);
    return Math.max(0, p);
  }

  function formatDuration(seconds) {
    if (seconds == null || !(seconds > 0)) return '—';
    var mins = Math.floor(seconds / 60);
    var secs = Math.floor(seconds % 60);
    return mins + ':' + (secs < 10 ? '0' : '') + secs;
  }

  // Build the /api/library/search query string. MUST send scope=mine: the
  // endpoint returns the FULL multi-user library unless scope=mine engages
  // the owner gate (the device/auth header identifies the caller but does
  // NOT itself scope) — omitting it leaks every user's songs the instant
  // the SHARED_LIBRARY testing flag is off. Sorting happens server-side
  // BEFORE opaque-cursor paging, so we only name the order + echo cursors.
  function buildSearchQuery(opts) {
    opts = opts || {};
    var f = opts.filters || {};
    var params = new URLSearchParams();
    params.set('source', opts.source || 'library');
    params.set('sort', opts.sort || 'recent');
    params.set('limit', String(opts.limit || PAGE_LIMIT));
    // Engage the owner gate — see the function comment. Never omit.
    params.set('scope', 'mine');

    function add(name, value) {
      if (value == null) return;
      var v = String(value).trim();
      if (v) params.set(name, v);
    }
    add('q', opts.query);
    add('genre', f.genre);
    add('key', f.key);
    add('mood', f.mood);
    add('status', f.status);
    if (f.tempoMin != null && !isNaN(f.tempoMin)) params.set('tempo_min', String(f.tempoMin));
    if (f.tempoMax != null && !isNaN(f.tempoMax)) params.set('tempo_max', String(f.tempoMax));
    if (f.tags && f.tags.length) params.set('tags', f.tags.join(','));
    add('cursor', opts.cursor);
    return params.toString();
  }

  // The virtualization window: which slice of `total` rows to build for a
  // given scroll offset, plus the top pad + the full content height that
  // sizes the scrollbar. Pure so the windowing is unit-tested.
  function computeWindow(scrollTop, viewportH, rowH, total, overscan) {
    if (!(rowH > 0) || total <= 0) {
      return { start: 0, end: 0, padTop: 0, totalHeight: 0 };
    }
    overscan = overscan || 0;
    var start = Math.floor(scrollTop / rowH) - overscan;
    if (start < 0) start = 0;
    var visible = Math.ceil(viewportH / rowH) + overscan * 2;
    var end = start + visible;
    if (end > total) end = total;
    if (start > end) start = end;
    return { start: start, end: end, padTop: start * rowH, totalHeight: total * rowH };
  }

  var _pure = {
    mergeKey: mergeKey,
    liveKey: liveKey,
    liveStatus: liveStatus,
    isActiveStatus: isActiveStatus,
    hasMetadataFilter: hasMetadataFilter,
    filtersEmpty: filtersEmpty,
    mergeLive: mergeLive,
    processingCount: processingCount,
    progressFraction: progressFraction,
    formatDuration: formatDuration,
    buildSearchQuery: buildSearchQuery,
    computeWindow: computeWindow,
  };

  // =====================================================================
  // View (DOM). Everything below only runs once mount() is called, so the
  // module loads cleanly in the DOM-free test harness.
  // =====================================================================

  function newState() {
    return {
      source: 'library',
      query: '',
      sort: 'recent',
      filters: { genre: null, key: null, mood: null, status: null, tags: [], tempoMin: null, tempoMax: null },
      serverTracks: [],
      facets: {},
      nextCursor: null,
      total: null,
      isLoading: false,
      isLoadingMore: false,
      error: null,
    };
  }

  var st = newState();
  var els = null;          // built-once DOM refs
  var mounted = false;
  var pollTimer = null;
  var debounceTimer = null;
  var liveProvider = function () { return []; };  // set by router (BandRoomQueue.snapshot)

  // Callbacks the router wires (kept out of this module so it stays
  // DOM/engine-agnostic and testable).
  var handlers = {
    onOpen: function () {},        // (historyId, title) -> deep-open via /api/history/{id}
    onDismiss: function () {},     // (track) -> drop the live queue card
  };

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function authHeaders() {
    try { return window.tfAccount ? window.tfAccount.deviceHeaders() : {}; }
    catch (_) { return {}; }
  }

  function liveItems() {
    try { return liveProvider() || []; } catch (_) { return []; }
  }

  function displayTracks() {
    return mergeLive(st.serverTracks, liveItems(), st.filters);
  }

  // ---- fetching -------------------------------------------------------

  function fetchPage(cursor) {
    var qs = buildSearchQuery({
      source: st.source, query: st.query.trim(), filters: st.filters,
      sort: st.sort, cursor: cursor, limit: PAGE_LIMIT,
    });
    return fetch('/api/library/search?' + qs, { headers: authHeaders(), cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      });
  }

  function reload() {
    st.isLoading = true;
    st.error = null;
    renderChrome();
    return fetchPage(null)
      .then(function (page) {
        st.serverTracks = (page && page.tracks) || [];
        st.facets = (page && page.facets) || {};
        st.nextCursor = (page && page.next_cursor) || null;
        st.total = (page && typeof page.total === 'number') ? page.total : st.serverTracks.length;
      })
      .catch(function (e) { st.error = 'Could not load songs (' + (e && e.message) + ').'; })
      .then(function () { st.isLoading = false; renderAll(); });
  }

  function loadMore() {
    if (!st.nextCursor || st.isLoading || st.isLoadingMore) return;
    st.isLoadingMore = true;
    renderChrome();
    fetchPage(st.nextCursor)
      .then(function (page) {
        // Dedupe by merge key: a background ingest could surface a row
        // that's already on-screen. The opaque cursor keeps the window
        // stable, but belt-and-suspenders against overlap.
        var known = Object.create(null);
        st.serverTracks.forEach(function (t) { known[mergeKey(t)] = true; });
        ((page && page.tracks) || []).forEach(function (t) {
          if (!known[mergeKey(t)]) st.serverTracks.push(t);
        });
        st.nextCursor = (page && page.next_cursor) || null;
        if (page && typeof page.total === 'number') st.total = page.total;
      })
      .catch(function (e) { st.error = 'Could not load more (' + (e && e.message) + ').'; })
      .then(function () { st.isLoadingMore = false; renderAll(); });
  }

  // ---- filter mutations (each is a fresh first page) ------------------

  function setSort(id) { st.sort = id; reload(); }
  function setStatusFilter(status) { st.filters.status = status || null; reload(); }
  function setTempoRange(min, max) { st.filters.tempoMin = min; st.filters.tempoMax = max; reload(); }
  function clearFilters() {
    st.filters = { genre: null, key: null, mood: null, status: null, tags: [], tempoMin: null, tempoMax: null };
    st.query = '';
    if (els) { els.search.value = ''; els.tempoMin.value = ''; els.tempoMax.value = ''; }
    reload();
  }
  function isFacetSelected(field, value) {
    if (field === 'tags') return st.filters.tags.indexOf(value) !== -1;
    return st.filters[field] === value;
  }
  function toggleFacet(field, value) {
    if (field === 'tags') {
      var idx = st.filters.tags.indexOf(value);
      if (idx === -1) st.filters.tags.push(value); else st.filters.tags.splice(idx, 1);
    } else {
      st.filters[field] = (st.filters[field] === value) ? null : value;
    }
    reload();
  }

  // ---- row actions ----------------------------------------------------

  function openTrack(track) {
    var id = track.history_id || (track.source_ref || '');
    if (!id) return;
    handlers.onOpen(id, track.title || '');
  }

  // Retry a failed row through the one ingest door (content-hash dedupe
  // reuses an already-analyzed track), then refresh.
  function retryTrack(track) {
    fetch('/api/library/ingest', {
      method: 'POST',
      headers: Object.assign({ 'Content-Type': 'application/json' }, authHeaders()),
      body: JSON.stringify({ source: track.source || 'library', source_ref: track.source_ref || '' }),
    }).catch(function () {}).then(function () { reload(); });
  }

  // Dismiss an ERROR row. Such a row is a FAILED engine JOB from the
  // server union (source_ref = job id, no history_id), not a live client
  // card — so dropping the local card alone let the next poll re-fetch it
  // and the row came back (the "Dismiss does nothing" bug). Delete the
  // backing job so it's gone, then reload. Also drop any matching live
  // card (a client-only error card) via the router handler.
  function dismissTrack(track) {
    try { handlers.onDismiss(track); } catch (_) {}
    var key = mergeKey(track);
    // Optimistic: remove the row immediately for instant feedback.
    st.serverTracks = st.serverTracks.filter(function (t) { return mergeKey(t) !== key; });
    renderAll();
    var jobId = (!track.history_id && track.source_ref) ? track.source_ref : '';
    if (jobId) {
      fetch('/api/jobs/' + encodeURIComponent(jobId), {
        method: 'DELETE', headers: authHeaders(),
      }).catch(function () {}).then(function () { reload(); });
    } else {
      reload();
    }
  }

  // Remove a finished song from the library: purge it server-side (stems,
  // R2 objects, graph) via DELETE /api/history/{id}, drop the row
  // optimistically, then reconcile with a reload. This is also how a user
  // clears pre-dedupe duplicate rows.
  function removeTrack(track) {
    var id = track.history_id || (track.source_ref || '');
    if (!id) return;
    var key = mergeKey(track);
    st.serverTracks = st.serverTracks.filter(function (t) { return mergeKey(t) !== key; });
    if (st.total != null && st.total > 0) st.total -= 1;
    renderAll();
    fetch('/api/history/' + encodeURIComponent(id), {
      method: 'DELETE', headers: authHeaders(),
    }).catch(function () {}).then(function () { reload(); });
  }

  // ---- rendering ------------------------------------------------------

  var COLUMNS = 'minmax(200px,2.4fr) minmax(90px,1.3fr) 54px 60px 60px minmax(80px,1fr) 116px 130px';

  function buildChrome(container) {
    container.textContent = '';
    var page = el('div', 'songs-page');

    // Top bar
    var top = el('div', 'songs-topbar');
    var titleRow = el('div', 'songs-title-row');
    var h1 = el('h1', 'songs-title', 'Songs');
    var total = el('span', 'songs-total');
    titleRow.appendChild(h1);
    titleRow.appendChild(total);
    var spacer = el('div', 'songs-spacer');
    titleRow.appendChild(spacer);
    // Sort menu
    var sortWrap = el('label', 'songs-sort');
    sortWrap.appendChild(el('span', 'songs-sort-label', 'Sort'));
    var sortSel = el('select', 'songs-sort-select');
    SORTS.forEach(function (o) {
      var opt = el('option', null, o.label); opt.value = o.id; sortSel.appendChild(opt);
    });
    sortSel.value = st.sort;
    sortSel.addEventListener('change', function () { setSort(sortSel.value); });
    sortWrap.appendChild(sortSel);
    titleRow.appendChild(sortWrap);
    top.appendChild(titleRow);

    var tabsRow = el('div', 'songs-tabs-row');
    var tabs = el('div', 'songs-tabs');
    SOURCE_TABS.forEach(function (tab) {
      var b = el('button', 'songs-tab' + (tab.id === st.source ? ' is-active' : ''));
      b.type = 'button';
      b.appendChild(el('span', null, tab.title));
      if (!tab.enabled) {
        b.appendChild(el('span', 'songs-tab-soon', 'Soon'));
        b.disabled = true;
        b.title = tab.title + ' — coming soon';
      }
      b.addEventListener('click', function () {
        if (!tab.enabled || tab.id === st.source) return;
        st.source = tab.id; reload();
      });
      tabs.appendChild(b);
    });
    tabsRow.appendChild(tabs);
    var searchWrap = el('div', 'songs-search');
    searchWrap.appendChild(el('span', 'songs-search-icon', '🔍'));
    var search = el('input', 'songs-search-input');
    search.type = 'search';
    search.placeholder = 'Search songs…';
    search.value = st.query;
    search.setAttribute('aria-label', 'Search songs');
    search.addEventListener('input', function () {
      st.query = search.value;
      if (debounceTimer) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () { reload(); }, DEBOUNCE_MS);
    });
    searchWrap.appendChild(search);
    tabsRow.appendChild(searchWrap);
    top.appendChild(tabsRow);
    page.appendChild(top);

    // Body: rail + main
    var body = el('div', 'songs-body');
    var rail = el('aside', 'songs-rail');
    body.appendChild(rail);

    var main = el('div', 'songs-main');
    var head = el('div', 'songs-thead');
    head.style.gridTemplateColumns = COLUMNS;
    ['Title', 'Artist', 'Key', 'Tempo', 'Time', 'Genre', 'Status', ''].forEach(function (c) {
      head.appendChild(el('div', 'songs-th', c));
    });
    main.appendChild(head);

    var scroll = el('div', 'songs-scroll');
    var vport = el('div', 'songs-vport');
    scroll.appendChild(vport);
    scroll.addEventListener('scroll', onScroll);
    main.appendChild(scroll);

    var footer = el('div', 'songs-footer');
    main.appendChild(footer);

    var stateBox = el('div', 'songs-state');   // loading / empty overlay
    main.appendChild(stateBox);

    body.appendChild(main);
    page.appendChild(body);
    container.appendChild(page);

    els = {
      total: total, sortSel: sortSel, tabs: tabs, search: search,
      rail: rail, scroll: scroll, vport: vport, footer: footer,
      stateBox: stateBox, tempoMin: null, tempoMax: null,
    };
  }

  function renderRail() {
    var rail = els.rail;
    rail.textContent = '';

    // Status chips (Band Room is now this filter, not a destination).
    var statusSec = el('div', 'songs-facet');
    statusSec.appendChild(el('div', 'songs-facet-head', 'Status'));
    var pc = processingCount(st.serverTracks, liveItems());
    statusChip(statusSec, 'All', null, null);
    statusChip(statusSec, 'Processing', 'processing', pc || null);
    statusChip(statusSec, 'Done', 'done', facetCount('status', 'done'));
    statusChip(statusSec, 'Error', 'error', facetCount('status', 'error'));
    rail.appendChild(statusSec);

    // Tempo range
    var tempoSec = el('div', 'songs-facet');
    tempoSec.appendChild(el('div', 'songs-facet-head', 'Tempo (BPM)'));
    var tempoRow = el('div', 'songs-tempo');
    var lo = el('input', 'songs-tempo-input'); lo.type = 'number'; lo.placeholder = 'Min'; lo.min = '0';
    var hi = el('input', 'songs-tempo-input'); hi.type = 'number'; hi.placeholder = 'Max'; hi.min = '0';
    lo.value = st.filters.tempoMin == null ? '' : String(st.filters.tempoMin);
    hi.value = st.filters.tempoMax == null ? '' : String(st.filters.tempoMax);
    function commitTempo() {
      var a = parseFloat(lo.value); var b = parseFloat(hi.value);
      setTempoRange(isNaN(a) ? null : a, isNaN(b) ? null : b);
    }
    lo.addEventListener('change', commitTempo);
    hi.addEventListener('change', commitTempo);
    lo.addEventListener('keydown', function (e) { if (e.key === 'Enter') commitTempo(); });
    hi.addEventListener('keydown', function (e) { if (e.key === 'Enter') commitTempo(); });
    tempoRow.appendChild(lo);
    tempoRow.appendChild(el('span', 'songs-tempo-dash', '–'));
    tempoRow.appendChild(hi);
    tempoSec.appendChild(tempoRow);
    rail.appendChild(tempoSec);
    els.tempoMin = lo; els.tempoMax = hi;

    facetSection('Genre', 'genre');
    facetSection('Key', 'key');
    facetSection('Mood', 'mood');
    facetSection('Tags', 'tags');

    if (!filtersEmpty(st.filters) || st.query) {
      var clear = el('button', 'songs-clear', '✕ Clear filters');
      clear.type = 'button';
      clear.addEventListener('click', clearFilters);
      rail.appendChild(clear);
    }
  }

  function statusChip(parent, label, value, count) {
    var active = (st.filters.status || null) === value;
    var b = el('button', 'songs-chip' + (active ? ' is-active' : ''));
    b.type = 'button';
    b.appendChild(el('span', 'songs-chip-label', label));
    if (count != null) b.appendChild(el('span', 'songs-chip-count', String(count)));
    b.addEventListener('click', function () { setStatusFilter(value); });
    parent.appendChild(b);
  }

  function facetSection(title, field) {
    var buckets = st.facets[field] || [];
    if (!buckets.length) return;
    var sec = el('div', 'songs-facet');
    sec.appendChild(el('div', 'songs-facet-head', title));
    buckets.forEach(function (bucket) {
      var active = isFacetSelected(field, bucket.value);
      var b = el('button', 'songs-chip' + (active ? ' is-active' : ''));
      b.type = 'button';
      b.appendChild(el('span', 'songs-chip-label', bucket.label || bucket.value));
      b.appendChild(el('span', 'songs-chip-count', String(bucket.count)));
      b.addEventListener('click', function () { toggleFacet(field, bucket.value); });
      sec.appendChild(b);
    });
    els.rail.appendChild(sec);
  }

  function facetCount(field, value) {
    var buckets = st.facets[field] || [];
    for (var i = 0; i < buckets.length; i++) {
      if (buckets[i].value === value) return buckets[i].count;
    }
    return null;
  }

  var _lastRows = [];

  // Virtualized body render: size the viewport to the full row count, then
  // build ONLY the rows inside the scroll window (+ overscan). Called on
  // data change and on scroll.
  function renderRows() {
    var rows = displayTracks();
    _lastRows = rows;
    var scroll = els.scroll, vport = els.vport;

    var win = computeWindow(scroll.scrollTop, scroll.clientHeight || 0, ROW_H, rows.length, OVERSCAN);
    vport.style.height = win.totalHeight + 'px';
    vport.textContent = '';

    for (var i = win.start; i < win.end; i++) {
      vport.appendChild(buildRow(rows[i], i));
    }

    // Empty / loading overlay.
    els.stateBox.textContent = '';
    if (st.isLoading && !rows.length) {
      els.stateBox.className = 'songs-state is-shown';
      els.stateBox.appendChild(el('div', 'songs-state-msg', 'Loading songs…'));
    } else if (!rows.length) {
      els.stateBox.className = 'songs-state is-shown';
      var emptyTitle = filtersEmpty(st.filters) && !st.query ? 'No songs yet' : 'No matches';
      var emptySub = filtersEmpty(st.filters) && !st.query
        ? 'Analyze a song from Intake — it lands here.'
        : 'Try clearing a filter or search term.';
      els.stateBox.appendChild(el('div', 'songs-state-title', emptyTitle));
      els.stateBox.appendChild(el('div', 'songs-state-sub', emptySub));
    } else {
      els.stateBox.className = 'songs-state';
    }
  }

  function buildRow(track, index) {
    var row = el('div', 'songs-row');
    row.style.gridTemplateColumns = COLUMNS;
    row.style.top = (index * ROW_H) + 'px';
    row.style.height = ROW_H + 'px';

    // Title (+ artwork thumb)
    var titleCell = el('div', 'songs-cell songs-cell-title');
    try {
      if (window.JamnArtwork) {
        titleCell.appendChild(window.JamnArtwork.thumbEl({
          id: track.history_id || track.source_ref, name: track.title, artist: track.artist,
        }));
      }
    } catch (_) {}
    titleCell.appendChild(el('span', 'songs-title-text', track.title || 'Untitled'));
    row.appendChild(titleCell);

    row.appendChild(el('div', 'songs-cell songs-dim', track.artist || '—'));
    row.appendChild(el('div', 'songs-cell songs-dim', track.key || '—'));
    row.appendChild(el('div', 'songs-cell songs-dim songs-num',
      track.tempo_bpm != null ? String(Math.round(track.tempo_bpm)) : '—'));
    row.appendChild(el('div', 'songs-cell songs-dim songs-num', formatDuration(track.duration_s)));
    row.appendChild(el('div', 'songs-cell songs-dim',
      track.genre ? (track.genre.charAt(0).toUpperCase() + track.genre.slice(1)) : '—'));

    // Status badge
    var statusCell = el('div', 'songs-cell');
    statusCell.appendChild(statusBadge(track));
    row.appendChild(statusCell);

    // Action cell
    row.appendChild(actionCell(track));
    return row;
  }

  function statusBadge(track) {
    var s = track.status;
    var label, cls;
    if (s === 'running') {
      var pf = progressFraction(track);
      label = pf != null ? 'Running ' + Math.round(pf * 100) + '%' : 'Running';
      cls = 'running';
    } else if (s === 'queued') { label = 'Queued'; cls = 'queued'; }
    else if (s === 'error') { label = 'Error'; cls = 'error'; }
    else { label = 'Ready'; cls = 'done'; }
    var badge = el('span', 'songs-badge songs-badge--' + cls);
    badge.appendChild(el('span', 'songs-badge-dot'));
    badge.appendChild(el('span', null, label));
    return badge;
  }

  function actionCell(track) {
    var cell = el('div', 'songs-cell songs-cell-action');
    var s = track.status;
    if (s === 'running') {
      var pf = progressFraction(track);
      var wrap = el('span', 'songs-action-progress');
      wrap.appendChild(el('span', 'songs-spinner'));
      if (pf != null) wrap.appendChild(el('span', 'songs-pct', Math.round(pf * 100) + '%'));
      cell.appendChild(wrap);
    } else if (s === 'queued') {
      cell.appendChild(el('span', 'songs-dim', 'Queued'));
    } else if (s === 'error') {
      var retry = el('button', 'songs-btn songs-btn--sm', 'Retry');
      retry.type = 'button';
      retry.addEventListener('click', function () { retryTrack(track); });
      var dismiss = el('button', 'songs-btn songs-btn--ghost songs-btn--sm', 'Dismiss');
      dismiss.type = 'button';
      dismiss.addEventListener('click', function () { dismissTrack(track); });
      cell.appendChild(retry);
      cell.appendChild(dismiss);
    } else {
      var open = el('button', 'songs-btn songs-btn--primary songs-btn--sm', 'Open');
      open.type = 'button';
      open.disabled = !(track.history_id || track.source_ref);
      open.addEventListener('click', function () { openTrack(track); });
      cell.appendChild(open);
      // Remove from library — purges the analysis server-side. Also the
      // way to clear pre-dedupe duplicate rows.
      var remove = el('button', 'songs-btn songs-btn--ghost songs-btn--sm', 'Remove');
      remove.type = 'button';
      remove.title = 'Remove from Library';
      remove.disabled = !(track.history_id || track.source_ref);
      remove.addEventListener('click', function () { removeTrack(track); });
      cell.appendChild(remove);
    }
    return cell;
  }

  function renderChrome() {
    if (!els) return;
    // Total + sort + tab active states + footer.
    els.total.textContent = st.total != null ? String(st.total) : '';
    els.sortSel.value = st.sort;
    Array.prototype.forEach.call(els.tabs.children, function (b, i) {
      b.classList.toggle('is-active', SOURCE_TABS[i].id === st.source);
    });
    renderFooter();
  }

  function renderFooter() {
    var f = els.footer;
    f.textContent = '';
    if (st.isLoadingMore) {
      f.appendChild(el('span', 'songs-dim', 'Loading…'));
    } else if (st.nextCursor) {
      var more = el('button', 'songs-btn songs-btn--ghost', 'Load more');
      more.type = 'button';
      more.addEventListener('click', loadMore);
      f.appendChild(more);
    }
    if (st.error) {
      f.appendChild(el('span', 'songs-error', '⚠ ' + st.error));
    }
  }

  function renderAll() {
    if (!els) return;
    renderChrome();
    renderRail();
    renderRows();
  }

  var scrollRaf = 0;
  function onScroll() {
    // Load-more when near the bottom (opaque-cursor paging).
    var scroll = els.scroll;
    if (st.nextCursor && !st.isLoadingMore && !st.isLoading &&
        scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - ROW_H * 3) {
      loadMore();
    }
    if (scrollRaf) return;
    scrollRaf = requestAnimationFrame(function () { scrollRaf = 0; renderRows(); });
  }

  // ---- lifecycle ------------------------------------------------------

  function mount(container) {
    if (mounted) return;
    if (!container) container = document.getElementById('view-songs');
    if (!container) return;
    buildChrome(container);
    mounted = true;
    renderAll();
  }

  function startPoll() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(function () {
      var live = liveItems();
      var cooking = processingCount(st.serverTracks, live) > 0;
      // Repaint every tick so the live overlay's percent tracks the SSE
      // even between server reloads; re-fetch the union only while a job
      // is still cooking (a completing job then collapses into its row).
      if (mounted) renderRows();
      if (cooking) reload();
    }, POLL_MS);
  }

  function enter() {
    if (!mounted) mount();
    if (!st.serverTracks.length) reload(); else renderAll();
    startPoll();
  }

  function leave() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }

  var api = {
    mount: mount,
    enter: enter,
    leave: leave,
    refresh: function () { if (mounted) reload(); },
    setStatusFilter: function (s) { if (!mounted) mount(); setStatusFilter(s); },
    setLiveProvider: function (fn) { if (typeof fn === 'function') liveProvider = fn; },
    set onOpen(fn) { if (typeof fn === 'function') handlers.onOpen = fn; },
    set onDismiss(fn) { if (typeof fn === 'function') handlers.onDismiss = fn; },
    _pure: _pure,
  };

  if (typeof window !== 'undefined') window.JamnSongs = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})();
