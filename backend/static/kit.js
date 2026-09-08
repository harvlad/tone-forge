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
        padEls: [], // { el, ring, sweep, canvas, tint, ui:'idle'|'armed'|'playing', loop:bool }
        mode: "tap", // DEFAULT Tap
        latch: false,
        raf: 0,
        onResize: null,
        kitKind: (opts && opts.kind && opts.kind !== "auto") ? opts.kind : null,
      };
      if (!entry || !entry.id || !entry.result) {
        showError(current, "No analysis loaded.");
        return;
      }
      renderShell(current);
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

  function load(s) {
    var entry = s.entry;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return Promise.reject(new Error("Web Audio unsupported"));
    s.ctx = new AC();

    var kindQ = s.kitKind ? "&kind=" + encodeURIComponent(s.kitKind) : "";
    var kitP = fetch("/api/song/" + encodeURIComponent(entry.id) + "/kit?pads=16" + kindQ).then(
      function (r) {
        if (!r.ok) throw new Error("kit HTTP " + r.status);
        return r.json();
      }
    );
    var engineP = import("./padengine.js");

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
      setStatus("Loading stems 0/" + roles.length + "…");

      return Promise.all(
        roles.map(function (role) {
          var url = resolveStemUrl(paths[role]);
          // Cross-origin R2 presigned URLs are unreachable from a browser
          // (bucket sends no CORS headers) — stream via the backend proxy.
          if (url && url.indexOf(window.location.origin) !== 0 && /^https?:/i.test(url)) {
            url = window.location.origin + "/api/history/" +
              encodeURIComponent(entry.id) + "/stem-audio/" +
              encodeURIComponent(role);
          }
          if (!url) return null;
          var proxied = url.indexOf("/stem-audio/") !== -1;
          return fetch(url)
            .then(function (r) {
              if (!r.ok) throw new Error(role + " HTTP " + r.status);
              return r.arrayBuffer();
            })
            .then(function (buf) {
              // Safari's decodeAudioData can't decode FLAC (Chrome can) —
              // retry once via the proxy's on-the-fly WAV transcode.
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
            .then(function (audio) {
              stemsDone++;
              setStatus("Loading stems " + stemsDone + "/" + roles.length + "…");
              return { role: role, buffer: audio };
            })
            .catch(function () {
              stemsDone++;
              setStatus("Loading stems " + stemsDone + "/" + roles.length + "…");
              return null; // a single bad stem mutes its pads, not the kit
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

    // Tap / Loop segmented toggle (DEFAULT Tap).
    var seg = document.createElement("div");
    seg.className = "kit-seg";
    seg.setAttribute("role", "group");
    ["tap", "loop"].forEach(function (mode) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = "kit-seg-btn" + (mode === s.mode ? " is-on" : "");
      b.textContent = mode === "tap" ? "Tap" : "Loop";
      b.addEventListener("click", function () {
        s.mode = mode;
        var btns = seg.querySelectorAll(".kit-seg-btn");
        for (var i = 0; i < btns.length; i++) btns[i].classList.remove("is-on");
        b.classList.add("is-on");
        s.latchEl.classList.toggle("is-disabled", mode !== "loop");
      });
      seg.appendChild(b);
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

    controls.appendChild(seg);
    controls.appendChild(latch);
    controls.appendChild(stop);
    head.appendChild(title);
    head.appendChild(status);
    head.appendChild(controls);

    var grid = document.createElement("div");
    grid.className = "kit-grid";
    s.gridEl = grid;

    // Loading skeleton: 16 shimmer tiles while stems fetch + decode.
    for (var i = 0; i < PAD_COUNT; i++) {
      var sk = document.createElement("div");
      sk.className = "kit-pad is-skeleton";
      grid.appendChild(sk);
    }

    s.root.appendChild(head);
    s.root.appendChild(grid);
  }

  function renderPads(s) {
    if (s.kit && s.kit.name) s.titleEl.textContent = s.kit.name;
    s.gridEl.innerHTML = "";
    s.padEls = [];

    // Server pad order — kit=5+ already lays rows out grouped by category,
    // so a straight padIdx grid reproduces the mobile grouped rack.
    var byIdx = [];
    s.pads.forEach(function (p) {
      if (p && typeof p.padIdx === "number") byIdx[p.padIdx] = p;
    });

    for (var i = 0; i < PAD_COUNT; i++) {
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

      var canvas = document.createElement("canvas");
      canvas.className = "kit-pad-wave";

      var sweep = document.createElement("span");
      sweep.className = "kit-pad-sweep";

      var ring = document.createElement("span");
      ring.className = "kit-pad-ring";

      el.appendChild(name);
      el.appendChild(canvas);
      el.appendChild(sweep);
      el.appendChild(ring);
      s.gridEl.appendChild(el);

      s.padEls[i] = { el: el, ring: ring, sweep: sweep, canvas: canvas, tint: tint, ui: "idle", loop: false };
      wirePad(s, i, el);
    }

    // Waveforms need laid-out canvas sizes — draw on the next frame.
    requestAnimationFrame(function () {
      drawAllWaves(s);
    });
    s.onResize = function () {
      drawAllWaves(s);
    };
    window.addEventListener("resize", s.onResize);
  }

  function wirePad(s, padIdx, el) {
    el.addEventListener("pointerdown", function (ev) {
      try {
        ev.preventDefault();
        if (s.ctx && s.ctx.state === "suspended") s.ctx.resume().catch(function () {});
        if (ev.pointerId !== undefined && el.setPointerCapture) {
          try {
            el.setPointerCapture(ev.pointerId);
          } catch (_) {}
        }
        padDown(s, padIdx);
      } catch (_) {}
    });
    var up = function () {
      try {
        padUp(s, padIdx);
      } catch (_) {}
    };
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    // Flash pressed feedback regardless of mode.
    el.addEventListener("pointerdown", function () {
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
    if (s.mode === "tap") {
      // One-shot fire-and-forget; release is ignored (padUp checks .loop).
      p.loop = false;
      s.engine.trigger(padIdx, { loop: false, quantized: false });
      setUi(s, padIdx, "playing"); // immediate start; rAF ends it via padProgress
      return;
    }
    // Loop mode.
    if (s.latch && (p.ui === "armed" || p.ui === "playing")) {
      // Latch ON: second tap toggles off.
      if (can(s.engine, "release")) s.engine.release(padIdx);
      setUi(s, padIdx, "idle");
      return;
    }
    p.loop = true;
    s.engine.trigger(padIdx, { loop: true, quantized: true });
    // Armed until padProgress (or onstate) reports actual start — the
    // pulsing border says "waiting for the beat", not silence.
    setUi(s, padIdx, "armed");
  }

  function padUp(s, padIdx) {
    var p = s.padEls[padIdx];
    if (!p) return;
    if (s.mode !== "loop" || s.latch || !p.loop) return; // tap = fire-and-forget; latch holds
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
    var canvas = p.canvas;
    var w = canvas.clientWidth,
      h = canvas.clientHeight;
    if (!w || !h) return;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    var g = canvas.getContext("2d");
    if (!g) return;
    g.scale(dpr, dpr);
    g.clearRect(0, 0, w, h);

    var bins = Math.max(16, Math.min(64, Math.round(w / 4)));
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
      g.fillStyle = rgba(p.tint, 0.9);
      g.fillRect(0, h - 3, 26, 3);
      return;
    }
    // Mirrored bars, light strokes — PadWaveformBars look.
    var n = peaks.length;
    var bw = w / n;
    g.fillStyle = rgba(p.tint, 0.9);
    for (var i = 0; i < n; i++) {
      var v = Math.max(0, Math.min(1, Number(peaks[i]) || 0));
      var bh = Math.max(1.5, v * h);
      g.fillRect(i * bw + bw * 0.15, (h - bh) / 2, Math.max(0.6, bw * 0.7), bh);
    }
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
      pads: [], padEls: [], mode: "tap", latch: false, raf: 0, onResize: null,
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
          return import("./padengine.js").then(function (mod) {
            if (!s.alive) return;
            var PadEngine = mod && (mod.PadEngine || (mod.default && mod.default.PadEngine));
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
    // Pure helpers exposed for the DOM-free smoke test only.
    _internals: { resolveStemUrl: resolveStemUrl, parseColor: parseColor },
  };
})();
