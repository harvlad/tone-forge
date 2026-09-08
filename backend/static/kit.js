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
    if (s.transportTimer) clearInterval(s.transportTimer);
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

    // Loading skeleton: 16 shimmer tiles while stems fetch + decode.
    for (var i = 0; i < PAD_COUNT; i++) {
      var sk = document.createElement("div");
      sk.className = "kit-pad is-skeleton";
      grid.appendChild(sk);
    }

    s.root.appendChild(head);
    s.root.appendChild(transport);
    s.root.appendChild(grid);

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
        setUi(s, idx, "armed");
      } catch (_) {}
    });
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

      // Corner badge showing a radial per-pad loop/one-shot override.
      var badge = document.createElement("span");
      badge.className = "kit-pad-badge";

      el.appendChild(name);
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
    s.onResize = function () {
      drawAllWaves(s);
    };
    window.addEventListener("resize", s.onResize);
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
    s.engine.trigger(padIdx, opts);
    // Armed until padProgress (or onstate) reports actual start — the
    // pulsing border says "waiting for the beat", not silence. Unquantized
    // loops start now, so they're playing already.
    setUi(s, padIdx, q ? "armed" : "playing");
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
      transportTimer: 0,
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
    // Chop-editor integration: re-slice a pad from its stem with a
    // user-set region (onset snap skipped) and swap buffers in place.
    applyPadRegion: applyPadRegion,
    // Pure helpers exposed for the DOM-free smoke test only.
    _internals: {
      resolveStemUrl: resolveStemUrl,
      parseColor: parseColor,
      pickInstantGroove: pickInstantGroove,
      fmtTime: fmtTime,
    },
  };
})();
