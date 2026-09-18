/* projects.js — Projects / per-song pad workspaces for the Jamn web app
 * (web port of iOS Projects v1, mobile-ios c887452d). Classic script:
 * defines
 *
 *   window.JamnProjects = { mount, unmount, noteChange, _pure }
 *
 * THE CONTRACT is the iOS ProjectSnapshot JSON (schemaVersion 1) —
 * mobile-ios/Sources/ToneForgeEngine/Projects/ProjectSnapshot.swift.
 * Web serializes the same Project/{snapshot} shape so a project written
 * here decodes on iOS and vice versa. Rules this module enforces:
 *
 *   * PRESERVE-DON'T-DROP: any snapshot field web can't render
 *     (.localSample / .sequence pad refs, padFX keyed "packId#padIdx",
 *     hiddenPads, sectionGates, chopEdits, non-"sample" pad modes) is
 *     carried through a load→re-save round trip byte-for-byte intact.
 *     sectionGates keeps its tri-state: absent (allow all) is never
 *     rewritten as [] (deny all).
 *   * Additive web-only state lives under one extra top-level snapshot
 *     key, `webExtras` — Swift's keyed decoding ignores unknown keys,
 *     so iOS readers are unaffected. It holds the web replay lineage:
 *     {surface, chopLoad, swaps, padFx (grid-keyed), loopOverrides,
 *     hiddenGridPads, seqSlotIds}.
 *   * Dates on the Project wrapper are APPLE REFERENCE SECONDS
 *     (seconds since 2001-01-01, Swift JSONEncoder's default Date
 *     encoding — iOS ProjectStore uses a plain JSONEncoder), NOT unix
 *     ms. See APPLE_EPOCH_OFFSET_S.
 *
 * COORDINATE MAPPING (padAssignments): web's pad grid is a 0-based
 * LINEAR index, row-major from the TOP-LEFT (kit.js padIdx), with no
 * mode axis. iOS keys are AppMode.rawValue → String(PadIndex) where
 * PadIndex = row*10 + col, row/col in 1..8 and row 1 = the BOTTOM row
 * (Launchpad Programmer-Mode addressing; "11" = bottom-left, "88" =
 * top-right). Web reads/writes ONLY the "sample" mode; translation for
 * a padCount-N grid (cols = 4 for 16, 8 for 64):
 *
 *   web idx → rowTop = floor(idx/cols), col0 = idx % cols
 *             PadIndex = (rows - rowTop)*10 + (col0 + 1)
 *   PadIndex → idx = (rows - row)*cols + (col - 1)
 *
 * A 16-pad grid occupies rows 1..4 × cols 1..4 (keys "11".."44").
 * padCount travels in snapshot.launchpad so the mapping is reversible.
 *
 * WHAT ROUND-TRIPS (web v1) vs WHAT'S LOSSY:
 *   ✓ pack-pad source swaps  — as real PadSlot {type:"packPad"} in
 *     padAssignments.sample (cross-surface) + url lineage in webExtras.
 *   ✓ chop loads             — replayed from {stem, sliceMode}.
 *   ✓ per-pad FX / loop overrides / hidden pads — grid-keyed in
 *     webExtras (web-only key space; iOS padFX/hiddenPads preserved).
 *   ✓ sequencer patterns     — exported as native SequencerPattern wire
 *     (velocity-only; probability/volume/pan/mute/solo default), with
 *     stable ids so restore/upsert is idempotent; imported via
 *     JamnSequencer.stageDefaultSequence.
 *   ✓ launchpad {padCount, sampleTriggerMode} ("one" ↔ "oneShot").
 *   ✓ borrows                — content-addressed BorrowRef (donor span
 *     + assetId, NEVER response padIdx); restore re-requests the borrow
 *     and re-matches; exact grid placement re-derives (lossy).
 *   ✗ .localSample / .sequence pad refs — no web store; preserved
 *     intact, pads render as the base kit's own (reported, not shown
 *     inert).
 *   ✗ song-pad copies — web-only lineage; content follows the fresh
 *     kit (drift semantics like borrows).
 *   ✗ chopEdits / sectionGates / iOS padFX — preserved, not applied.
 *
 * Store: localStorage (metadata + refs only — no audio), one blob:
 *   "jamn.projects.v1" = {v:1, projects:[Project…], working:{songId:
 *   Project}, seqIds:{songId:{A..D: patternId}}}
 * Working projects auto-save (debounced) per song and stay out of the
 * named list, mirroring iOS ProjectStore's working/ directory.
 */
(function () {
  'use strict';

  // ---------- constants ----------

  var STORE_KEY = 'jamn.projects.v1';
  var SCHEMA_VERSION = 1;
  /** Swift Date reference epoch (2001-01-01T00:00:00Z) in unix seconds. */
  var APPLE_EPOCH_OFFSET_S = 978307200;
  var AUTOSAVE_DEBOUNCE_MS = 1200;
  var SEQ_SLOT_IDS = ['A', 'B', 'C', 'D'];

  // ---------- pure helpers (exported via _pure, node-testable) ----------

  /** Web linear grid idx → iOS PadIndex key ("11".."88"). Null when the
   * idx falls outside the padCount grid. See the module header. */
  function gridToPadKey(idx, padCount) {
    var cols = padCount === 64 ? 8 : 4;
    var rows = padCount === 64 ? 8 : 4;
    idx = Number(idx);
    if (!Number.isInteger(idx) || idx < 0 || idx >= cols * rows) return null;
    var rowTop = Math.floor(idx / cols);
    var col0 = idx % cols;
    return String((rows - rowTop) * 10 + (col0 + 1));
  }

  /** iOS PadIndex key → web linear grid idx. Null when the key doesn't
   * land on the padCount grid (e.g. row 7 of a 16-pad kit). */
  function padKeyToGrid(key, padCount) {
    var cols = padCount === 64 ? 8 : 4;
    var rows = padCount === 64 ? 8 : 4;
    var n = parseInt(key, 10);
    if (!Number.isInteger(n)) return null;
    var row = Math.floor(n / 10);
    var col = n % 10;
    if (row < 1 || row > rows || col < 1 || col > cols) return null;
    return (rows - row) * cols + (col - 1);
  }

  /** Web trigger mode ("one"|"follow"|"latch") → iOS
   * SampleTriggerMode.rawValue ("oneShot"|"follow"|"latch"). */
  function triggerModeToIOS(mode) {
    if (mode === 'one') return 'oneShot';
    if (mode === 'latch') return 'latch';
    return 'follow';
  }

  /** iOS rawValue → web mode; unknown values map to the default
   * (Follow), matching the iOS reader contract. */
  function triggerModeFromIOS(raw) {
    if (raw === 'oneShot') return 'one';
    if (raw === 'latch') return 'latch';
    return 'follow';
  }

  function toAppleSeconds(epochMs) { return epochMs / 1000 - APPLE_EPOCH_OFFSET_S; }
  function fromAppleSeconds(s) { return (Number(s) + APPLE_EPOCH_OFFSET_S) * 1000; }

  /** RFC-4122 v4, uppercase like Swift's UUID().uuidString. */
  function uuid() {
    try {
      if (window.crypto && window.crypto.randomUUID) {
        return window.crypto.randomUUID().toUpperCase();
      }
    } catch (_) {}
    var s = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
    return s.toUpperCase();
  }

  /**
   * Web sequencer slot ({stepCount, swing, rows: {padIdx: [vel…]}}) →
   * native SequencerPattern wire. Null for an empty slot. Lossy edges:
   * probability/volume/pan/mute/solo take their defaults (web has no
   * equivalents), and chopRef packId is only cross-surface-resolvable
   * when the kit was a curated pack — song kits get a web-scoped
   * "web-song-kit:{songId}" id that iOS shows as unavailable (preserve-
   * don't-drop on its side).
   */
  function slotToNativePattern(slot, patternId, name, packId) {
    if (!slot || !slot.rows) return null;
    var stepCount = slot.stepCount === 32 ? 32 : 16;
    var tracks = [];
    Object.keys(slot.rows).sort(function (a, b) { return a - b; }).forEach(function (k) {
      var padIdx = parseInt(k, 10);
      var vels = slot.rows[k];
      if (!Number.isInteger(padIdx) || padIdx < 0 || !Array.isArray(vels)) return;
      var any = false;
      var steps = [];
      for (var i = 0; i < stepCount; i++) {
        var v = Number(vels[i]);
        v = isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
        if (v > 0) any = true;
        steps.push({ velocity: v, probability: 1 });
      }
      if (!any) return;
      tracks.push({
        id: uuid(),
        chopRef: { type: 'packPad', packId: packId, padIdx: padIdx },
        steps: steps,
        volume: 1, pan: 0, isMuted: false, isSoloed: false,
        name: null,
      });
    });
    if (!tracks.length) return null;
    var swing = Number(slot.swing);
    return {
      id: patternId,
      name: name,
      stepCount: stepCount,
      bpmOverride: null,
      tracks: tracks,
      swing: isFinite(swing) ? Math.max(0, Math.min(0.5, swing)) : 0,
      isLooping: true,
    };
  }

  /** BorrowRef ↔ fresh borrow-response pad match: assetId when both
   * sides carry one, else donor-timeline span ±2 ms + stemRole — the
   * exact BorrowRef.matches(pad:) rule. Never response padIdx. */
  function borrowRefMatchesPad(ref, pad) {
    if (!ref || !pad || pad.source !== 'donor') return false;
    if (ref.assetId && pad.assetId && ref.assetId === pad.assetId) return true;
    var a = pad.sourceLoopStartSec;
    var b = pad.sourceLoopEndSec;
    return typeof a === 'number' && typeof b === 'number' &&
      Math.abs(a - ref.loopStartSec) < 0.002 &&
      Math.abs(b - ref.loopEndSec) < 0.002 &&
      (pad.stemRole || '') === (ref.stemRole || '');
  }

  /**
   * Assemble the cross-surface ProjectSnapshot JSON from the kit
   * surface's captured workspace (`ws`, JamnKit.captureWorkspace) plus
   * the song's sequencer slots. `base` = the previously loaded/saved
   * snapshot, whose web-unrepresentable fields are PRESERVED.
   */
  function buildSnapshot(base, ws, seqSlots, seqIds) {
    base = base || {};
    var snap = {};
    snap.schemaVersion = SCHEMA_VERSION;

    // padAssignments: web owns the "sample" mode's packPad entries;
    // every other mode, and any non-packPad slot in "sample", is
    // preserved from `base` at keys web isn't writing.
    var assignments = {};
    var prevAssign = base.padAssignments || {};
    for (var mode in prevAssign) {
      if (mode === 'sample') continue;
      assignments[mode] = prevAssign[mode]; // untouched foreign modes
    }
    var sample = {};
    var prevSample = prevAssign.sample || {};
    for (var key in prevSample) {
      var slot = prevSample[key];
      var refType = slot && slot.ref && slot.ref.type;
      // Non-packPad refs (.localSample/.sequence) are native-only:
      // keep the slot bytes intact (round-trip contract).
      if (refType && refType !== 'packPad') sample[key] = slot;
    }
    var swaps = (ws && ws.swaps) || {};
    Object.keys(swaps).forEach(function (k) {
      var src = swaps[k];
      if (!src || src.kind !== 'packPad') return; // songPad = webExtras only
      var padKey = gridToPadKey(parseInt(k, 10), ws.padCount);
      if (!padKey) return;
      // P3-era minimal PadSlot ({ref} only) — transforms/timing decode
      // to defaults on iOS (decodeIfPresent).
      sample[padKey] = {
        ref: { type: 'packPad', packId: src.packId, padIdx: src.padIdx },
      };
    });
    if (Object.keys(sample).length) assignments.sample = sample;
    snap.padAssignments = assignments;

    // Native-keyed maps web can't apply: preserved verbatim.
    snap.padFX = base.padFX || {};
    snap.hiddenPads = (base.hiddenPads || []).slice().sort();
    // Tri-state: absent must STAY absent (allow all), [] must stay []
    // (deny all).
    if (base.sectionGates != null) snap.sectionGates = base.sectionGates;

    // Sequencer: web slots export under stable per-song ids (imported
    // patterns keep their ORIGINAL id via seqIds, so a load→save round
    // trip upserts instead of duplicating); foreign patterns preserved.
    var patterns = [];
    var webIds = {};
    var packRefId = ws && ws.surface && ws.surface.type === 'pack' && ws.surface.packId
      ? ws.surface.packId
      : 'web-song-kit:' + ((ws && ws.analysisId) || 'unknown');
    SEQ_SLOT_IDS.forEach(function (slotId) {
      var slot = seqSlots && seqSlots[slotId];
      var pid = (seqIds && seqIds[slotId]) || null;
      var pat = slot ? slotToNativePattern(
        slot, pid || uuid(), 'Web Slot ' + slotId, packRefId) : null;
      if (pat) {
        patterns.push(pat);
        webIds[pat.id] = true;
        if (seqIds) seqIds[slotId] = pat.id;
      }
    });
    (base.sequencerPatterns || []).forEach(function (p) {
      if (p && p.id && !webIds[p.id]) patterns.push(p);
    });
    snap.sequencerPatterns = patterns;

    if (base.chopEdits != null) snap.chopEdits = base.chopEdits;

    if (ws && ws.surface && ws.surface.type === 'song') {
      if (ws.arrangement) snap.arrangement = ws.arrangement;
    } else if (base.arrangement != null) {
      snap.arrangement = base.arrangement;
    }

    snap.launchpad = {
      padCount: ws && ws.padCount === 64 ? 64 : 16,
      sampleTriggerMode: triggerModeToIOS(ws && ws.triggerMode),
    };

    snap.borrows = (ws && ws.borrows) || [];

    // Additive web-only lineage (see module header).
    snap.webExtras = {
      surface: (ws && ws.surface) || { type: 'song', kind: 'auto' },
      chopLoad: (ws && ws.chopLoad) || null,
      swaps: swaps,
      padFx: (ws && ws.padFx) || {},
      loopOverrides: (ws && ws.loopOverrides) || {},
      hiddenGridPads: (ws && ws.hiddenPads) || [],
      seqSlotIds: seqIds || {},
    };

    // Preserve any unknown ADDITIVE fields a newer writer put on the
    // base snapshot (schema growth tolerance goes both ways).
    for (var extra in base) {
      if (!(extra in snap) && extra !== 'padAssignments' &&
          extra !== 'sectionGates' && extra !== 'chopEdits' &&
          extra !== 'arrangement') {
        snap[extra] = base[extra];
      }
    }
    return snap;
  }

  /**
   * The inverse: a kit-facing workspace from a snapshot. iOS-authored
   * packPad assignments (no web lineage) come back as swaps WITHOUT a
   * url — the loader resolves urls from the pack manifest before
   * applying. Returns {ws, unrepresentable} where `unrepresentable`
   * counts native-only pad refs web keeps but can't render.
   */
  function workspaceFromSnapshot(snap) {
    snap = snap || {};
    var extras = snap.webExtras || {};
    var launch = snap.launchpad || {};
    var padCount = launch.padCount === 64 ? 64 : 16;
    var ws = {
      surface: extras.surface || { type: 'song', kind: 'auto' },
      padCount: padCount,
      triggerMode: triggerModeFromIOS(launch.sampleTriggerMode),
      chopLoad: extras.chopLoad || null,
      swaps: {},
      padFx: extras.padFx || {},
      loopOverrides: extras.loopOverrides || {},
      hiddenPads: extras.hiddenGridPads || [],
      arrangement: snap.arrangement || null,
      borrows: snap.borrows || [],
    };
    var unrepresentable = 0;
    // iOS-authored sample-mode packPad slots first…
    var sample = (snap.padAssignments || {}).sample || {};
    for (var key in sample) {
      var slot = sample[key];
      var ref = slot && slot.ref;
      if (!ref) continue;
      if (ref.type !== 'packPad') { unrepresentable++; continue; }
      var idx = padKeyToGrid(key, padCount);
      if (idx == null) { unrepresentable++; continue; }
      ws.swaps[idx] = {
        kind: 'packPad', packId: ref.packId, padIdx: ref.padIdx,
        url: null, name: null, colorHint: null,
      };
    }
    // …then the web lineage (has urls/names) wins where both exist.
    var webSwaps = extras.swaps || {};
    Object.keys(webSwaps).forEach(function (k) {
      var idx = parseInt(k, 10);
      if (Number.isInteger(idx) && idx >= 0 && webSwaps[k]) ws.swaps[idx] = webSwaps[k];
    });
    return { ws: ws, unrepresentable: unrepresentable };
  }

  // ---------- store (localStorage; metadata + refs only) ----------

  function readStore() {
    var raw = null;
    try { raw = window.localStorage ? window.localStorage.getItem(STORE_KEY) : null; } catch (_) {}
    var obj = null;
    if (raw) { try { obj = JSON.parse(raw); } catch (_) { obj = null; } }
    if (!obj || typeof obj !== 'object') obj = {};
    return {
      v: 1,
      projects: Array.isArray(obj.projects) ? obj.projects : [],
      working: obj.working && typeof obj.working === 'object' ? obj.working : {},
      seqIds: obj.seqIds && typeof obj.seqIds === 'object' ? obj.seqIds : {},
    };
  }

  function writeStore(store) {
    try {
      if (window.localStorage) {
        window.localStorage.setItem(STORE_KEY, JSON.stringify(store));
      }
    } catch (e) {
      setStatus('Could not save projects: ' + (e && e.message ? e.message : e));
    }
  }

  // ---------- host context ----------

  var host = null;        // { onMountKit(desc), getEntry() } from jam.js
  var root = null;
  var listEl = null;
  var statusEl = null;
  var mounted = false;
  // The snapshot a subsequent auto-save merges its preserved fields
  // from: the loaded project's snapshot (per song).
  var baseSnapshots = {}; // songId → snapshot

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg || '';
  }

  // ---------- capture / auto-save ----------

  var noteTimer = 0;

  /** Debounced change signal from kit.js — capture the working project
   * shortly after the surface settles. */
  function noteChange() {
    clearTimeout(noteTimer);
    noteTimer = setTimeout(captureWorking, AUTOSAVE_DEBOUNCE_MS);
  }

  function readSeqSlots(songId) {
    var raw = null;
    try {
      raw = window.localStorage
        ? window.localStorage.getItem('jamn.seq.' + songId) : null;
    } catch (_) {}
    if (!raw) return null;
    var obj = null;
    try { obj = JSON.parse(raw); } catch (_) { return null; }
    return obj && obj.slots && typeof obj.slots === 'object' ? obj.slots : null;
  }

  /** Capture the current surface into the song's working project. */
  function captureWorking() {
    var K = window.JamnKit;
    if (!K || typeof K.captureWorkspace !== 'function') return null;
    var ws = null;
    try { ws = K.captureWorkspace(); } catch (_) {}
    if (!ws || !ws.analysisId) return null; // pack-only mounts: no song anchor (v1)
    var songId = ws.analysisId;
    var store = readStore();
    var seqIds = store.seqIds[songId] || {};
    var snap = buildSnapshot(
      baseSnapshots[songId] || (store.working[songId] && store.working[songId].snapshot),
      ws, readSeqSlots(songId), seqIds);
    store.seqIds[songId] = seqIds;
    var prev = store.working[songId];
    var nowS = toAppleSeconds(Date.now());
    var entry = host && typeof host.getEntry === 'function' ? host.getEntry() : null;
    var title = entry && entry.id === songId ? (entry.name || null)
      : (prev ? prev.baseSongTitle : null);
    store.working[songId] = {
      id: prev && prev.id ? prev.id : uuid(),
      name: prev && prev.name ? prev.name : 'Working project',
      createdAt: prev && prev.createdAt != null ? prev.createdAt : nowS,
      updatedAt: nowS,
      baseSongId: songId,
      baseSongTitle: title,
      snapshot: snap,
    };
    writeStore(store);
    return store.working[songId];
  }

  // ---------- load / restore ----------

  function preSeedStores(songId, snapRes) {
    var ws = snapRes.ws;
    try {
      if (window.localStorage) {
        window.localStorage.setItem('jamn.kit.pads', String(ws.padCount));
        window.localStorage.setItem('jamn.kit.mode', ws.triggerMode);
        // Per-pad FX ride the kit's own store so every rebake path
        // (applyStoredFx) re-applies them.
        var fx = ws.padFx || {};
        var anyFx = false;
        for (var k in fx) { if (fx[k]) { anyFx = true; break; } }
        if (anyFx) {
          window.localStorage.setItem('jamn.padfx.' + songId, JSON.stringify(fx));
        } else {
          window.localStorage.removeItem('jamn.padfx.' + songId);
        }
        if (ws.arrangement) {
          window.localStorage.setItem(
            'jamn.arrangement.' + songId, JSON.stringify(ws.arrangement));
        }
      }
    } catch (_) {}
  }

  /** Import snapshot sequencer patterns through the sanctioned web
   * importer (stageDefaultSequence). Restore = replace semantics: the
   * song's slot store is cleared first, then patterns fill slots in
   * order; their ORIGINAL ids are remembered so re-save upserts. */
  function importSequencerPatterns(songId, patterns, store) {
    var SQ = window.JamnSequencer;
    if (!SQ || typeof SQ.stageDefaultSequence !== 'function') return 0;
    if (!Array.isArray(patterns) || !patterns.length) return 0;
    try {
      if (window.localStorage) window.localStorage.removeItem('jamn.seq.' + songId);
    } catch (_) {}
    var seqIds = {};
    var imported = 0;
    patterns.forEach(function (pat) {
      if (imported >= SEQ_SLOT_IDS.length) return; // 4 slots on web
      var slot = null;
      try { slot = SQ.stageDefaultSequence(songId, pat); } catch (_) {}
      if (slot) {
        seqIds[slot] = pat.id || null;
        imported++;
      }
    });
    store.seqIds[songId] = seqIds;
    return imported;
  }

  /** Resolve pack-pad URLs for iOS-authored swaps (web lineage carries
   * its own url; slots imported from padAssignments don't). */
  function resolveSwapUrls(ws) {
    var byPack = {};
    Object.keys(ws.swaps).forEach(function (k) {
      var src = ws.swaps[k];
      if (src && src.kind === 'packPad' && !src.url && src.packId) {
        (byPack[src.packId] = byPack[src.packId] || []).push(src);
      }
    });
    var jobs = Object.keys(byPack).map(function (packId) {
      return fetch('/api/sample-packs/' + encodeURIComponent(packId))
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (manifest) {
          var pads = (manifest && manifest.pads) || [];
          byPack[packId].forEach(function (src) {
            for (var i = 0; i < pads.length; i++) {
              var p = pads[i];
              var idx = typeof p.padIdx === 'number' ? p.padIdx : i;
              if (idx !== src.padIdx) continue;
              var fname = p.sampleFile || p.file || p.sampleUrl || p.filename;
              if (!fname) break;
              src.url = /^https?:|^\//.test(fname) ? fname
                : '/api/sample-packs/' + encodeURIComponent(packId) +
                  '/pads/' + encodeURIComponent(fname);
              if (!src.name) src.name = p.name || null;
              if (!src.colorHint) src.colorHint = p.colorHint || null;
              break;
            }
          });
        })
        .catch(function () {});
    });
    return Promise.all(jobs);
  }

  /** Wait for a FRESH kit mount to be ready (new AudioContext + pads).
   * `prevCtx` is the context before mounting so a stale surface can't
   * satisfy the poll. */
  function waitKitReady(prevCtx, timeoutMs) {
    var deadline = Date.now() + (timeoutMs || 90000);
    return new Promise(function (resolve, reject) {
      (function poll() {
        var K = window.JamnKit;
        var ctx = K && K.audioContext ? K.audioContext() : null;
        var pads = K && K.pads ? K.pads() : [];
        var engine = K && K.engine ? K.engine() : null;
        if (ctx && ctx !== prevCtx && engine && pads.length) { resolve(); return; }
        if (Date.now() > deadline) { reject(new Error('kit mount timed out')); return; }
        setTimeout(poll, 300);
      })();
    });
  }

  function loadProject(project) {
    var snap = project.snapshot || {};
    var songId = project.baseSongId;
    if (!songId) {
      setStatus('Blank-canvas projects need iOS — web v1 loads song projects only.');
      return Promise.resolve(false);
    }
    var res = workspaceFromSnapshot(snap);
    var ws = res.ws;
    setStatus('Loading project…');

    var store = readStore();
    baseSnapshots[songId] = snap;
    preSeedStores(songId, res);
    var seqImported = importSequencerPatterns(
      songId, snap.sequencerPatterns, store);
    writeStore(store);

    var K = window.JamnKit;
    var prevCtx = K && K.audioContext ? K.audioContext() : null;

    var mountP;
    var borrowMissing = 0;
    if (ws.surface && ws.surface.type === 'borrow' && ws.surface.donor) {
      // Re-derive the borrow like iOS: same host+donor+stem request,
      // pads matched content-addressed (span/assetId), NEVER by the
      // response's padIdx. Placement re-derives from the arranger.
      mountP = fetch('/api/song/' + encodeURIComponent(songId) +
          '/borrow?donor=' + encodeURIComponent(ws.surface.donor) +
          '&stem=' + encodeURIComponent(ws.surface.stem || 'drums'))
        .then(function (r) {
          if (!r.ok) throw new Error('borrow HTTP ' + r.status);
          return r.json();
        })
        .then(function (manifest) {
          var pads = (manifest && manifest.pads) || [];
          (ws.borrows || []).forEach(function (ref) {
            var hit = false;
            for (var i = 0; i < pads.length; i++) {
              if (borrowRefMatchesPad(ref, pads[i])) { hit = true; break; }
            }
            if (!hit) borrowMissing++;
          });
          if (!K || typeof K.mountManifest !== 'function') {
            throw new Error('kit surface unavailable');
          }
          K.mountManifest(manifest);
        });
    } else {
      mountP = new Promise(function (resolve, reject) {
        if (!host || typeof host.onMountKit !== 'function') {
          reject(new Error('no kit host'));
          return;
        }
        host.onMountKit({
          entryId: songId,
          kind: (ws.surface && ws.surface.kind) || 'auto',
          name: project.baseSongTitle || project.name || 'Song',
        });
        resolve();
      });
    }

    return mountP
      .then(function () { return resolveSwapUrls(ws); })
      .then(function () { return waitKitReady(prevCtx); })
      .then(function () { return window.JamnKit.applyWorkspace(ws); })
      .then(function (report) {
        var bits = [];
        if (report) {
          if (report.chopLoaded) bits.push('chops');
          if (report.swapsApplied) bits.push(report.swapsApplied + ' swaps');
          if (report.fxApplied) bits.push(report.fxApplied + ' FX');
          if (report.hidden) bits.push(report.hidden + ' hidden');
          if (report.swapsFailed) bits.push(report.swapsFailed + ' swaps failed');
        }
        if (seqImported) bits.push(seqImported + ' patterns');
        if (borrowMissing) bits.push(borrowMissing + ' borrowed loops unavailable');
        if (res.unrepresentable) {
          bits.push(res.unrepresentable + ' pads need the iOS app');
        }
        setStatus('Project loaded' + (bits.length ? ' — ' + bits.join(', ') : '') + '.');
        renderList();
        return true;
      })
      .catch(function (err) {
        setStatus('Load failed: ' + ((err && err.message) || err));
        return false;
      });
  }

  // ---------- named-project CRUD ----------

  function saveCurrentAsProject() {
    var working = captureWorking();
    if (!working) {
      setStatus('Open a song’s pads first — there’s no workspace to save.');
      return;
    }
    var name = window.prompt('Project name:',
      (working.baseSongTitle || 'Project') + ' workspace');
    if (!name || !name.trim()) return;
    var store = readStore();
    var nowS = toAppleSeconds(Date.now());
    store.projects.unshift({
      id: uuid(),
      name: name.trim(),
      createdAt: nowS,
      updatedAt: nowS,
      baseSongId: working.baseSongId,
      baseSongTitle: working.baseSongTitle,
      snapshot: working.snapshot,
    });
    writeStore(store);
    setStatus('Saved.');
    renderList();
  }

  function duplicateProject(id) {
    var store = readStore();
    for (var i = 0; i < store.projects.length; i++) {
      var p = store.projects[i];
      if (p.id !== id) continue;
      var nowS = toAppleSeconds(Date.now());
      // Fresh identity + reset timestamps (Project.duplicated parity).
      store.projects.unshift({
        id: uuid(),
        name: p.name + ' copy',
        createdAt: nowS,
        updatedAt: nowS,
        baseSongId: p.baseSongId,
        baseSongTitle: p.baseSongTitle,
        snapshot: p.snapshot,
      });
      writeStore(store);
      renderList();
      return;
    }
  }

  function deleteProject(id) {
    var store = readStore();
    var next = store.projects.filter(function (p) { return p.id !== id; });
    if (next.length === store.projects.length) return;
    store.projects = next;
    writeStore(store);
    renderList();
  }

  function renameProject(id, name) {
    var store = readStore();
    for (var i = 0; i < store.projects.length; i++) {
      if (store.projects[i].id === id) {
        store.projects[i].name = name;
        store.projects[i].updatedAt = toAppleSeconds(Date.now());
        writeStore(store);
        return;
      }
    }
  }

  /** Reset the CURRENT song's workspace: forget the working project +
   * per-song stores and remount the clean base kit. */
  function resetWorkspace() {
    var entry = host && typeof host.getEntry === 'function' ? host.getEntry() : null;
    var songId = entry && entry.id;
    if (!songId) { setStatus('Load a song first.'); return; }
    if (!window.confirm('Reset this song’s workspace? Pad swaps, FX, hidden pads, patterns and the auto-saved working project are cleared.')) return;
    var store = readStore();
    delete store.working[songId];
    delete store.seqIds[songId];
    writeStore(store);
    delete baseSnapshots[songId];
    try {
      if (window.localStorage) {
        window.localStorage.removeItem('jamn.padfx.' + songId);
        window.localStorage.removeItem('jamn.arrangement.' + songId);
        window.localStorage.removeItem('jamn.seq.' + songId);
      }
    } catch (_) {}
    if (host && typeof host.onMountKit === 'function') {
      host.onMountKit({ entryId: songId, kind: 'auto', name: entry.name || 'Song' });
    }
    setStatus('Workspace reset.');
    renderList();
  }

  // ---------- UI (packs.js idiom: sections + rows, no innerHTML) ----------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function fmtWhen(appleSeconds) {
    var d = new Date(fromAppleSeconds(appleSeconds));
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
    });
  }

  function projectRow(p) {
    var row = el('div', 'jamn-proj-row');

    var meta = el('div', 'jamn-proj-meta');
    var nameInput = el('input', 'jamn-proj-name');
    nameInput.type = 'text';
    nameInput.value = p.name || '';
    nameInput.setAttribute('aria-label', 'Project name');
    nameInput.addEventListener('change', function () {
      var name = nameInput.value.trim();
      if (!name) { nameInput.value = p.name; return; }
      p.name = name;
      renameProject(p.id, name);
    });
    nameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') nameInput.blur();
    });
    meta.appendChild(nameInput);
    meta.appendChild(el('div', 'jamn-proj-sub',
      (p.baseSongTitle || p.baseSongId || 'Song') + ' · ' + fmtWhen(p.updatedAt)));
    row.appendChild(meta);

    var actions = el('div', 'jamn-proj-actions');
    var loadBtn = el('button', 'jamn-proj-btn jamn-proj-load', 'Load');
    loadBtn.type = 'button';
    loadBtn.title = 'Load this workspace (song + pads + patterns)';
    loadBtn.addEventListener('click', function () {
      loadBtn.disabled = true;
      loadProject(p).then(function () { loadBtn.disabled = false; });
    });
    actions.appendChild(loadBtn);

    var dupBtn = el('button', 'jamn-proj-btn', 'Duplicate');
    dupBtn.type = 'button';
    dupBtn.addEventListener('click', function () { duplicateProject(p.id); });
    actions.appendChild(dupBtn);

    var delBtn = el('button', 'jamn-proj-btn jamn-proj-delete', '✕');
    delBtn.type = 'button';
    delBtn.title = 'Delete project';
    delBtn.addEventListener('click', function () {
      if (window.confirm('Delete "' + (p.name || 'this project') + '"?')) {
        deleteProject(p.id);
      }
    });
    actions.appendChild(delBtn);

    row.appendChild(actions);
    return row;
  }

  function renderList() {
    if (!listEl) return;
    var store = readStore();
    listEl.textContent = '';
    if (!store.projects.length) {
      var empty = el('div', 'jamn-packs-note',
        'No saved projects yet. Jam on a song’s pads, then Save — swaps, FX, patterns and borrows come back with it.');
      listEl.appendChild(empty);
      return;
    }
    store.projects.forEach(function (p) {
      listEl.appendChild(projectRow(p));
    });
  }

  function mount(container, ctx) {
    if (mounted) unmount();
    root = container;
    host = ctx || {};

    var sec = el('section', 'jamn-packs-section jamn-projects');
    var head = el('div', 'jamn-proj-head');
    head.appendChild(el('h3', 'jamn-packs-heading', 'Projects'));

    var saveBtn = el('button', 'jamn-proj-btn jamn-proj-save', 'Save current workspace');
    saveBtn.type = 'button';
    saveBtn.title = 'Save the loaded song’s pad workspace as a named project';
    saveBtn.addEventListener('click', saveCurrentAsProject);
    head.appendChild(saveBtn);

    var resetBtn = el('button', 'jamn-proj-btn', 'Reset workspace');
    resetBtn.type = 'button';
    resetBtn.title = 'Clear the current song’s auto-saved workspace and reload its clean kit';
    resetBtn.addEventListener('click', resetWorkspace);
    head.appendChild(resetBtn);

    sec.appendChild(head);
    statusEl = el('div', 'jamn-proj-status');
    sec.appendChild(statusEl);
    listEl = el('div', 'jamn-proj-list');
    sec.appendChild(listEl);
    root.appendChild(sec);
    mounted = true;
    renderList();
  }

  function unmount() {
    if (!mounted) return;
    clearTimeout(noteTimer);
    noteTimer = 0;
    if (root) {
      var sec = root.querySelector('.jamn-projects');
      if (sec) sec.remove();
    }
    root = null;
    listEl = null;
    statusEl = null;
    host = null;
    mounted = false;
  }

  window.JamnProjects = {
    mount: mount,
    unmount: unmount,
    noteChange: noteChange,
    // Pure helpers for tests (projects.test.mjs evaluates this file in
    // a stub window and exercises these directly).
    _pure: {
      gridToPadKey: gridToPadKey,
      padKeyToGrid: padKeyToGrid,
      triggerModeToIOS: triggerModeToIOS,
      triggerModeFromIOS: triggerModeFromIOS,
      toAppleSeconds: toAppleSeconds,
      fromAppleSeconds: fromAppleSeconds,
      slotToNativePattern: slotToNativePattern,
      borrowRefMatchesPad: borrowRefMatchesPad,
      buildSnapshot: buildSnapshot,
      workspaceFromSnapshot: workspaceFromSnapshot,
    },
  };
})();
