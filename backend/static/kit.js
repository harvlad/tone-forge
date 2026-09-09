/* kit.js — web 16-pad Auto Kit surface (mirror of mobile's Jam tab
 * SamplePadGrid4x4). Classic script: defines window.JamnKit = { mount,
 * unmount }. The host page provides <div id="kit-root"> and calls
 * JamnKit.mount(entry) with a full /api/history/{id} object; audio playback
 * is delegated to PadEngine (padengine.js, loaded via dynamic import so this
 * file stays a plain script).
 *
 * UI states per pad: idle → armed (triggered, waiting for the quantize
 * boundary — pulsing border, same "waiting for the beat, not broken
 * silence" fix as mobile) → playing (progress ring + waveform sweep from
 * engine.padProgress at rAF rate) → idle. Every engine call is
 * feature-checked so a partial engine degrades to a quieter UI, never an
 * uncaught throw.
 */
(function () {
  "use strict";

  var PAD_COUNT = 16;
  var ACCENT = { r: 139, g: 92, b: 246 }; // --jamn-accent fallback #8B5CF6

  // ---------- small pure helpers (exposed on _internals for smoke tests) ----------

  /** Stem URLs may be backend-relative (/api/...) or absolute R2 presigned. */
  function resolveStemUrl(u) {
    if (!u || typeof u !== "string") return null;
    if (/^https?:\/\//i.test(u)) return u;
    if (u.charAt(0) === "/") return window.location.origin + u;
    return u;
  }

  /** colorHint arrives as "#RRGGBB" (kit_builder _CATEGORY_HEX); tolerate
   * bare hex or ints. Falls back to the accent so a missing hint still
   * renders a tinted (not dead) pad. */
  function parseColor(hint) {
    var v = null;
    if (typeof hint === "number" && isFinite(hint)) v = hint >>> 0;
    else if (typeof hint === "string") {
      var m = hint.trim().match(/^#?([0-9a-fA-F]{6})$/);
      if (m) v = parseInt(m[1], 16);
    }
    if (v === null) return ACCENT;
    return { r: (v >> 16) & 0xff, g: (v >> 8) & 0xff, b: v & 0xff };
  }

  function rgba(c, a) {
    return "rgba(" + c.r + "," + c.g + "," + c.b + "," + a + ")";
  }

  // Cached 2D context to normalize an arbitrary CSS color (name, rgb(),
  // #hex) to {r,g,b}. Sibling surfaces (Launchpad) pass CSS-string tints
  // from their own palette; kit's own callers already pass {r,g,b}. The
  // browser resolves fillStyle to a canonical "#rrggbb"/"rgb(...)" we read
  // back — so drawPadWave stays a thin wrapper over the exact kit renderer.
  var _tintCtx = null;
  function resolveTint(tint) {
    if (tint && typeof tint.r === "number") return tint;
    if (typeof tint === "number") return parseColor(tint);
    if (typeof tint === "string") {
      try {
        if (!_tintCtx) _tintCtx = document.createElement("canvas").getContext("2d");
        _tintCtx.fillStyle = "#000";
        _tintCtx.fillStyle = tint; // invalid strings leave the prior value
        var v = _tintCtx.fillStyle;
        var m = v.match(/^#([0-9a-f]{6})$/i);
        if (m) {
          var n = parseInt(m[1], 16);
          return { r: (n >> 16) & 0xff, g: (n >> 8) & 0xff, b: n & 0xff };
        }
        m = v.match(/(\d+)[,\s]+(\d+)[,\s]+(\d+)/);
        if (m) return { r: +m[1], g: +m[2], b: +m[3] };
      } catch (_) {}
    }
    return ACCENT;
  }

  function can(obj, method) {
    return !!obj && typeof obj[method] === "function";
  }

  /** Pad-count preference: the page URL's ?pads= wins, else the persisted
   * choice, else 16. Only 16 (4×4) and 64 (8×8) are real layouts — any
   * other value falls back to 16 rather than a broken grid. */
  function resolvePadCount(search, stored) {
    var m = /[?&]pads=(\d+)\b/.exec(search || "");
    var v = m ? parseInt(m[1], 10) : parseInt(stored, 10);
    return v === 64 ? 64 : 16;
  }

  /** Normalize the /api/sample-packs catalog into sound-picker rows.
   * Accepts the raw `{packs:[...]}` response or a bare array; drops
   * entries without a packId. Pure — the picker sheet renders the result.
   * (Mirror of the desktop SoundPickerSheet packList over PacksModel.) */
  function pickerPackList(catalog) {
    var arr = catalog && catalog.packs ? catalog.packs : catalog;
    if (!Array.isArray(arr)) return [];
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var e = arr[i];
      if (!e || !e.packId) continue;
      out.push({
        packId: e.packId,
        name: e.name || e.packId,
        padCount: typeof e.padCount === "number" ? e.padCount : null,
        family: e.family || null,
        paletteHint: e.paletteHint || null,
      });
    }
    return out;
  }

  /** The song's OTHER kit pads, as picker rows (the "This song" tab —
   * swap another slot's sound onto this pad). Excludes the target pad and
   * any pad dict without a numeric padIdx; sorted by padIdx. Pure. */
  function pickerSongPads(pads, excludeIdx) {
    if (!Array.isArray(pads)) return [];
    var out = [];
    for (var i = 0; i < pads.length; i++) {
      var p = pads[i];
      if (!p || typeof p.padIdx !== "number" || p.padIdx === excludeIdx) continue;
      out.push({
        padIdx: p.padIdx,
        name: p.name || "Pad " + (p.padIdx + 1),
        colorHint: p.colorHint || null,
      });
    }
    out.sort(function (a, b) { return a.padIdx - b.padIdx; });
    return out;
  }

  /** A curated pack manifest's pads as picker rows with resolved audio
   * URLs — the same sampleFile/file/sampleUrl/filename → URL resolution
   * mountPack uses. Rows without a usable filename are dropped; sorted by
   * padIdx. Pure (given packId). */
  function packPadRows(manifest, packId) {
    var pads = (manifest && manifest.pads) || [];
    if (!Array.isArray(pads)) return [];
    var out = [];
    for (var i = 0; i < pads.length; i++) {
      var p = pads[i];
      if (!p) continue;
      var idx = typeof p.padIdx === "number" ? p.padIdx : i;
      var fname = p.sampleFile || p.file || p.sampleUrl || p.filename;
      if (!fname) continue;
      var url = /^https?:|^\//.test(fname)
        ? fname
        : "/api/sample-packs/" + encodeURIComponent(packId) +
          "/pads/" + encodeURIComponent(fname);
      out.push({
        padIdx: idx,
        name: p.name || "Pad " + (idx + 1),
        colorHint: p.colorHint || null,
        loopable: !!p.loopable,
        url: url,
      });
    }
    out.sort(function (a, b) { return a.padIdx - b.padIdx; });
    return out;
  }

  /** Score used everywhere ranking pads (native `??` chain:
   * performanceScore ?? loopScore ?? 0). */
  function padScore(p) {
    return p.performanceScore != null ? p.performanceScore : p.loopScore != null ? p.loopScore : 0;
  }

  // Layer-row order — the full category set the kit builder emits, in its
  // grid grouping order (desktop LayerStackView shows a subset; the web rack
  // shows every category actually present).
  var LAYER_ORDER = ["DRUMS", "BASS", "CHORDS", "LEAD", "RHYTHM", "TEXTURE", "VOCAL"];

  /** Categories present in the kit, in fixed LAYER_ORDER. */
  function layerCategories(pads) {
    var present = {};
    (pads || []).forEach(function (p) {
      if (p && typeof p.padIdx === "number")
        present[String(p.category || "").toUpperCase()] = true;
    });
    return LAYER_ORDER.filter(function (c) {
      return !!present[c];
    });
  }

  /** pads(in:) — a category's pads, best performanceScore first (the swap
   * picker for a layer row). Mirrors LaunchpadController.pads(in:). */
  function padsInCategory(pads, category) {
    var cat = String(category || "").toUpperCase();
    return (pads || [])
      .filter(function (p) {
        return (
          p && typeof p.padIdx === "number" && String(p.category || "").toUpperCase() === cat
        );
      })
      .slice()
      .sort(function (a, b) {
        return padScore(b) - padScore(a);
      });
  }

  /** Per-pad sequence step flags from the KIT-LEVEL defaultSequence
   * (kind=flip wire format: tracks[].chopRef.packPad.padIdx +
   * steps[].velocity). Pad dicts themselves carry no step fields
   * server-side — this is the only source. Null when the kit has none. */
  function padStepFlags(kit) {
    var seq = kit && kit.defaultSequence;
    var tracks = seq && Array.isArray(seq.tracks) ? seq.tracks : null;
    if (!tracks) return null;
    var out = {};
    var any = false;
    tracks.forEach(function (t) {
      if (!t || !Array.isArray(t.steps)) return;
      // Frozen wire form is flat {type:'packPad', packId, padIdx};
      // tolerate the legacy nested {packPad:{...}} for old caches.
      var ref = t.chopRef && (t.chopRef.type === "packPad" ? t.chopRef : t.chopRef.packPad);
      var idx = ref && typeof ref.padIdx === "number" ? ref.padIdx : null;
      if (idx === null) return;
      var flags = t.steps.map(function (st) {
        return !!(st && typeof st.velocity === "number" && st.velocity > 0);
      });
      if (out[idx]) {
        // Two tracks on one pad (e.g. kick + ghost layer): a step lights
        // when either track fires.
        for (var i = 0; i < flags.length && i < out[idx].length; i++)
          out[idx][i] = out[idx][i] || flags[i];
      } else out[idx] = flags;
      any = true;
    });
    return any ? out : null;
  }

  /** Feedback queue push — native cap parity (SessionController drops the
   * oldest past 256 so an abandoned tab can't grow unbounded). */
  function pushPadEvent(queue, assetId, kind, cap) {
    if (typeof assetId !== "string" || !assetId) return queue;
    if (kind !== "play" && kind !== "skip") return queue;
    queue.push({ assetId: assetId, kind: kind });
    var max = cap || 256;
    while (queue.length > max) queue.shift();
    return queue;
  }

  /** Radial wheel geometry for `count` satellites. 92 px radius was tuned
   * for the original 4-button wheel; past ~6 the 54 px buttons collide, so
   * the radius grows to keep ≥78 px of arc per button (labels included).
   * `disc` is the unifying ring diameter behind hub + satellites. */
  function radialGeometry(count) {
    var n = Math.max(1, count | 0);
    var radius = Math.max(92, Math.round((78 * n) / (2 * Math.PI)));
    return { radius: radius, disc: (radius + 34) * 2 };
  }

  // ---------- pie-wheel geometry (desktop PadRadialMenu parity) ----------
  //
  // The radial is a ring of equal WEDGES around a solid hub — divider-
  // stroked pie slices, not floating round buttons. These pure builders
  // mirror PadRadialMenu.angles/SegmentShape so the web wheel matches the
  // desktop segment layout. All angles are in DEGREES, SVG space
  // (0° = east, positive = clockwise because SVG y grows downward).

  /** Inner/outer radii + SVG box size for `count` wedges. Desktop is a
   * fixed 120/44; the web ring carries a couple more actions (Stop/Solo/
   * Reset/To Sequence), so the outer radius grows to keep ≥ 66 px of outer
   * arc per wedge — past that, icon+label crowd. */
  function wedgeGeometry(count) {
    var n = Math.max(1, count | 0);
    var inner = 50;
    var outer = Math.max(126, Math.ceil((66 * n) / (2 * Math.PI)));
    return { inner: inner, outer: outer, size: (outer + 6) * 2 };
  }

  /** Boundary + mid angles for wedge `index` of `count` evenly-sized
   * slices. Wedge 0 is CENTERED at the top (12 o'clock); wedges proceed
   * clockwise (PadRadialAction.angles, rotated so index 0 points up). */
  function wedgeAngles(index, count) {
    var n = Math.max(1, count | 0);
    var slice = 360 / n;
    var start = -90 - slice / 2 + index * slice;
    return { start: start, end: start + slice, mid: start + slice / 2 };
  }

  /** Point at radius `r`, angle `deg`, offset from (cx, cy). SVG space. */
  function polarPoint(cx, cy, r, deg) {
    var a = (deg * Math.PI) / 180;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  }

  /** SVG path `d` for one donut wedge (outer arc CW → inner arc CCW,
   * closed) — the pie slice desktop draws with SegmentShape. Pure. */
  function wedgePath(index, count, inner, outer, cx, cy) {
    var ang = wedgeAngles(index, count);
    var large = ang.end - ang.start > 180 ? 1 : 0;
    var oS = polarPoint(cx, cy, outer, ang.start);
    var oE = polarPoint(cx, cy, outer, ang.end);
    var iE = polarPoint(cx, cy, inner, ang.end);
    var iS = polarPoint(cx, cy, inner, ang.start);
    function f(v) { return v.toFixed(2); }
    return (
      "M" + f(oS.x) + "," + f(oS.y) +
      " A" + outer + "," + outer + " 0 " + large + " 1 " + f(oE.x) + "," + f(oE.y) +
      " L" + f(iE.x) + "," + f(iE.y) +
      " A" + inner + "," + inner + " 0 " + large + " 0 " + f(iS.x) + "," + f(iS.y) +
      " Z"
    );
  }

  /** Center point for a wedge's icon/label (mid-angle, mid-radius). */
  function wedgeLabelPoint(index, count, inner, outer, cx, cy) {
    return polarPoint(cx, cy, (inner + outer) / 2, wedgeAngles(index, count).mid);
  }

  /** Parse the persisted per-song FX store (localStorage
   * jamn.padfx.<analysisId>): JSON {padIdx: fxDict}. Value clamping is the
   * engine's job (normalizePadFx at apply time) — this only drops rows that
   * can't possibly be FX (non-numeric keys, non-object values) so a
   * corrupted blob degrades to "fewer pads have FX", never a throw. */
  function parsePadFxStore(json) {
    var out = {};
    var any = false;
    var obj;
    try {
      obj = JSON.parse(json);
    } catch (_) {
      return null;
    }
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return null;
    for (var k in obj) {
      var idx = parseInt(k, 10);
      var fx = obj[k];
      if (!isFinite(idx) || idx < 0 || String(idx) !== String(k)) continue;
      if (!fx || typeof fx !== "object" || Array.isArray(fx)) continue;
      out[idx] = fx;
      any = true;
    }
    return any ? out : null;
  }

  /** Serialize the FX map for localStorage; null when there is nothing to
   * store (caller removes the key instead of writing "{}"). */
  function serializePadFxStore(map) {
    if (!map) return null;
    var any = false;
    for (var k in map) {
      if (map[k]) {
        any = true;
        break;
      }
    }
    return any ? JSON.stringify(map) : null;
  }

  /** Reset-state math (pure): which radial-era overrides a pad carries.
   * `orig` = bake-time region snapshot (chop edit applied), `gated` =
   * preserve-length trim applied, `loopOverride` = radial loop/one-shot
   * force, `fx` = non-neutral pad FX. Drives the Reset button's enabled
   * state and what Reset must undo. */
  function padOverrides(orig, gated, loopOverride, fx) {
    var region = orig != null;
    var gate = gated != null;
    var loop = loopOverride != null;
    var hasFx = fx != null;
    return {
      region: region,
      gate: gate,
      loop: loop,
      fx: hasFx,
      any: region || gate || loop || hasFx,
    };
  }

  // ---------- mount state (one surface at a time) ----------

  var current = null;

  function mount(entry, opts) {
    try {
      var root = document.getElementById("kit-root");
      if (!root) return;
      if (current) unmount();
      current = {
        root: root,
        entry: entry,
        alive: true,
        ctx: null,
        engine: null,
        pads: [], // server pad dicts, index = padIdx
        padEls: [], // { el, ring, sweep, canvas, badge, tint, ui, loop, loopOverride, lp, lpX, lpY }
        mode: "tap", // DEFAULT Tap
        // Quantize grid for triggers. Default "bar" — the engine's lock
        // cycle is bar-ish, and loops always quantized before this control
        // existed; "off" would silently change how existing kits feel.
        quantize: "bar",
        latch: false,
        raf: 0,
        onResize: null,
        stems: null, // role → decoded AudioBuffer (kept for applyPadRegion rebakes)
        dsp: null, // padengine module (pure DSP exports) once imported
        radial: null, // open radial-menu state, or null
        transportTimer: 0,
        engineTransport: null, // transport object handed to engine.setTransport
        engineTransportTimer: 0, // slow re-check: the host can appear post-mount
        kitKind: (opts && opts.kind && opts.kind !== "auto") ? opts.kind : null,
        view: "grid", // "grid" | "layers" (desktop LayerStackView port)
        padCount: 16, // 16 (4×4) or 64 (8×8 compact); resolved below
        layersEl: null,
        layerRows: null, // category → row elements, built by renderLayers
        // Usage feedback (assetId-keyed play/skip events, batched to
        // /api/song/{id}/pad-feedback like the native SessionController).
        fb: { events: [], startedAt: {}, timer: 0 },
        // Radial per-pad state beyond loopOverride:
        origRegions: {}, // padIdx → bake-time region snapshot (radial Reset)
        gated: {}, // padIdx → {startSec, endSec} preserve-length gate applied
        padFx: {}, // padIdx → normalized FX dict (persisted per song)
        fxKey: entry && entry.id ? entry.id : null, // localStorage jamn.padfx.<id>
        fxPop: null, // open FX-editor popover state, or null
        deleted: {}, // padIdx → {pad, token, timer} undo window after Delete
        // Source-swap display snapshot (radial "Add sound"): padIdx →
        // {name, colorHint} of the pad BEFORE its first swap, so Reset can
        // repaint the tile. The engine holds the matching buffer snapshot.
        origSource: {},
        pickerPop: null, // open sound-picker popover state, or null
        pickerPreview: null, // { source, gain, timer } for the hovered preview
      };
      if (!entry || !entry.id || !entry.result) {
        showError(current, "No analysis loaded.");
        return;
      }
      try {
        var stored = window.localStorage ? window.localStorage.getItem("jamn.kit.pads") : null;
        current.padCount = resolvePadCount(
          window.location && window.location.search, stored);
      } catch (_) {}
      renderShell(current);
      // Feedback batches every 20 s (native parity); unmount flushes the
      // remainder via sendBeacon. Failures are silent — best-effort telemetry.
      (function (s) {
        s.fb.timer = setInterval(function () {
          if (s.alive) flushPadFeedback(s);
        }, 20000);
      })(current);
      load(current).catch(function (err) {
        if (current && current.alive) {
          showError(current, "Kit failed to load: " + ((err && err.message) || err));
        }
      });
    } catch (err) {
      // Errors degrade to a message, never an uncaught console throw.
      try {
        if (current) showError(current, "Kit failed to start.");
      } catch (_) {}
    }
  }

  function unmount() {
    var s = current;
    current = null;
    if (!s) return;
    s.alive = false;
    if (s.raf) cancelAnimationFrame(s.raf);
    if (s.transportTimer) clearInterval(s.transportTimer);
    if (s.engineTransportTimer) clearInterval(s.engineTransportTimer);
    if (s.fb && s.fb.timer) clearInterval(s.fb.timer);
    flushPadFeedback(s, true); // last batch rides sendBeacon past teardown
    closeRadial(s);
    closeFxEditor(s);
    closeSoundPicker(s);
    for (var k in s.deleted) {
      if (s.deleted[k] && s.deleted[k].timer) clearTimeout(s.deleted[k].timer);
    }
    for (var i = 0; i < s.padEls.length; i++) {
      if (s.padEls[i] && s.padEls[i].lp) clearTimeout(s.padEls[i].lp);
    }
    if (s.onResize) window.removeEventListener("resize", s.onResize);
    try {
      if (can(s.engine, "stopAll")) s.engine.stopAll();
    } catch (_) {}
    try {
      if (s.ctx && s.ctx.state !== "closed") s.ctx.close();
    } catch (_) {}
    try {
      s.root.innerHTML = "";
    } catch (_) {}
  }

  // ---------- data + audio load ----------

  /** Fetch the kit manifest at the surface's current pad count. The server
   * clamps pads at 16 today (Query le=16) — a 64 ask degrades to 16 in
   * place (grid follows padCount, so the fallback renders 4×4, not a
   * half-empty 8×8) instead of erroring the whole surface. */
  function fetchKitJson(s, entry) {
    var kindQ = s.kitKind ? "&kind=" + encodeURIComponent(s.kitKind) : "";
    var urlFor = function (n) {
      return "/api/song/" + encodeURIComponent(entry.id) + "/kit?pads=" + n + kindQ;
    };
    return fetch(urlFor(s.padCount)).then(function (r) {
      if (r.ok) return r.json();
      if (s.padCount > 16) {
        s.padCount = 16;
        syncPadCountUi(s);
        return fetch(urlFor(16)).then(function (r2) {
          if (!r2.ok) throw new Error("kit HTTP " + r2.status);
          return r2.json();
        });
      }
      throw new Error("kit HTTP " + r.status);
    });
  }

  /** Stream a response body, reporting cumulative bytes received, and
   * resolve the concatenated ArrayBuffer. Falls back to arrayBuffer()
   * where ReadableStream reads aren't available (older Safari). */
  function readBodyWithProgress(r, onBytes) {
    if (!r.body || typeof r.body.getReader !== "function") {
      return r.arrayBuffer();
    }
    var reader = r.body.getReader();
    var chunks = [];
    var received = 0;
    function pump() {
      return reader.read().then(function (step) {
        if (step.done) {
          var out = new Uint8Array(received);
          var off = 0;
          chunks.forEach(function (c) { out.set(c, off); off += c.length; });
          return out.buffer;
        }
        chunks.push(step.value);
        received += step.value.length;
        if (onBytes) onBytes(received);
        return pump();
      });
    }
    return pump();
  }

  /** Fetch + decode one stem role (proxying cross-origin R2 URLs, and
   * retrying Safari's FLAC decode failure via the proxy's WAV transcode).
   * onProgress(role, loadedBytes, totalBytes) fires as the body streams;
   * totalBytes is 0 when the proxy responds chunked without a length.
   * Resolves null on any failure — a single bad stem mutes its pads, not
   * the kit. */
  function fetchStemBuffer(s, entry, paths, role, onProgress) {
    var url = resolveStemUrl(paths[role]);
    // Cross-origin R2 presigned URLs are unreachable from a browser
    // (bucket sends no CORS headers) — stream via the backend proxy.
    if (url && url.indexOf(window.location.origin) !== 0 && /^https?:/i.test(url)) {
      url = window.location.origin + "/api/history/" +
        encodeURIComponent(entry.id) + "/stem-audio/" +
        encodeURIComponent(role);
    }
    if (!url) return Promise.resolve(null);
    var proxied = url.indexOf("/stem-audio/") !== -1;
    return fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error(role + " HTTP " + r.status);
        var total = parseInt(r.headers.get("content-length") || "0", 10) || 0;
        return readBodyWithProgress(r, function (loaded) {
          if (onProgress) onProgress(role, loaded, total);
        });
      })
      .then(function (buf) {
        return s.ctx.decodeAudioData(buf).catch(function (err) {
          if (!proxied) throw err;
          return fetch(url + "?format=wav")
            .then(function (r2) {
              if (!r2.ok) throw err;
              return r2.arrayBuffer();
            })
            .then(function (b2) { return s.ctx.decodeAudioData(b2); });
        });
      })
      .then(function (audio) { return { role: role, buffer: audio }; })
      .catch(function () { return null; });
  }

  function load(s) {
    var entry = s.entry;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return Promise.reject(new Error("Web Audio unsupported"));
    s.ctx = new AC();

    var kitP = fetchKitJson(s, entry);
    var engineP = import("./padengine.js?v=5");

    return kitP.then(function (kit) {
      if (!s.alive) return;
      var pads = (kit && kit.pads) || [];
      if (!pads.length) throw new Error("kit has no pads");
      s.kit = kit;
      s.pads = pads;

      // Flip kits ship a ready-to-play defaultSequence. Stage it into the
      // sequencer store on activation — same semantic point where iOS
      // saves it to SequencerPatternStore (activateSamplePack) — so the
      // beat is armed when the Sequencer pane opens. Web has no headless
      // sequencer clock, so unlike iOS the flip does not auto-start.
      if (kit.defaultSequence && window.JamnSequencer &&
          typeof window.JamnSequencer.stageDefaultSequence === "function") {
        try {
          window.JamnSequencer.stageDefaultSequence(entry.id, kit.defaultSequence);
        } catch (_) {}
      }

      // Only fetch stem roles the pads actually slice; fall back to all.
      var paths = entry.result.stems_paths || {};
      var wanted = {};
      pads.forEach(function (p) {
        var role = p && p.stemSlice && p.stemSlice.stemRole;
        if (role && paths[role]) wanted[role] = true;
      });
      var roles = Object.keys(wanted);
      if (!roles.length) roles = Object.keys(paths);
      if (!roles.length) throw new Error("song has no stems");

      var stemsDone = 0;
      function setStatus(text) {
        if (s.alive && s.statusEl) s.statusEl.textContent = text;
      }

      // Full-length stems through the proxy run 100MB+ on long songs, so
      // a bare "0/4" counter sits frozen for tens of seconds and reads as
      // a hang. Stream the bodies and show aggregate megabytes instead;
      // renders are throttled because 64KB chunks arrive far faster than
      // a status line is worth repainting.
      var loadedByRole = {};
      var totalByRole = {};
      var lastRender = 0;
      function mb(n) { return (n / 1048576).toFixed(1); }
      function renderProgress(force) {
        var now = Date.now();
        if (!force && now - lastRender < 150) return;
        lastRender = now;
        var loaded = 0;
        var total = 0;
        var allKnown = true;
        roles.forEach(function (r) {
          loaded += loadedByRole[r] || 0;
          if (totalByRole[r]) total += totalByRole[r];
          else allKnown = false;
        });
        setStatus("Loading stems " + stemsDone + "/" + roles.length +
          " — " + mb(loaded) +
          (allKnown && total ? " / " + mb(total) : "") + " MB…");
      }
      renderProgress(true);

      return Promise.all(
        roles.map(function (role) {
          return fetchStemBuffer(s, entry, paths, role, function (r, loaded, total) {
            loadedByRole[r] = loaded;
            if (total) totalByRole[r] = total;
            renderProgress();
          }).then(function (d) {
            stemsDone++;
            renderProgress(true);
            return d; // null = a single bad stem mutes its pads, not the kit
          });
        })
      ).then(function (decoded) {
        if (!s.alive) return;
        var stems = {};
        var count = 0;
        decoded.forEach(function (d) {
          if (d) {
            stems[d.role] = d.buffer;
            count++;
          }
        });
        if (!count) throw new Error("no stems could be decoded");
        return engineP.then(function (mod) {
          if (!s.alive) return;
          var PadEngine = mod && (mod.PadEngine || (mod.default && mod.default.PadEngine));
          if (typeof PadEngine !== "function") throw new Error("PadEngine missing");
          s.dsp = mod; // pure DSP exports feed applyPadRegion rebakes
          s.stems = stems;
          s.engine = new PadEngine(s.ctx, s.ctx.destination);
          if (can(s.engine, "setStems")) s.engine.setStems(stems);
          if (can(s.engine, "setKit"))
            s.engine.setKit(s.kit, { tempoBpm: entry.result.tempo_bpm });
          setStatus("Building pads…");
          var prep = can(s.engine, "prepare") ? s.engine.prepare() : null;
          return Promise.resolve(prep).then(function () {
            if (!s.alive) return;
            setStatus("");
            applyStoredFx(s); // persisted per-pad FX ride every fresh bake
            attachEngineState(s);
            // Song-bar quantize while the transport rolls: wire now and
            // re-check on a slow clock (jam.js defines JamnKitHost in its
            // own boot path, which can land after the kit mounts).
            syncEngineTransport(s);
            s.engineTransportTimer = setInterval(function () {
              if (s.alive) syncEngineTransport(s);
            }, 2000);
            renderPads(s);
            startRaf(s);
          });
        });
      });
    });
  }

  // ---------- engine state (defensive: rAF polling is the authority,
  // onstate is a fast-path hint whose exact shape we don't rely on) ----------

  function attachEngineState(s) {
    try {
      s.engine.onstate = function (a, b) {
        try {
          if (typeof a === "number") applyEngineState(s, a, b);
          else if (a && typeof a === "object") {
            if (typeof a.padIdx === "number") applyEngineState(s, a.padIdx, a.state || a.status);
            else if (Array.isArray(a.pads))
              a.pads.forEach(function (p, i) {
                if (p && typeof p === "object")
                  applyEngineState(s, typeof p.padIdx === "number" ? p.padIdx : i, p.state || p.status);
              });
          }
        } catch (_) {}
      };
    } catch (_) {}
  }

  function applyEngineState(s, padIdx, state) {
    var p = s.padEls[padIdx];
    if (!p || typeof state !== "string") return;
    if (/^(armed|pending|queued)$/i.test(state)) setUi(s, padIdx, "armed");
    else if (/^(playing|started|active)$/i.test(state)) setUi(s, padIdx, "playing");
    else if (/^(stopped|idle|ended|released)$/i.test(state)) setUi(s, padIdx, "idle");
  }

  function setUi(s, padIdx, ui) {
    var p = s.padEls[padIdx];
    if (!p || p.ui === ui) return;
    p.ui = ui;
    p.el.classList.toggle("is-armed", ui === "armed");
    p.el.classList.toggle("is-playing", ui === "playing");
    if (ui !== "playing") {
      p.ring.style.background = "none";
      p.sweep.style.width = "0";
    }
    // Layer rows mirror pad state (setUi only fires on transitions, so
    // this is a handful of class/text updates, not per-frame work).
    if (s.layerRows) refreshLayers(s);
  }

  // ---------- rendering ----------

  function renderShell(s) {
    s.root.innerHTML = "";
    s.root.classList.add("kit-surface");

    var head = document.createElement("div");
    head.className = "kit-head";

    var title = document.createElement("div");
    title.className = "kit-title";
    title.textContent = "Auto Kit";
    s.titleEl = title;

    // Live load status ("Loading stems 2/6…") — the bare skeleton read
    // as a dead page during the multi-second stem fetch.
    var status = document.createElement("div");
    status.className = "kit-status";
    status.textContent = "Loading kit…";
    s.statusEl = status;

    var controls = document.createElement("div");
    controls.className = "kit-controls";

    // Grid ⇄ Layers view toggle — Layers is the desktop hand-jam rack
    // (LayerStackView): one active loop per category with a swap picker.
    var viewSeg = document.createElement("div");
    viewSeg.className = "kit-seg kit-viewseg";
    viewSeg.setAttribute("role", "group");
    viewSeg.title = "Grid of pads, or one layer row per category";
    s.viewBtns = {};
    [["grid", "Grid"], ["layers", "Layers"]].forEach(function (pair) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-seg-btn" + (pair[0] === s.view ? " is-on" : "");
      b.textContent = pair[1];
      b.addEventListener("click", function () {
        setView(s, pair[0]);
      });
      s.viewBtns[pair[0]] = b;
      viewSeg.appendChild(b);
    });

    // Pad-count toggle: 16 = the native 4×4 scale, 64 = 8×8 compact.
    var sizeSeg = document.createElement("div");
    sizeSeg.className = "kit-seg kit-sizeseg";
    sizeSeg.setAttribute("role", "group");
    sizeSeg.title = "Pad count";
    s.sizeBtns = {};
    [16, 64].forEach(function (n) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-seg-btn" + (n === s.padCount ? " is-on" : "");
      b.textContent = String(n);
      b.addEventListener("click", function () {
        setPadCount(s, n);
      });
      s.sizeBtns[n] = b;
      sizeSeg.appendChild(b);
    });

    // Tap / Loop segmented toggle (DEFAULT Tap). Buttons kept on state so
    // Instant Groove can flip the mode programmatically (setMode).
    var seg = document.createElement("div");
    seg.className = "kit-seg";
    seg.setAttribute("role", "group");
    s.modeBtns = {};
    ["tap", "loop"].forEach(function (mode) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-seg-btn" + (mode === s.mode ? " is-on" : "");
      b.textContent = mode === "tap" ? "Tap" : "Loop";
      b.addEventListener("click", function () {
        setMode(s, mode);
      });
      s.modeBtns[mode] = b;
      seg.appendChild(b);
    });

    // Quantize selector — Off | Beat | Bar (native QuantizeMode subset the
    // web engine can honor: its lock cycle is bar-ish, Beat==Bar until the
    // engine learns to split; the grid option is plumbed regardless).
    var quant = document.createElement("div");
    quant.className = "kit-seg kit-quant";
    quant.setAttribute("role", "group");
    quant.title = "Quantize — when triggered pads start";
    s.quantBtns = {};
    [["off", "Off"], ["beat", "Beat"], ["bar", "Bar"]].forEach(function (pair) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-seg-btn" + (pair[0] === s.quantize ? " is-on" : "");
      b.textContent = pair[1];
      b.addEventListener("click", function () {
        s.quantize = pair[0];
        for (var k in s.quantBtns) s.quantBtns[k].classList.remove("is-on");
        b.classList.add("is-on");
      });
      s.quantBtns[pair[0]] = b;
      quant.appendChild(b);
    });

    // Instant Groove — one tap starts the single best loop per category,
    // all quantized to the shared cycle (desktop's "Groove" button).
    var groove = document.createElement("button");
    groove.type = "button";
    groove.className = "kit-groove";
    groove.textContent = "⚡ Groove";
    groove.title = "Instant Groove — start the best loop of each category, locked to the grid";
    groove.addEventListener("click", function () {
      try {
        if (s.ctx && s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
        instantGroove(s);
      } catch (_) {}
    });

    // Latch toggle (default OFF; only meaningful in Loop mode).
    var latch = document.createElement("button");
    latch.type = "button";
    latch.className = "kit-toggle is-disabled"; // Tap is the default mode
    latch.textContent = "Latch";
    latch.setAttribute("aria-pressed", "false");
    latch.addEventListener("click", function () {
      s.latch = !s.latch;
      latch.classList.toggle("is-on", s.latch);
      latch.setAttribute("aria-pressed", String(s.latch));
    });
    s.latchEl = latch;

    var stop = document.createElement("button");
    stop.type = "button";
    stop.className = "kit-stop";
    stop.textContent = "Stop All";
    stop.addEventListener("click", function () {
      try {
        if (can(s.engine, "stopAll")) s.engine.stopAll();
      } catch (_) {}
      for (var i = 0; i < s.padEls.length; i++) if (s.padEls[i]) setUi(s, i, "idle");
    });

    controls.appendChild(viewSeg);
    controls.appendChild(sizeSeg);
    controls.appendChild(quant);
    controls.appendChild(seg);
    controls.appendChild(latch);
    controls.appendChild(groove);
    controls.appendChild(stop);
    head.appendChild(title);
    head.appendChild(status);
    head.appendChild(controls);

    // Transport strip: song Play/Pause + time readout (host-provided via
    // window.JamnKitHost, feature-checked — no host hides them) and Kill
    // All, which always works on engine voices even hostless.
    var transport = document.createElement("div");
    transport.className = "kit-transport";

    var play = document.createElement("button");
    play.type = "button";
    play.className = "kit-play";
    play.textContent = "▶ Play";
    play.addEventListener("click", function () {
      var host = getHost();
      try {
        var playing = can(host, "isPlaying") ? !!host.isPlaying() : false;
        if (playing && can(host, "pauseSong")) host.pauseSong();
        else if (!playing && can(host, "playSong")) host.playSong();
      } catch (_) {}
      updateTransport(s);
    });
    s.playEl = play;

    var time = document.createElement("div");
    time.className = "kit-time";
    time.textContent = "0:00 / 0:00";
    s.timeEl = time;

    var tSpacer = document.createElement("div");
    tSpacer.className = "kit-transport-spacer";

    var kill = document.createElement("button");
    kill.type = "button";
    kill.className = "kit-kill";
    kill.textContent = "Kill All";
    kill.title = "Stop every pad voice and the song";
    kill.addEventListener("click", function () {
      killAll(s);
    });

    transport.appendChild(play);
    transport.appendChild(time);
    transport.appendChild(tSpacer);
    transport.appendChild(kill);
    s.transportEl = transport;

    var grid = document.createElement("div");
    grid.className = "kit-grid";
    s.gridEl = grid;

    // Loading skeleton: one shimmer tile per pad while stems fetch + decode.
    for (var i = 0; i < (s.padCount || PAD_COUNT); i++) {
      var sk = document.createElement("div");
      sk.className = "kit-pad is-skeleton";
      grid.appendChild(sk);
    }

    // Layers view container (hidden until the Layers toggle) — rows are
    // built from real pads by renderLayers once the kit loads.
    var layers = document.createElement("div");
    layers.className = "kit-layers";
    layers.style.display = "none";
    s.layersEl = layers;

    s.root.appendChild(head);
    s.root.appendChild(transport);
    s.root.appendChild(grid);
    s.root.appendChild(layers);
    syncPadCountUi(s);

    // Transport polls on its own slow clock (not the pad rAF, which only
    // runs once pads exist) so time/play state stay live from mount.
    updateTransport(s);
    s.transportTimer = setInterval(function () {
      if (s.alive) updateTransport(s);
    }, 250);
  }

  // ---------- transport (host bridge) ----------

  /** Host callbacks, if the embedding page provides them. Every method is
   * feature-checked at call time — a partial host degrades per-control.
   *
   * Separate from JamnKitHost, the OPTIONAL `window.JamnKitHooks` object
   * lets the host own navigation the kit can request but not perform:
   *   openSequencer(padIdx) — switch to the Sequencer surface with the
   *     given pad's track highlighted (radial "Sequence" action). When
   *     absent the kit falls back to `location.hash = '#sequencer'` and
   *     the page router (if any) owns the view switch. */
  function getHost() {
    var h = window.JamnKitHost;
    return h && typeof h === "object" ? h : null;
  }

  function fmtTime(t) {
    if (typeof t !== "number" || !isFinite(t) || t < 0) t = 0;
    var m = Math.floor(t / 60);
    var sec = Math.floor(t % 60);
    return m + ":" + (sec < 10 ? "0" : "") + sec;
  }

  /** Wire the engine's quantize grid to the SONG transport. Without this,
   * quantized loop launches while the song plays snap to the engine's
   * free-run lock grid — anchored at the first loop of the SESSION, up to
   * a full ~8 s cycle away and unrelated to any beat the user hears — the
   * "pads arm but don't fire / fire at wrong times" bug. With it, the
   * engine quantizes to the song's own bars while the song rolls (desktop
   * LaunchpadController behavior) and only free-runs when it's stopped.
   * Everything is feature-checked; closures read the LIVE host each call so
   * a replaced JamnKitHost keeps working without re-wiring. Re-run on a
   * slow clock because the host can appear/disappear after mount. */
  function syncEngineTransport(s) {
    if (!can(s.engine, "setTransport")) return;
    var host = getHost();
    var tempo = s.entry && s.entry.result && s.entry.result.tempo_bpm;
    var usable =
      host && can(host, "isPlaying") && can(host, "getTime") &&
      typeof tempo === "number" && isFinite(tempo) && tempo > 0;
    if (!usable) {
      if (s.engineTransport) {
        s.engineTransport = null;
        try {
          s.engine.setTransport(null);
        } catch (_) {}
      }
      return;
    }
    if (s.engineTransport) return; // already wired; closures track the host
    var t = {
      isPlaying: function () {
        var h = getHost();
        try {
          return !!(h && can(h, "isPlaying") && h.isPlaying());
        } catch (_) {
          return false;
        }
      },
      getSongTime: function () {
        var h = getHost();
        try {
          return h && can(h, "getTime") ? Number(h.getTime()) : NaN;
        } catch (_) {
          return NaN;
        }
      },
      tempoBpm: tempo,
      // The analyzer's loop regions are cut on real downbeats measured from
      // song time 0, so 0 is the bar anchor the pads were baked against.
      barAnchorSongTime: 0,
    };
    try {
      s.engine.setTransport(t);
      s.engineTransport = t;
    } catch (_) {}
  }

  function updateTransport(s) {
    if (!s.transportEl) return;
    var host = getHost();
    var hasPlay = can(host, "isPlaying") && (can(host, "playSong") || can(host, "pauseSong"));
    var hasTime = can(host, "getTime") && can(host, "getDuration");
    s.playEl.style.display = hasPlay ? "" : "none";
    s.timeEl.style.display = hasTime ? "" : "none";
    if (hasPlay) {
      var playing = false;
      try {
        playing = !!host.isPlaying();
      } catch (_) {}
      var label = playing ? "❚❚ Pause" : "▶ Play";
      if (s.playEl.textContent !== label) s.playEl.textContent = label;
      s.playEl.classList.toggle("is-playing", playing);
    }
    if (hasTime) {
      var text = "0:00 / 0:00";
      try {
        text = fmtTime(host.getTime()) + " / " + fmtTime(host.getDuration());
      } catch (_) {}
      if (s.timeEl.textContent !== text) s.timeEl.textContent = text;
    }
  }

  /** Kill All: silence every engine voice, reset pad UI, and ask the host
   * to stop song playback too (host absent → engine-only, still useful). */
  function killAll(s) {
    try {
      if (can(s.engine, "stopAll")) s.engine.stopAll();
    } catch (_) {}
    for (var i = 0; i < s.padEls.length; i++) if (s.padEls[i]) setUi(s, i, "idle");
    var host = getHost();
    try {
      if (can(host, "killAll")) host.killAll();
      else if (can(host, "pauseSong")) host.pauseSong();
    } catch (_) {}
    updateTransport(s);
  }

  // ---------- mode / quantize / instant groove ----------

  function setMode(s, mode) {
    s.mode = mode;
    if (s.modeBtns) {
      for (var k in s.modeBtns) s.modeBtns[k].classList.toggle("is-on", k === mode);
    }
    if (s.latchEl) s.latchEl.classList.toggle("is-disabled", mode !== "loop");
  }

  /** Effective loop behavior for a pad press: the per-pad radial override
   * wins; otherwise the surface mode decides. */
  function effectiveLoop(s, p) {
    return p.loopOverride != null ? p.loopOverride : s.mode === "loop";
  }

  /** Instant Groove picker (pure — mirrors LaunchpadController.instantGroove):
   * the single best pad per core category by performanceScore || loopScore.
   * Returns padIdx values in the fixed category order. */
  function pickInstantGroove(pads) {
    var targets = ["DRUMS", "BASS", "CHORDS", "LEAD", "RHYTHM", "TEXTURE"];
    var best = {};
    (pads || []).forEach(function (p) {
      if (!p || typeof p.padIdx !== "number") return;
      var cat = String(p.category || "").toUpperCase();
      if (targets.indexOf(cat) === -1) return;
      var score =
        p.performanceScore != null ? p.performanceScore : p.loopScore != null ? p.loopScore : 0;
      if (!(cat in best) || score > best[cat].score) best[cat] = { padIdx: p.padIdx, score: score };
    });
    var out = [];
    targets.forEach(function (cat) {
      if (cat in best) out.push(best[cat].padIdx);
    });
    return out;
  }

  function instantGroove(s) {
    if (!can(s.engine, "trigger")) return;
    setMode(s, "loop");
    var grid = s.quantize === "off" ? "bar" : s.quantize;
    pickInstantGroove(s.pads).forEach(function (idx) {
      var p = s.padEls[idx];
      if (!p || p.ui !== "idle") return; // native: skip already-active pads
      p.loop = true;
      try {
        s.engine.trigger(idx, { loop: true, quantized: true, grid: grid });
        noteTrigger(s, idx);
        setUi(s, idx, "armed");
      } catch (_) {}
    });
  }

  // ---------- view toggle (Grid ⇄ Layers) ----------

  function setView(s, view) {
    s.view = view;
    if (s.viewBtns) {
      for (var k in s.viewBtns) s.viewBtns[k].classList.toggle("is-on", k === view);
    }
    if (s.gridEl) s.gridEl.style.display = view === "grid" ? "" : "none";
    if (s.layersEl) s.layersEl.style.display = view === "layers" ? "" : "none";
    if (view === "layers") renderLayers(s);
    else drawAllWaves(s); // grid canvases may have resized while hidden
  }

  // ---------- pad count (16 | 64) ----------

  function syncPadCountUi(s) {
    if (s.sizeBtns) {
      for (var k in s.sizeBtns)
        s.sizeBtns[k].classList.toggle("is-on", Number(k) === s.padCount);
    }
    try {
      s.root.classList.toggle("kit-is-64", s.padCount === 64);
    } catch (_) {}
    if (s.gridEl) s.gridEl.classList.toggle("kit-grid-64", s.padCount === 64);
  }

  function setPadCount(s, n) {
    if (s.padCount === n) return;
    s.padCount = n;
    try {
      if (window.localStorage) window.localStorage.setItem("jamn.kit.pads", String(n));
    } catch (_) {}
    syncPadCountUi(s);
    reloadKit(s);
  }

  /** Refetch the kit at the current pad count and rebuild pads in place.
   * Stems stay decoded — only roles the new pads reference that we don't
   * already hold are fetched. No-op before the initial load finishes (that
   * load reads s.padCount when it builds its URL anyway). */
  function reloadKit(s) {
    if (!s.engine || !s.stems || !s.entry) return;
    try {
      if (can(s.engine, "stopAll")) s.engine.stopAll();
    } catch (_) {}
    s.fb.startedAt = {}; // padIdx keys change meaning across a rebuild
    // Per-pad override snapshots are keyed by padIdx too — a rebuilt kit
    // reassigns those indices, so stale snapshots would restore the wrong
    // pad. FX re-applies from the persisted store after prepare().
    s.origRegions = {};
    s.gated = {};
    for (var dk in s.deleted) {
      if (s.deleted[dk] && s.deleted[dk].timer) clearTimeout(s.deleted[dk].timer);
    }
    s.deleted = {};
    if (s.statusEl) s.statusEl.textContent = "Rebuilding pads…";
    var entry = s.entry;
    fetchKitJson(s, entry)
      .then(function (kit) {
        if (!s.alive) return;
        var pads = (kit && kit.pads) || [];
        if (!pads.length) throw new Error("kit has no pads");
        var paths = entry.result.stems_paths || {};
        var missing = {};
        pads.forEach(function (p) {
          var role = p && p.stemSlice && p.stemSlice.stemRole;
          if (role && paths[role] && !s.stems[role]) missing[role] = true;
        });
        return Promise.all(
          Object.keys(missing).map(function (role) {
            return fetchStemBuffer(s, entry, paths, role);
          })
        ).then(function (decoded) {
          if (!s.alive) return;
          decoded.forEach(function (d) {
            if (d) s.stems[d.role] = d.buffer;
          });
          s.kit = kit;
          s.pads = pads;
          if (can(s.engine, "setStems")) s.engine.setStems(s.stems);
          if (can(s.engine, "setKit"))
            s.engine.setKit(kit, { tempoBpm: entry.result.tempo_bpm });
          var prep = can(s.engine, "prepare") ? s.engine.prepare() : null;
          return Promise.resolve(prep).then(function () {
            if (!s.alive) return;
            if (s.statusEl) s.statusEl.textContent = "";
            applyStoredFx(s);
            renderPads(s);
          });
        });
      })
      .catch(function () {
        if (s.alive && s.statusEl) s.statusEl.textContent = "Kit rebuild failed.";
      });
  }

  // ---------- layers view (desktop LayerStackView port) ----------

  /** The padIdx currently sounding in a category (the active layer), or
   * null. "Sounding" includes armed — a queued layer is already claimed. */
  function activeLayerIdx(s, cat) {
    var members = padsInCategory(s.pads, cat);
    for (var i = 0; i < members.length; i++) {
      var pe = s.padEls[members[i].padIdx];
      if (pe && pe.ui !== "idle") return members[i].padIdx;
    }
    return null;
  }

  /** setLayer: stop whatever loops in the category, start this pad —
   * looping, quantized (category-exclusive, LaunchpadController.setLayer). */
  function setLayer(s, cat, padIdx) {
    if (!can(s.engine, "trigger")) return;
    setMode(s, "loop");
    var members = padsInCategory(s.pads, cat);
    for (var i = 0; i < members.length; i++) {
      var idx = members[i].padIdx;
      if (idx === padIdx) continue;
      var pe = s.padEls[idx];
      if (pe && pe.ui !== "idle") {
        try {
          if (can(s.engine, "release")) s.engine.release(idx);
        } catch (_) {}
        setUi(s, idx, "idle");
      }
    }
    var p = s.padEls[padIdx];
    if (!p || p.ui !== "idle") {
      refreshLayers(s); // already the live layer — nothing to start
      return;
    }
    p.loop = true;
    var grid = s.quantize === "off" ? "bar" : s.quantize;
    try {
      s.engine.trigger(padIdx, { loop: true, quantized: true, grid: grid });
      noteTrigger(s, padIdx);
      setUi(s, padIdx, "armed");
    } catch (_) {}
  }

  /** clearLayer: stop the category's active loop (empty the row). */
  function clearLayer(s, cat) {
    var members = padsInCategory(s.pads, cat);
    for (var i = 0; i < members.length; i++) {
      var idx = members[i].padIdx;
      var pe = s.padEls[idx];
      if (pe && pe.ui !== "idle") {
        try {
          if (can(s.engine, "release")) s.engine.release(idx);
        } catch (_) {}
        setUi(s, idx, "idle");
      }
    }
  }

  /** toggleLayer: stop if active, else start the category's best pad. */
  function toggleLayer(s, cat) {
    if (activeLayerIdx(s, cat) != null) {
      clearLayer(s, cat);
      return;
    }
    var members = padsInCategory(s.pads, cat);
    if (members.length) setLayer(s, cat, members[0].padIdx);
  }

  /** Build the layer rack: one row per category present in the kit —
   * color bar + label, the active pad (name + mini waveform), play/stop,
   * and a swap-chip strip (all category pads, best score first). */
  function renderLayers(s) {
    if (!s.layersEl) return;
    s.layersEl.innerHTML = "";
    s.layerRows = null;
    var cats = layerCategories(s.pads);
    if (!cats.length) {
      var note = document.createElement("div");
      note.className = "kit-error";
      note.textContent = s.pads.length
        ? "This kit has no layered categories."
        : "Load a song to build layers.";
      s.layersEl.appendChild(note);
      return;
    }
    s.layerRows = {};
    cats.forEach(function (cat) {
      var members = padsInCategory(s.pads, cat);
      var row = document.createElement("div");
      row.className = "kit-layer-row";
      var tint = parseColor(members[0] && members[0].colorHint);
      row.style.setProperty("--pad-tint", tint.r + "," + tint.g + "," + tint.b);

      var bar = document.createElement("span");
      bar.className = "kit-layer-bar";

      var label = document.createElement("span");
      label.className = "kit-layer-cat";
      label.textContent = cat;

      var active = document.createElement("div");
      active.className = "kit-layer-active";
      var wave = document.createElement("canvas");
      wave.className = "kit-layer-wave";
      var name = document.createElement("span");
      name.className = "kit-layer-name";
      active.appendChild(wave);
      active.appendChild(name);

      var play = document.createElement("button");
      play.type = "button";
      play.className = "kit-layer-play";
      play.addEventListener("click", function () {
        try {
          if (s.ctx && s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
          toggleLayer(s, cat);
        } catch (_) {}
      });

      var chips = document.createElement("div");
      chips.className = "kit-layer-chips";
      members.forEach(function (pd) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "kit-layer-chip";
        chip.textContent = pd.name || "Pad " + (pd.padIdx + 1);
        chip.title = "Swap the " + cat + " layer to this loop";
        chip.addEventListener("click", function () {
          try {
            if (s.ctx && s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
            setLayer(s, cat, pd.padIdx);
          } catch (_) {}
        });
        chips.appendChild(chip);
      });

      row.appendChild(bar);
      row.appendChild(label);
      row.appendChild(active);
      row.appendChild(play);
      row.appendChild(chips);
      s.layersEl.appendChild(row);
      s.layerRows[cat] = {
        row: row, name: name, wave: wave, play: play, chips: chips,
        members: members, shownIdx: -1,
      };
    });
    refreshLayers(s);
  }

  /** Sync every layer row to current pad state: live highlight, shown pad
   * name/waveform (active layer, else the category's best), play glyph,
   * and the active swap chip. Waveform redraws only when the shown pad
   * changes (and only once it has laid-out size). */
  function refreshLayers(s) {
    if (!s.layerRows) return;
    for (var cat in s.layerRows) {
      var row = s.layerRows[cat];
      var activeIdx = activeLayerIdx(s, cat);
      var shown = activeIdx != null ? padByIdx(s, activeIdx)
        : row.members.length ? row.members[0] : null;
      var live = activeIdx != null;
      var pe = live ? s.padEls[activeIdx] : null;
      row.row.classList.toggle("is-live", live);
      row.row.classList.toggle("is-armed", !!(pe && pe.ui === "armed"));
      row.name.textContent = shown ? shown.name || "Pad " + (shown.padIdx + 1) : "—";
      var glyph = live ? "■" : "▶";
      if (row.play.textContent !== glyph) row.play.textContent = glyph;
      row.play.title = live ? "Stop this layer" : "Start the best " + cat + " loop";
      var chipEls = row.chips.children;
      for (var i = 0; i < row.members.length && i < chipEls.length; i++) {
        chipEls[i].classList.toggle("is-on", row.members[i].padIdx === activeIdx);
      }
      var shownIdx = shown ? shown.padIdx : -1;
      if (row.shownIdx !== shownIdx && shownIdx >= 0) {
        var tint = parseColor(shown.colorHint);
        if (drawWaveInto(s, shownIdx, row.wave, tint, 0)) row.shownIdx = shownIdx;
      }
    }
  }

  // ---------- usage feedback (assetId play/skip → pad-feedback) ----------

  /** Any trigger counts as a play (kit ranking rewards reach-for), and
   * stamps a start time so a radial Stop inside 1.5 s downgrades intent
   * to a skip. Pads without a stable assetId (packs, drumfile pads) are
   * ignored — they mean nothing to the server's ranking loop. */
  function noteTrigger(s, padIdx) {
    if (!s.fb || !s.entry) return;
    var pad = padByIdx(s, padIdx);
    var assetId = pad && pad.assetId;
    if (typeof assetId !== "string" || !assetId) return;
    s.fb.startedAt[padIdx] = Date.now();
    pushPadEvent(s.fb.events, assetId, "play", 256);
  }

  /** Radial Stop within 1.5 s of the trigger = "launched but didn't want
   * it" — the explicit skip signal (native judges loops at toggle-off). */
  function noteRadialStop(s, padIdx) {
    if (!s.fb || !s.entry) return;
    var t0 = s.fb.startedAt[padIdx];
    delete s.fb.startedAt[padIdx];
    if (typeof t0 !== "number" || Date.now() - t0 >= 1500) return;
    var pad = padByIdx(s, padIdx);
    var assetId = pad && pad.assetId;
    if (typeof assetId === "string" && assetId)
      pushPadEvent(s.fb.events, assetId, "skip", 256);
  }

  /** Batch-post queued events. Fire-and-forget: feedback is best-effort
   * telemetry, so every failure path is silent. `final` (unmount) rides
   * sendBeacon so the POST survives page teardown. */
  function flushPadFeedback(s, final) {
    try {
      if (!s || !s.fb || !s.fb.events.length || !s.entry || !s.entry.id) return;
      var events = s.fb.events.splice(0, s.fb.events.length);
      var url = "/api/song/" + encodeURIComponent(s.entry.id) + "/pad-feedback";
      var body = JSON.stringify({ events: events });
      if (final && typeof navigator !== "undefined" && navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
        return;
      }
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: !!final,
      }).catch(function () {});
    } catch (_) {}
  }

  function renderPads(s) {
    if (s.kit && s.kit.name) s.titleEl.textContent = s.kit.name;
    s.gridEl.innerHTML = "";
    s.padEls = [];
    syncPadCountUi(s);

    // Server pad order — kit=5+ already lays rows out grouped by category,
    // so a straight padIdx grid reproduces the mobile grouped rack.
    var byIdx = [];
    s.pads.forEach(function (p) {
      if (p && typeof p.padIdx === "number") byIdx[p.padIdx] = p;
    });

    // Step dots come from the kit-level defaultSequence (kind=flip only);
    // per-pad dicts carry no step metadata server-side.
    var stepFlags = padStepFlags(s.kit);

    var count = s.padCount || PAD_COUNT;
    for (var i = 0; i < count; i++) {
      s.gridEl.appendChild(buildPadTile(s, i, byIdx[i], stepFlags));
    }

    // Waveforms need laid-out canvas sizes — draw on the next frame.
    requestAnimationFrame(function () {
      drawAllWaves(s);
    });
    if (!s.onResize) {
      s.onResize = function () {
        drawAllWaves(s);
      };
      window.addEventListener("resize", s.onResize);
    }

    // Rebuild the layer rack against the (possibly new) pads.
    renderLayers(s);
  }

  /** Build one grid tile (assigned or empty) and register it in s.padEls.
   * Shared by renderPads and the Delete/Undo path, which swaps a single
   * tile in place instead of rebuilding the grid (a full renderPads would
   * reset the ui state of every other, possibly sounding, pad). */
  function buildPadTile(s, i, pad, stepFlags) {
    var el = document.createElement("button");
    el.type = "button";
    el.className = "kit-pad";
    if (!pad) {
      el.classList.add("is-empty");
      el.disabled = true;
      s.padEls[i] = null;
      return el;
    }
    var tint = parseColor(pad.colorHint);
    el.style.setProperty("--pad-tint", tint.r + "," + tint.g + "," + tint.b);
    el.title = (pad.category ? pad.category + " — " : "") + (pad.name || "Pad " + (i + 1));

    var name = document.createElement("span");
    name.className = "kit-pad-name";
    name.textContent = pad.name || "Pad " + (i + 1);

    // Sequence step dots (light version): which 16th-steps of the kit's
    // defaultSequence this pad fires on.
    var flags = stepFlags && stepFlags[i];
    var dots = null;
    if (flags && flags.length) {
      dots = document.createElement("span");
      dots.className = "kit-pad-steps";
      for (var d = 0; d < flags.length; d++) {
        var dot = document.createElement("i");
        dot.className = "kit-pad-step" + (flags[d] ? " is-on" : "");
        dots.appendChild(dot);
      }
    }

    var canvas = document.createElement("canvas");
    canvas.className = "kit-pad-wave";

    var sweep = document.createElement("span");
    sweep.className = "kit-pad-sweep";

    var ring = document.createElement("span");
    ring.className = "kit-pad-ring";

    // Corner badge showing a radial per-pad loop/one-shot override.
    var badge = document.createElement("span");
    badge.className = "kit-pad-badge";

    // "FX" chip — lit while the pad carries non-neutral effects.
    var fxBadge = document.createElement("span");
    fxBadge.className = "kit-pad-fx";
    fxBadge.textContent = "FX";

    el.appendChild(name);
    if (dots) el.appendChild(dots);
    el.appendChild(canvas);
    el.appendChild(sweep);
    el.appendChild(ring);
    el.appendChild(badge);
    el.appendChild(fxBadge);

    s.padEls[i] = {
      el: el, ring: ring, sweep: sweep, canvas: canvas, badge: badge, fxBadge: fxBadge, tint: tint,
      ui: "idle", loop: false,
      loopOverride: null, // radial per-pad override: true=loop, false=one-shot, null=follow mode
      lp: null, lpX: 0, lpY: 0, // long-press (radial) timer state
    };
    wirePad(s, i, el);
    updateFxBadge(s, i);
    return el;
  }

  function wirePad(s, padIdx, el) {
    var clearLp = function () {
      var p = s.padEls[padIdx];
      if (p && p.lp) {
        clearTimeout(p.lp);
        p.lp = null;
      }
    };
    el.addEventListener("pointerdown", function (ev) {
      try {
        // Secondary buttons never sound the pad — the right-click pointerdown
        // precedes contextmenu, and firing audio under the radial felt broken.
        if (typeof ev.button === "number" && ev.button !== 0) return;
        ev.preventDefault();
        if (s.ctx && s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
        if (ev.pointerId !== undefined && el.setPointerCapture) {
          try {
            el.setPointerCapture(ev.pointerId);
          } catch (_) {}
        }
        padDown(s, padIdx);
        // Long-press ≥450 ms = the radial gesture (mobile hold-radial
        // parity; also the only path on touch, where contextmenu may
        // never fire). Movement past ~8 px cancels — that's a scrub.
        var p = s.padEls[padIdx];
        if (p) {
          clearLp();
          p.lpX = ev.clientX;
          p.lpY = ev.clientY;
          p.lp = setTimeout(function () {
            p.lp = null;
            try {
              padUp(s, padIdx); // release the held voice before the menu takes over
              el.classList.remove("is-pressed");
              openRadial(s, padIdx, p.lpX, p.lpY);
            } catch (_) {}
          }, 450);
        }
      } catch (_) {}
    });
    el.addEventListener("pointermove", function (ev) {
      var p = s.padEls[padIdx];
      if (!p || p.lp == null) return;
      var dx = ev.clientX - p.lpX;
      var dy = ev.clientY - p.lpY;
      if (dx * dx + dy * dy > 64) clearLp();
    });
    var up = function () {
      clearLp();
      try {
        padUp(s, padIdx);
      } catch (_) {}
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    // Right-click = the same radial menu, never the browser menu.
    el.addEventListener("contextmenu", function (ev) {
      ev.preventDefault();
      clearLp();
      try {
        openRadial(s, padIdx, ev.clientX, ev.clientY);
      } catch (_) {}
    });
    // Flash pressed feedback regardless of mode (primary button only).
    el.addEventListener("pointerdown", function (ev) {
      if (typeof ev.button === "number" && ev.button !== 0) return;
      el.classList.add("is-pressed");
    });
    el.addEventListener("pointerup", function () {
      el.classList.remove("is-pressed");
    });
    el.addEventListener("pointercancel", function () {
      el.classList.remove("is-pressed");
    });
  }

  function padDown(s, padIdx) {
    var p = s.padEls[padIdx];
    if (!p || !can(s.engine, "trigger")) return;
    var wantLoop = effectiveLoop(s, p);
    var q = s.quantize !== "off";
    // grid ("beat" | "bar") is the quantize unit the engine aligns to: Beat
    // fires on the next quarter-note, Bar on the next downbeat, both locked
    // to the SONG's bar grid while the transport rolls (free-run lock grid
    // when it's stopped). See padengine quantizeUnitSec / quantizeWaitSec.
    var opts = { loop: wantLoop, quantized: q };
    if (q) opts.grid = s.quantize;
    if (!wantLoop) {
      // One-shot fire-and-forget; release is ignored (padUp checks .loop).
      p.loop = false;
      s.engine.trigger(padIdx, opts);
      noteTrigger(s, padIdx);
      setUi(s, padIdx, "playing"); // immediate start; rAF ends it via padProgress
      return;
    }
    // Loop path (Loop mode, or a radial loop-override in Tap mode).
    if ((s.latch || p.loopOverride === true) && (p.ui === "armed" || p.ui === "playing")) {
      // Latch ON (and override-loops, which behave latched): second tap
      // toggles off.
      if (can(s.engine, "release")) s.engine.release(padIdx);
      setUi(s, padIdx, "idle");
      return;
    }
    p.loop = true;
    var res = s.engine.trigger(padIdx, opts);
    noteTrigger(s, padIdx);
    // Armed until padProgress (or onstate) reports actual start — the
    // pulsing border says "waiting for the beat", not silence. Unquantized
    // loops start now, so they're playing already.
    setUi(s, padIdx, q || (res && res.deferred) ? "armed" : "playing");
  }

  function padUp(s, padIdx) {
    var p = s.padEls[padIdx];
    if (!p) return;
    if (s.latch || !p.loop) return; // tap/one-shot = fire-and-forget; latch holds
    if (p.loopOverride === true) return; // override-loops latch (radial Stop / re-tap ends them)
    if (s.mode !== "loop") return;
    if (can(s.engine, "release")) s.engine.release(padIdx);
    setUi(s, padIdx, "idle");
  }

  // ---------- progress animation ----------

  function safeProgress(s, padIdx) {
    if (!can(s.engine, "padProgress")) return null;
    try {
      var v = s.engine.padProgress(padIdx);
      return typeof v === "number" && isFinite(v) && v >= 0 ? Math.min(v, 1) : null;
    } catch (_) {
      return null;
    }
  }

  function startRaf(s) {
    var tick = function () {
      if (!s.alive) return;
      for (var i = 0; i < s.padEls.length; i++) {
        var p = s.padEls[i];
        if (!p || p.ui === "idle") continue;
        var prog = safeProgress(s, i);
        if (p.ui === "armed") {
          // Quantize boundary hit → the loop is audible now.
          if (prog !== null && prog > 0.001) setUi(s, i, "playing");
          continue;
        }
        // playing
        if (prog === null) {
          // One-shot finished (or the engine dropped the voice).
          setUi(s, i, "idle");
          continue;
        }
        if (p.loop) {
          // Progress ring: conic sweep in the pad tint around the tile edge.
          p.ring.style.background =
            "conic-gradient(" + rgba(p.tint, 0.95) + " " + (prog * 360).toFixed(1) +
            "deg, rgba(255,255,255,0.10) 0)";
        }
        // Elapsed sweep + playhead over the waveform (mobile loopPlayhead look).
        p.sweep.style.width = (prog * 100).toFixed(2) + "%";
      }
      s.raf = requestAnimationFrame(tick);
    };
    s.raf = requestAnimationFrame(tick);
  }

  // ---------- waveforms ----------

  function drawAllWaves(s) {
    if (!s.alive) return;
    for (var i = 0; i < s.padEls.length; i++) {
      var p = s.padEls[i];
      if (p) drawWave(s, i, p);
    }
  }

  function drawWave(s, padIdx, p) {
    // 8×8 tiles are too small for a dense waveform — coarser bins read
    // as a clean silhouette instead of noise.
    var bins = s.padCount === 64 ? -6 : 0; // sentinel: px-per-bin override
    drawWaveInto(s, padIdx, p.canvas, p.tint, bins);
  }

  /** Waveform painter shared by grid pads and layer rows.
   * `binsOpt`: 0 = default density (w/4, 16..64 bins); negative = px per
   * bin with a lower cap (compact 64-grid simplification); positive =
   * exact bin count. Returns true when something was drawn (false when
   * the canvas has no laid-out size yet). */
  function drawWaveInto(s, padIdx, canvas, tint, binsOpt) {
    var w = canvas.clientWidth,
      h = canvas.clientHeight;
    if (!w || !h) return false;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    var g = canvas.getContext("2d");
    if (!g) return false;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);

    var bins;
    if (binsOpt > 0) bins = binsOpt;
    else if (binsOpt < 0) bins = Math.max(6, Math.min(20, Math.round(w / -binsOpt)));
    else bins = Math.max(16, Math.min(64, Math.round(w / 4)));
    var peaks = null;
    if (can(s.engine, "peaks")) {
      try {
        peaks = s.engine.peaks(padIdx, bins);
      } catch (_) {
        peaks = null;
      }
    }
    if (!peaks || !peaks.length) {
      // Fallback accent underline (no buffer resident) — mobile parity.
      g.fillStyle = rgba(tint, 0.9);
      g.fillRect(0, h - 3, 26, 3);
      return true;
    }
    // Mirrored bars, light strokes — PadWaveformBars look.
    var n = peaks.length;
    var bw = w / n;
    g.fillStyle = rgba(tint, 0.9);
    for (var i = 0; i < n; i++) {
      var v = Math.max(0, Math.min(1, Number(peaks[i]) || 0));
      var bh = Math.max(1.5, v * h);
      g.fillRect(i * bw + bw * 0.15, (h - bh) / 2, Math.max(0.6, bw * 0.7), bh);
    }
    return true;
  }

  // ---------- delete / undo (radial Delete) ----------

  /** Clear a pad assignment (native radial Delete). The engine entry is
   * removed (trigger() goes dead) and the tile swaps to .is-empty. Unlike
   * native, a 5 s Undo toast can restore it — web has no per-pad re-add
   * picker yet, so an un-undoable delete would be a dead end. */
  function deletePad(s, padIdx) {
    var pad = padByIdx(s, padIdx);
    var p = s.padEls[padIdx];
    if (!pad || !p) return;
    try {
      if (can(s.engine, "release")) s.engine.release(padIdx);
    } catch (_) {}
    setUi(s, padIdx, "idle");
    var token = null;
    try {
      token = can(s.engine, "removePad") ? s.engine.removePad(padIdx) : null;
    } catch (_) {}
    var at = s.pads.indexOf(pad);
    if (at !== -1) s.pads.splice(at, 1); // layers/groove stop offering it
    delete s.padFx[padIdx]; // token carries the fx for undo
    savePadFx(s);
    delete s.origRegions[padIdx];
    delete s.gated[padIdx];
    if (p.lp) clearTimeout(p.lp);
    var emptyEl = buildPadTile(s, padIdx, null, null);
    try {
      p.el.parentNode.replaceChild(emptyEl, p.el);
    } catch (_) {}
    renderLayers(s);
    var snap = { pad: pad, token: token, timer: 0 };
    s.deleted[padIdx] = snap;
    snap.timer = setTimeout(function () {
      if (s.deleted[padIdx] === snap) delete s.deleted[padIdx]; // undo window over
    }, 5000);
    toastAction(s, "Pad deleted", "Undo", function () {
      undoDeletePad(s, padIdx);
    }, 5000);
  }

  function undoDeletePad(s, padIdx) {
    var snap = s.deleted[padIdx];
    if (!snap || !s.alive) return;
    delete s.deleted[padIdx];
    if (snap.timer) clearTimeout(snap.timer);
    s.pads.push(snap.pad);
    try {
      if (snap.token && can(s.engine, "restorePad")) s.engine.restorePad(padIdx, snap.token);
    } catch (_) {}
    if (snap.token && snap.token.fx) s.padFx[padIdx] = snap.token.fx; // engine restored it too
    savePadFx(s);
    var el = buildPadTile(s, padIdx, snap.pad, padStepFlags(s.kit));
    var old = s.gridEl.children[padIdx];
    if (old) s.gridEl.replaceChild(el, old);
    var pEl = s.padEls[padIdx];
    if (pEl) drawWave(s, padIdx, pEl);
    renderLayers(s);
  }

  // ---------- per-pad FX (SamplePadEffects twin) ----------

  /** Load the song's persisted FX map and push it into the engine.
   * Called once per prepare() — a rebuilt engine starts FX-empty. */
  function applyStoredFx(s) {
    if (!s.fxKey || !can(s.engine, "setPadEffects")) return;
    var map = null;
    try {
      map = window.localStorage
        ? parsePadFxStore(window.localStorage.getItem("jamn.padfx." + s.fxKey))
        : null;
    } catch (_) {}
    s.padFx = {};
    if (!map) return;
    for (var k in map) {
      var idx = parseInt(k, 10);
      try {
        var norm = s.engine.setPadEffects(idx, map[k]); // clamps; null = neutral
        if (norm) s.padFx[idx] = norm;
      } catch (_) {}
    }
  }

  function savePadFx(s) {
    if (!s.fxKey) return;
    try {
      if (!window.localStorage) return;
      var json = serializePadFxStore(s.padFx);
      if (json) window.localStorage.setItem("jamn.padfx." + s.fxKey, json);
      else window.localStorage.removeItem("jamn.padfx." + s.fxKey);
    } catch (_) {}
  }

  /** "FX" corner chip — a pad carrying non-neutral FX shows it (the web
   * stand-in for the native editor's clean/dirty indicator). */
  function updateFxBadge(s, padIdx) {
    var p = s.padEls[padIdx];
    if (!p || !p.fxBadge) return;
    p.fxBadge.classList.toggle("is-on", s.padFx[padIdx] != null);
  }

  function closeFxEditor(s) {
    var f = s && s.fxPop;
    if (!f) return;
    s.fxPop = null;
    try {
      document.removeEventListener("keydown", f.onKey, true);
      document.removeEventListener("pointerdown", f.onDown, true);
    } catch (_) {}
    try {
      if (f.el && f.el.parentNode) f.el.parentNode.removeChild(f.el);
    } catch (_) {}
  }

  /** Log-scale cutoff mapping: slider t∈[0,1] ↔ 100..20000 Hz. A linear
   * Hz slider wastes 90% of its travel above 2 kHz where nothing musical
   * happens; log spacing matches how the filter is heard. */
  function cutoffFromSlider(t) {
    return Math.round(100 * Math.pow(200, Math.max(0, Math.min(1, t))));
  }
  function sliderFromCutoff(hz) {
    var v = Math.max(100, Math.min(20000, hz || 20000));
    return Math.log(v / 100) / Math.log(200);
  }

  /** Small per-pad FX popover (not a page): delay time/feedback/mix +
   * resonant low-pass cutoff/resonance + gain — mirror of the mobile
   * PadEffectsEditor over SamplePadEffects. Values apply live to the
   * ENGINE but, like the native voice pool, a sounding voice keeps its
   * chain until the pad is retriggered. */
  function openFxEditor(s, padIdx) {
    closeFxEditor(s);
    var p = s.padEls[padIdx];
    if (!p || !can(s.engine, "setPadEffects")) {
      toast(s, "Effects need the current pad engine");
      return;
    }
    var pad = padByIdx(s, padIdx);
    var neutral = (s.dsp && s.dsp.NEUTRAL_PAD_FX) || {
      delayTimeSec: 0.25, delayFeedback: 0, delayMix: 0,
      filterCutoffHz: 20000, filterResonanceDb: 0, gain: 1.0,
    };
    var fx = {};
    var stored = s.padFx[padIdx];
    for (var k in neutral) fx[k] = stored && stored[k] != null ? stored[k] : neutral[k];

    var pop = document.createElement("div");
    pop.className = "kit-fx-pop";
    if (p.tint) pop.style.setProperty("--pad-tint", p.tint.r + "," + p.tint.g + "," + p.tint.b);

    var head = document.createElement("div");
    head.className = "kit-fx-head";
    var title = document.createElement("span");
    title.className = "kit-fx-title";
    title.textContent = ((pad && pad.name) || "Pad " + (padIdx + 1)) + " — Effects";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "kit-fx-close";
    close.textContent = "✕";
    close.title = "Close";
    close.addEventListener("click", function () {
      closeFxEditor(s);
    });
    head.appendChild(title);
    head.appendChild(close);
    pop.appendChild(head);

    function push() {
      try {
        var norm = s.engine.setPadEffects(padIdx, fx); // null = neutral/cleared
        if (norm) s.padFx[padIdx] = norm;
        else delete s.padFx[padIdx];
        savePadFx(s);
        updateFxBadge(s, padIdx);
      } catch (_) {}
    }

    var readouts = [];
    /** One slider row. `toFx` maps slider value → fx field; `fmt` renders
     * the readout from the current fx. */
    function row(label, min, max, step, value, toFx, fmt) {
      var r = document.createElement("label");
      r.className = "kit-fx-row";
      var name = document.createElement("span");
      name.className = "kit-fx-name";
      name.textContent = label;
      var input = document.createElement("input");
      input.type = "range";
      input.min = String(min);
      input.max = String(max);
      input.step = String(step);
      input.value = String(value);
      var out = document.createElement("span");
      out.className = "kit-fx-val";
      var render = function () {
        out.textContent = fmt();
      };
      readouts.push({ input: input, render: render, reset: null });
      input.addEventListener("input", function () {
        toFx(parseFloat(input.value));
        render();
        push();
      });
      r.appendChild(name);
      r.appendChild(input);
      r.appendChild(out);
      pop.appendChild(r);
      render();
      return input;
    }

    var inTime = row("Delay time", 0, 2, 0.01, fx.delayTimeSec,
      function (v) { fx.delayTimeSec = v; },
      function () { return fx.delayTimeSec.toFixed(2) + " s"; });
    var inFb = row("Feedback", 0, 95, 1, fx.delayFeedback,
      function (v) { fx.delayFeedback = v; },
      function () { return Math.round(fx.delayFeedback) + " %"; });
    var inMix = row("Delay mix", 0, 100, 1, fx.delayMix,
      function (v) { fx.delayMix = v; },
      function () { return Math.round(fx.delayMix) + " %"; });
    var inCut = row("Cutoff", 0, 1, 0.001, sliderFromCutoff(fx.filterCutoffHz),
      function (v) { fx.filterCutoffHz = cutoffFromSlider(v); },
      function () {
        var hz = fx.filterCutoffHz;
        return hz >= 19999 ? "open" : hz >= 1000 ? (hz / 1000).toFixed(1) + " kHz" : Math.round(hz) + " Hz";
      });
    var inRes = row("Resonance", 0, 24, 0.5, fx.filterResonanceDb,
      function (v) { fx.filterResonanceDb = v; },
      function () { return fx.filterResonanceDb.toFixed(1) + " dB"; });
    var inGain = row("Gain", 0, 200, 1, fx.gain * 100,
      function (v) { fx.gain = v / 100; },
      function () { return Math.round(fx.gain * 100) + " %"; });

    var foot = document.createElement("div");
    foot.className = "kit-fx-foot";
    var hint = document.createElement("span");
    hint.className = "kit-fx-hint";
    hint.textContent = "Applies on the next trigger"; // native voice-pool semantics
    var reset = document.createElement("button");
    reset.type = "button";
    reset.className = "kit-fx-reset";
    reset.textContent = "Neutral";
    reset.title = "Clear this pad's effects";
    reset.addEventListener("click", function () {
      for (var k in neutral) fx[k] = neutral[k];
      inTime.value = String(fx.delayTimeSec);
      inFb.value = String(fx.delayFeedback);
      inMix.value = String(fx.delayMix);
      inCut.value = String(sliderFromCutoff(fx.filterCutoffHz));
      inRes.value = String(fx.filterResonanceDb);
      inGain.value = String(fx.gain * 100);
      readouts.forEach(function (r) { r.render(); });
      push();
    });
    foot.appendChild(hint);
    foot.appendChild(reset);
    pop.appendChild(foot);

    document.body.appendChild(pop);
    // Anchor beside the pad, clamped on-viewport (position: fixed).
    try {
      var rect = p.el.getBoundingClientRect();
      var vw = window.innerWidth || 0;
      var vh = window.innerHeight || 0;
      var w = pop.offsetWidth || 260;
      var h = pop.offsetHeight || 220;
      var left = Math.max(8, Math.min(vw - w - 8, rect.right + 10));
      var top = Math.max(8, Math.min(vh - h - 8, rect.top));
      pop.style.left = left + "px";
      pop.style.top = top + "px";
    } catch (_) {}

    var onKey = function (ev) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeFxEditor(s);
      }
    };
    var onDown = function (ev) {
      if (pop.contains(ev.target)) return;
      closeFxEditor(s);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    s.fxPop = { el: pop, onKey: onKey, onDown: onDown, padIdx: padIdx };
  }

  // ---------- sound picker (radial "Add sound") ----------

  function stopPickerPreview(s) {
    var pv = s && s.pickerPreview;
    if (!pv) return;
    s.pickerPreview = null;
    try { if (pv.timer) clearTimeout(pv.timer); } catch (_) {}
    try {
      // 40 ms fade so a cut-off preview doesn't click.
      if (s.ctx && pv.gain && pv.gain.gain) {
        var t = s.ctx.currentTime;
        pv.gain.gain.setValueAtTime(pv.gain.gain.value, t);
        pv.gain.gain.linearRampToValueAtTime(0, t + 0.04);
      }
    } catch (_) {}
    try { if (pv.source && s.ctx) pv.source.stop(s.ctx.currentTime + 0.05); } catch (_) {}
  }

  /** Short one-shot preview of a decoded AudioBuffer straight through the
   * page context — NOT the pad voice pool (a hovered pack pad isn't baked
   * onto any pad yet). Capped at 1.5 s so a long loop sample can't run
   * away, with tiny edge ramps so it never clicks. */
  function previewBuffer(s, buffer) {
    if (!s || !s.ctx || !buffer) return;
    stopPickerPreview(s);
    try {
      if (s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
      var src = s.ctx.createBufferSource();
      src.buffer = buffer;
      var g = s.ctx.createGain();
      src.connect(g);
      g.connect(s.ctx.destination);
      var now = s.ctx.currentTime;
      var cap = Math.min(buffer.duration || 1.5, 1.5);
      g.gain.setValueAtTime(0, now);
      g.gain.linearRampToValueAtTime(1, now + 0.008);
      g.gain.setValueAtTime(1, now + Math.max(0.02, cap - 0.05));
      g.gain.linearRampToValueAtTime(0, now + cap);
      src.start(now);
      src.stop(now + cap + 0.02);
      var timer = setTimeout(function () {
        if (s.pickerPreview && s.pickerPreview.source === src) s.pickerPreview = null;
      }, (cap + 0.05) * 1000);
      s.pickerPreview = { source: src, gain: g, timer: timer };
    } catch (_) {}
  }

  /** Repaint a tile after its source changed: name, tint, title, wave.
   * (The slot keeps its position; only the assigned sample's identity
   * follows — native SoundPickerSheet assign semantics.) */
  function repaintPadSource(s, padIdx) {
    var pad = padByIdx(s, padIdx);
    var p = s.padEls[padIdx];
    if (!p || !pad) return;
    var tint = parseColor(pad.colorHint);
    p.tint = tint;
    try {
      p.el.style.setProperty("--pad-tint", tint.r + "," + tint.g + "," + tint.b);
      p.el.title = (pad.category ? pad.category + " — " : "") + (pad.name || "Pad " + (padIdx + 1));
      var nm = p.el.querySelector(".kit-pad-name");
      if (nm) nm.textContent = pad.name || "Pad " + (padIdx + 1);
    } catch (_) {}
    drawWave(s, padIdx, p);
  }

  /** Swap `buffer` onto the pad through the engine and reflect it in the
   * UI. Snapshots the pad's display identity ONCE (first swap) so the
   * radial Reset can repaint back; the engine snapshots the buffer. */
  function commitPadSource(s, padIdx, buffer, meta) {
    if (!buffer || !can(s.engine, "setPadSource")) {
      toast(s, "Add sound needs the current pad engine");
      return false;
    }
    var pad = padByIdx(s, padIdx);
    // Take the display snapshot only when this is the FIRST swap, so a
    // later failure that leaves an already-swapped pad untouched can't
    // clobber the true original.
    var firstSwap = !(can(s.engine, "hasSwappedSource") && s.engine.hasSwappedSource(padIdx));
    var ok = false;
    try {
      ok = s.engine.setPadSource(padIdx, buffer, {
        name: meta && meta.name,
        colorHint: meta && meta.colorHint,
      });
    } catch (_) { ok = false; }
    if (!ok) {
      toast(s, "Could not add that sound");
      return false;
    }
    if (pad && firstSwap && s.origSource && s.origSource[padIdx] == null) {
      s.origSource[padIdx] = { name: pad.name, colorHint: pad.colorHint };
    }
    if (pad) {
      if (meta && meta.name) pad.name = meta.name;
      if (meta && meta.colorHint) pad.colorHint = meta.colorHint;
    }
    repaintPadSource(s, padIdx);
    setUi(s, padIdx, "idle");
    return true;
  }

  function closeSoundPicker(s) {
    var f = s && s.pickerPop;
    if (!f) return;
    s.pickerPop = null;
    stopPickerPreview(s);
    try {
      document.removeEventListener("keydown", f.onKey, true);
      document.removeEventListener("pointerdown", f.onDown, true);
    } catch (_) {}
    try { if (f.el && f.el.parentNode) f.el.parentNode.removeChild(f.el); } catch (_) {}
  }

  /** Per-pad sound picker popover (NOT a page nav — the FX-popover idiom):
   * two sections, "Curated packs" (fetch /api/sample-packs → drill into a
   * pack's pads) and "This song" (the other kit pads). Rows preview on
   * hover/tap and commit on click via commitPadSource. A "Browse all
   * packs →" link keeps the old full-page Packs route as a secondary exit.
   * Click-away or Escape dismisses. Web twin of the native
   * SoundPickerSheet. */
  function openSoundPicker(s, padIdx) {
    closeSoundPicker(s);
    closeFxEditor(s); // never stack popovers
    var p = s.padEls[padIdx];
    if (!p) return;
    if (!can(s.engine, "setPadSource")) {
      toast(s, "Add sound needs the current pad engine");
      return;
    }
    var pad = padByIdx(s, padIdx);

    var state = { tab: "packs", openPack: null };

    var pop = document.createElement("div");
    pop.className = "kit-pick-pop";
    if (p.tint) pop.style.setProperty("--pad-tint", p.tint.r + "," + p.tint.g + "," + p.tint.b);

    var head = document.createElement("div");
    head.className = "kit-pick-head";
    var back = document.createElement("button");
    back.type = "button";
    back.className = "kit-pick-back";
    back.textContent = "‹";
    back.title = "Back";
    back.addEventListener("click", function () {
      state.openPack = null;
      stopPickerPreview(s);
      render();
    });
    var title = document.createElement("span");
    title.className = "kit-pick-title";
    var close = document.createElement("button");
    close.type = "button";
    close.className = "kit-pick-close";
    close.textContent = "✕";
    close.title = "Close";
    close.addEventListener("click", function () { closeSoundPicker(s); });
    head.appendChild(back);
    head.appendChild(title);
    head.appendChild(close);
    pop.appendChild(head);

    var tabs = document.createElement("div");
    tabs.className = "kit-pick-tabs";
    var tabPacks = document.createElement("button");
    tabPacks.type = "button";
    tabPacks.className = "kit-pick-tab";
    tabPacks.textContent = "Curated packs";
    tabPacks.addEventListener("click", function () {
      state.tab = "packs";
      stopPickerPreview(s);
      render();
    });
    var tabSong = document.createElement("button");
    tabSong.type = "button";
    tabSong.className = "kit-pick-tab";
    tabSong.textContent = "This song";
    tabSong.addEventListener("click", function () {
      state.tab = "song";
      state.openPack = null;
      stopPickerPreview(s);
      render();
    });
    tabs.appendChild(tabPacks);
    tabs.appendChild(tabSong);
    pop.appendChild(tabs);

    var body = document.createElement("div");
    body.className = "kit-pick-body";
    pop.appendChild(body);

    var foot = document.createElement("div");
    foot.className = "kit-pick-foot";
    var browse = document.createElement("button");
    browse.type = "button";
    browse.className = "kit-pick-browse";
    browse.textContent = "Browse all packs →";
    browse.title = "Open the full Packs view";
    browse.addEventListener("click", function () {
      closeSoundPicker(s);
      var hooks = window.JamnKitHooks;
      if (hooks && typeof hooks.openPacks === "function") {
        try { hooks.openPacks(); return; } catch (_) {}
      }
      try { window.location.hash = "#packs"; } catch (_) {}
    });
    foot.appendChild(browse);
    pop.appendChild(foot);

    function msg(text) {
      var d = document.createElement("div");
      d.className = "kit-pick-msg";
      d.textContent = text;
      body.appendChild(d);
    }

    /** One clickable row: swatch + name + optional sub. `buffer()` returns
     * (sync or via Promise) the AudioBuffer to preview/commit; commit uses
     * `meta` for the pad's new name/color. */
    function row(opts) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-pick-row";
      var sw = document.createElement("span");
      sw.className = "kit-pick-swatch";
      var c = parseColor(opts.colorHint);
      sw.style.background = rgba(c, 0.9);
      var text = document.createElement("span");
      text.className = "kit-pick-rowtext";
      var nm = document.createElement("span");
      nm.className = "kit-pick-rowname";
      nm.textContent = opts.name;
      text.appendChild(nm);
      if (opts.sub) {
        var sub = document.createElement("span");
        sub.className = "kit-pick-rowsub";
        sub.textContent = opts.sub;
        text.appendChild(sub);
      }
      b.appendChild(sw);
      b.appendChild(text);
      if (opts.chevron) {
        var ch = document.createElement("span");
        ch.className = "kit-pick-chev";
        ch.textContent = "›";
        b.appendChild(ch);
      }
      var doPreview = function () {
        if (typeof opts.preview !== "function") return;
        try {
          var r = opts.preview();
          if (r && typeof r.then === "function") r.then(function (buf) { if (buf) previewBuffer(s, buf); });
          else if (r) previewBuffer(s, r);
        } catch (_) {}
      };
      b.addEventListener("pointerenter", function () {
        if (opts.preview) doPreview();
      });
      b.addEventListener("pointerdown", function (ev) {
        // Touch has no hover — preview on the press instead.
        if (ev && ev.pointerType && ev.pointerType !== "mouse" && opts.preview) doPreview();
      });
      b.addEventListener("click", function () {
        if (typeof opts.activate === "function") { opts.activate(); return; }
      });
      body.appendChild(b);
      return b;
    }

    function renderPackList() {
      var render2 = function (catalog) {
        if (s.pickerPop !== ref) return;
        body.innerHTML = "";
        var packs = pickerPackList(catalog);
        if (!packs.length) { msg("No curated packs on this backend."); return; }
        packs.forEach(function (pk) {
          row({
            name: pk.name,
            sub: pk.padCount != null ? pk.padCount + " pads" : null,
            colorHint: pk.paletteHint,
            chevron: true,
            activate: function () {
              state.openPack = { packId: pk.packId, name: pk.name };
              stopPickerPreview(s);
              render();
            },
          });
        });
      };
      if (s._pickerCatalog) { render2(s._pickerCatalog); return; }
      body.innerHTML = "";
      msg("Loading packs…");
      fetch("/api/sample-packs")
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
        .then(function (data) { s._pickerCatalog = data; render2(data); })
        .catch(function () { if (s.pickerPop === ref) { body.innerHTML = ""; msg("Could not load packs."); } });
    }

    function renderPackPads(packId, packName) {
      var render2 = function (manifest) {
        if (s.pickerPop !== ref) return;
        body.innerHTML = "";
        var rows = packPadRows(manifest, packId);
        if (!rows.length) { msg("This pack has no pads."); return; }
        rows.forEach(function (pr) {
          row({
            name: pr.name,
            colorHint: pr.colorHint,
            preview: function () { return fetchPadBuffer(s, pr.url); },
            activate: function () {
              fetchPadBuffer(s, pr.url).then(function (buf) {
                if (!buf) { toast(s, "That sound wouldn't load"); return; }
                if (commitPadSource(s, padIdx, buf, { name: pr.name, colorHint: pr.colorHint })) {
                  closeSoundPicker(s);
                  toast(s, "Sound added");
                }
              });
            },
          });
        });
      };
      s._pickerManifests = s._pickerManifests || {};
      if (s._pickerManifests[packId]) { render2(s._pickerManifests[packId]); return; }
      body.innerHTML = "";
      msg("Loading " + (packName || "pack") + "…");
      fetch("/api/sample-packs/" + encodeURIComponent(packId))
        .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("HTTP " + r.status)); })
        .then(function (m) { s._pickerManifests[packId] = m; render2(m); })
        .catch(function () { if (s.pickerPop === ref) { body.innerHTML = ""; msg("Could not load this pack."); } });
    }

    function renderSongPads() {
      body.innerHTML = "";
      var rows = pickerSongPads(s.pads, padIdx);
      if (!rows.length) { msg("No other pads in this song."); return; }
      rows.forEach(function (sp) {
        row({
          name: sp.name,
          sub: "Pad " + (sp.padIdx + 1),
          colorHint: sp.colorHint,
          preview: function () {
            return can(s.engine, "sourceBuffer") ? s.engine.sourceBuffer(sp.padIdx) : null;
          },
          activate: function () {
            var buf = can(s.engine, "sourceBuffer") ? s.engine.sourceBuffer(sp.padIdx) : null;
            if (!buf) { toast(s, "That pad has no sound to copy"); return; }
            if (commitPadSource(s, padIdx, buf, { name: sp.name, colorHint: sp.colorHint })) {
              closeSoundPicker(s);
              toast(s, "Sound added");
            }
          },
        });
      });
    }

    function render() {
      var drilled = state.tab === "packs" && state.openPack;
      back.style.display = drilled ? "" : "none";
      title.textContent = drilled
        ? state.openPack.name
        : ((pad && pad.name) || "Pad " + (padIdx + 1)) + " — Add sound";
      tabPacks.classList.toggle("is-active", state.tab === "packs");
      tabSong.classList.toggle("is-active", state.tab === "song");
      if (state.tab === "song") renderSongPads();
      else if (drilled) renderPackPads(state.openPack.packId, state.openPack.name);
      else renderPackList();
    }

    document.body.appendChild(pop);
    // Anchor beside the pad, clamped on-viewport (position: fixed).
    try {
      var rect = p.el.getBoundingClientRect();
      var vw = window.innerWidth || 0;
      var vh = window.innerHeight || 0;
      var w = pop.offsetWidth || 300;
      var h = pop.offsetHeight || 360;
      var left = Math.max(8, Math.min(vw - w - 8, rect.right + 10));
      var top = Math.max(8, Math.min(vh - h - 8, rect.top));
      pop.style.left = left + "px";
      pop.style.top = top + "px";
    } catch (_) {}

    var onKey = function (ev) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeSoundPicker(s);
      }
    };
    var onDown = function (ev) {
      if (pop.contains(ev.target)) return;
      closeSoundPicker(s);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onDown, true);
    var ref = { el: pop, onKey: onKey, onDown: onDown, padIdx: padIdx };
    s.pickerPop = ref;
    render();
  }

  /** Fetch + decode one pad audio file into an AudioBuffer, cached on the
   * session so re-hovering a row doesn't re-download. Resolves null on any
   * failure (the caller toasts). */
  function fetchPadBuffer(s, url) {
    if (!s || !s.ctx || !url) return Promise.resolve(null);
    s._padBufCache = s._padBufCache || {};
    if (s._padBufCache[url]) return Promise.resolve(s._padBufCache[url]);
    return fetch(url)
      .then(function (r) { return r.ok ? r.arrayBuffer() : Promise.reject(new Error("HTTP " + r.status)); })
      .then(function (b) { return s.ctx.decodeAudioData(b); })
      .then(function (buf) { s._padBufCache[url] = buf; return buf; })
      .catch(function () { return null; });
  }

  // ---------- radial pad menu (right-click / long-press) ----------

  function padByIdx(s, padIdx) {
    for (var i = 0; i < s.pads.length; i++) {
      var p = s.pads[i];
      if (p && p.padIdx === padIdx) return p;
    }
    return null;
  }

  function closeRadial(s) {
    var r = s && s.radial;
    if (!r) return;
    s.radial = null;
    try {
      document.removeEventListener("keydown", r.onKey, true);
    } catch (_) {}
    try {
      if (r.el && r.el.parentNode) r.el.parentNode.removeChild(r.el);
    } catch (_) {}
  }

  function updateOverrideBadge(s, padIdx) {
    var p = s.padEls[padIdx];
    if (!p || !p.badge) return;
    // ∞ = forced loop, 1 = forced one-shot, hidden = follow the mode.
    p.badge.textContent = p.loopOverride === true ? "∞" : p.loopOverride === false ? "1" : "";
    p.badge.classList.toggle("is-on", p.loopOverride != null);
  }

  /** Pie-wheel action menu around (x, y) — the mobile hold-radial / desktop
   * right-click wheel (PadRadialMenu parity), in SVG. A ring of equal
   * divider-stroked wedges (icon + label each) around a solid center hub
   * that shows the pad name + a mini waveform and doubles as cancel;
   * hovering highlights the wedge; click-away or Escape dismisses. */
  function openRadial(s, padIdx, x, y) {
    closeRadial(s);
    closeFxEditor(s); // never stack the FX popover under the wheel
    closeSoundPicker(s);
    var p = s.padEls[padIdx];
    if (!p) return;
    var pad = padByIdx(s, padIdx);
    var effLoop = effectiveLoop(s, p);
    var sounding = p.ui === "armed" || p.ui === "playing";
    // Solo only means something while ANOTHER pad sounds.
    var anyOther = false;
    for (var oi = 0; oi < s.padEls.length; oi++) {
      if (oi !== padIdx && s.padEls[oi] && s.padEls[oi].ui !== "idle") {
        anyOther = true;
        break;
      }
    }
    var overrides = padOverrides(
      s.origRegions[padIdx], s.gated[padIdx], p.loopOverride, s.padFx[padIdx]);
    // A swapped source (radial "Add sound") also makes the pad resettable,
    // even when no region/gate/loop/fx override is present.
    var swapped = can(s.engine, "hasSwappedSource") && s.engine.hasSwappedSource(padIdx);

    // "To Sequence" (desktop addToSequence) adds this pad to the sequencer
    // as its own TRACK — distinct from "Sequence", which just navigates to
    // the sequencer surface. It needs an add-track hook on JamnSequencer;
    // that surface exposes mount/focusRow/stageDefaultSequence today but no
    // per-pad add, so feature-detect the likely names and dim the wedge
    // with a tooltip until one lands (never a dead click).
    var seqAddHook = (function () {
      var Q = window.JamnSequencer;
      if (!Q) return null;
      var names = ["addTrack", "addPadTrack", "addTrackForPad"];
      for (var i = 0; i < names.length; i++)
        if (typeof Q[names[i]] === "function") return Q[names[i]].bind(Q);
      return null;
    })();

    // Full native assigned ring (delete/chop/addSound/loop/reset/effects/
    // sequence — PadRadialMenu.assigned) plus the two web-only transport
    // actions (Stop pad, Solo). Inapplicable actions render dimmed with
    // their label, like the native ring.
    var actions = [
      {
        icon: "∞",
        label: effLoop ? "One-shot" : "Loop",
        active: effLoop,
        run: function () {
          p.loopOverride = !effLoop;
          updateOverrideBadge(s, padIdx);
        },
      },
      {
        icon: "≡",
        label: "Effects",
        active: overrides.fx,
        run: function () {
          openFxEditor(s, padIdx);
        },
      },
      {
        icon: "✎",
        label: "Chop",
        run: function () {
          var CE = window.JamnChopEdit;
          if (CE && typeof CE.open === "function") {
            CE.open({
              pad: pad,
              entry: s.entry,
              onSave: function (region) {
                applyPadRegion(padIdx, region);
              },
            });
          } else {
            toast(s, "Chop editor coming soon");
          }
        },
      },
      {
        icon: "▦",
        label: "Sequence",
        run: function () {
          // Host hook first (window.JamnKitHooks.openSequencer — see the
          // JamnKitHooks doc above getHost); the hash fallback hands the
          // view switch to whatever router owns the page.
          var hooks = window.JamnKitHooks;
          if (hooks && typeof hooks.openSequencer === "function") {
            try {
              hooks.openSequencer(padIdx);
              return;
            } catch (_) {}
          }
          try {
            window.location.hash = "#sequencer";
          } catch (_) {}
        },
      },
      {
        icon: "⊞",
        label: "To Sequence",
        disabled: !seqAddHook,
        tip: seqAddHook
          ? "Add this pad to the sequencer as its own track"
          : "Add as sequencer track (not available yet)",
        run: function () {
          if (!seqAddHook) return;
          try {
            seqAddHook(s.entry && s.entry.id, padIdx, pad);
            toast(s, "Added to sequencer");
          } catch (_) {}
        },
      },
      {
        icon: "＋",
        label: "Add sound",
        run: function () {
          // Per-pad sound picker (native SoundPickerSheet twin): swap this
          // pad's sample from a curated pack or another kit pad, in place.
          // The "Browse all packs →" footer keeps the old full-page route.
          openSoundPicker(s, padIdx);
        },
      },
      {
        icon: "■",
        label: "Stop pad",
        danger: true,
        disabled: !sounding,
        run: function () {
          noteRadialStop(s, padIdx); // <1.5 s since trigger = skip signal
          try {
            if (can(s.engine, "release")) s.engine.release(padIdx);
          } catch (_) {}
          setUi(s, padIdx, "idle");
        },
      },
      {
        icon: "◎",
        label: "Solo",
        disabled: !anyOther,
        run: function () {
          for (var i = 0; i < s.padEls.length; i++) {
            var other = s.padEls[i];
            if (i === padIdx || !other || other.ui === "idle") continue;
            try {
              if (can(s.engine, "release")) s.engine.release(i);
            } catch (_) {}
            setUi(s, i, "idle");
          }
        },
      },
      {
        icon: "✕",
        label: "Delete",
        danger: true,
        run: function () {
          deletePad(s, padIdx);
        },
      },
      {
        icon: "↺",
        label: "Reset",
        disabled: !(overrides.any || swapped),
        run: function () {
          // Undo the radial-era overrides: FX → neutral, loop override →
          // follow mode, chop/gate → rebake from the server pad dict.
          if (overrides.fx && can(s.engine, "setPadEffects")) {
            try {
              s.engine.setPadEffects(padIdx, null);
            } catch (_) {}
            delete s.padFx[padIdx];
            savePadFx(s);
            updateFxBadge(s, padIdx);
          }
          p.loopOverride = null;
          updateOverrideBadge(s, padIdx);
          var orig = s.origRegions[padIdx];
          if (orig && pad && pad.stemSlice) {
            pad.stemSlice.startSec = orig.startSec;
            pad.stemSlice.endSec = orig.endSec;
            pad.loopStartSec = orig.loopStartSec;
            pad.loopEndSec = orig.loopEndSec;
            delete s.origRegions[padIdx];
          }
          if (orig || overrides.gate) {
            delete s.gated[padIdx];
            try {
              // Full server-state bake (onset snap included) — the voice
              // playing the stale buffer is stopped by the engine.
              if (can(s.engine, "rebakePad")) s.engine.rebakePad(padIdx);
            } catch (_) {}
            setUi(s, padIdx, "idle");
            drawWave(s, padIdx, p);
          }
          // Undo an "Add sound" source swap LAST so the restored original
          // buffer wins over any rebake above. The engine snapshotted the
          // buffer; s.origSource holds the tile's pre-swap name/color.
          if (swapped && can(s.engine, "restorePadSource")) {
            try { s.engine.restorePadSource(padIdx); } catch (_) {}
            var snap = s.origSource && s.origSource[padIdx];
            if (snap && pad) {
              pad.name = snap.name;
              pad.colorHint = snap.colorHint;
            }
            if (s.origSource) delete s.origSource[padIdx];
            repaintPadSource(s, padIdx);
            setUi(s, padIdx, "idle");
          }
          toast(s, "Pad reset");
        },
      },
    ];

    var geom = wedgeGeometry(actions.length);
    var count = actions.length;
    var cxy = geom.size / 2; // wheel center within its own SVG box
    var R = geom.outer; // for cursor clamping so the wheel stays on-screen
    var vw = window.innerWidth || 0;
    var vh = window.innerHeight || 0;
    var cx = Math.max(R + 12, Math.min(vw - R - 12, x));
    var cy = Math.max(R + 12, Math.min(vh - R - 12, y));

    var backdrop = document.createElement("div");
    backdrop.className = "kit-radial-backdrop";
    backdrop.addEventListener("pointerdown", function (ev) {
      if (ev.target === backdrop) closeRadial(s);
    });
    backdrop.addEventListener("contextmenu", function (ev) {
      ev.preventDefault(); // a second right-click anywhere just re-aims/dismisses
      closeRadial(s);
    });

    var menu = document.createElement("div");
    menu.className = "kit-radial";
    menu.style.left = cx + "px";
    menu.style.top = cy + "px";
    if (p.tint) menu.style.setProperty("--pad-tint", p.tint.r + "," + p.tint.g + "," + p.tint.b);

    // The wheel is a single SVG of equal pie wedges (desktop PadRadialMenu
    // parity). Empty regions of the SVG box carry pointer-events:none so a
    // click outside the ring falls through to the backdrop and dismisses;
    // only the wedge slices capture (icon/label ride on top, inert).
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "kit-radial-svg");
    svg.setAttribute("width", String(geom.size));
    svg.setAttribute("height", String(geom.size));
    svg.setAttribute("viewBox", "0 0 " + geom.size + " " + geom.size);
    menu.appendChild(svg);

    actions.forEach(function (a, i) {
      var g = document.createElementNS(NS, "g");
      g.setAttribute(
        "class",
        "kit-wedge" +
          (a.active ? " is-active" : "") +
          (a.danger ? " is-danger" : "") +
          (a.disabled ? " is-disabled" : "")
      );

      var slice = document.createElementNS(NS, "path");
      slice.setAttribute("class", "kit-wedge-slice");
      slice.setAttribute("d", wedgePath(i, count, geom.inner, geom.outer, cxy, cxy));
      g.appendChild(slice);

      // Native <title> = hover tooltip (the To Sequence dim explainer).
      if (a.tip) {
        var tt = document.createElementNS(NS, "title");
        tt.textContent = a.tip;
        g.appendChild(tt);
      }

      var lp = wedgeLabelPoint(i, count, geom.inner, geom.outer, cxy, cxy);
      var icon = document.createElementNS(NS, "text");
      icon.setAttribute("class", "kit-wedge-icon");
      icon.setAttribute("x", lp.x.toFixed(1));
      icon.setAttribute("y", (lp.y - 6).toFixed(1));
      icon.setAttribute("text-anchor", "middle");
      icon.setAttribute("dominant-baseline", "central");
      icon.textContent = a.icon;
      g.appendChild(icon);

      var label = document.createElementNS(NS, "text");
      label.setAttribute("class", "kit-wedge-label");
      label.setAttribute("x", lp.x.toFixed(1));
      label.setAttribute("y", (lp.y + 9).toFixed(1));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("dominant-baseline", "central");
      label.textContent = a.label;
      g.appendChild(label);

      g.addEventListener("click", function () {
        closeRadial(s);
        if (a.disabled) return;
        try {
          a.run();
        } catch (_) {}
      });
      svg.appendChild(g);
    });

    // Solid center hub over the SVG hole: pad name + a mini waveform (the
    // pad's own peaks, like desktop's center identity), doubling as the
    // cancel zone. HTML so it can host the reused <canvas> waveform.
    var hubD = geom.inner * 2 - 8;
    var hub = document.createElement("button");
    hub.type = "button";
    hub.className = "kit-radial-hub";
    hub.title = "Close";
    hub.style.width = hubD + "px";
    hub.style.height = hubD + "px";
    var hubWave = document.createElement("canvas");
    hubWave.className = "kit-radial-hub-wave";
    var hubName = document.createElement("span");
    hubName.className = "kit-radial-hub-name";
    hubName.textContent = (pad && pad.name) || "Pad " + (padIdx + 1);
    hub.appendChild(hubWave);
    hub.appendChild(hubName);
    hub.addEventListener("click", function () {
      closeRadial(s);
    });
    menu.appendChild(hub);

    backdrop.appendChild(menu);
    document.body.appendChild(backdrop);

    // Paint the hub waveform once the canvas has a laid-out size (reuses
    // engine.peaks like the grid tiles; degrades to the accent underline).
    try {
      drawWaveInto(s, padIdx, hubWave, p.tint || ACCENT, 18);
    } catch (_) {}

    var onKey = function (ev) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        closeRadial(s);
      }
    };
    document.addEventListener("keydown", onKey, true);
    s.radial = { el: backdrop, onKey: onKey };
  }

  // ---------- toast ----------

  function toast(s, msg) {
    try {
      if (s.toastEl && s.toastEl.parentNode) s.toastEl.parentNode.removeChild(s.toastEl);
      var t = document.createElement("div");
      t.className = "kit-toast";
      t.textContent = msg;
      s.root.appendChild(t);
      s.toastEl = t;
      setTimeout(function () {
        if (t.parentNode) t.parentNode.removeChild(t);
        if (s.toastEl === t) s.toastEl = null;
      }, 2000);
    } catch (_) {}
  }

  /** Toast with one action button (Delete's Undo). Same lifecycle as
   * toast(); the button removes the toast before running the action. */
  function toastAction(s, msg, actionLabel, fn, ms) {
    try {
      if (s.toastEl && s.toastEl.parentNode) s.toastEl.parentNode.removeChild(s.toastEl);
      var t = document.createElement("div");
      t.className = "kit-toast";
      var text = document.createElement("span");
      text.textContent = msg;
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-toast-act";
      b.textContent = actionLabel;
      b.addEventListener("click", function () {
        if (t.parentNode) t.parentNode.removeChild(t);
        if (s.toastEl === t) s.toastEl = null;
        try {
          fn();
        } catch (_) {}
      });
      t.appendChild(text);
      t.appendChild(b);
      s.root.appendChild(t);
      s.toastEl = t;
      setTimeout(function () {
        if (t.parentNode) t.parentNode.removeChild(t);
        if (s.toastEl === t) s.toastEl = null;
      }, ms || 5000);
    } catch (_) {}
  }

  // ---------- chop-editor integration ----------

  /**
   * Re-slice a pad from its stem buffer with a user-set region — the same
   * bake path as PadEngine.prepare (normalize → edge fades → exact-length
   * seam bake) EXCEPT the onset-phase snap, which is skipped: the user put
   * the cut exactly where they want it. Swaps the engine's baked entry and
   * redraws the pad waveform in place.
   *
   * `region.preserveLength` (chop editor "Keep timing", mobile
   * SampleTrimmerSheet parity): the pad KEEPS its original window/loop
   * length and the trim only GATES the audio — the full original region is
   * baked with everything outside [startSec, endSec] silenced (5 ms ramps
   * at the gate edges), so a looping pad still fires on its musical cycle
   * with silence filling the rest. The pad dict is NOT mutated in this
   * mode; the radial Reset rebakes the ungated server state.
   * @param {number} padIdx
   * @param {{startSec: number, endSec: number, preserveLength?: boolean}}
   *   region seconds into the stem
   * @returns {boolean} true when the pad was re-sliced
   */
  function applyPadRegion(padIdx, region) {
    var s = current;
    if (!s || !s.alive || !s.engine || !s.ctx || !s.dsp) return false;
    if (!region || typeof region.startSec !== "number" || typeof region.endSec !== "number")
      return false;
    var startSec = Math.max(0, region.startSec);
    var endSec = region.endSec;
    if (!isFinite(startSec) || !isFinite(endSec) || !(endSec > startSec + 0.02)) return false;
    var pad = padByIdx(s, padIdx);
    if (!pad || !pad.stemSlice) return false;
    var stem = s.stems && s.stems[pad.stemSlice.stemRole];
    if (!stem) return false;
    // The rebaked entry is swapped straight into the engine's bake map —
    // deep coupling, but kit.js and padengine.js ship as a pair, and the
    // guard keeps a differently-built engine from throwing.
    if (!(s.engine._baked instanceof Map)) return false;

    var preserve = !!region.preserveLength;
    if (preserve && typeof s.dsp.gateRegionInPlace !== "function") return false;

    var sr = stem.sampleRate;
    var stemLen = stem.length;
    // Preserve mode bakes the pad's CURRENT window (analyzer loop region
    // when present — the engine's own region preference — else the slice);
    // normal mode re-windows to the user's region.
    var winStartSec = startSec;
    var winEndSec = endSec;
    if (preserve) {
      if (pad.loopStartSec != null && pad.loopEndSec != null && pad.loopEndSec > pad.loopStartSec) {
        winStartSec = pad.loopStartSec;
        winEndSec = pad.loopEndSec;
      } else {
        winStartSec = pad.stemSlice.startSec;
        winEndSec = pad.stemSlice.endSec;
      }
      winStartSec = Math.max(0, winStartSec);
      if (!isFinite(winStartSec) || !isFinite(winEndSec)) return false;
    }
    var startFrame = Math.trunc(winStartSec * sr);
    var endFrame = Math.min(Math.trunc(winEndSec * sr), stemLen);
    var bodyCount = endFrame - startFrame;
    if (!(bodyCount > 8) || startFrame >= stemLen) return false;

    var mayLoop = !!pad.loopable || pad.loopPointSec != null || pad.loopStartSec != null;
    var contSec = mayLoop
      ? (typeof s.dsp.LOOP_CONTINUATION_SEC === "number" ? s.dsp.LOOP_CONTINUATION_SEC : 0.035)
      : 0;
    var extra = Math.min(Math.trunc(contSec * sr), Math.max(0, stemLen - startFrame - bodyCount));

    var channels = [];
    for (var c = 0; c < stem.numberOfChannels; c++) {
      channels.push(
        new Float32Array(stem.getChannelData(c).subarray(startFrame, startFrame + bodyCount + extra))
      );
    }
    if (preserve) {
      // Gate BEFORE normalize so the peak target measures what will
      // actually sound. Gate frames are window-relative; the continuation
      // tail past the gate end is silenced too (gateRegionInPlace zeroes
      // to the buffer end) so the seam bake can't reintroduce gated audio.
      var gs = Math.trunc(Math.max(winStartSec, startSec) * sr) - startFrame;
      var ge = Math.trunc(Math.min(winEndSec, endSec) * sr) - startFrame;
      if (!(ge > gs + Math.trunc(0.02 * sr))) return false; // gate misses the window
      s.dsp.gateRegionInPlace(channels, sr, gs, ge);
    }
    s.dsp.normalizePeak(channels);
    s.dsp.applyEdgeFades(channels, sr);

    var toBuf = function (chs) {
      var buf = s.ctx.createBuffer(chs.length, chs[0].length, sr);
      for (var i = 0; i < chs.length; i++) buf.copyToChannel(chs[i], i);
      return buf;
    };
    var oneShotBuffer = toBuf(channels);
    var loopChannels = null;
    var loopBuffer = null;
    if (mayLoop) {
      loopChannels = s.dsp.exactCrossfaded(channels, sr, bodyCount, s.dsp.chooseCrossfadeMs(pad));
      loopBuffer = toBuf(loopChannels);
    }

    // Stop the old voice — it plays the stale buffer at the stale length.
    try {
      if (can(s.engine, "release")) s.engine.release(padIdx);
    } catch (_) {}
    var pEl = s.padEls[padIdx];
    if (pEl) setUi(s, padIdx, "idle");

    s.engine._baked.set(padIdx, {
      pad: pad,
      sampleRate: sr,
      bodySec: bodyCount / sr,
      shiftSec: 0, // onset snap deliberately skipped for user-set regions
      oneShotBuffer: oneShotBuffer,
      oneShotChannels: channels,
      loopBuffer: loopBuffer,
      loopChannels: loopChannels,
    });

    if (preserve) {
      // Window untouched — record the gate so the radial Reset knows this
      // pad diverges from server state (and what the gate was).
      if (s.gated) s.gated[padIdx] = { startSec: startSec, endSec: endSec };
    } else {
      // Snapshot the pre-edit region ONCE (first edit wins) so the radial
      // Reset can return the pad to the state it was baked with.
      if (s.origRegions && s.origRegions[padIdx] == null) {
        s.origRegions[padIdx] = {
          startSec: pad.stemSlice.startSec,
          endSec: pad.stemSlice.endSec,
          loopStartSec: pad.loopStartSec,
          loopEndSec: pad.loopEndSec,
        };
      }
      // Keep the pad dict honest so any later full re-bake agrees.
      pad.stemSlice.startSec = startSec;
      pad.stemSlice.endSec = endSec;
      if (pad.loopStartSec != null) {
        pad.loopStartSec = startSec;
        pad.loopEndSec = endSec;
      }
      if (s.gated) delete s.gated[padIdx]; // a re-window supersedes any gate
    }

    if (pEl) drawWave(s, padIdx, pEl); // peaks() now reads the new bake
    return true;
  }

  // ---------- errors ----------

  function showError(s, msg) {
    try {
      s.root.innerHTML = "";
      var div = document.createElement("div");
      div.className = "kit-error";
      div.textContent = msg;
      s.root.appendChild(div);
    } catch (_) {}
  }

  /** Mount a curated sample pack (/api/sample-packs/{packId}) onto the pad
   * surface: each pad's audio file becomes its own single-pad "stem" with a
   * whole-buffer slice, so PadEngine plays them as one-shots with the same
   * normalize/edge-fade path the song kits get. */
  function mountPack(desc) {
    var root = document.getElementById("kit-root");
    if (!root || !desc || !desc.packId) return;
    if (current) unmount();
    current = {
      root: root, entry: null, alive: true, ctx: null, engine: null,
      pads: [], padEls: [], mode: "tap", quantize: "bar", latch: false,
      raf: 0, onResize: null, stems: null, dsp: null, radial: null,
      transportTimer: 0, engineTransport: null, engineTransportTimer: 0,
      // Packs keep the 16 grid (their manifests are 16-pad) and have no
      // entry, so feedback stays inert (noteTrigger requires s.entry).
      view: "grid", padCount: 16, layersEl: null, layerRows: null,
      fb: { events: [], startedAt: {}, timer: 0 },
      origRegions: {}, gated: {}, padFx: {},
      fxKey: "pack:" + desc.packId, // pack FX persist per pack, not per song
      fxPop: null, deleted: {},
      origSource: {}, pickerPop: null, pickerPreview: null,
    };
    renderShell(current);
    var s = current;
    if (s.titleEl) s.titleEl.textContent = desc.name || "Pack";
    var AC = window.AudioContext || window.webkitAudioContext;
    s.ctx = new AC();
    // desc.manifest = a manifest already in hand (Borrow loops); otherwise
    // fetch the curated pack. Same pad-loading path either way — sampleUrl
    // pads resolve as absolute URLs below.
    var manifestP = desc.manifest
      ? Promise.resolve(desc.manifest)
      : fetch("/api/sample-packs/" + encodeURIComponent(desc.packId))
          .then(function (r) {
            if (!r.ok) throw new Error("pack HTTP " + r.status);
            return r.json();
          });
    manifestP
      .then(function (manifest) {
        if (!s.alive) return;
        var pads = (manifest && manifest.pads) || [];
        if (!pads.length) throw new Error("pack has no pads");
        var stems = {};
        var kitPads = [];
        var loads = pads.map(function (p, i) {
          var idx = typeof p.padIdx === "number" ? p.padIdx : i;
          var fname = p.sampleFile || p.file || p.sampleUrl || p.filename;
          if (!fname) return null;
          var url = /^https?:|^\//.test(fname)
            ? fname
            : "/api/sample-packs/" + encodeURIComponent(desc.packId) +
              "/pads/" + encodeURIComponent(fname);
          return fetch(url)
            .then(function (r) { return r.ok ? r.arrayBuffer() : Promise.reject(new Error("pad HTTP " + r.status)); })
            .then(function (b) { return s.ctx.decodeAudioData(b); })
            .then(function (buf) {
              var role = "pad" + idx;
              stems[role] = buf;
              kitPads.push({
                padIdx: idx,
                name: p.name || ("Pad " + (idx + 1)),
                colorHint: p.colorHint || desc.paletteHint || null,
                stemSlice: { stemRole: role, startSec: 0, endSec: buf.duration },
                loopable: !!p.loopable,
              });
            })
            .catch(function () { return null; });
        });
        return Promise.all(loads).then(function () {
          if (!s.alive) return;
          if (!kitPads.length) throw new Error("no pack pads decoded");
          kitPads.sort(function (a, b) { return a.padIdx - b.padIdx; });
          s.kit = { name: desc.name || manifest.name || "Pack", pads: kitPads };
          s.pads = kitPads;
          return import("./padengine.js?v=5").then(function (mod) {
            if (!s.alive) return;
            var PadEngine = mod && (mod.PadEngine || (mod.default && mod.default.PadEngine));
            s.dsp = mod;
            s.stems = stems;
            s.engine = new PadEngine(s.ctx, s.ctx.destination);
            s.engine.setStems(stems);
            s.engine.setKit(s.kit, { tempoBpm: manifest.tempoBpm || 0 });
            return Promise.resolve(s.engine.prepare()).then(function () {
              if (!s.alive) return;
              applyStoredFx(s);
              attachEngineState(s);
              renderPads(s);
              startRaf(s);
            });
          });
        });
      })
      .catch(function (err) {
        if (s.alive) showError(s, "Pack failed to load: " + ((err && err.message) || err));
      });
  }

  /** Mount a manifest already in hand (Borrow loops) — loopable file pads
   * decoded straight from their sampleUrls. */
  function mountManifest(manifest) {
    if (!manifest || !manifest.packId) return;
    mountPack({
      packId: manifest.packId,
      name: manifest.name,
      paletteHint: manifest.paletteHint,
      manifest: manifest,
    });
  }

  window.JamnKit = {
    mount: mount,
    unmount: unmount,
    mountPack: mountPack,
    mountManifest: mountManifest,
    // Live handles for sibling tools (sequencer drives the same engine).
    engine: function () { return current && current.engine; },
    pads: function () { return (current && current.pads) || []; },
    audioContext: function () { return current && current.ctx; },
    // Current kit kind: 'auto' (default song kit), 'drums', 'flip', or
    // 'pack' — lets Remix's pads-follow respect the mode the user chose
    // instead of force-switching to the drum kit on every Re-Drum.
    kind: function () {
      if (!current) return null;
      if (!current.entry) return "pack";
      return current.kitKind || "auto";
    },
    // Chop-editor integration: re-slice a pad from its stem with a
    // user-set region (onset snap skipped) and swap buffers in place.
    applyPadRegion: applyPadRegion,
    // Shared pad-waveform painter for sibling surfaces (Launchpad) so they
    // draw the SAME coarse silhouette as Jam Pads instead of duplicating the
    // renderer — the pad's baked buffer lives on the shared engine, keyed by
    // padIdx. `tint` may be {r,g,b} or any CSS color string; `binsOpt`
    // matches drawWaveInto (0 default, <0 px-per-bin, >0 exact). Returns
    // false when there's no active kit or the canvas has no laid-out size.
    drawPadWave: function (canvas, padIdx, tint, binsOpt) {
      if (!current || !canvas) return false;
      return drawWaveInto(current, padIdx, canvas, resolveTint(tint), binsOpt || 0);
    },
    // Pure helpers exposed for the DOM-free smoke test only.
    _internals: {
      resolveStemUrl: resolveStemUrl,
      parseColor: parseColor,
      pickInstantGroove: pickInstantGroove,
      fmtTime: fmtTime,
      resolvePadCount: resolvePadCount,
      layerCategories: layerCategories,
      padsInCategory: padsInCategory,
      padStepFlags: padStepFlags,
      pushPadEvent: pushPadEvent,
      syncEngineTransport: syncEngineTransport,
      radialGeometry: radialGeometry,
      wedgeGeometry: wedgeGeometry,
      wedgeAngles: wedgeAngles,
      wedgePath: wedgePath,
      wedgeLabelPoint: wedgeLabelPoint,
      parsePadFxStore: parsePadFxStore,
      serializePadFxStore: serializePadFxStore,
      padOverrides: padOverrides,
      cutoffFromSlider: cutoffFromSlider,
      sliderFromCutoff: sliderFromCutoff,
      pickerPackList: pickerPackList,
      pickerSongPads: pickerSongPads,
      packPadRows: packPadRows,
    },
  };
})();
