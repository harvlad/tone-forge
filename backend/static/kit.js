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
    var engineP = import("./padengine.js?v=2");

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
   * feature-checked at call time — a partial host degrades per-control. */
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
      var pad = byIdx[i];
      var el = document.createElement("button");
      el.type = "button";
      el.className = "kit-pad";
      if (!pad) {
        el.classList.add("is-empty");
        el.disabled = true;
        s.gridEl.appendChild(el);
        s.padEls[i] = null;
        continue;
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

      el.appendChild(name);
      if (dots) el.appendChild(dots);
      el.appendChild(canvas);
      el.appendChild(sweep);
      el.appendChild(ring);
      el.appendChild(badge);
      s.gridEl.appendChild(el);

      s.padEls[i] = {
        el: el, ring: ring, sweep: sweep, canvas: canvas, badge: badge, tint: tint,
        ui: "idle", loop: false,
        loopOverride: null, // radial per-pad override: true=loop, false=one-shot, null=follow mode
        lp: null, lpX: 0, lpY: 0, // long-press (radial) timer state
      };
      wirePad(s, i, el);
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
    // grid is plumbed even though today's engine quantizes everything to
    // its lock cycle (bar-ish; Beat==Bar until it can split) — a future
    // engine reads it, current one ignores unknown opts.
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

  /** Circular action menu around (x, y) — the mobile hold-radial / desktop
   * right-click wheel, in DOM. Dark ring of tinted round buttons; labels on
   * hover; click-away or Escape dismisses. */
  function openRadial(s, padIdx, x, y) {
    closeRadial(s);
    var p = s.padEls[padIdx];
    if (!p) return;
    var pad = padByIdx(s, padIdx);
    var effLoop = effectiveLoop(s, p);
    var sounding = p.ui === "armed" || p.ui === "playing";

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
        icon: "✎",
        label: "Edit chop",
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
    ];

    var R = 92; // wheel radius (button centers)
    var vw = window.innerWidth || 0;
    var vh = window.innerHeight || 0;
    var cx = Math.max(R + 36, Math.min(vw - R - 36, x));
    var cy = Math.max(R + 36, Math.min(vh - R - 36, y));

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

    // Unifying wheel disc behind hub + satellites — without it the
    // buttons read as unrelated floating circles over the pad noise.
    var ring = document.createElement("div");
    ring.className = "kit-radial-ring";
    menu.appendChild(ring);

    var hub = document.createElement("button");
    hub.type = "button";
    hub.className = "kit-radial-hub";
    hub.textContent = (pad && pad.name) || "Pad " + (padIdx + 1);
    hub.title = "Close";
    hub.addEventListener("click", function () {
      closeRadial(s);
    });
    menu.appendChild(hub);

    actions.forEach(function (a, i) {
      var b = document.createElement("button");
      b.type = "button";
      b.className =
        "kit-radial-btn" +
        (a.active ? " is-active" : "") +
        (a.danger ? " is-danger" : "") +
        (a.disabled ? " is-disabled" : "");
      var angle = (-90 + (360 / actions.length) * i) * (Math.PI / 180); // start at top
      b.style.left = Math.round(Math.cos(angle) * R) + "px";
      b.style.top = Math.round(Math.sin(angle) * R) + "px";
      var icon = document.createElement("span");
      icon.className = "kit-radial-icon";
      icon.textContent = a.icon;
      var label = document.createElement("span");
      label.className = "kit-radial-label";
      label.textContent = a.label;
      b.appendChild(icon);
      b.appendChild(label);
      b.addEventListener("click", function () {
        closeRadial(s);
        if (a.disabled) return;
        try {
          a.run();
        } catch (_) {}
      });
      menu.appendChild(b);
    });

    backdrop.appendChild(menu);
    document.body.appendChild(backdrop);

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

  // ---------- chop-editor integration ----------

  /**
   * Re-slice a pad from its stem buffer with a user-set region — the same
   * bake path as PadEngine.prepare (normalize → edge fades → exact-length
   * seam bake) EXCEPT the onset-phase snap, which is skipped: the user put
   * the cut exactly where they want it. Swaps the engine's baked entry and
   * redraws the pad waveform in place.
   * @param {number} padIdx
   * @param {{startSec: number, endSec: number}} region seconds into the stem
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

    var sr = stem.sampleRate;
    var stemLen = stem.length;
    var startFrame = Math.trunc(startSec * sr);
    var endFrame = Math.min(Math.trunc(endSec * sr), stemLen);
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

    // Keep the pad dict honest so any later full re-bake agrees.
    pad.stemSlice.startSec = startSec;
    pad.stemSlice.endSec = endSec;
    if (pad.loopStartSec != null) {
      pad.loopStartSec = startSec;
      pad.loopEndSec = endSec;
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
    };
    renderShell(current);
    var s = current;
    if (s.titleEl) s.titleEl.textContent = desc.name || "Pack";
    var AC = window.AudioContext || window.webkitAudioContext;
    s.ctx = new AC();
    fetch("/api/sample-packs/" + encodeURIComponent(desc.packId))
      .then(function (r) {
        if (!r.ok) throw new Error("pack HTTP " + r.status);
        return r.json();
      })
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
          return import("./padengine.js?v=2").then(function (mod) {
            if (!s.alive) return;
            var PadEngine = mod && (mod.PadEngine || (mod.default && mod.default.PadEngine));
            s.dsp = mod;
            s.stems = stems;
            s.engine = new PadEngine(s.ctx, s.ctx.destination);
            s.engine.setStems(stems);
            s.engine.setKit(s.kit, { tempoBpm: manifest.tempoBpm || 0 });
            return Promise.resolve(s.engine.prepare()).then(function () {
              if (!s.alive) return;
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

  window.JamnKit = {
    mount: mount,
    unmount: unmount,
    mountPack: mountPack,
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
    },
  };
})();
