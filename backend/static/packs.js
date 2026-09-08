// packs.js — Packs browser surface for the Jamn web app.
//
// Web parity for the native pack browsers:
//   mobile-ios  Sources/ToneForgeMobile/Views/Library/PacksBrowserView.swift
//   jam-desktop Sources/JamDesktop/Library/PacksBrowserView.swift
//
// Two sections, mirroring the native ordering ("proximity to the
// current musical moment" — see the iOS PacksBrowserView header):
//
//   1. Song Kits  — kits derived from analyzed songs. Backed by
//      GET /api/history (list) + GET /api/song/{id}/kit?kind=auto|drums
//      (the host's kit surface does the actual fetch/audio; we only
//      hand it a descriptor). The currently-loaded song (ctx.entry)
//      is pinned first.
//   2. Curated Packs — the server catalog at GET /api/sample-packs
//      (same endpoint the native PackClient.fetchCatalog reads).
//
// Host contract:
//
//   window.JamnPacks.mount(container, ctx)
//     container : Element the browser renders into (we own its children)
//     ctx.entry : full /api/history/{id} entry for the loaded song, or
//                 null — used only to pin/badge the current song.
//     ctx.onMountKit(kitDescriptor) : host callback. The HOST switches
//                 the Jam Pads surface to the chosen kit; this module
//                 never touches audio. Descriptor is a discriminated
//                 union on `kind`:
//                   { entryId, kind: 'auto',  name }        song performance kit
//                   { entryId, kind: 'drums', name }        song drum kit
//                   { packId,  kind: 'sample-pack', name,   curated pack —
//                     family, paletteHint, padCount }       manifest at
//                                                           /api/sample-packs/{packId}
//   window.JamnPacks.unmount()
//
// No dependencies; no globals beyond window.JamnPacks. Styling lives
// in packs.css on the shared --jamn-* tokens.
(function () {
  'use strict';

  // ------------------------------------------------------------------
  // Palette
  //
  // paletteHint values come from static/samples/catalog.json; family
  // fallbacks match the native familyColor() palette (jam-desktop
  // PacksBrowserView.swift:207) so a pack tints identically on every
  // surface.
  var HINT_COLORS = {
    purple: '#A855F7', amber: '#F59E0B', steel: '#64748B',
    crimson: '#DC2626', gold: '#EAB308', teal: '#14B8A6',
    magenta: '#D946EF', lime: '#84CC16', slate: '#94A3B8',
    violet: '#8B5CF6', indigo: '#6366F1', orange: '#F97316',
    green: '#22C55E', blue: '#3B82F6', pink: '#EC4899', red: '#EF4444'
  };
  var FAMILY_COLORS = {
    pads: '#A855F7', percussion: '#F97316', textures: '#14B8A6',
    stabs: '#EC4899', bass: '#3B82F6', fx: '#EAB308',
    vocals: '#22C55E', mixed: '#9CA3AF'
  };

  function packTint(pack) {
    var hint = (pack.paletteHint || '').toLowerCase();
    if (HINT_COLORS[hint]) return HINT_COLORS[hint];
    // A hint may already be a literal color ("#a855f7").
    if (/^#[0-9a-f]{3,8}$/i.test(hint)) return hint;
    return FAMILY_COLORS[pack.family] || FAMILY_COLORS.mixed;
  }

  // Song cards have no server-declared tint; derive a stable one from
  // the entry id so a song keeps its color across visits.
  var SONG_TINTS = ['#8B5CF6', '#3B82F6', '#14B8A6', '#F97316',
                    '#EC4899', '#EAB308', '#22C55E', '#6366F1'];
  function songTint(id) {
    var h = 0;
    for (var i = 0; i < id.length; i++) h = ((h * 31) + id.charCodeAt(i)) >>> 0;
    return SONG_TINTS[h % SONG_TINTS.length];
  }

  // ------------------------------------------------------------------
  // State
  var state = {
    container: null,
    ctx: null,
    aborter: null,
    selectedKey: null // 'pack:{packId}' | 'song:{entryId}:{kind}'
  };

  // ------------------------------------------------------------------
  // DOM helpers (no innerHTML for data-bearing nodes — names/artists
  // are user-supplied strings).
  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // ------------------------------------------------------------------
  // Selection → host hand-off
  function select(key, descriptor) {
    state.selectedKey = key;
    if (state.container) {
      var cards = state.container.querySelectorAll('.jamn-pack-card');
      for (var i = 0; i < cards.length; i++) {
        cards[i].classList.toggle(
          'jamn-pack-card--selected', cards[i].dataset.packKey === key);
      }
    }
    try {
      if (state.ctx && typeof state.ctx.onMountKit === 'function') {
        state.ctx.onMountKit(descriptor);
      }
    } catch (e) {
      // Host errors must not break the browser surface.
      console.warn('[jamn-packs] onMountKit failed:', e);
    }
  }

  // ------------------------------------------------------------------
  // Cards
  function coverBlock(tint, coverUrl, glyph) {
    var cover = el('div', 'jamn-pack-cover');
    cover.style.background =
      'linear-gradient(135deg, ' + tint + 'CC, ' + tint + '55)';
    if (coverUrl) {
      var img = el('img', 'jamn-pack-cover-img');
      img.src = coverUrl;
      img.alt = '';
      img.loading = 'lazy';
      // Broken/missing art degrades to the tint (native AsyncImage
      // fallback behavior).
      img.addEventListener('error', function () { img.remove(); });
      cover.appendChild(img);
    } else {
      cover.appendChild(el('div', 'jamn-pack-glyph', glyph));
    }
    return cover;
  }

  function cardShell(key, tint) {
    var card = el('button', 'jamn-pack-card');
    card.type = 'button';
    card.dataset.packKey = key;
    card.style.setProperty('--pack-tint', tint);
    if (key === state.selectedKey) card.classList.add('jamn-pack-card--selected');
    return card;
  }

  function curatedCard(pack) {
    var tint = packTint(pack);
    var key = 'pack:' + pack.packId;
    var card = cardShell(key, tint);
    if (pack.description) card.title = pack.description;

    card.appendChild(coverBlock(tint, pack.coverUrl, '▦'));

    var body = el('div', 'jamn-pack-body');
    body.appendChild(el('div', 'jamn-pack-name', pack.name || pack.packId));
    var familyLabel = pack.family
      ? pack.family.charAt(0).toUpperCase() + pack.family.slice(1) : 'Mixed';
    var padCount = pack.padCount || 0;
    body.appendChild(el('div', 'jamn-pack-meta',
      familyLabel + ' · ' + padCount + ' pad' + (padCount === 1 ? '' : 's')));
    card.appendChild(body);

    card.addEventListener('click', function () {
      select(key, {
        packId: pack.packId,
        kind: 'sample-pack',
        name: pack.name || pack.packId,
        family: pack.family || 'mixed',
        paletteHint: pack.paletteHint || null,
        padCount: padCount
      });
    });
    return card;
  }

  function songCard(row, isCurrent) {
    var tint = songTint(row.id);
    var key = 'song:' + row.id + ':auto';
    var card = cardShell(key, tint);

    var cover = coverBlock(tint, null, '♫');
    if (isCurrent) cover.appendChild(el('span', 'jamn-pack-badge', 'Now playing'));
    card.appendChild(cover);

    var body = el('div', 'jamn-pack-body');
    body.appendChild(el('div', 'jamn-pack-name', row.name || 'Untitled'));
    body.appendChild(el('div', 'jamn-pack-meta',
      row.artist ? row.artist : 'Performance kit'));
    card.appendChild(body);

    // Whole card = the mixed performance kit (kind=auto)…
    card.addEventListener('click', function () {
      select(key, { entryId: row.id, kind: 'auto', name: row.name || 'Untitled' });
    });

    // …with a secondary chip for the one-shot drum kit (kind=drums).
    var drumKey = 'song:' + row.id + ':drums';
    var drums = el('span', 'jamn-pack-chip', 'Drums');
    drums.setAttribute('role', 'button');
    drums.tabIndex = 0;
    drums.title = 'Load this song’s drum kit on the pads';
    if (drumKey === state.selectedKey) {
      card.classList.add('jamn-pack-card--selected');
    }
    function fireDrums(ev) {
      ev.stopPropagation(); // don't also fire the auto-kit click
      select(drumKey, { entryId: row.id, kind: 'drums', name: row.name || 'Untitled' });
    }
    drums.addEventListener('click', fireDrums);
    drums.addEventListener('keydown', function (ev) {
      if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); fireDrums(ev); }
    });
    body.appendChild(drums);
    return card;
  }

  // ------------------------------------------------------------------
  // Sections
  function section(title) {
    var sec = el('section', 'jamn-packs-section');
    sec.appendChild(el('h3', 'jamn-packs-heading', title));
    var grid = el('div', 'jamn-packs-grid');
    sec.appendChild(grid);
    return { root: sec, grid: grid };
  }

  function note(grid, text, isError) {
    clear(grid);
    grid.appendChild(el('div',
      'jamn-packs-note' + (isError ? ' jamn-packs-note--error' : ''), text));
  }

  function renderCurated(grid, packs) {
    clear(grid);
    if (!packs.length) {
      note(grid, 'No curated packs on this server yet.');
      return;
    }
    packs.forEach(function (p) { grid.appendChild(curatedCard(p)); });
  }

  function renderSongs(grid, rows) {
    clear(grid);
    if (!rows.length) {
      note(grid, 'Analyze a song and its kits show up here.');
      return;
    }
    var currentId = state.ctx && state.ctx.entry ? state.ctx.entry.id : null;
    // Pin the loaded song first — it's the kit the user most likely wants.
    rows = rows.slice().sort(function (a, b) {
      return (b.id === currentId) - (a.id === currentId);
    });
    rows.forEach(function (r) {
      grid.appendChild(songCard(r, r.id === currentId));
    });
  }

  // ------------------------------------------------------------------
  // Data
  function fetchJson(url, signal) {
    return fetch(url, { signal: signal }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  function load() {
    var signal = state.aborter.signal;
    var root = el('div', 'jamn-packs');

    var songs = section('Song Kits');
    var curated = section('Curated Packs');
    note(songs.grid, 'Loading…');
    note(curated.grid, 'Loading…');
    root.appendChild(songs.root);
    root.appendChild(curated.root);

    clear(state.container);
    state.container.appendChild(root);

    // Same endpoint the native PackClient reads; response is
    // { "packs": [...] } (tone_forge_api.py list_sample_packs).
    fetchJson('/api/sample-packs', signal)
      .then(function (data) {
        renderCurated(curated.grid, (data && data.packs) || []);
      })
      .catch(function (e) {
        if (e && e.name === 'AbortError') return;
        note(curated.grid, 'Couldn’t load the pack catalog.', true);
      });

    // Lightweight metadata rows: { history: [{id, name, artist?, …}] }.
    fetchJson('/api/history?limit=50', signal)
      .then(function (data) {
        renderSongs(songs.grid, (data && data.history) || []);
      })
      .catch(function (e) {
        if (e && e.name === 'AbortError') return;
        note(songs.grid, 'Couldn’t load your songs.', true);
      });
  }

  // ------------------------------------------------------------------
  // Public surface
  window.JamnPacks = {
    mount: function (container, ctx) {
      if (!container) return;
      // Re-mount replaces any prior instance (matches JamnKit's
      // one-instance model).
      window.JamnPacks.unmount();
      state.container = container;
      state.ctx = ctx || {};
      state.aborter = new AbortController();
      load();
    },
    unmount: function () {
      if (state.aborter) { state.aborter.abort(); state.aborter = null; }
      if (state.container) clear(state.container);
      state.container = null;
      state.ctx = null;
    }
  };
})();
