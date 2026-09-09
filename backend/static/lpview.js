/*
 * lpview.js — window.JamnLaunchpad: the web equivalent of the native
 * desktop "Launchpad" tool (jam-desktop LaunchpadPanelView + Controller).
 *
 * This is the 8×8 pad grid with a two-row header of chop controls —
 * DISTINCT from the 16-pad "Jam Pads" surface (kit.js). It reuses the
 * existing audio stack rather than re-implementing it:
 *
 *   - Audio + PadEngine come from window.JamnKit (engine()/pads()/mount).
 *     We drive engine.trigger/release/stopAll directly, porting kit.js's
 *     Tap / Loop / Lock(=latch) / Quantize semantics 1:1.
 *   - Voice pad (top-left) opens window.JamnContribute (Voice tab); the
 *     returned sample is baked onto the pad via engine.setPadSource.
 *   - Stem / Slices / Load repopulate the grid from the "contribute chops"
 *     path: GET /api/song/{id}/chops?stem=&sliceMode=, decode the stem
 *     once, slice each chop region, and setPadSource it onto a pad.
 *   - Auto Kit / Drum kit / Beat-kind reload the pads via
 *     JamnKit.mount(entry, {kind}).
 *   - Groove (⚡) humanizes the sequencer (GET /api/song/{id}/groove →
 *     JamnSequencer.setGrooveOffsets) when it's mounted, else fires the
 *     best loop per category on the pads (native "instant groove").
 *   - Physical Novation Launchpad: window.JamnLpHW (lp-hw.js) is attached
 *     on mount / detached on unmount. It LED-paints all 64 hardware pads
 *     to match this grid (padRgb == padFill) and routes hardware presses
 *     back through onPadDown/onPadUp — the same path as an on-screen tap.
 *     The header status pill reflects lp-hw's device name. Link status is
 *     still status-only (owned elsewhere on web).
 *
 * Contract:  window.JamnLaunchpad = { mount(container, ctx), unmount() }
 *   ctx = { entry, engine?, pads?, onOpenContribute?, onClose? }
 * The host wires the sidebar 'launchpad' item to showView('launchpad')
 * and calls JamnLaunchpad.mount(document.getElementById('launchpad-root'),
 * { entry: currentEntry }).
 */
(function () {
  "use strict";

  var COLS = 8;
  var ROWS = 8;
  var CAPACITY = COLS * ROWS; // 64

  // Category → 0xRRGGBB accent, matching LaunchpadController.PadCategory.
  var CATEGORY_HEX = {
    DRUMS: 0xef4444,
    BASS: 0x22c55e,
    CHORDS: 0xf59e0b,
    LEAD: 0xf97316,
    VOCAL: 0xec4899,
    RHYTHM: 0x3b82f6,
    TEXTURE: 0x06b6d4,
    FX: 0xa855f7,
    STAB: 0x8b5cf6,
    SAMPLE: 0x64748b,
  };

  var DEFAULT_HEX = 0x5b6b8c; // muted blue (Controller.defaultColorHint feel)
  var VOICE_HEX = 0x9b4dff; // vocoded purple (local sample)
  var SEQUENCE_HEX = 0x9933cc; // purple sequence tile

  var STEMS = ["mix", "vocals", "drums", "bass", "other"];
  var SLICE_MODES = ["beat", "phrase", "onset", "chord", "section", "drum-bundle"];
  // Grid values the engine understands (kit.js uses off/bar/beat); phrase is
  // plumbed for a future engine, ignored today (same as kit.js).
  var QUANTIZE = [
    ["off", "Off"],
    ["bar", "Bar"],
    ["beat", "Beat"],
    ["phrase", "Phrase"],
  ];
  var BEAT_KINDS = [
    ["auto", "Auto"],
    ["drums", "Drums"],
    ["flip", "Flip"],
  ];

  // ---------------------------------------------------------------- helpers
  // (pure, exported on _internals for the DOM-free smoke test)

  /** row/col → padIdx. Row 0 is the TOP row (like the native grid, which
   * lays out ForEach(0..<8) top-to-bottom). */
  function padIndex(row, col, cols) {
    return row * (cols || COLS) + col;
  }

  /** Map a colorHint (number, "0xRRGGBB", "#rrggbb", "255,0,0", or a CSS
   * name) to a CSS color string. Unknown → null so callers can fall back. */
  function colorFromHint(hint) {
    if (hint == null) return null;
    if (typeof hint === "number" && isFinite(hint)) return hexToCss(hint);
    if (typeof hint === "string") {
      var s = hint.trim();
      if (!s) return null;
      if (/^0x[0-9a-f]{6}$/i.test(s)) return hexToCss(parseInt(s, 16));
      if (/^#[0-9a-f]{3,8}$/i.test(s)) return s;
      if (/^\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}$/.test(s)) return "rgb(" + s + ")";
      return s; // assume a CSS color name ("red", "teal", …)
    }
    return null;
  }

  function hexToCss(n) {
    var v = (n >>> 0) & 0xffffff;
    return (
      "rgb(" +
      ((v >> 16) & 0xff) +
      "," +
      ((v >> 8) & 0xff) +
      "," +
      (v & 0xff) +
      ")"
    );
  }

  function categoryColor(cat) {
    var hex = CATEGORY_HEX[String(cat || "").toUpperCase()];
    return hex != null ? hexToCss(hex) : null;
  }

  /** The fill color for a pad meta dict: voice/sequence → category →
   * colorHint → default. */
  function padFill(meta) {
    if (!meta) return hexToCss(DEFAULT_HEX);
    if (meta.voice) return hexToCss(VOICE_HEX);
    if (meta.sequence) return hexToCss(SEQUENCE_HEX);
    var cat = meta.category;
    var byCat = cat ? categoryColor(cat) : null;
    if (byCat) return byCat;
    var byHint = colorFromHint(meta.colorHint);
    return byHint || hexToCss(DEFAULT_HEX);
  }

  function hexToRgb(n) {
    var v = (n >>> 0) & 0xffffff;
    return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff };
  }

  /** RGB (0..255) for a colorHint — the numeric- / hex- / "r,g,b"-shaped
   * forms padFill can resolve to a concrete triple. Returns null for a
   * bare CSS color name (which the hardware can't parse), so callers fall
   * back to the default tint just like padFill does. */
  function rgbFromHint(hint) {
    if (hint == null) return null;
    if (typeof hint === "number" && isFinite(hint)) return hexToRgb(hint);
    if (typeof hint === "string") {
      var s = hint.trim();
      if (!s) return null;
      if (/^0x[0-9a-f]{6}$/i.test(s)) return hexToRgb(parseInt(s, 16));
      if (/^#[0-9a-f]{6}$/i.test(s)) return hexToRgb(parseInt(s.slice(1), 16));
      var m = s.match(/^(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})$/);
      if (m) return { r: +m[1], g: +m[2], b: +m[3] };
      return null; // CSS name — no concrete RGB for the hardware LED
    }
    return null;
  }

  /** The RGB (0..255) twin of padFill — same voice/sequence → category →
   * colorHint → default precedence, but returns { r, g, b } so lp-hw can
   * light the physical pad the SAME color as the on-screen tile. */
  function padRgb(meta) {
    if (!meta) return hexToRgb(DEFAULT_HEX);
    if (meta.voice) return hexToRgb(VOICE_HEX);
    if (meta.sequence) return hexToRgb(SEQUENCE_HEX);
    var cat = meta.category;
    var hex = cat ? CATEGORY_HEX[String(cat).toUpperCase()] : null;
    if (hex != null) return hexToRgb(hex);
    var byHint = rgbFromHint(meta.colorHint);
    return byHint || hexToRgb(DEFAULT_HEX);
  }

  /** Content-first pad label (mobile/desktop parity): explicit name, then a
   * section label, then a non-chord kind, then chord symbol, else null. */
  function padLabel(meta) {
    if (!meta) return null;
    if (meta.voice) return "Voice";
    if (meta.name && String(meta.name).trim()) return String(meta.name).trim();
    if (meta.sectionLabel) return meta.sectionLabel;
    if (meta.kind && meta.kind !== "chord") return capitalize(meta.kind);
    if (meta.chordSymbol) return meta.chordSymbol;
    return null;
  }

  function capitalize(s) {
    s = String(s || "");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  /** Category for a chop/pad from its stem + Riley contentType — a port of
   * LaunchpadController.category(stem:contentType:). */
  function categoryFor(stem, contentType) {
    switch (stem) {
      case "drums":
        return "DRUMS";
      case "bass":
        return "BASS";
      case "vocals":
        return "VOCAL";
      default:
        break;
    }
    switch (contentType) {
      case "rhythm_loop":
        return "RHYTHM";
      case "lead_loop":
        return "LEAD";
      case "chord_loop":
        return "CHORDS";
      case "bass_groove":
        return "BASS";
      case "texture":
      case "drone":
      case "ambient":
        return "TEXTURE";
      case "impact":
      case "transition":
      case "pickup":
      case "ending":
        return "FX";
      case "one_shot":
        return "STAB";
      default:
        return "SAMPLE";
    }
  }

  /** Per-pad step flags from a kit-level defaultSequence (kind=flip wire
   * form). Mirrors kit.js padStepFlags — returns { padIdx: [bool,…] } or
   * null. Pure. */
  function sequenceStepFlags(kit) {
    var seq = kit && kit.defaultSequence;
    var tracks = seq && Array.isArray(seq.tracks) ? seq.tracks : null;
    if (!tracks) return null;
    var out = {};
    var any = false;
    tracks.forEach(function (t) {
      if (!t || !Array.isArray(t.steps)) return;
      var ref = t.chopRef && (t.chopRef.type === "packPad" ? t.chopRef : t.chopRef.packPad);
      var idx = ref && typeof ref.padIdx === "number" ? ref.padIdx : null;
      if (idx === null) return;
      var flags = t.steps.map(function (st) {
        return !!(st && typeof st.velocity === "number" && st.velocity > 0);
      });
      if (out[idx]) {
        for (var i = 0; i < flags.length && i < out[idx].length; i++)
          out[idx][i] = out[idx][i] || flags[i];
      } else out[idx] = flags;
      any = true;
    });
    return any ? out : null;
  }

  /** Layout for the step-dot mini-grid drawn on a sequence pad: `cols`
   * columns × however many rows the flags need, plus a flat cells[] with
   * {on} in row-major order. Pure — the tile renders the result. */
  function sequenceStepDots(flags, cols) {
    var c = cols || COLS;
    var n = Array.isArray(flags) ? flags.length : 0;
    var rows = Math.max(1, Math.ceil(n / c));
    var cells = [];
    for (var i = 0; i < n; i++) cells.push({ on: !!flags[i] });
    return { cols: c, rows: rows, cells: cells };
  }

  /** Build the engine.trigger opts for a press, porting kit.js padDown:
   *   Tap  → one-shot (loop:false), plays through.
   *   Loop → loop:true; quantized unless Lock is off (Lock ON = start on the
   *          shared cycle; OFF = start immediately). grid carries the
   *          quantize choice (engine reads it; unknown grids ignored).
   * Pure. */
  function buildTriggerOpts(state) {
    if (state.mode !== "loop") {
      return { loop: false, quantized: false };
    }
    var q = state.lock !== false && state.quantize !== "off";
    var opts = { loop: true, quantized: q };
    if (q) opts.grid = state.quantize === "off" ? "bar" : state.quantize;
    return opts;
  }

  /** Pick the best (highest-scoring) loop pad per musical category — a port
   * of kit.js pickInstantGroove. Returns an array of padIdx. Pure. */
  function pickInstantGroove(pads) {
    var targets = ["DRUMS", "BASS", "CHORDS", "LEAD", "RHYTHM", "TEXTURE"];
    var best = {};
    (pads || []).forEach(function (p) {
      if (!p || typeof p.padIdx !== "number") return;
      var cat = String(p.category || "").toUpperCase();
      if (targets.indexOf(cat) === -1) return;
      var score =
        p.performanceScore != null
          ? p.performanceScore
          : p.loopScore != null
          ? p.loopScore
          : 0;
      if (!(cat in best) || score > best[cat].score)
        best[cat] = { padIdx: p.padIdx, score: score };
    });
    var out = [];
    targets.forEach(function (cat) {
      if (cat in best) out.push(best[cat].padIdx);
    });
    return out;
  }

  /** Device-status line text from a JamnKitHW.status() result. Accepts a
   * string, {connected,name}, or null. Pure. */
  function deviceStatusLabel(status) {
    if (!status) return "No device";
    if (typeof status === "string") return status || "No device";
    if (status.name && (status.connected == null || status.connected))
      return status.name;
    if (status.connected) return "Connected";
    return "No device";
  }

  // ------------------------------------------------------------------ DOM

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function iconBtn(cls, glyph, title, onClick) {
    var b = el("button", "lpv-icon " + (cls || ""), glyph);
    b.type = "button";
    if (title) b.title = title;
    b.setAttribute("aria-label", title || glyph);
    if (onClick) b.addEventListener("click", onClick);
    return b;
  }

  function can(obj, method) {
    return !!(obj && typeof obj[method] === "function");
  }

  // ---------------------------------------------------------------- module

  var current = null; // the one live mount (singleton, like kit.js)

  function mount(container, ctx) {
    if (current) unmount();
    if (!container) return;
    ctx = ctx || {};
    var entry = ctx.entry || null;

    var s = {
      root: container,
      ctx: ctx,
      entry: entry,
      alive: true,
      engine: ctx.engine || null,
      pads: ctx.pads || null,
      padMeta: {}, // padIdx → meta dict (color/label/category/…)
      chopBuffers: {}, // padIdx → sliced AudioBuffer (move-mode swap cache)
      stepFlags: null, // padIdx → [bool] from defaultSequence
      tiles: [], // padIdx → { el }
      // control state
      mode: "tap", // "tap" | "loop"
      lock: true, // Lock = latch / start-on-shared-cycle
      augment: false,
      quantize: "bar",
      stem: "mix",
      sliceMode: "beat",
      kind: "auto",
      view: "grid", // "grid" | "layers"
      ui: {}, // padIdx → "idle" | "armed" | "playing"
      moveMode: false,
      moveSel: null, // first-selected padIdx in a swap
      busy: false,
      raf: 0,
      els: {}, // named control elements
      _contribMounted: false,
    };
    current = s;

    container.textContent = "";
    container.classList.add("lpv-root");

    buildShell(s);
    ensureEngine(s);
    startRaf(s);
    // Connect the physical Launchpad (triggers Chrome's MIDI permission
    // prompt). Independent of engine readiness — the grid fills via
    // renderGrid once pads load; the status pill updates immediately.
    attachHardware(s);

    // Re-fit the coarse waveforms when the tile size changes (canvas backing
    // store is dpr-scaled off clientWidth/Height, so a resize needs a repaint).
    s.onResize = function () {
      drawPadWaves(s);
    };
    window.addEventListener("resize", s.onResize);
  }

  function unmount() {
    var s = current;
    current = null;
    if (!s) return;
    s.alive = false;
    detachHardware(s); // release the physical device (hands LEDs back to the driver)
    if (s.raf) cancelAnimationFrame(s.raf);
    if (s.onResize) {
      window.removeEventListener("resize", s.onResize);
      s.onResize = null;
    }
    try {
      if (can(s.engine, "stopAll")) s.engine.stopAll();
    } catch (_) {}
    try {
      if (window.JamnContribute && s._contribMounted) window.JamnContribute.unmount();
    } catch (_) {}
    try {
      s.root.classList.remove("lpv-root", "is-move");
      s.root.innerHTML = "";
    } catch (_) {}
  }

  // --------------------------------------------------- engine acquisition

  /** Reuse the kit's PadEngine. If nothing is loaded yet, mount the kit for
   * this song (kit.js owns #kit-root; the pane needn't be visible) and poll
   * until the engine + pads are ready. */
  function ensureEngine(s) {
    var K = window.JamnKit;
    if (!s.engine && can(K, "engine")) s.engine = K.engine();
    if (!s.pads && can(K, "pads")) s.pads = K.pads();

    var haveEngine = !!s.engine;
    var havePads = s.pads && s.pads.length;
    if (haveEngine && havePads) {
      onEngineReady(s);
      return;
    }

    if (!s.entry || !s.entry.id) {
      setStatus(s, "Load a song to open the Launchpad.");
      return;
    }
    if (!can(K, "mount")) {
      setStatus(s, "Kit module missing — pads unavailable.");
      return;
    }

    setStatus(s, "Loading pads…");
    try {
      // Only (re)mount if the kit isn't already loaded.
      var mountedKind = can(K, "kind") ? K.kind() : null;
      if (!haveEngine || mountedKind == null) {
        K.mount(s.entry, s.kind !== "auto" ? { kind: s.kind } : {});
      }
    } catch (e) {
      setStatus(s, "Kit failed to load.");
      return;
    }
    pollEngine(s, 0);
  }

  function pollEngine(s, tries) {
    if (!s.alive) return;
    var K = window.JamnKit;
    var engine = can(K, "engine") ? K.engine() : null;
    var pads = can(K, "pads") ? K.pads() : null;
    if (engine && pads && pads.length) {
      s.engine = engine;
      s.pads = pads;
      onEngineReady(s);
      return;
    }
    if (tries > 120) {
      // ~12s at 100ms — give up gracefully.
      setStatus(s, "Pads didn’t load. Open Jam Pads, then reopen the Launchpad.");
      return;
    }
    setTimeout(function () {
      pollEngine(s, tries + 1);
    }, 100);
  }

  function onEngineReady(s) {
    if (!s.alive) return;
    setStatus(s, "");
    buildPadMetaFromKit(s);
    fetchKitSequence(s);
    renderGrid(s);
    refreshDeviceStatus(s);
  }

  /** Seed pad meta from the loaded kit's server pad dicts. */
  function buildPadMetaFromKit(s) {
    s.padMeta = {};
    s.chopBuffers = {};
    (s.pads || []).forEach(function (p) {
      if (!p || typeof p.padIdx !== "number") return;
      s.padMeta[p.padIdx] = {
        name: p.name || null,
        colorHint: p.colorHint,
        category:
          p.category ||
          categoryFor((p.stemSlice && p.stemSlice.stemRole) || "", p.contentType),
        contentType: p.contentType || null,
        sectionLabel: p.sectionLabel || null,
        chordSymbol: p.chordSymbol || null,
        kind: p.kind || null,
      };
    });
  }

  /** Best-effort fetch of the kit JSON for defaultSequence step-dots (flip
   * kits only). Failure just omits the dots. */
  function fetchKitSequence(s) {
    if (!s.entry || !s.entry.id) return;
    var kindQ = s.kind && s.kind !== "auto" ? "&kind=" + encodeURIComponent(s.kind) : "";
    fetch("/api/song/" + encodeURIComponent(s.entry.id) + "/kit?pads=16" + kindQ)
      .then(function (r) {
        return r.ok ? r.json() : null;
      })
      .then(function (kit) {
        if (!s.alive || !kit) return;
        s.stepFlags = sequenceStepFlags(kit);
        if (s.stepFlags) renderGrid(s);
      })
      .catch(function () {});
  }

  // --------------------------------------------------------------- shell

  function buildShell(s) {
    var root = s.root;

    // ---- Header row: title, move / reset, device status, close ----
    var header = el("div", "lpv-header");
    header.appendChild(el("span", "lpv-title", "Launchpad"));
    header.appendChild(
      iconBtn("", "✥", "Move mode: click two pads to swap their sounds", function () {
        s.moveMode = !s.moveMode;
        s.moveSel = null;
        root.classList.toggle("is-move", s.moveMode);
        this.classList.toggle("is-on", s.moveMode);
        renderGrid(s);
      })
    );
    header.appendChild(
      iconBtn("", "↺", "Reset layout — reload this song's kit", function () {
        resetLayout(s);
      })
    );
    header.appendChild(el("div", "lpv-spacer"));
    s.els.device = el("span", "lpv-device", "No device");
    header.appendChild(s.els.device);
    header.appendChild(
      iconBtn("lpv-close", "✕", "Close", function () {
        if (typeof s.ctx.onClose === "function") s.ctx.onClose();
      })
    );
    root.appendChild(header);

    // ---- Control row 1 ----
    var row1 = el("div", "lpv-controls");

    // grid / mixer(layers) view toggle
    var viewGrp = el("div", "lpv-seg");
    s.els.viewGrid = segBtn("▦", "Grid view", function () {
      setView(s, "grid");
    });
    s.els.viewMix = segBtn("≡", "Mixer / layers view", function () {
      setView(s, "layers");
    });
    viewGrp.appendChild(s.els.viewGrid);
    viewGrp.appendChild(s.els.viewMix);
    row1.appendChild(viewGrp);

    // Tap | Loop
    var playGrp = el("div", "lpv-seg");
    s.els.tapBtn = segBtn("Tap", "One-shot: plays through on tap", function () {
      setMode(s, "tap");
    });
    s.els.loopBtn = segBtn("Loop", "Loop while held (or latched with Lock)", function () {
      setMode(s, "loop");
    });
    playGrp.appendChild(s.els.tapBtn);
    playGrp.appendChild(s.els.loopBtn);
    row1.appendChild(playGrp);

    // Lock (= latch)
    s.els.lockBtn = toggleBtn(
      "🔒 Lock",
      "Lock ON: loops latch + start on the shared cycle. OFF: start immediately.",
      function () {
        s.lock = !s.lock;
        s.els.lockBtn.classList.toggle("is-on", s.lock);
      }
    );
    row1.appendChild(s.els.lockBtn);

    // Augment (advisory — see report; web engine auto-ducks per pad stem)
    s.els.augBtn = toggleBtn(
      "⇄ Augment",
      "Augment: sample takes over its source stem while playing (automatic per-pad on web).",
      function () {
        s.augment = !s.augment;
        s.els.augBtn.classList.toggle("is-on", s.augment);
      }
    );
    row1.appendChild(s.els.augBtn);

    // Quantize
    row1.appendChild(
      labeledSelect("Quantize", QUANTIZE, s.quantize, function (v) {
        s.quantize = v;
      })
    );

    root.appendChild(row1);

    // ---- Control row 2 ----
    var row2 = el("div", "lpv-controls");

    row2.appendChild(
      labeledSelect(
        "Stem",
        STEMS.map(function (x) {
          return [x, capitalize(x)];
        }),
        s.stem,
        function (v) {
          s.stem = v;
        }
      )
    );
    row2.appendChild(
      labeledSelect(
        "Slices",
        SLICE_MODES.map(function (x) {
          return [x, capitalize(x)];
        }),
        s.sliceMode,
        function (v) {
          s.sliceMode = v;
        }
      )
    );
    s.els.loadBtn = el("button", "lpv-btn", "Load");
    s.els.loadBtn.type = "button";
    s.els.loadBtn.title = "Load chops from the chosen stem + slice mode";
    s.els.loadBtn.addEventListener("click", function () {
      loadChops(s);
    });
    row2.appendChild(s.els.loadBtn);

    row2.appendChild(el("div", "lpv-spacer"));

    var autoBtn = el("button", "lpv-btn", "✨ Auto Kit");
    autoBtn.type = "button";
    autoBtn.title = "Load the auto-built kit for this song";
    autoBtn.addEventListener("click", function () {
      reloadKind(s, "auto");
    });
    row2.appendChild(autoBtn);

    var drumBtn = el("button", "lpv-btn", "▦ Drum Kit");
    drumBtn.type = "button";
    drumBtn.title = "The song's own kick / snare / hats + grooves on the pads";
    drumBtn.addEventListener("click", function () {
      reloadKind(s, "drums");
    });
    row2.appendChild(drumBtn);

    s.els.linkChip = el("span", "lpv-chip", "🔗 Link");
    s.els.linkChip.title = "Ableton Link status (hardware / Link owned elsewhere on web).";
    row2.appendChild(s.els.linkChip);

    var grooveBtn = el("button", "lpv-btn lpv-btn--accent", "⚡ Groove");
    grooveBtn.type = "button";
    grooveBtn.title =
      "Instant Groove — humanize the sequencer, or fire the best loop per category";
    grooveBtn.addEventListener("click", function () {
      groove(s);
    });
    row2.appendChild(grooveBtn);

    row2.appendChild(
      labeledSelect("Beat", BEAT_KINDS, s.kind, function (v) {
        reloadKind(s, v);
      })
    );

    root.appendChild(row2);

    // ---- Status + grid + layers + overlay hosts ----
    s.els.status = el("div", "lpv-status");
    s.els.status.hidden = true;
    root.appendChild(s.els.status);

    s.els.grid = el("div", "lpv-grid");
    s.els.grid.setAttribute("role", "grid");
    s.els.grid.setAttribute("aria-label", "Launchpad 8×8 grid");
    root.appendChild(s.els.grid);

    s.els.layers = el("div", "lpv-layers");
    s.els.layers.hidden = true;
    root.appendChild(s.els.layers);

    s.els.overlay = el("div", "lpv-overlay");
    s.els.overlay.hidden = true;
    root.appendChild(s.els.overlay);

    syncControlUi(s);
  }

  function segBtn(label, title, onClick) {
    var b = el("button", "lpv-seg-btn", label);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function toggleBtn(label, title, onClick) {
    var b = el("button", "lpv-toggle", label);
    b.type = "button";
    if (title) b.title = title;
    b.addEventListener("click", onClick);
    return b;
  }

  function labeledSelect(caption, options, value, onChange) {
    var wrap = el("label", "lpv-select");
    wrap.appendChild(el("span", "lpv-select-cap", caption));
    var sel = el("select");
    options.forEach(function (opt) {
      var o = document.createElement("option");
      o.value = opt[0];
      o.textContent = opt[1];
      if (opt[0] === value) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () {
      onChange(sel.value);
    });
    wrap.appendChild(sel);
    return wrap;
  }

  function setView(s, view) {
    s.view = view;
    s.els.grid.hidden = view !== "grid";
    s.els.layers.hidden = view !== "layers";
    s.els.viewGrid.classList.toggle("is-on", view === "grid");
    s.els.viewMix.classList.toggle("is-on", view === "layers");
    if (view === "layers") renderLayers(s);
  }

  function setMode(s, mode) {
    s.mode = mode;
    s.els.tapBtn.classList.toggle("is-on", mode === "tap");
    s.els.loopBtn.classList.toggle("is-on", mode === "loop");
  }

  function syncControlUi(s) {
    setMode(s, s.mode);
    setView(s, s.view);
    s.els.lockBtn.classList.toggle("is-on", s.lock);
    s.els.augBtn.classList.toggle("is-on", s.augment);
  }

  function setStatus(s, msg) {
    if (!s.els.status) return;
    s.els.status.textContent = msg || "";
    s.els.status.hidden = !msg;
  }

  // ---------------------------------------------------------------- grid

  function renderGrid(s) {
    var grid = s.els.grid;
    if (!grid) return;
    grid.textContent = "";
    s.tiles = [];
    for (var idx = 0; idx < CAPACITY; idx++) {
      grid.appendChild(buildTile(s, idx));
    }
    // Waveforms need laid-out canvas sizes — draw on the next frame (kit.js
    // does the same after its grid build). renderGrid is the single funnel
    // for every repopulate (Load / Auto Kit / Drum Kit / Stem / Slices),
    // so this one hook keeps the silhouettes current across all of them.
    requestAnimationFrame(function () {
      drawPadWaves(s);
    });
    // Mirror the freshly-rendered grid onto the physical Launchpad LEDs.
    // renderGrid is the single funnel for every repopulate (Load / Auto
    // Kit / Drum Kit / Stem / Slices / move / voice-bake), so this one
    // hook keeps hardware and screen in lockstep across all of them.
    repaintHardware(s);
  }

  /** Paint each real pad's baked-buffer waveform via the shared kit renderer
   * (JamnKit.drawPadWave) so Launchpad tiles match Jam Pads exactly instead
   * of duplicating the DSP. bins = -6 is kit's px-per-bin sentinel for the
   * compact 64-grid (coarse silhouette, not noise). */
  function drawPadWaves(s) {
    if (!s.alive) return;
    var K = window.JamnKit;
    if (!can(K, "drawPadWave")) return;
    for (var idx = 0; idx < CAPACITY; idx++) {
      var t = s.tiles[idx];
      var meta = s.padMeta[idx];
      if (!t || !t.wave || !meta) continue;
      try {
        K.drawPadWave(t.wave, idx, padFill(meta), -6);
      } catch (_) {}
    }
  }

  function buildTile(s, idx) {
    var isVoice = idx === 0;
    var meta = s.padMeta[idx] || null;
    var seqFlags = s.stepFlags && s.stepFlags[idx];
    var hasContent = !!meta || isVoice || !!seqFlags;

    var tile = el("div", "lpv-pad");
    tile.setAttribute("role", "gridcell");
    tile.dataset.idx = String(idx);

    var tileMeta = meta;
    if (isVoice && !meta) tileMeta = { voice: true };
    if (seqFlags && !meta) tileMeta = { sequence: true };

    tile.style.setProperty(
      "--lpv-pad",
      hasContent ? padFill(tileMeta) : "var(--lpv-empty, rgba(255,255,255,0.05))"
    );
    if (!hasContent) tile.classList.add("is-empty");
    if (isVoice) tile.classList.add("is-voice");

    // Baked-buffer waveform (parity with Jam Pads / kit.js). Only real pads
    // carry a resident buffer — the voice placeholder and pure step-dot
    // tiles have no audio to draw. Appended first so it sits UNDER the glyph
    // and label; painted on the next frame once the canvas has a size.
    var wave = null;
    if (meta) {
      wave = el("canvas", "lp-pad-wave");
      tile.appendChild(wave);
    }

    // glyph + step-dots + label
    if (isVoice && !meta) {
      tile.appendChild(el("div", "lpv-pad-glyph", "🎤"));
    } else if (seqFlags) {
      tile.appendChild(buildStepDots(seqFlags));
    }
    var label = padLabel(tileMeta);
    if (label) tile.appendChild(el("div", "lpv-pad-label", label));

    // interaction
    tile.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      onPadDown(s, idx);
    });
    tile.addEventListener("pointerup", function () {
      onPadUp(s, idx);
    });
    tile.addEventListener("pointercancel", function () {
      onPadUp(s, idx);
    });
    tile.addEventListener("pointerleave", function () {
      onPadUp(s, idx);
    });

    s.tiles[idx] = { el: tile, wave: wave };
    applyUi(s, idx);
    return tile;
  }

  function buildStepDots(flags) {
    var layout = sequenceStepDots(flags, COLS);
    var wrap = el("div", "lpv-dots");
    wrap.style.gridTemplateColumns = "repeat(" + layout.cols + ", 1fr)";
    layout.cells.forEach(function (c) {
      wrap.appendChild(el("div", "lpv-dot" + (c.on ? " is-on" : "")));
    });
    return wrap;
  }

  function onPadDown(s, idx) {
    if (s.moveMode) {
      handleMoveClick(s, idx);
      return;
    }
    var isVoice = idx === 0;
    var hasContent = !!s.padMeta[idx];
    if (isVoice && !hasContent) {
      openVoiceCapture(s, idx);
      return;
    }
    if (!hasContent) return;
    if (!can(s.engine, "trigger")) return;

    var opts = buildTriggerOpts(s);
    if (!opts.loop) {
      s.engine.trigger(idx, opts);
      applyUi(s, idx, "playing");
      return;
    }
    // Loop path — Lock(=latch) makes a second tap toggle it off.
    if (s.lock && (s.ui[idx] === "armed" || s.ui[idx] === "playing")) {
      if (can(s.engine, "release")) s.engine.release(idx);
      applyUi(s, idx, "idle");
      return;
    }
    var res = s.engine.trigger(idx, opts);
    var armed = opts.quantized || (res && res.deferred);
    applyUi(s, idx, armed ? "armed" : "playing");
  }

  function onPadUp(s, idx) {
    if (s.moveMode) return;
    if (!s.padMeta[idx]) return;
    // Latched / tap = fire-and-forget; only hold-to-loop stops on release.
    if (s.lock || s.mode !== "loop") return;
    if (can(s.engine, "release")) s.engine.release(idx);
    applyUi(s, idx, "idle");
  }

  function applyUi(s, idx, ui) {
    if (ui != null) s.ui[idx] = ui;
    var t = s.tiles[idx];
    if (!t || !t.el) return;
    t.el.classList.toggle("is-armed", s.ui[idx] === "armed");
    t.el.classList.toggle("is-playing", s.ui[idx] === "playing");
    if (s.moveMode) t.el.classList.toggle("is-sel", s.moveSel === idx);
  }

  // ---------- progress animation (arm→play promotion + playhead) ----------

  function startRaf(s) {
    var tick = function () {
      if (!s.alive) return;
      if (can(s.engine, "padProgress")) {
        for (var idx = 0; idx < CAPACITY; idx++) {
          var ui = s.ui[idx];
          if (ui !== "armed" && ui !== "playing") continue;
          var p = safeProgress(s, idx);
          if (ui === "armed" && p != null && p > 0) applyUi(s, idx, "playing");
          if (ui === "playing" && s.mode !== "loop" && p == null) applyUi(s, idx, "idle");
          var t = s.tiles[idx];
          if (t && t.el && p != null)
            t.el.style.setProperty("--lpv-prog", (p * 100).toFixed(1) + "%");
        }
      }
      s.raf = requestAnimationFrame(tick);
    };
    s.raf = requestAnimationFrame(tick);
  }

  function safeProgress(s, idx) {
    try {
      var v = s.engine.padProgress(idx);
      return typeof v === "number" && isFinite(v) && v >= 0 ? Math.min(v, 1) : null;
    } catch (_) {
      return null;
    }
  }

  // -------------------------------------------------------- move / reset

  function handleMoveClick(s, idx) {
    if (s.moveSel == null) {
      s.moveSel = idx;
      applyUi(s, idx);
      return;
    }
    var a = s.moveSel;
    var b = idx;
    s.moveSel = null;
    if (a === b) {
      applyUi(s, a);
      return;
    }
    swapPads(s, a, b);
    renderGrid(s);
  }

  /** Swap two pads' sources. Works for pads we hold a sliced buffer for
   * (chops / voice); kit-baked pads with no cached buffer can't be re-baked
   * from here, so those swaps are skipped with a note. */
  function swapPads(s, a, b) {
    var bufA = s.chopBuffers[a];
    var bufB = s.chopBuffers[b];
    if (!bufA && !bufB) {
      setStatus(s, "Move works on loaded chops / Voice pads. Load chops first.");
      setTimeout(function () {
        setStatus(s, "");
      }, 2500);
      return;
    }
    if (!can(s.engine, "setPadSource")) return;
    var metaA = s.padMeta[a];
    var metaB = s.padMeta[b];
    if (bufA) s.engine.setPadSource(b, bufA, sourceOpts(metaA));
    if (bufB) s.engine.setPadSource(a, bufB, sourceOpts(metaB));
    var tmpBuf = s.chopBuffers[a];
    s.chopBuffers[a] = s.chopBuffers[b];
    s.chopBuffers[b] = tmpBuf;
    var tmpMeta = s.padMeta[a];
    s.padMeta[a] = s.padMeta[b];
    s.padMeta[b] = tmpMeta;
  }

  function sourceOpts(meta) {
    var o = { loop: true };
    if (meta) {
      if (meta.name != null) o.name = meta.name;
      if (meta.colorHint != null) o.colorHint = meta.colorHint;
    }
    return o;
  }

  function resetLayout(s) {
    try {
      if (can(s.engine, "stopAll")) s.engine.stopAll();
    } catch (_) {}
    s.ui = {};
    reloadKind(s, s.kind);
  }

  // ---------------------------------------------------------- kit reload

  function reloadKind(s, kind) {
    s.kind = kind;
    if (!s.entry || !s.entry.id || !can(window.JamnKit, "mount")) return;
    setStatus(s, "Loading " + kind + " kit…");
    try {
      window.JamnKit.mount(s.entry, kind !== "auto" ? { kind: kind } : {});
    } catch (e) {
      setStatus(s, "Kit failed to load.");
      return;
    }
    s.engine = null;
    s.pads = null;
    pollEngine(s, 0);
  }

  // -------------------------------------------------- chops (Stem/Slices)

  function loadChops(s) {
    if (s.busy) return;
    if (!s.entry || !s.entry.id) {
      setStatus(s, "Load a song first.");
      return;
    }
    if (!can(s.engine, "setPadSource")) {
      setStatus(s, "Pads not ready.");
      return;
    }
    s.busy = true;
    s.els.loadBtn.disabled = true;
    setStatus(s, "Loading " + s.stem + " / " + s.sliceMode + " chops…");

    var url =
      "/api/song/" +
      encodeURIComponent(s.entry.id) +
      "/chops?stem=" +
      encodeURIComponent(s.stem) +
      "&sliceMode=" +
      encodeURIComponent(s.sliceMode);

    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("chops HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!s.alive) return null;
        var chops = (data && data.chops) || [];
        if (!chops.length || !data.stemUrl) {
          throw new Error("no chops for this stem/slice mode");
        }
        return decodeStem(s, data.stemUrl).then(function (stemBuf) {
          applyChops(s, chops, stemBuf, data.stem);
        });
      })
      .catch(function (err) {
        setStatus(s, "Chops failed: " + ((err && err.message) || err));
      })
      .then(function () {
        s.busy = false;
        if (s.els.loadBtn) s.els.loadBtn.disabled = false;
      });
  }

  function decodeStem(s, stemUrl) {
    var ctx = can(window.JamnKit, "audioContext") ? window.JamnKit.audioContext() : null;
    if (!ctx) return Promise.reject(new Error("no audio context"));
    return fetch(stemUrl)
      .then(function (r) {
        if (!r.ok) throw new Error("stem HTTP " + r.status);
        return r.arrayBuffer();
      })
      .then(function (ab) {
        return new Promise(function (resolve, reject) {
          // callback form for Safari; some engines return a promise too.
          var p = ctx.decodeAudioData(ab, resolve, reject);
          if (p && typeof p.then === "function") p.then(resolve, reject);
        });
      });
  }

  function applyChops(s, chops, stemBuf, stem) {
    var ctx = window.JamnKit.audioContext();
    var count = Math.min(chops.length, CAPACITY);
    s.padMeta = {};
    s.chopBuffers = {};
    for (var i = 0; i < count; i++) {
      var chop = chops[i];
      var region = sliceRegion(ctx, stemBuf, chop.startSec, chop.endSec);
      if (!region) continue;
      var cat = categoryFor(stem, chop.contentType);
      var meta = {
        name: chop.sectionLabel || chop.chordSymbol || chop.kind || null,
        colorHint: chop.colorHint,
        category: cat,
        contentType: chop.contentType || null,
        sectionLabel: chop.sectionLabel || null,
        chordSymbol: chop.chordSymbol || null,
        kind: chop.kind || null,
      };
      s.padMeta[i] = meta;
      s.chopBuffers[i] = region;
      try {
        s.engine.setPadSource(i, region, sourceOpts(meta));
      } catch (_) {}
    }
    setStatus(s, "");
    s.ui = {};
    renderGrid(s);
  }

  /** Slice [startSec, endSec) out of a decoded stem into a fresh AudioBuffer
   * on the engine's context. Returns null on a degenerate region. */
  function sliceRegion(ctx, stemBuf, startSec, endSec) {
    if (!stemBuf) return null;
    var sr = stemBuf.sampleRate;
    var start = Math.max(0, Math.floor((startSec || 0) * sr));
    var end = Math.min(stemBuf.length, Math.floor((endSec || 0) * sr));
    var frames = end - start;
    if (frames <= 0) return null;
    var ch = stemBuf.numberOfChannels;
    var out = ctx.createBuffer(ch, frames, sr);
    for (var c = 0; c < ch; c++) {
      var src = stemBuf.getChannelData(c).subarray(start, end);
      if (out.copyToChannel) out.copyToChannel(src, c, 0);
      else out.getChannelData(c).set(src);
    }
    return out;
  }

  // ----------------------------------------------------------- groove ⚡

  function groove(s) {
    // Preferred: humanize the sequencer if it's mounted.
    if (can(window.JamnSequencer, "setGrooveOffsets") && s.entry && s.entry.id) {
      fetch("/api/song/" + encodeURIComponent(s.entry.id) + "/groove")
        .then(function (r) {
          return r.ok ? r.json() : null;
        })
        .then(function (data) {
          if (data && data.groove) {
            window.JamnSequencer.setGrooveOffsets(data.groove);
            setStatus(s, "Groove applied to the sequencer.");
            setTimeout(function () {
              setStatus(s, "");
            }, 2000);
          } else {
            instantGroove(s);
          }
        })
        .catch(function () {
          instantGroove(s);
        });
      return;
    }
    instantGroove(s);
  }

  /** Fire the best loop of each category, bar-synced — native "Groove" ⚡. */
  function instantGroove(s) {
    if (!can(s.engine, "trigger")) return;
    setMode(s, "loop");
    var padList = (s.pads || []).map(function (p) {
      var m = s.padMeta[p.padIdx];
      return {
        padIdx: p.padIdx,
        category: m ? m.category : p.category,
        performanceScore: p.performanceScore,
        loopScore: p.loopScore,
      };
    });
    var grid = s.quantize === "off" ? "bar" : s.quantize;
    pickInstantGroove(padList).forEach(function (idx) {
      if (s.ui[idx] === "playing" || s.ui[idx] === "armed") return;
      try {
        s.engine.trigger(idx, { loop: true, quantized: true, grid: grid });
        applyUi(s, idx, "armed");
      } catch (_) {}
    });
  }

  // ----------------------------------------------------------- layers view

  function renderLayers(s) {
    var host = s.els.layers;
    if (!host) return;
    host.textContent = "";
    var byCat = {};
    Object.keys(s.padMeta).forEach(function (k) {
      var m = s.padMeta[k];
      var cat = (m && m.category) || "SAMPLE";
      (byCat[cat] = byCat[cat] || []).push(parseInt(k, 10));
    });
    var cats = Object.keys(byCat);
    if (!cats.length) {
      host.appendChild(el("div", "lpv-status", "No pads loaded yet."));
      return;
    }
    cats.forEach(function (cat) {
      var row = el("div", "lpv-layer-row");
      var sw = el("span", "lpv-layer-swatch");
      sw.style.background = categoryColor(cat) || hexToCss(DEFAULT_HEX);
      row.appendChild(sw);
      row.appendChild(el("span", "lpv-layer-name", capitalize(cat.toLowerCase())));
      row.appendChild(el("span", "lpv-layer-count", byCat[cat].length + " pads"));
      var play = el("button", "lpv-btn", "▶ Play");
      play.type = "button";
      play.addEventListener("click", function () {
        var idxs = byCat[cat];
        if (!idxs.length || !can(s.engine, "trigger")) return;
        var grid = s.quantize === "off" ? "bar" : s.quantize;
        s.engine.trigger(idxs[0], { loop: true, quantized: true, grid: grid });
        applyUi(s, idxs[0], "armed");
      });
      row.appendChild(play);
      host.appendChild(row);
    });
  }

  // ------------------------------------------------------- voice capture

  function openVoiceCapture(s, idx) {
    if (typeof s.ctx.onOpenContribute === "function") {
      // Let the host route to its Contribute pane if it prefers.
      s.ctx.onOpenContribute("voice", function (sample) {
        bakeVoiceSample(s, idx, sample);
      });
      return;
    }
    if (!can(window.JamnContribute, "mount")) {
      setStatus(s, "Contribute module unavailable.");
      return;
    }
    var ov = s.els.overlay;
    ov.hidden = false;
    ov.textContent = "";
    var sheet = el("div", "lpv-sheet");
    var bar = el("div", "lpv-sheet-bar");
    bar.appendChild(el("span", "lpv-sheet-title", "Voice → Pad"));
    bar.appendChild(el("div", "lpv-spacer"));
    bar.appendChild(
      iconBtn("lpv-close", "✕", "Close", function () {
        closeVoiceCapture(s);
      })
    );
    sheet.appendChild(bar);
    var host = el("div", "lpv-sheet-body");
    sheet.appendChild(host);
    ov.appendChild(sheet);

    var ctx = can(window.JamnKit, "audioContext") ? window.JamnKit.audioContext() : undefined;
    try {
      window.JamnContribute.mount(host, {
        audioContext: ctx,
        entry: s.entry,
        onSampleReady: function (sample) {
          bakeVoiceSample(s, idx, sample);
          closeVoiceCapture(s);
        },
      });
      s._contribMounted = true;
    } catch (e) {
      setStatus(s, "Voice capture failed to open.");
      closeVoiceCapture(s);
    }
  }

  function closeVoiceCapture(s) {
    try {
      if (window.JamnContribute && s._contribMounted) window.JamnContribute.unmount();
    } catch (_) {}
    s._contribMounted = false;
    if (s.els.overlay) {
      s.els.overlay.hidden = true;
      s.els.overlay.textContent = "";
    }
  }

  function bakeVoiceSample(s, idx, sample) {
    if (!sample || !sample.buffer || !can(s.engine, "setPadSource")) return;
    var meta = { voice: true, name: sample.name || "Voice", colorHint: VOICE_HEX };
    s.padMeta[idx] = meta;
    s.chopBuffers[idx] = sample.buffer;
    try {
      s.engine.setPadSource(idx, sample.buffer, {
        loop: true,
        name: meta.name,
        colorHint: VOICE_HEX,
      });
    } catch (_) {}
    renderGrid(s);
  }

  // ----------------------------------------------------- device status

  function refreshDeviceStatus(s) {
    var label = "No device";
    try {
      // Read our OWN hardware bridge (lp-hw.js), not JamnKitHW: KitHW
      // mirrors the Jam Pads 4×4 and is detached on this surface, so it
      // always reported "No device" here. lp-hw owns the physical device
      // while the Launchpad view is mounted.
      if (can(window.JamnLpHW, "status")) {
        var st = window.JamnLpHW.status();
        label = deviceStatusLabel(st ? { connected: st.connected, name: st.device } : null);
      }
    } catch (_) {}
    if (s.els.device) s.els.device.textContent = label;
  }

  // ------------------------------------------------- hardware bridge (lp-hw)

  /** RGB (0..255) for the physical pad mirroring lpview idx, or null for
   * an empty pad (LED off). Mirrors buildTile's content test so hardware
   * and screen agree on what's lit and its color (padRgb == padFill). */
  function hwPadColor(s, idx) {
    var isVoice = idx === 0;
    var meta = s.padMeta[idx] || null;
    var seqFlags = s.stepFlags && s.stepFlags[idx];
    var hasContent = !!meta || isVoice || !!seqFlags;
    if (!hasContent) return null;
    var tileMeta = meta;
    if (isVoice && !meta) tileMeta = { voice: true };
    if (seqFlags && !meta) tileMeta = { sequence: true };
    return padRgb(tileMeta);
  }

  /** Connect the physical Launchpad on mount. lp-hw.attach() drives
   * Launchpad.enable() (the permission prompt + Programmer Mode) and
   * routes hardware pad presses back through onPadDown/onPadUp — the
   * exact same trigger path as an on-screen tap. */
  function attachHardware(s) {
    if (!can(window.JamnLpHW, "attach")) return;
    s.hw = true;
    try {
      var r = window.JamnLpHW.attach({
        padColor: function (idx) { return hwPadColor(s, idx); },
        onPress: function (idx) { if (s.alive) onPadDown(s, idx); },
        onRelease: function (idx) { if (s.alive) onPadUp(s, idx); },
        onStatusChange: function () { if (s.alive) refreshDeviceStatus(s); },
      });
      if (r && typeof r.then === "function") {
        r.then(function () { if (s.alive) refreshDeviceStatus(s); });
      }
    } catch (_) {}
  }

  function detachHardware(s) {
    if (!s.hw || !can(window.JamnLpHW, "detach")) return;
    s.hw = false;
    try { window.JamnLpHW.detach(); } catch (_) {}
  }

  /** Repaint every hardware LED from the current grid. Called from
   * renderGrid (the single funnel for every repopulate). */
  function repaintHardware(s) {
    if (!s.hw || !can(window.JamnLpHW, "repaint")) return;
    try { window.JamnLpHW.repaint(); } catch (_) {}
  }

  // ---------------------------------------------------------------- export

  window.JamnLaunchpad = {
    mount: mount,
    unmount: unmount,
    // Pure helpers, exposed for the DOM-free smoke test (kit.js pattern).
    _internals: {
      padIndex: padIndex,
      colorFromHint: colorFromHint,
      categoryColor: categoryColor,
      categoryFor: categoryFor,
      padFill: padFill,
      padLabel: padLabel,
      sequenceStepFlags: sequenceStepFlags,
      sequenceStepDots: sequenceStepDots,
      buildTriggerOpts: buildTriggerOpts,
      pickInstantGroove: pickInstantGroove,
      deviceStatusLabel: deviceStatusLabel,
    },
  };
})();
