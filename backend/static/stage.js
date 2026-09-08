/**
 * stage.js — web Perform stage, mirroring the native desktop PerformView
 * (jam-desktop/Sources/JamDesktop/Perform/*).
 *
 * Contract (classic script, no modules):
 *
 *   window.JamnStage = {
 *     mount(container, ctx)   // build + start the stage inside `container`
 *     unmount()               // stop rAF, remove listeners, clear DOM
 *     setTransport(api)       // host audio transport (may be called any time)
 *     _internals              // pure logic, exported for stage.test.mjs
 *   }
 *
 *   ctx = {
 *     entry,                  // /api/history item {id, name, artist?, duration, ...}
 *     bundle,                 // /api/session/{id} object or null
 *     getTime?(),             // seconds — authoritative clock if provided
 *     onStemGain?(role, gain),    // 0..1
 *     onStemMute?(role, muted),   // bool
 *     onSolo?(role, soloed),      // bool
 *     onSongGain?(gain),          // Song master row + transport volume
 *     onApplyTone?(chainId),      // shows an Apply button on the tone banner
 *   }
 *
 *   transport api (all optional — missing pieces fall back to an
 *   internal clock): { play(), pause(), isPlaying(), getTime(),
 *   getDuration(), seek(s), setRate(r), setLoop(inS, outS),
 *   clearLoop(), setVolume(v), record(on) }
 *
 * Data driving each display layer (fields verified against the live
 * /api/session bundle — see the fingering notes in extractTimeline):
 *   Dots/Motion — understanding.chords_beat_snapped (fallback chords /
 *                 guidance.chord_lane) → chord symbol → voicing (curated
 *                 table, then movable-barre fallback) → fingered contacts.
 *                 The bundle serves NO per-note string/fret positions, so
 *                 dots are chord-shape derived, exactly like the desktop
 *                 HandNeckView (ChordDiagram.make + HandFingering).
 *   Chord       — same chord timeline, big current-symbol overlay.
 *   TAB         — user_midi.notes (pitch/start/end) via midiToFret.
 *   Hand        — DISABLED stub: the desktop hand needs the baked pose
 *                 library / IK solver; no pose data is served to the web.
 */
(function () {
  "use strict";

  // =====================================================================
  // Pure logic (exported through _internals; no DOM below this banner)
  // =====================================================================

  // String indexing everywhere: 0 = low E … 5 = high E.
  var STANDARD_TUNING = [40, 45, 50, 55, 59, 64]; // E2 A2 D3 G3 B3 E4

  var ROOT_PC = {
    C: 0, "C#": 1, Db: 1, D: 2, "D#": 3, Eb: 3,
    E: 4, F: 5, "F#": 6, Gb: 6, G: 7, "G#": 8,
    Ab: 8, A: 9, "A#": 10, Bb: 10, B: 11,
  };
  var PC_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

  // Order matters: longest suffix first (matches chord_diagrams.js).
  var QUALITY_SUFFIXES = [
    ["maj7", "maj7"], ["m7", "m7"], ["dim7", "dim"], ["dim", "dim"],
    ["aug", "aug"], ["sus2", "sus2"], ["sus4", "sus4"], ["maj", "maj"],
    ["min", "min"], ["m", "min"], ["5", "5"], ["7", "7"], ["", "maj"],
  ];

  // Movable barre templates (fret offsets from the barre fret, -1 = muted).
  var E_SHAPE = {
    maj: [0, 2, 2, 1, 0, 0], min: [0, 2, 2, 0, 0, 0], "5": [0, 2, 2, -1, -1, -1],
    "7": [0, 2, 0, 1, 0, 0], m7: [0, 2, 0, 0, 0, 0], maj7: [0, 2, 1, 1, 0, 0],
    sus2: [0, 2, 4, -1, 0, 0], sus4: [0, 2, 2, 2, 0, 0],
    dim: [0, 1, 2, 0, -1, -1], aug: [0, 3, 2, 1, 1, 0],
  };
  var A_SHAPE = {
    maj: [-1, 0, 2, 2, 2, 0], min: [-1, 0, 2, 2, 1, 0], "5": [-1, 0, 2, 2, -1, -1],
    "7": [-1, 0, 2, 0, 2, 0], m7: [-1, 0, 2, 0, 1, 0], maj7: [-1, 0, 2, 1, 2, 0],
    sus2: [-1, 0, 2, 2, 0, 0], sus4: [-1, 0, 2, 2, 3, 0],
    dim: [-1, 0, 1, 2, 1, -1], aug: [-1, 0, 3, 2, 2, 1],
  };

  // Fallback subset of /static/chord_shapes.json (fetched at mount to
  // extend this). Common open majors / minors / 7ths / m7 / maj7 —
  // enough that the Dots layer works offline and in tests.
  var EMBEDDED_SHAPES = {
    "C:maj": { frets: [-1, 3, 2, 0, 1, 0], fingers: [0, 3, 2, 0, 1, 0], barre: null },
    "D:maj": { frets: [-1, -1, 0, 2, 3, 2], fingers: [0, 0, 0, 1, 3, 2], barre: null },
    "E:maj": { frets: [0, 2, 2, 1, 0, 0], fingers: [0, 2, 3, 1, 0, 0], barre: null },
    "F:maj": { frets: [1, 3, 3, 2, 1, 1], fingers: [1, 3, 4, 2, 1, 1], barre: { fret: 1, from_string: 0, to_string: 5 } },
    "G:maj": { frets: [3, 2, 0, 0, 0, 3], fingers: [3, 2, 0, 0, 0, 4], barre: null },
    "A:maj": { frets: [-1, 0, 2, 2, 2, 0], fingers: [0, 0, 1, 2, 3, 0], barre: null },
    "B:maj": { frets: [-1, 2, 4, 4, 4, 2], fingers: [0, 1, 2, 3, 4, 1], barre: { fret: 2, from_string: 1, to_string: 5 } },
    "A:min": { frets: [-1, 0, 2, 2, 1, 0], fingers: [0, 0, 2, 3, 1, 0], barre: null },
    "D:min": { frets: [-1, -1, 0, 2, 3, 1], fingers: [0, 0, 0, 2, 3, 1], barre: null },
    "E:min": { frets: [0, 2, 2, 0, 0, 0], fingers: [0, 2, 3, 0, 0, 0], barre: null },
    "B:min": { frets: [-1, 2, 4, 4, 3, 2], fingers: [0, 1, 3, 4, 2, 1], barre: { fret: 2, from_string: 1, to_string: 5 } },
    "F#:min": { frets: [2, 4, 4, 2, 2, 2], fingers: [1, 3, 4, 1, 1, 1], barre: { fret: 2, from_string: 0, to_string: 5 } },
    "C:7": { frets: [-1, 3, 2, 3, 1, 0], fingers: [0, 3, 2, 4, 1, 0], barre: null },
    "D:7": { frets: [-1, -1, 0, 2, 1, 2], fingers: [0, 0, 0, 2, 1, 3], barre: null },
    "E:7": { frets: [0, 2, 0, 1, 0, 0], fingers: [0, 2, 0, 1, 0, 0], barre: null },
    "G:7": { frets: [3, 2, 0, 0, 0, 1], fingers: [3, 2, 0, 0, 0, 1], barre: null },
    "A:7": { frets: [-1, 0, 2, 0, 2, 0], fingers: [0, 0, 2, 0, 3, 0], barre: null },
    "B:7": { frets: [-1, 2, 1, 2, 0, 2], fingers: [0, 2, 1, 3, 0, 4], barre: null },
    "A:m7": { frets: [-1, 0, 2, 0, 1, 0], fingers: [0, 0, 2, 0, 1, 0], barre: null },
    "B:m7": { frets: [-1, 2, 4, 2, 3, 2], fingers: [0, 1, 3, 1, 2, 1], barre: { fret: 2, from_string: 1, to_string: 5 } },
    "D:m7": { frets: [-1, -1, 0, 2, 1, 1], fingers: [0, 0, 0, 2, 1, 1], barre: null },
    "E:m7": { frets: [0, 2, 0, 0, 0, 0], fingers: [0, 2, 0, 0, 0, 0], barre: null },
    "F#:m7": { frets: [2, 4, 2, 2, 2, 2], fingers: [1, 3, 1, 1, 1, 1], barre: { fret: 2, from_string: 0, to_string: 5 } },
    "C:maj7": { frets: [-1, 3, 2, 0, 0, 0], fingers: [0, 3, 2, 0, 0, 0], barre: null },
    "D:maj7": { frets: [-1, -1, 0, 2, 2, 2], fingers: [0, 0, 0, 1, 2, 3], barre: null },
    "F:maj7": { frets: [-1, -1, 3, 2, 1, 0], fingers: [0, 0, 3, 2, 1, 0], barre: null },
    "G:maj7": { frets: [3, 2, 0, 0, 0, 2], fingers: [3, 2, 0, 0, 0, 1], barre: null },
    "A:maj7": { frets: [-1, 0, 2, 1, 2, 0], fingers: [0, 0, 2, 1, 3, 0], barre: null },
  };

  /** "Bm7" → { root:"B", rootPc:11, quality:"m7" } or null. */
  function normalizeSymbol(symbol) {
    if (typeof symbol !== "string" || !symbol) return null;
    var root = null;
    if (symbol.length >= 2 && (symbol[1] === "#" || symbol[1] === "b")) {
      var two = symbol.slice(0, 2);
      if (two in ROOT_PC) root = two;
    }
    if (root === null && symbol[0] in ROOT_PC) root = symbol[0];
    if (root === null) return null;
    var suffix = symbol.slice(root.length);
    for (var i = 0; i < QUALITY_SUFFIXES.length; i++) {
      if (suffix === QUALITY_SUFFIXES[i][0]) {
        return { root: root, rootPc: ROOT_PC[root], quality: QUALITY_SUFFIXES[i][1] };
      }
    }
    return null;
  }

  function buildBarreShape(pattern, rootPc, rootStringIdx) {
    if (!pattern) return null;
    var openPc = STANDARD_TUNING[rootStringIdx] % 12;
    var barreFret = (rootPc - openPc + 12) % 12;
    var frets = pattern.map(function (f) { return f === -1 ? -1 : f + barreFret; });
    for (var i = 0; i < frets.length; i++) {
      if (frets[i] !== -1 && (frets[i] < 0 || frets[i] > 15)) return null;
    }
    return {
      frets: frets, fingers: null,
      barre: barreFret > 0
        ? { fret: barreFret, from_string: rootStringIdx, to_string: 5 }
        : null,
    };
  }

  /** Curated registry lookup → movable-barre fallback → null. */
  function lookupShape(symbol, registry) {
    var parsed = normalizeSymbol(symbol);
    if (parsed === null) return null;
    var shapes = registry && registry.shapes ? registry.shapes : EMBEDDED_SHAPES;
    var key = parsed.root + ":" + parsed.quality;
    if (key in shapes) return shapes[key];
    var altKey = PC_NAME[parsed.rootPc] + ":" + parsed.quality;
    if (altKey !== key && altKey in shapes) return shapes[altKey];
    var e = buildBarreShape(E_SHAPE[parsed.quality], parsed.rootPc, 0);
    var a = buildBarreShape(A_SHAPE[parsed.quality], parsed.rootPc, 1);
    var cands = [e, a].filter(function (s) { return s !== null; });
    if (!cands.length) return null;
    cands.sort(function (x, y) {
      var fx = x.frets.filter(function (f) { return f >= 0; });
      var fy = y.frets.filter(function (f) { return f >= 0; });
      var mx = Math.max.apply(null, fx), my = Math.max.apply(null, fy);
      if (mx !== my) return mx - my;
      return Math.min.apply(null, fx) - Math.min.apply(null, fy);
    });
    return cands[0];
  }

  /**
   * Shape → HandShape { barre:{fret,lo,hi}|null, fingers:[{finger,string,fret}] }.
   * Prefers curated `fingers`/`barre`; otherwise the desktop HandFingering
   * heuristic: >=2 dots on the lowest fret with dots above → index barre.
   */
  function fingeringForShape(shape) {
    if (!shape || !Array.isArray(shape.frets)) return { barre: null, fingers: [] };
    var dots = [];
    for (var s = 0; s < 6; s++) {
      if (shape.frets[s] > 0) dots.push({ string: s, fret: shape.frets[s] });
    }
    if (!dots.length) return { barre: null, fingers: [] };

    if (Array.isArray(shape.fingers)) {
      var barre = null;
      if (shape.barre) {
        barre = { fret: shape.barre.fret, lo: shape.barre.from_string, hi: shape.barre.to_string };
      }
      var fingers = [];
      dots.forEach(function (d) {
        var fi = shape.fingers[d.string] || 0;
        // The barre finger's contacts are covered by the bar itself.
        if (barre && fi === 1 && d.fret === barre.fret) return;
        if (fi > 0) fingers.push({ finger: Math.min(4, fi), string: d.string, fret: d.fret });
      });
      fingers.sort(function (a, b) {
        return a.fret !== b.fret ? a.fret - b.fret : a.string - b.string;
      });
      return { barre: barre, fingers: fingers };
    }

    // Heuristic (HandNeckView.HandFingering port).
    dots.sort(function (a, b) {
      return a.fret !== b.fret ? a.fret - b.fret : a.string - b.string;
    });
    var minFret = dots[0].fret;
    var atMin = dots.filter(function (d) { return d.fret === minFret; });
    var higher = dots.filter(function (d) { return d.fret > minFret; });
    var out = [], f;
    if (atMin.length >= 2 && higher.length) {
      var strs = atMin.map(function (d) { return d.string; });
      f = 2;
      higher.forEach(function (d) {
        out.push({ finger: Math.min(4, f), string: d.string, fret: d.fret }); f++;
      });
      return {
        barre: { fret: minFret, lo: Math.min.apply(null, strs), hi: Math.max.apply(null, strs) },
        fingers: out,
      };
    }
    f = 1;
    dots.forEach(function (d) {
      out.push({ finger: Math.min(4, f), string: d.string, fret: d.fret }); f++;
    });
    return { barre: null, fingers: out };
  }

  /** Binary search: index of the last event with start <= t (0 if none). */
  function activeIndexAt(events, t) {
    if (!events || !events.length) return -1;
    var lo = 0, hi = events.length - 1, cand = 0;
    while (lo <= hi) {
      var m = (lo + hi) >> 1;
      if (events[m].start_s <= t) { cand = m; lo = m + 1; } else { hi = m - 1; }
    }
    return cand;
  }

  /** MIDI pitch → lowest playable {string, fret} in standard tuning. */
  function midiToFret(pitch, tuning) {
    tuning = tuning || STANDARD_TUNING;
    var best = null;
    for (var s = 0; s < tuning.length; s++) {
      var fret = pitch - tuning[s];
      if (fret < 0 || fret > 22) continue;
      if (best === null || fret < best.fret) best = { string: s, fret: fret };
    }
    return best;
  }

  /**
   * Normalize the /api/session bundle into what the stage draws.
   * Verified fields (live prod bundle): understanding.chords_beat_snapped /
   * chords [{start_s,end_s,symbol,confidence}], understanding.sections
   * [{start_s,end_s,label}], understanding.beats_s, tempo_bpm, key,
   * audio.duration_s/source_title, user_midi.notes [{pitch,start,end,
   * velocity,role}], guidance.chord_lane, legacy_tempo_bpm,
   * legacy_detected_key, stems.{drums,bass,vocals,other} + stems.extras.
   */
  function extractTimeline(bundle, entry) {
    var u = (bundle && bundle.understanding) || {};
    var rawChords = (u.chords_beat_snapped && u.chords_beat_snapped.length && u.chords_beat_snapped)
      || (u.chords && u.chords.length && u.chords)
      || (bundle && bundle.guidance && bundle.guidance.chord_lane) || [];
    var chords = rawChords.map(function (c) {
      return { start_s: c.start_s, end_s: c.end_s, symbol: c.symbol };
    });
    var sections = (u.sections || []).map(function (s) {
      return { start_s: s.start_s, end_s: s.end_s, label: s.label || "section" };
    });
    var duration = (bundle && bundle.audio && bundle.audio.duration_s)
      || (entry && entry.duration) || 0;
    if (!duration) {
      chords.concat(sections).forEach(function (e) { duration = Math.max(duration, e.end_s || 0); });
    }
    var midiNotes = (bundle && bundle.user_midi && bundle.user_midi.notes) || [];
    var tabNotes = [];
    midiNotes.forEach(function (n) {
      var pos = midiToFret(n.pitch);
      if (pos) tabNotes.push({ start: n.start, end: n.end, pitch: n.pitch, string: pos.string, fret: pos.fret });
    });
    tabNotes.sort(function (a, b) { return a.start - b.start; });
    return {
      chords: chords,
      sections: sections,
      duration: duration,
      beats: u.beats_s || [],
      bpm: u.tempo_bpm || (bundle && bundle.legacy_tempo_bpm) || null,
      key: u.key || (bundle && bundle.legacy_detected_key) || null,
      tabNotes: tabNotes,
    };
  }

  /** Mixer rows from bundle.stems: main four in fixed order, then extras. */
  function stemRows(bundle) {
    var out = [];
    var stems = bundle && bundle.stems;
    if (!stems) return out;
    ["drums", "bass", "vocals", "other"].forEach(function (key) {
      var s = stems[key];
      if (s && s.audio_url) out.push({ role: key, label: s.display_name || key });
    });
    ["guitar_left", "guitar_right"].forEach(function (key) {
      var s = stems[key];
      if (s && s.audio_url) out.push({ role: key, label: s.display_name || key });
    });
    (stems.extras || []).forEach(function (s) {
      if (!s || !s.audio_url) return;
      var role = (s.display_name || s.id || "extra").toLowerCase().replace(/[^a-z0-9]+/g, "_");
      out.push({ role: role, label: s.display_name || role });
    });
    return out;
  }

  /** Tone banner data from bundle.tone / legacy_tone; null if nothing usable. */
  function toneSummary(bundle) {
    if (!bundle) return null;
    var t = bundle.tone && (bundle.tone.chosen || (bundle.tone.alternates || []).length)
      ? bundle.tone : null;
    var lt = bundle.legacy_tone || null;
    var chosen = (t && t.chosen) || null;
    var name = (chosen && chosen.display_name)
      || (lt && lt.match && lt.match.display_name)
      || (lt && lt.fallback && lt.fallback.display_name) || null;
    var chainId = (chosen && chosen.chain_id)
      || (lt && lt.apply && lt.apply.chain_id)
      || (lt && lt.fallback && lt.fallback.chain_id) || null;
    var rationale = (t && t.rationale) || (lt && lt.rationale) || null;
    var tier = (t && t.tier) || (lt && lt.tier) || null;
    if (!name && !rationale) return null;
    return { name: name, chainId: chainId, rationale: rationale, tier: tier };
  }

  function fmtTime(s) {
    var total = Math.max(0, Math.floor(s || 0));
    return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
  }

  // ---------------------------------------------------------------------
  // Fretboard geometry (rule-of-18 spacing, nut on the RIGHT — the app
  // convention shared with the desktop HandNeckView).
  // ---------------------------------------------------------------------
  function wirePos(n) { return 1 - Math.pow(2, -n / 12); }        // 0..~0.58 for 15 frets
  function fingerPos(n) {                                          // contact point inside fret n
    if (n <= 0) return 0;
    return (wirePos(n - 1) + wirePos(n)) / 2;
  }

  // smootherstep: zero 1st AND 2nd derivative at the ends — no jerk.
  function easeIO(x) { var c = Math.min(1, Math.max(0, x)); return c * c * c * (c * (c * 6 - 15) + 10); }

  // =====================================================================
  // Stage runtime (DOM/canvas from here down)
  // =====================================================================

  var FINGER_COLORS = { 1: "#5C9EFF", 2: "#4FD1A1", 3: "#F2B55C", 4: "#D98CFF" };
  var LAYERS_KEY = "jamn.stage.layers";
  var DEFAULT_LAYERS = { motion: true, hand: false, dots: true, chord: false, tab: false };

  var S = null; // singleton active stage

  function loadLayers() {
    try {
      var raw = localStorage.getItem(LAYERS_KEY);
      if (raw) {
        var v = JSON.parse(raw);
        return {
          motion: !!v.motion, hand: false, dots: !!v.dots,
          chord: !!v.chord, tab: !!v.tab && !v.chord,
        };
      }
    } catch (e) { /* private mode etc. */ }
    return Object.assign({}, DEFAULT_LAYERS);
  }
  function saveLayers(layers) {
    try { localStorage.setItem(LAYERS_KEY, JSON.stringify(layers)); } catch (e) {}
  }

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function mount(container, ctx) {
    if (S) unmount();
    ctx = ctx || {};
    var tl = extractTimeline(ctx.bundle, ctx.entry);

    S = {
      ctx: ctx, tl: tl, container: container,
      api: null, raf: 0, listeners: [], resizeObs: null, dead: false,
      layers: loadLayers(),
      // internal fallback clock (used when neither ctx.getTime nor the
      // host transport supplies a position)
      clock: { playing: false, pos: 0, wall: 0, rate: 1 },
      loop: { inS: null, outS: null },
      volume: 1,
      recording: false,
      scrubbing: false,
      shapes: null,          // HandShape per chord (built below)
      registry: null,        // chord_shapes.json once fetched
      lastChordIdx: -2, lastSectionIdx: -2,
      mixer: {},             // role -> {gain, muted, soloed}
      dom: {},
    };

    buildShapes();
    buildDOM(container);
    fetchRegistry();

    S.raf = requestAnimationFrame(frame);
    return window.JamnStage;
  }

  function unmount() {
    if (!S) return;
    S.dead = true;
    cancelAnimationFrame(S.raf);
    S.listeners.forEach(function (l) { l[0].removeEventListener(l[1], l[2]); });
    if (S.resizeObs) S.resizeObs.disconnect();
    if (S.container) S.container.innerHTML = "";
    S = null;
  }

  function setTransport(api) {
    if (S) S.api = api || null;
  }

  function on(target, type, fn) {
    target.addEventListener(type, fn);
    S.listeners.push([target, type, fn]);
  }

  // ------------------------------------------------------------------
  // Chord shapes: symbol → HandShape, cached per unique symbol.
  // ------------------------------------------------------------------
  function buildShapes() {
    var cache = {};
    S.shapes = S.tl.chords.map(function (c) {
      if (!(c.symbol in cache)) {
        cache[c.symbol] = fingeringForShape(lookupShape(c.symbol, S.registry));
      }
      return cache[c.symbol];
    });
    var maxUsed = 4;
    S.shapes.forEach(function (sh) {
      sh.fingers.forEach(function (f) { maxUsed = Math.max(maxUsed, f.fret); });
      if (sh.barre) maxUsed = Math.max(maxUsed, sh.barre.fret);
    });
    S.maxFret = Math.min(15, Math.max(9, maxUsed + 1));
  }

  function fetchRegistry() {
    if (typeof fetch !== "function") return;
    fetch("/static/chord_shapes.json").then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (json) {
      if (json && json.shapes && S && !S.dead) {
        // Merge over the embedded subset so curated voicings win.
        S.registry = { shapes: Object.assign({}, EMBEDDED_SHAPES, json.shapes) };
        buildShapes();
      }
    }).catch(function () { /* embedded fallback is fine */ });
  }

  // ------------------------------------------------------------------
  // Transport plumbing
  // ------------------------------------------------------------------
  function nowSeconds() {
    if (typeof S.ctx.getTime === "function") return S.ctx.getTime() || 0;
    if (S.api && typeof S.api.getTime === "function") return S.api.getTime() || 0;
    var c = S.clock;
    var t = c.playing ? c.pos + (performance.now() / 1000 - c.wall) * c.rate : c.pos;
    // Internal loop wrap (host transports enforce their own loops).
    if (S.loop.inS != null && S.loop.outS != null && t >= S.loop.outS) {
      internalSeek(S.loop.inS + (t - S.loop.outS) % Math.max(0.01, S.loop.outS - S.loop.inS));
      return S.loop.inS;
    }
    if (t >= duration() && duration() > 0) { internalPause(); internalSeek(duration()); return duration(); }
    return t;
  }
  function duration() {
    if (S.api && typeof S.api.getDuration === "function") {
      var d = S.api.getDuration();
      if (d > 0) return d;
    }
    return S.tl.duration || 0;
  }
  function isPlaying() {
    if (S.api && typeof S.api.isPlaying === "function") return !!S.api.isPlaying();
    return S.clock.playing;
  }
  function internalSeek(t) { S.clock.pos = Math.max(0, t); S.clock.wall = performance.now() / 1000; }
  function internalPause() { S.clock.pos = nowInternal(); S.clock.playing = false; }
  function nowInternal() {
    var c = S.clock;
    return c.playing ? c.pos + (performance.now() / 1000 - c.wall) * c.rate : c.pos;
  }
  function togglePlay() {
    if (S.api && S.api.play && S.api.pause) {
      if (isPlaying()) S.api.pause(); else S.api.play();
      return;
    }
    var c = S.clock;
    if (c.playing) { internalPause(); }
    else { c.wall = performance.now() / 1000; c.playing = true; }
  }
  function seek(t) {
    t = Math.min(Math.max(0, t), duration());
    if (S.api && typeof S.api.seek === "function") { S.api.seek(t); return; }
    internalSeek(t);
  }
  function setRate(r) {
    if (S.api && typeof S.api.setRate === "function") S.api.setRate(r);
    var c = S.clock; c.pos = nowInternal(); c.wall = performance.now() / 1000; c.rate = r;
  }

  // ------------------------------------------------------------------
  // DOM build
  // ------------------------------------------------------------------
  function buildDOM(container) {
    container.innerHTML = "";
    var root = el("div", "jstage");
    var main = el("div", "jstage-main");
    var d = S.dom;

    main.appendChild(buildHeader());
    var tone = buildToneBanner();
    if (tone) main.appendChild(tone);
    main.appendChild(buildLayersRow());

    // Fretboard stage
    var wrap = el("div", "jstage-board-wrap");
    d.board = el("canvas", "jstage-board");
    wrap.appendChild(d.board);
    d.chordOverlay = el("div", "jstage-chord-overlay");
    d.chordOverlayNow = el("div", "jstage-chord-now");
    d.chordOverlayNext = el("div", "jstage-chord-next");
    d.chordOverlay.appendChild(d.chordOverlayNow);
    d.chordOverlay.appendChild(d.chordOverlayNext);
    wrap.appendChild(d.chordOverlay);
    main.appendChild(wrap);
    d.boardWrap = wrap;

    // TAB reference card (hidden unless layer on)
    d.tabCard = el("div", "jstage-tabcard");
    var tabHead = el("div", "jstage-card-head");
    tabHead.appendChild(el("span", "jstage-card-dot"));
    tabHead.appendChild(el("span", null, "TAB"));
    d.tabCard.appendChild(tabHead);
    d.tab = el("canvas", "jstage-tab");
    d.tabCard.appendChild(d.tab);
    main.appendChild(d.tabCard);

    d.chips = el("div", "jstage-chips");
    main.appendChild(d.chips);

    main.appendChild(buildSections());
    main.appendChild(buildTransport());

    root.appendChild(main);
    root.appendChild(buildMixer());
    container.appendChild(root);

    // dpr-aware canvas sizing
    S.resizeObs = new ResizeObserver(function () { sizeCanvases(); });
    S.resizeObs.observe(wrap);
    sizeCanvases();
    applyLayerVisibility();
  }

  function sizeCanvases() {
    if (!S || S.dead) return;
    var dpr = window.devicePixelRatio || 1;
    [S.dom.board, S.dom.tab].forEach(function (cv) {
      var rect = cv.getBoundingClientRect();
      if (rect.width < 2) return;
      cv.width = Math.round(rect.width * dpr);
      cv.height = Math.round(rect.height * dpr);
    });
  }

  function buildHeader() {
    var entry = S.ctx.entry || {};
    var bundle = S.ctx.bundle;
    var head = el("div", "jstage-header");
    var art = el("div", "jstage-art");
    var title = entry.name || (bundle && bundle.audio && bundle.audio.source_title) || "Untitled";
    // Stable per-song hue — same seed hash the native art tile uses.
    var seed = 0;
    for (var i = 0; i < title.length; i++) seed = (seed * 31 + title.charCodeAt(i)) | 0;
    var hue = Math.abs(seed) % 360;
    art.style.background = "linear-gradient(135deg, hsl(" + hue + ",55%,45%), hsl(" + hue + ",65%,20%))";
    art.textContent = "♪";
    head.appendChild(art);
    var col = el("div", "jstage-header-text");
    col.appendChild(el("div", "jstage-title", title));
    var parts = [];
    if (entry.artist) parts.push(entry.artist);
    if (S.tl.duration) parts.push(fmtTime(S.tl.duration));
    if (S.tl.key) parts.push("Key: " + S.tl.key);
    if (S.tl.bpm) parts.push(Math.round(S.tl.bpm) + " BPM");
    col.appendChild(el("div", "jstage-meta", parts.join(" · ")));
    head.appendChild(col);
    return head;
  }

  function buildToneBanner() {
    var tone = toneSummary(S.ctx.bundle);
    if (!tone) return null;
    var b = el("div", "jstage-tone");
    var text = el("div", "jstage-tone-text");
    var head = el("div", "jstage-tone-head");
    head.appendChild(el("span", "jstage-tone-label", "Tone Match"));
    if (tone.name) head.appendChild(el("span", "jstage-tone-name", tone.name));
    if (tone.tier) head.appendChild(el("span", "jstage-tone-tier jstage-tone-tier--" + tone.tier, tone.tier));
    text.appendChild(head);
    if (tone.rationale) text.appendChild(el("div", "jstage-tone-why", tone.rationale));
    b.appendChild(text);
    var actions = el("div", "jstage-tone-actions");
    if (tone.chainId && typeof S.ctx.onApplyTone === "function") {
      var apply = el("button", "jstage-btn jstage-btn--accent", "Apply");
      on(apply, "click", function () { S.ctx.onApplyTone(tone.chainId); });
      actions.appendChild(apply);
    }
    var close = el("button", "jstage-btn jstage-btn--ghost", "×");
    close.title = "Dismiss";
    on(close, "click", function () { b.remove(); });
    actions.appendChild(close);
    b.appendChild(actions);
    return b;
  }

  function buildLayersRow() {
    var row = el("div", "jstage-layers");
    row.appendChild(el("span", "jstage-layers-label", "Display Layers"));
    S.dom.layerChips = {};
    var defs = [
      ["motion", "Motion", null],
      ["hand", "Hand", "Hand pose needs the desktop app — no hand-pose data is served to the web yet"],
      ["dots", "Dots", null],
      ["chord", "Chord", null],
      ["tab", "TAB", S.tl.tabNotes.length ? null : "No MIDI notes in this session"],
    ];
    defs.forEach(function (def) {
      var key = def[0], label = def[1], disabledTip = def[2];
      var chip = el("button", "jstage-chip", label);
      if (disabledTip) {
        chip.disabled = true;
        chip.title = disabledTip;
        chip.classList.add("jstage-chip--disabled");
      } else {
        on(chip, "click", function () { toggleLayer(key); });
      }
      S.dom.layerChips[key] = chip;
      row.appendChild(chip);
    });
    var spacer = el("div", "jstage-layers-spacer");
    row.appendChild(spacer);
    var reset = el("button", "jstage-chip jstage-chip--ghost", "↻ Reset Layout");
    on(reset, "click", function () {
      S.layers = Object.assign({}, DEFAULT_LAYERS);
      saveLayers(S.layers);
      applyLayerVisibility();
    });
    row.appendChild(reset);
    return row;
  }

  function layersOnCount() {
    var L = S.layers;
    return (L.motion ? 1 : 0) + (L.hand ? 1 : 0) + (L.dots ? 1 : 0) + (L.chord ? 1 : 0) + (L.tab ? 1 : 0);
  }

  function toggleLayer(key) {
    var L = S.layers;
    if (key === "chord" || key === "tab") {
      // Reference cards are either/or (desktop rule).
      if (L[key]) { if (layersOnCount() > 1) L[key] = false; }
      else { L[key] = true; L[key === "chord" ? "tab" : "chord"] = false; }
    } else {
      if (!(L[key] && layersOnCount() === 1)) L[key] = !L[key];
    }
    saveLayers(L);
    applyLayerVisibility();
  }

  function applyLayerVisibility() {
    var L = S.layers, chips = S.dom.layerChips;
    Object.keys(chips).forEach(function (k) {
      chips[k].classList.toggle("jstage-chip--on", !!L[k] && !chips[k].disabled);
    });
    S.dom.chordOverlay.style.display = L.chord ? "" : "none";
    S.dom.tabCard.style.display = (L.tab && S.tl.tabNotes.length) ? "" : "none";
    S.lastChordIdx = -2; // force overlay refresh
    sizeCanvases();
  }

  function buildSections() {
    var strip = el("div", "jstage-sections");
    S.dom.sectionBlocks = [];
    var dur = S.tl.duration || 1;
    S.tl.sections.forEach(function (sec) {
      var block = el("div", "jstage-section", sec.label);
      block.style.left = (sec.start_s / dur * 100) + "%";
      block.style.width = Math.max(0.5, (sec.end_s - sec.start_s) / dur * 100 - 0.2) + "%";
      on(block, "click", function () { seek(sec.start_s); });
      strip.appendChild(block);
      S.dom.sectionBlocks.push({ el: block, sec: sec });
    });
    S.dom.sectionPlayhead = el("div", "jstage-section-playhead");
    strip.appendChild(S.dom.sectionPlayhead);
    return strip;
  }

  function buildTransport() {
    var bar = el("div", "jstage-transport");
    var d = S.dom;

    d.playBtn = el("button", "jstage-play", "▶");
    d.playBtn.title = "Play / pause (space)";
    on(d.playBtn, "click", togglePlay);
    bar.appendChild(d.playBtn);

    d.timeCur = el("span", "jstage-time", "0:00");
    bar.appendChild(d.timeCur);

    d.posSlider = el("input", "jstage-pos");
    d.posSlider.type = "range";
    d.posSlider.min = "0"; d.posSlider.max = String(Math.max(1, duration())); d.posSlider.step = "0.1";
    d.posSlider.value = "0";
    on(d.posSlider, "input", function () { S.scrubbing = true; d.timeCur.textContent = fmtTime(+d.posSlider.value); });
    on(d.posSlider, "change", function () { S.scrubbing = false; seek(+d.posSlider.value); });
    bar.appendChild(d.posSlider);

    d.timeDur = el("span", "jstage-time", fmtTime(duration()));
    bar.appendChild(d.timeDur);

    bar.appendChild(el("span", "jstage-tdiv"));

    // Rate (practice speed)
    var rateWrap = el("span", "jstage-tgroup");
    rateWrap.title = "Practice speed";
    rateWrap.appendChild(el("span", "jstage-ticon", "🐢"));
    d.rateSlider = el("input", "jstage-rate");
    d.rateSlider.type = "range"; d.rateSlider.min = "0.5"; d.rateSlider.max = "1.5"; d.rateSlider.step = "0.05"; d.rateSlider.value = "1";
    d.rateLabel = el("span", "jstage-time", "100%");
    on(d.rateSlider, "input", function () {
      var r = +d.rateSlider.value;
      d.rateLabel.textContent = Math.round(r * 100) + "%";
      setRate(r);
    });
    rateWrap.appendChild(d.rateSlider);
    rateWrap.appendChild(d.rateLabel);
    bar.appendChild(rateWrap);

    bar.appendChild(el("span", "jstage-tdiv"));

    // Loop
    var loopIn = el("button", "jstage-btn", "Loop In");
    var loopOut = el("button", "jstage-btn", "Loop Out");
    d.loopChip = el("span", "jstage-loop-chip");
    d.loopChip.style.display = "none";
    var loopClear = el("button", "jstage-btn jstage-btn--ghost", "×");
    loopClear.title = "Clear loop";
    loopClear.style.display = "none";
    d.loopClear = loopClear;
    function pushLoop() {
      if (S.loop.inS != null && S.loop.outS != null) {
        if (S.api && typeof S.api.setLoop === "function") S.api.setLoop(S.loop.inS, S.loop.outS);
        d.loopChip.textContent = fmtTime(S.loop.inS) + "–" + fmtTime(S.loop.outS);
        d.loopChip.style.display = ""; loopClear.style.display = "";
      }
    }
    on(loopIn, "click", function () {
      S.loop.inS = nowSeconds();
      if (S.loop.outS == null || S.loop.outS <= S.loop.inS) S.loop.outS = Math.max(S.loop.inS + 1, duration());
      pushLoop();
    });
    on(loopOut, "click", function () {
      S.loop.outS = nowSeconds();
      if (S.loop.inS == null || S.loop.inS >= S.loop.outS) S.loop.inS = 0;
      pushLoop();
    });
    on(loopClear, "click", function () {
      S.loop.inS = S.loop.outS = null;
      if (S.api && typeof S.api.clearLoop === "function") S.api.clearLoop();
      d.loopChip.style.display = "none"; loopClear.style.display = "none";
    });
    bar.appendChild(loopIn); bar.appendChild(loopOut);
    bar.appendChild(d.loopChip); bar.appendChild(loopClear);

    bar.appendChild(el("span", "jstage-tdiv"));

    // Volume
    var volWrap = el("span", "jstage-tgroup");
    volWrap.title = "Song volume";
    volWrap.appendChild(el("span", "jstage-ticon", "🔊"));
    d.volSlider = el("input", "jstage-vol");
    d.volSlider.type = "range"; d.volSlider.min = "0"; d.volSlider.max = "1"; d.volSlider.step = "0.01"; d.volSlider.value = "1";
    on(d.volSlider, "input", function () {
      S.volume = +d.volSlider.value;
      if (S.api && typeof S.api.setVolume === "function") S.api.setVolume(S.volume);
      else if (typeof S.ctx.onSongGain === "function") S.ctx.onSongGain(S.volume);
      if (S.dom.songSlider) S.dom.songSlider.value = String(S.volume);
    });
    volWrap.appendChild(d.volSlider);
    bar.appendChild(volWrap);

    // Record
    d.recBtn = el("button", "jstage-rec", "●");
    if (S.api && typeof S.api.record === "function") {
      d.recBtn.title = "Record";
    } else {
      d.recBtn.disabled = true;
      d.recBtn.title = "Recording is available in the desktop app";
    }
    on(d.recBtn, "click", function () {
      if (!(S.api && typeof S.api.record === "function")) return;
      S.recording = !S.recording;
      S.api.record(S.recording);
      d.recBtn.classList.toggle("jstage-rec--on", S.recording);
    });
    bar.appendChild(d.recBtn);

    return bar;
  }

  function buildMixer() {
    var panel = el("div", "jstage-mixer");
    panel.appendChild(el("div", "jstage-mixer-title", "Mixer"));

    // Song master
    var song = el("div", "jstage-strip");
    song.appendChild(el("div", "jstage-strip-name jstage-strip-name--song", "Song"));
    var songSlider = el("input", "jstage-fader");
    songSlider.type = "range"; songSlider.min = "0"; songSlider.max = "1"; songSlider.step = "0.01"; songSlider.value = "1";
    on(songSlider, "input", function () {
      S.volume = +songSlider.value;
      if (typeof S.ctx.onSongGain === "function") S.ctx.onSongGain(S.volume);
      else if (S.api && typeof S.api.setVolume === "function") S.api.setVolume(S.volume);
      if (S.dom.volSlider) S.dom.volSlider.value = String(S.volume);
    });
    song.appendChild(songSlider);
    S.dom.songSlider = songSlider;
    panel.appendChild(song);
    panel.appendChild(el("div", "jstage-mixer-div"));

    var rows = stemRows(S.ctx.bundle);
    S.dom.stemStrips = [];
    rows.forEach(function (row) {
      S.mixer[row.role] = { gain: 1, muted: false, soloed: false };
      var strip = el("div", "jstage-strip");
      var head = el("div", "jstage-strip-head");
      head.appendChild(el("div", "jstage-strip-name", row.label));
      var btns = el("div", "jstage-strip-btns");
      var m = el("button", "jstage-ms", "M");
      var s = el("button", "jstage-ms", "S");
      m.title = "Mute"; s.title = "Solo";
      on(m, "click", function () {
        var st = S.mixer[row.role];
        st.muted = !st.muted;
        m.classList.toggle("jstage-ms--muted", st.muted);
        if (typeof S.ctx.onStemMute === "function") S.ctx.onStemMute(row.role, st.muted);
        refreshMixerDim();
      });
      on(s, "click", function () {
        var st = S.mixer[row.role];
        st.soloed = !st.soloed;
        s.classList.toggle("jstage-ms--soloed", st.soloed);
        if (typeof S.ctx.onSolo === "function") S.ctx.onSolo(row.role, st.soloed);
        refreshMixerDim();
      });
      btns.appendChild(m); btns.appendChild(s);
      head.appendChild(btns);
      strip.appendChild(head);
      var fader = el("input", "jstage-fader");
      fader.type = "range"; fader.min = "0"; fader.max = "1"; fader.step = "0.01"; fader.value = "1";
      on(fader, "input", function () {
        S.mixer[row.role].gain = +fader.value;
        if (typeof S.ctx.onStemGain === "function") S.ctx.onStemGain(row.role, +fader.value);
      });
      strip.appendChild(fader);
      panel.appendChild(strip);
      S.dom.stemStrips.push({ role: row.role, el: strip });
    });
    if (!rows.length) {
      panel.appendChild(el("div", "jstage-mixer-empty", "No stems in this session"));
    }
    return panel;
  }

  /** Mute wins; any solo silences the rest — same semantics as StemMixModel. */
  function refreshMixerDim() {
    var anySolo = Object.keys(S.mixer).some(function (r) { return S.mixer[r].soloed; });
    S.dom.stemStrips.forEach(function (row) {
      var st = S.mixer[row.role];
      var silent = st.muted || (anySolo && !st.soloed);
      row.el.classList.toggle("jstage-strip--dim", silent && st.gain > 0);
    });
  }

  // ------------------------------------------------------------------
  // Per-frame rendering
  // ------------------------------------------------------------------
  function frame() {
    if (!S || S.dead) return;
    var t = nowSeconds();

    drawBoard(t);
    if (S.layers.tab && S.tl.tabNotes.length) drawTab(t);
    updateTransportUI(t);
    updateChips(t);
    updateSections(t);

    S.raf = requestAnimationFrame(frame);
  }

  function updateTransportUI(t) {
    var d = S.dom;
    d.playBtn.textContent = isPlaying() ? "❙❙" : "▶";
    if (!S.scrubbing) {
      d.timeCur.textContent = fmtTime(t);
      d.posSlider.value = String(t);
      var dur = duration();
      if (+d.posSlider.max !== Math.max(1, dur)) {
        d.posSlider.max = String(Math.max(1, dur));
        d.timeDur.textContent = fmtTime(dur);
      }
    }
  }

  function updateChips(t) {
    var idx = activeIndexAt(S.tl.chords, t);
    if (idx === S.lastChordIdx) return;
    S.lastChordIdx = idx;
    var chips = S.dom.chips;
    chips.innerHTML = "";
    if (!S.tl.chords.length) return;
    var from = Math.max(0, idx - 1);
    var to = Math.min(S.tl.chords.length, from + 8);
    for (var i = from; i < to; i++) {
      (function (i) {
        var c = S.tl.chords[i];
        var chip = el("button", "jstage-chordchip" + (i === idx ? " jstage-chordchip--now" : ""), c.symbol);
        on(chip, "click", function () { seek(c.start_s); });
        chips.appendChild(chip);
      })(i);
    }
    // Chord overlay (big current symbol + next)
    if (S.layers.chord) {
      var cur = idx >= 0 ? S.tl.chords[idx] : null;
      var next = idx + 1 < S.tl.chords.length ? S.tl.chords[idx + 1] : null;
      S.dom.chordOverlayNow.textContent = cur ? cur.symbol : "—";
      S.dom.chordOverlayNext.textContent = next ? "→ " + next.symbol : "";
    }
  }

  function updateSections(t) {
    var dur = S.tl.duration || 1;
    S.dom.sectionPlayhead.style.left = Math.min(100, t / dur * 100) + "%";
    S.dom.sectionBlocks.forEach(function (b) {
      b.el.classList.toggle("jstage-section--now", t >= b.sec.start_s && t < b.sec.end_s);
    });
  }

  // ---- finger animation (port of HandNeckView.sample/target) ----
  function restString(fi) { return 1.4 + (fi - 1) * 0.7; }
  function anchorFret(idx) {
    if (idx < 0 || idx >= S.shapes.length) return 2;
    var sh = S.shapes[idx];
    var fs = sh.fingers.map(function (f) { return f.fret; });
    if (sh.barre) fs.push(sh.barre.fret);
    if (!fs.length) return 2;
    return Math.max(1, fs.reduce(function (a, b) { return a + b; }, 0) / fs.length);
  }
  function target(fi, idx) {
    if (idx < 0 || idx >= S.shapes.length) {
      return { f: 2, sLo: restString(fi), sHi: restString(fi), pressed: false };
    }
    var sh = S.shapes[idx];
    if (fi === 1 && sh.barre) {
      return { f: sh.barre.fret, sLo: sh.barre.lo, sHi: sh.barre.hi, pressed: true };
    }
    for (var i = 0; i < sh.fingers.length; i++) {
      if (sh.fingers[i].finger === fi) {
        var c = sh.fingers[i];
        return { f: c.fret, sLo: c.string, sHi: c.string, pressed: true };
      }
    }
    return { f: anchorFret(idx), sLo: restString(fi), sHi: restString(fi), pressed: false };
  }
  function sampleFinger(fi, t) {
    var chords = S.tl.chords;
    if (!chords.length) {
      return { f: 2, sLo: restString(fi), sHi: restString(fi), press: 0, moving: false, arrive: 0 };
    }
    var i = Math.max(0, activeIndexAt(chords, t));
    var j = Math.min(i + 1, chords.length - 1);
    var boundary = chords[i].end_s;
    var dur = chords[i].end_s - chords[i].start_s;
    var trans = Math.min(0.34, Math.max(0.12, dur * 0.5));
    var a = target(fi, i);
    var arrive = 0;
    if (a.pressed) {
      var p = target(fi, i - 1);
      if (!p.pressed || p.f !== a.f || p.sLo !== a.sLo || p.sHi !== a.sHi) {
        var dt = t - chords[i].start_s;
        if (dt >= 0 && dt < 0.4) arrive = 1 - dt / 0.4;
      }
    }
    if (j === i || t < boundary - trans) {
      return { f: a.f, sLo: a.sLo, sHi: a.sHi, press: a.pressed ? 1 : 0, moving: false, arrive: arrive };
    }
    var b = target(fi, j);
    var k = easeIO((t - (boundary - trans)) / trans);
    function lp(x, y) { return x + (y - x) * k; }
    return {
      f: lp(a.f, b.f), sLo: lp(a.sLo, b.sLo), sHi: lp(a.sHi, b.sHi),
      press: (a.pressed ? 1 : 0) * (1 - k) + (b.pressed ? 1 : 0) * k,
      moving: true, arrive: 0,
    };
  }

  function drawBoard(t) {
    var cv = S.dom.board;
    var g = cv.getContext("2d");
    if (!g || cv.width < 4) return;
    var dpr = window.devicePixelRatio || 1;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var W = cv.width / dpr, H = cv.height / dpr;
    g.clearRect(0, 0, W, H);

    var maxFret = S.maxFret;
    var top = 28, padR = 40, padL = 14;
    var loMM = wirePos(0), hiMM = wirePos(maxFret), span = hiMM - loMM;
    var vBudget = Math.max(1, H - top - 44);
    var gap = Math.min(vBudget / 6, 46);
    var boardH = 6 * gap;
    var bot = top + boardH;
    var right = W - padR;
    var left = padL;
    var pxPerUnit = (right - left) / span;

    function wireX(n) { return right - (wirePos(n) - loMM) * pxPerUnit; }
    function cx(fret) {
      var f0 = Math.max(0, Math.floor(fret)), f1 = f0 + 1;
      var a = fingerPos(f0), b = fingerPos(f1);
      var mm = a + (b - a) * (fret - f0);
      return right - (mm - loMM) * pxPerUnit;
    }
    function sy(s) { return top + (s + 0.5) * gap; }

    // Wood slab
    var grd = g.createLinearGradient(0, top - 12, 0, bot + 12);
    grd.addColorStop(0, "#3C2B21");
    grd.addColorStop(0.5, "#291C14");
    grd.addColorStop(1, "#1F130E");
    roundRect(g, left - 4, top - 12, (right - left) + 16, boardH + 24, 8);
    g.fillStyle = grd; g.fill();

    // Inlays
    g.fillStyle = "rgba(255,255,255,0.16)";
    [3, 5, 7, 9, 12, 15].forEach(function (f) {
      if (f > maxFret) return;
      var x = (wireX(f) + wireX(f - 1)) / 2;
      g.beginPath(); g.arc(x, (top + bot) / 2, 4, 0, Math.PI * 2); g.fill();
      if (f === 12) {
        g.beginPath(); g.arc(x, sy(1), 3, 0, Math.PI * 2); g.fill();
        g.beginPath(); g.arc(x, sy(4), 3, 0, Math.PI * 2); g.fill();
      }
    });

    // Frets + numbers
    g.font = "11px ui-monospace, monospace";
    g.textAlign = "center"; g.textBaseline = "middle";
    for (var f = 1; f <= maxFret; f++) {
      var x = wireX(f);
      g.strokeStyle = "rgba(128,128,128,0.9)"; g.lineWidth = 2;
      g.beginPath(); g.moveTo(x, top - 11); g.lineTo(x, bot + 11); g.stroke();
      g.fillStyle = "rgba(255,255,255,0.42)";
      g.fillText(String(f), (wireX(f) + wireX(f - 1)) / 2, bot + 24);
    }

    // Nut (right)
    g.fillStyle = "#D9D9D9";
    roundRect(g, right + 2, top - 13, 6, boardH + 26, 2);
    g.fill();

    // Strings (low E thickest, top row = low E like the desktop board)
    var widths = [3.0, 2.6, 2.2, 1.8, 1.4, 1.0];
    for (var s0 = 0; s0 < 6; s0++) {
      var y0 = sy(s0);
      g.strokeStyle = "rgba(232,232,232,0.85)";
      g.lineWidth = widths[s0];
      g.beginPath(); g.moveTo(left - 4, y0); g.lineTo(right + 8, y0); g.stroke();
    }

    var L = S.layers;
    if (!(L.dots || L.motion)) return;

    // Sample all four fingers once; on-neck layers read the same states.
    var st = {};
    for (var fi = 1; fi <= 4; fi++) st[fi] = sampleFinger(fi, t);

    // Hand-base X for the light finger stems (knuckle row under the board).
    var sx = 0, sw = 0;
    for (fi = 1; fi <= 4; fi++) {
      var w = 0.2 + 0.8 * st[fi].press;
      sx += cx(st[fi].f) * w; sw += w;
    }
    var baseX = sw > 0 ? sx / sw : (left + right) / 2;
    var kY = bot + 26;
    function kX(fi) { return baseX + (fi - 2.5) * 22; }

    // Draw non-moving first, moving last (on top).
    var order = [1, 2, 3, 4].sort(function (a, b) {
      return (st[a].moving ? 1 : 0) - (st[b].moving ? 1 : 0);
    });
    order.forEach(function (fi) {
      var fs = st[fi], c = FINGER_COLORS[fi];
      var state = (fs.moving && L.motion) ? "move" : (fs.press < 0.5 ? "lift" : "plant");
      var lift = (1 - fs.press) * 14;
      var yLo = sy(fs.sLo) - lift, yHi = sy(fs.sHi) - lift;
      var x = cx(fs.f), yc = (yLo + yHi) / 2;
      var isBarre = (fs.sHi - fs.sLo) > 0.5;

      if (!isBarre && fs.press < 0.04 && state !== "move") return;

      // MOTION: ghost trail behind a moving fingertip
      if (state === "move" && !isBarre) {
        for (var gi = 1; gi <= 4; gi++) {
          var gp = sampleFinger(fi, t - gi * 0.05);
          var gy = sy((gp.sLo + gp.sHi) / 2);
          g.fillStyle = hexA(c, 0.10 * (1 - gi / 5));
          g.beginPath(); g.arc(cx(gp.f), gy, 9 - gi, 0, Math.PI * 2); g.fill();
        }
      }

      // DOTS: light stem from the knuckle row to the contact
      if (L.dots) {
        var emph = state === "move" ? 1 : state === "plant" ? 0.62 : 0.28;
        var mx = (kX(fi) + x) / 2;
        var my = (kY + yc) / 2 - 20 * fs.press - Math.abs(x - kX(fi)) * 0.06;
        g.strokeStyle = hexA(c, (0.12 + 0.30 * fs.press) * (0.5 + 0.5 * emph));
        g.lineWidth = 3 + 3 * fs.press; g.lineCap = "round";
        g.beginPath(); g.moveTo(kX(fi), kY); g.quadraticCurveTo(mx, my, x, yc); g.stroke();
        g.strokeStyle = hexA(c, 0.5 * emph); g.lineWidth = 1.7;
        g.beginPath(); g.moveTo(kX(fi), kY); g.quadraticCurveTo(mx, my, x, yc); g.stroke();
      }

      if (isBarre) {
        // One finger, many strings: a rounded bar across yLo..yHi.
        var rB = 12;
        g.save();
        g.globalAlpha = state === "lift" ? 0.34 : 1;
        if (state !== "lift") { g.shadowColor = hexA(c, 0.45); g.shadowBlur = 8; }
        g.fillStyle = c;
        roundRect(g, x - rB, yLo - rB, 2 * rB, (yHi - yLo) + 2 * rB, rB);
        g.fill();
        g.restore();
        g.strokeStyle = "rgba(15,15,15,1)"; g.lineWidth = 1.5;
        roundRect(g, x - rB, yLo - rB, 2 * rB, (yHi - yLo) + 2 * rB, rB);
        g.stroke();
        if (L.dots) {
          g.fillStyle = "#0D0D0D";
          g.font = "bold 14px ui-monospace, monospace";
          g.fillText(String(fi), x, yc);
        }
        return;
      }

      // MOTION: one subtle arrival pulse
      if (fs.arrive > 0 && L.motion) {
        var k = 1 - fs.arrive;
        g.strokeStyle = hexA(c, fs.arrive * 0.45); g.lineWidth = 2;
        g.beginPath(); g.arc(x, yc, 13 + k * 12, 0, Math.PI * 2); g.stroke();
      }

      // Fingertip — numbered circle
      var r = state === "move" ? 17 : state === "plant" ? 15 : 11;
      g.save();
      g.globalAlpha = state === "lift" ? 0.34 : 1;
      if (state === "move") { g.shadowColor = hexA(c, 0.7); g.shadowBlur = 9; }
      g.fillStyle = c;
      g.beginPath(); g.arc(x, yc, r, 0, Math.PI * 2); g.fill();
      g.restore();
      if (state === "move" && L.motion) {
        g.strokeStyle = "rgba(255,255,255,0.85)"; g.lineWidth = 2;
        g.beginPath(); g.arc(x, yc, r + 3.5, 0, Math.PI * 2); g.stroke();
      }
      g.strokeStyle = "rgba(15,15,15,1)"; g.lineWidth = 1.5;
      g.beginPath(); g.arc(x, yc, r, 0, Math.PI * 2); g.stroke();
      if (L.dots) {
        g.fillStyle = state === "lift" ? "rgba(13,13,13,0.6)" : "#0D0D0D";
        g.font = "bold " + (state === "move" ? 15 : 13) + "px ui-monospace, monospace";
        g.fillText(String(fi), x, yc);
      }
    });
  }

  function drawTab(t) {
    var cv = S.dom.tab;
    var g = cv.getContext("2d");
    if (!g || cv.width < 4) return;
    var dpr = window.devicePixelRatio || 1;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    var W = cv.width / dpr, H = cv.height / dpr;
    g.clearRect(0, 0, W, H);

    var padL = 26, padR = 8, topPad = 10;
    var spacing = (H - 2 * topPad) / 5;
    var playheadX = padL + (W - padL - padR) * 0.3;
    var pxPerSec = (W - padL - padR) / 8;   // ~8 s window

    // Strings top-to-bottom: high E first (tab convention).
    var labels = ["e", "B", "G", "D", "A", "E"];
    g.font = "9px ui-monospace, monospace";
    g.textAlign = "center"; g.textBaseline = "middle";
    for (var row = 0; row < 6; row++) {
      var y = topPad + row * spacing;
      g.strokeStyle = "rgba(160,160,170,0.5)"; g.lineWidth = 1;
      g.beginPath(); g.moveTo(padL, y); g.lineTo(W - padR, y); g.stroke();
      g.fillStyle = "rgba(160,160,170,0.9)";
      g.fillText(labels[row], padL - 12, y);
    }

    g.strokeStyle = "#8B5CF6"; g.lineWidth = 2;
    g.beginPath(); g.moveTo(playheadX, topPad - 6); g.lineTo(playheadX, topPad + 5 * spacing + 6); g.stroke();

    g.font = "10px ui-monospace, monospace";
    var notes = S.tl.tabNotes;
    for (var i = 0; i < notes.length; i++) {
      var n = notes[i];
      var x = playheadX + (n.start - t) * pxPerSec;
      if (x < padL - 8) continue;
      if (x > W - padR + 8) break;
      var y2 = topPad + (5 - n.string) * spacing;   // string 5 (high E) → top row
      var passed = x < playheadX;
      g.fillStyle = passed ? "rgba(161,161,170,0.4)" : "#FFFFFF";
      g.fillText(String(n.fret), x, y2);
    }
  }

  function roundRect(g, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    g.beginPath();
    g.moveTo(x + r, y);
    g.arcTo(x + w, y, x + w, y + h, r);
    g.arcTo(x + w, y + h, x, y + h, r);
    g.arcTo(x, y + h, x, y, r);
    g.arcTo(x, y, x + w, y, r);
    g.closePath();
  }

  function hexA(hex, a) {
    var r = parseInt(hex.slice(1, 3), 16),
        gg = parseInt(hex.slice(3, 5), 16),
        b = parseInt(hex.slice(5, 7), 16);
    return "rgba(" + r + "," + gg + "," + b + "," + Math.max(0, Math.min(1, a)) + ")";
  }

  // =====================================================================
  window.JamnStage = {
    mount: mount,
    unmount: unmount,
    setTransport: setTransport,
    _internals: {
      STANDARD_TUNING: STANDARD_TUNING,
      EMBEDDED_SHAPES: EMBEDDED_SHAPES,
      normalizeSymbol: normalizeSymbol,
      buildBarreShape: buildBarreShape,
      lookupShape: lookupShape,
      fingeringForShape: fingeringForShape,
      activeIndexAt: activeIndexAt,
      midiToFret: midiToFret,
      extractTimeline: extractTimeline,
      stemRows: stemRows,
      toneSummary: toneSummary,
      fmtTime: fmtTime,
      easeIO: easeIO,
      wirePos: wirePos,
      fingerPos: fingerPos,
    },
  };
})();
