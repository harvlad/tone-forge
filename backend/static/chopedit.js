/* chopedit.js — web chop-boundary editor modal, the browser counterpart of
 * jam-desktop's ChopEditorSheet (jam-desktop/Sources/JamDesktop/ChopEditor/
 * ChopEditorSheet.swift) and the iOS preview-only sheet. Classic script:
 * defines window.JamnChopEdit = { open, close }.
 *
 *   JamnChopEdit.open({ pad, entry, stemBuffer, audioContext, tempoBpm, onSave })
 *
 * Shows the pad's stem waveform inside a ±16 s context window with the
 * region highlighted between two draggable accent handles. Drags grab
 * whichever handle is closer to the pointer-down (desktop semantics); on
 * release the region LENGTH bar-snaps at the song tempo (ChopPlayer.
 * loopRegionEndSec semantics: whole bars, min 1 bar — else 0.5 s when no
 * tempo). Play previews the current region as a plain looping
 * AudioBufferSourceNode with 10 ms gain ramps; Save hands {startSec,endSec}
 * back to the caller — this module persists nothing itself.
 */
(function () {
  "use strict";

  var MIN_NO_TEMPO_SEC = 0.5; // min region length when tempo is unknown
  var SNAP_TOLERANCE_SEC = 0.01; // ±10 ms: "already whole bars, leave it"
  var CONTEXT_PAD_SEC = 16; // waveform context on each side of the region
  var RAMP_SEC = 0.01; // preview gain ramp (click-free start/stop)

  // ---------- pure helpers (exposed on _internals for chopedit.test.mjs) ----------

  /** One 4/4 bar at the song tempo, or null without tempo (PadEngine._barSeconds). */
  function barSeconds(tempoBpm) {
    if (!(tempoBpm > 0)) return null;
    return (60.0 / tempoBpm) * 4.0;
  }

  /** Min region length: one bar at known tempo, else 0.5 s. */
  function minLengthSec(tempoBpm) {
    var bar = barSeconds(tempoBpm);
    return bar != null ? bar : MIN_NO_TEMPO_SEC;
  }

  /** Whole-bar count for a length within ±10 ms, else null (display only). */
  function wholeBars(lengthSec, tempoBpm) {
    var bar = barSeconds(tempoBpm);
    if (bar == null || !(lengthSec > 0)) return null;
    var bars = Math.round(lengthSec / bar);
    if (bars < 1) return null;
    return Math.abs(lengthSec - bars * bar) <= SNAP_TOLERANCE_SEC ? bars : null;
  }

  /** Start-handle drag clamp: stay in window, keep min length to the end. */
  function clampStart(sec, windowStart, endSec, minLen) {
    return Math.min(Math.max(windowStart, sec), endSec - minLen);
  }

  /** End-handle drag clamp: stay in window, keep min length from the start. */
  function clampEnd(sec, windowEnd, startSec, minLen) {
    return Math.max(Math.min(windowEnd, sec), startSec + minLen);
  }

  /**
   * Bar-snap the region length on handle release (ChopPlayer.loopRegionEndSec:
   * bars = max(1, round(len/bar))). `anchor` is the edge the user did NOT
   * drag — it stays fixed and the dragged edge moves to the snapped length.
   * Whole bars are dropped (never below 1) if the snapped edge would leave
   * [minSec, maxSec]; a final hard clamp covers "even one bar doesn't fit".
   * Lengths already within ±10 ms of whole bars pass through untouched.
   */
  function snapLengthToBars(startSec, endSec, tempoBpm, anchor, minSec, maxSec) {
    var bar = barSeconds(tempoBpm);
    var region = { startSec: startSec, endSec: endSec };
    if (bar == null) return region;
    var len = endSec - startSec;
    var bars = Math.max(1, Math.round(len / bar));
    if (Math.abs(len - bars * bar) <= SNAP_TOLERANCE_SEC) return region;
    if (anchor === "end") {
      var s = endSec - bars * bar;
      while (bars > 1 && minSec != null && s < minSec) {
        bars -= 1;
        s = endSec - bars * bar;
      }
      if (minSec != null && s < minSec) s = minSec;
      region.startSec = s;
    } else {
      var e = startSec + bars * bar;
      while (bars > 1 && maxSec != null && e > maxSec) {
        bars -= 1;
        e = startSec + bars * bar;
      }
      if (maxSec != null && e > maxSec) e = maxSec;
      region.endSec = e;
    }
    return region;
  }

  /** ±16 s waveform context around the region, clamped to the stem. */
  function computeWindow(regionStart, regionEnd, stemDurationSec) {
    return {
      startSec: Math.max(0, regionStart - CONTEXT_PAD_SEC),
      endSec: Math.min(stemDurationSec, regionEnd + CONTEXT_PAD_SEC),
    };
  }

  /**
   * The pad's current playable region: the analyzer's explicit
   * [loopStartSec, loopEndSec] when present (real-downbeat bars —
   * PadEngine._loopRegion prefers them verbatim), else the raw stemSlice.
   */
  function initialRegion(pad) {
    if (!pad) return null;
    var ls = pad.loopStartSec;
    var le = pad.loopEndSec;
    if (ls != null && le != null && le > ls) return { startSec: ls, endSec: le };
    var slice = pad.stemSlice;
    if (slice && slice.endSec > slice.startSec) {
      return { startSec: slice.startSec, endSec: slice.endSec };
    }
    return null;
  }

  function fmtSec(v) {
    return v.toFixed(2) + "s";
  }

  /** Center readout: "7.56s long", plus "· N bars" when exactly whole bars. */
  function lengthLabel(startSec, endSec, tempoBpm) {
    var len = endSec - startSec;
    var bars = wholeBars(len, tempoBpm);
    var txt = fmtSec(len) + " long";
    if (bars != null) txt += " · " + bars + (bars === 1 ? " bar" : " bars");
    return txt;
  }

  /** Max |sample| per bin over [windowStart, windowEnd] of channel 0. */
  function computePeaks(channelData, sampleRate, windowStart, windowEnd, bins) {
    var lo = Math.max(0, Math.floor(windowStart * sampleRate));
    var hi = Math.min(channelData.length, Math.ceil(windowEnd * sampleRate));
    var peaks = new Array(bins);
    var span = hi - lo;
    if (span <= 0) {
      for (var z = 0; z < bins; z++) peaks[z] = 0;
      return peaks;
    }
    for (var b = 0; b < bins; b++) {
      var s0 = lo + Math.floor((b * span) / bins);
      var s1 = lo + Math.floor(((b + 1) * span) / bins);
      var m = 0;
      // Stride large bins so a 32 s window stays cheap at open time.
      var step = Math.max(1, Math.floor((s1 - s0) / 64));
      for (var i = s0; i < s1; i += step) {
        var a = Math.abs(channelData[i]);
        if (a > m) m = a;
      }
      peaks[b] = m;
    }
    return peaks;
  }

  // ---------- modal state (one editor at a time) ----------

  var current = null;

  function open(opts) {
    close();
    opts = opts || {};
    var pad = opts.pad;
    var region = initialRegion(pad);
    if (!region || !document.body) return;

    var s = {
      alive: true,
      pad: pad,
      entry: opts.entry,
      tempoBpm: opts.tempoBpm > 0 ? opts.tempoBpm : null,
      onSave: typeof opts.onSave === "function" ? opts.onSave : null,
      // Pad-truth boundaries: Reset target + hasChanges baseline.
      original: { startSec: region.startSec, endSec: region.endSec },
      startSec: region.startSec,
      endSec: region.endSec,
      buffer: null,
      peaks: null,
      window: null,
      ctx: opts.audioContext || null,
      ownsCtx: false,
      preview: null, // { src, gain }
      activeHandle: null, // "start" | "end" while dragging
      els: {},
      onKeyDown: null,
    };
    current = s;

    if (!s.ctx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        s.ctx = new AC();
        s.ownsCtx = true;
      }
    }

    buildDom(s);

    if (opts.stemBuffer) {
      acceptBuffer(s, opts.stemBuffer);
    } else {
      fetchStem(s);
    }
  }

  function close() {
    var s = current;
    current = null;
    if (!s) return;
    s.alive = false;
    stopPreview(s);
    if (s.onKeyDown) document.removeEventListener("keydown", s.onKeyDown, true);
    if (s.ownsCtx && s.ctx && s.ctx.state !== "closed") {
      try {
        s.ctx.close();
      } catch (_) {}
    }
    if (s.els.backdrop && s.els.backdrop.parentNode) {
      s.els.backdrop.parentNode.removeChild(s.els.backdrop);
    }
  }

  // ---------- stem audio ----------

  function acceptBuffer(s, buffer) {
    if (!s.alive) return;
    s.buffer = buffer;
    var dur = buffer.duration;
    // Regions can outrun the decoded stem by a frame of rounding; clamp.
    s.endSec = Math.min(s.endSec, dur);
    s.original.endSec = Math.min(s.original.endSec, dur);
    s.window = computeWindow(s.original.startSec, s.original.endSec, dur);
    s.peaks = computePeaks(
      buffer.getChannelData(0),
      buffer.sampleRate,
      s.window.startSec,
      s.window.endSec,
      600
    );
    setStatus(s, "");
    setButtonsEnabled(s, true);
    layout(s);
  }

  function fetchStem(s) {
    var role = s.pad.stemSlice && s.pad.stemSlice.stemRole;
    var entryId = s.entry && s.entry.id;
    if (!role || !entryId || !s.ctx) {
      setStatus(s, "No stem audio available.");
      return;
    }
    var url =
      "/api/history/" +
      encodeURIComponent(entryId) +
      "/stem-audio/" +
      encodeURIComponent(role);
    setStatus(s, "Loading waveform…");
    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error("stem HTTP " + r.status);
        return r.arrayBuffer();
      })
      .then(function (buf) {
        // Safari's decodeAudioData can't decode FLAC — retry via the
        // proxy's on-the-fly WAV transcode (same fallback as kit.js).
        return s.ctx.decodeAudioData(buf).catch(function (err) {
          return fetch(url + "?format=wav")
            .then(function (r2) {
              if (!r2.ok) throw err;
              return r2.arrayBuffer();
            })
            .then(function (b2) {
              return s.ctx.decodeAudioData(b2);
            });
        });
      })
      .then(function (audio) {
        acceptBuffer(s, audio);
      })
      .catch(function (err) {
        if (s.alive) setStatus(s, "Waveform failed: " + ((err && err.message) || err));
      });
  }

  // ---------- preview playback ----------

  function startPreview(s) {
    if (!s.buffer || !s.ctx) return;
    stopPreview(s);
    if (s.ctx.state === "suspended") {
      try {
        s.ctx.resume();
      } catch (_) {}
    }
    var src = s.ctx.createBufferSource();
    src.buffer = s.buffer;
    src.loop = true;
    src.loopStart = s.startSec;
    src.loopEnd = s.endSec;
    var gain = s.ctx.createGain();
    gain.gain.setValueAtTime(0, s.ctx.currentTime);
    gain.gain.linearRampToValueAtTime(1, s.ctx.currentTime + RAMP_SEC);
    src.connect(gain);
    gain.connect(s.ctx.destination);
    src.start(0, s.startSec);
    s.preview = { src: src, gain: gain };
    setPlayLabel(s, true);
  }

  function stopPreview(s) {
    var p = s.preview;
    s.preview = null;
    if (p) {
      try {
        var t = s.ctx.currentTime;
        p.gain.gain.cancelScheduledValues(t);
        p.gain.gain.setValueAtTime(p.gain.gain.value, t);
        p.gain.gain.linearRampToValueAtTime(0, t + RAMP_SEC);
        p.src.stop(t + RAMP_SEC + 0.005);
      } catch (_) {}
    }
    setPlayLabel(s, false);
  }

  /** Keep an active preview loop tracking the handles while dragging. */
  function syncPreviewRegion(s) {
    if (!s.preview) return;
    try {
      s.preview.src.loopStart = s.startSec;
      s.preview.src.loopEnd = s.endSec;
    } catch (_) {}
  }

  // ---------- DOM ----------

  function el(tag, cls, parent) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (parent) parent.appendChild(e);
    return e;
  }

  function subtitleText(pad) {
    // Desktop shows "<presetKey.capitalized> · chop <idx+1>". Web pads carry
    // category + padIdx instead; honor explicit presetKey/chopIdx if a
    // caller supplies them.
    var kind =
      pad.presetKey ||
      pad.category ||
      (pad.stemSlice && pad.stemSlice.stemRole) ||
      "chop";
    kind = String(kind);
    kind = kind.charAt(0).toUpperCase() + kind.slice(1);
    var n = pad.chopIdx != null ? pad.chopIdx : pad.padIdx;
    return kind + " · chop " + ((n != null ? n : 0) + 1);
  }

  function buildDom(s) {
    var backdrop = el("div", "chopedit-backdrop", document.body);
    var modal = el("div", "chopedit-modal", backdrop);
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", "Edit chop");
    modal.tabIndex = -1;

    var header = el("div", "chopedit-header", modal);
    var titleWrap = el("div", "chopedit-titles", header);
    el("div", "chopedit-title", titleWrap).textContent =
      s.pad.name || "Pad " + ((s.pad.padIdx != null ? s.pad.padIdx : 0) + 1);
    el("div", "chopedit-subtitle", titleWrap).textContent = subtitleText(s.pad);

    var wave = el("div", "chopedit-wave", modal);
    var canvas = el("canvas", "chopedit-canvas", wave);
    var regionEl = el("div", "chopedit-region", wave);
    var hStart = el("div", "chopedit-handle chopedit-handle--start", wave);
    var hEnd = el("div", "chopedit-handle chopedit-handle--end", wave);
    var status = el("div", "chopedit-status", wave);

    var times = el("div", "chopedit-times", modal);
    var tStart = el("span", "chopedit-time chopedit-time--edge", times);
    var tLen = el("span", "chopedit-time chopedit-time--len", times);
    var tEnd = el("span", "chopedit-time chopedit-time--edge", times);

    var actions = el("div", "chopedit-actions", modal);
    var bPlay = el("button", "chopedit-btn", actions);
    bPlay.type = "button";
    bPlay.textContent = "Play";
    var bReset = el("button", "chopedit-btn", actions);
    bReset.type = "button";
    bReset.textContent = "Reset";
    var spacer = el("div", "chopedit-spacer", actions);
    var bCancel = el("button", "chopedit-btn", actions);
    bCancel.type = "button";
    bCancel.textContent = "Cancel";
    var bSave = el("button", "chopedit-btn chopedit-btn--accent", actions);
    bSave.type = "button";
    bSave.textContent = "Save";

    s.els = {
      backdrop: backdrop,
      modal: modal,
      wave: wave,
      canvas: canvas,
      region: regionEl,
      hStart: hStart,
      hEnd: hEnd,
      status: status,
      tStart: tStart,
      tLen: tLen,
      tEnd: tEnd,
      bPlay: bPlay,
      bReset: bReset,
      bSave: bSave,
      spacer: spacer,
    };

    setButtonsEnabled(s, false);
    layout(s);

    // ----- interactions -----

    backdrop.addEventListener("click", function (ev) {
      if (ev.target === backdrop) close(); // click-on-backdrop = Cancel
    });
    bCancel.addEventListener("click", function () {
      close();
    });
    bSave.addEventListener("click", function () {
      save(s);
    });
    bReset.addEventListener("click", function () {
      s.startSec = s.original.startSec;
      s.endSec = s.original.endSec;
      syncPreviewRegion(s);
      layout(s);
    });
    bPlay.addEventListener("click", function () {
      if (s.preview) stopPreview(s);
      else startPreview(s);
    });

    // Drag grabs whichever handle is closer to the pointer-down point
    // (ChopWaveformEditor's DragGesture semantics), then tracks moves via
    // pointer capture so the drag survives leaving the strip.
    wave.addEventListener("pointerdown", function (ev) {
      if (!s.window) return;
      ev.preventDefault();
      var sec = secondsAtPointer(s, ev);
      s.activeHandle =
        Math.abs(sec - s.startSec) <= Math.abs(sec - s.endSec) ? "start" : "end";
      try {
        wave.setPointerCapture(ev.pointerId);
      } catch (_) {}
      dragTo(s, sec);
    });
    wave.addEventListener("pointermove", function (ev) {
      if (!s.activeHandle || !s.window) return;
      dragTo(s, secondsAtPointer(s, ev));
    });
    function endDrag() {
      if (!s.activeHandle || !s.window) {
        s.activeHandle = null;
        return;
      }
      // Bar-snap the region length on release, anchored on the edge the
      // user did not drag; clamp against the stem window. Pads whose
      // original region is shorter than a bar are one-shots — snapping
      // them up to a whole bar would fight the drag clamp's softened
      // minimum, so they keep free boundaries.
      var bar = barSeconds(s.tempoBpm);
      if (bar != null && s.original.endSec - s.original.startSec < bar) {
        s.activeHandle = null;
        layout(s);
        return;
      }
      var anchor = s.activeHandle === "start" ? "end" : "start";
      var snapped = snapLengthToBars(
        s.startSec,
        s.endSec,
        s.tempoBpm,
        anchor,
        s.window.startSec,
        s.window.endSec
      );
      s.startSec = snapped.startSec;
      s.endSec = snapped.endSec;
      s.activeHandle = null;
      syncPreviewRegion(s);
      layout(s);
    }
    wave.addEventListener("pointerup", endDrag);
    wave.addEventListener("pointercancel", endDrag);

    s.onKeyDown = function (ev) {
      if (ev.key === "Escape") {
        ev.preventDefault();
        close();
      } else if (ev.key === "Enter") {
        ev.preventDefault();
        if (!bSave.disabled) save(s);
      }
    };
    document.addEventListener("keydown", s.onKeyDown, true);
    try {
      modal.focus();
    } catch (_) {}
  }

  function save(s) {
    if (s.onSave) s.onSave({ startSec: s.startSec, endSec: s.endSec });
    close();
  }

  function setStatus(s, text) {
    if (s.els.status) s.els.status.textContent = text;
  }

  function setPlayLabel(s, playing) {
    if (s.els.bPlay) s.els.bPlay.textContent = playing ? "Stop" : "Play";
  }

  function setButtonsEnabled(s, on) {
    // Play needs the decoded buffer; Save/Reset gating is change-driven
    // and handled in layout(). Cancel always works.
    if (s.els.bPlay) s.els.bPlay.disabled = !on;
  }

  // ---------- geometry + drawing ----------

  function secondsAtPointer(s, ev) {
    var rect = s.els.wave.getBoundingClientRect();
    var f = rect.width > 0 ? (ev.clientX - rect.left) / rect.width : 0;
    f = Math.min(Math.max(0, f), 1);
    return s.window.startSec + f * (s.window.endSec - s.window.startSec);
  }

  function dragTo(s, sec) {
    var minLen = minLengthSec(s.tempoBpm);
    // A pad shorter than the nominal minimum must stay editable — never
    // demand more length than the original region had.
    minLen = Math.max(
      0.05,
      Math.min(minLen, s.original.endSec - s.original.startSec)
    );
    if (s.activeHandle === "start") {
      s.startSec = clampStart(sec, s.window.startSec, s.endSec, minLen);
    } else {
      s.endSec = clampEnd(sec, s.window.endSec, s.startSec, minLen);
    }
    syncPreviewRegion(s);
    layout(s);
  }

  function fraction(s, sec) {
    var span = s.window ? s.window.endSec - s.window.startSec : 0;
    if (!(span > 0)) return 0;
    return (sec - s.window.startSec) / span;
  }

  function layout(s) {
    if (!s.alive) return;
    var e = s.els;
    var hasWindow = !!s.window;

    var f0 = hasWindow ? fraction(s, s.startSec) : 0;
    var f1 = hasWindow ? fraction(s, s.endSec) : 1;
    e.region.style.left = (f0 * 100).toFixed(3) + "%";
    e.region.style.width = (Math.max(0, f1 - f0) * 100).toFixed(3) + "%";
    e.hStart.style.left = (f0 * 100).toFixed(3) + "%";
    e.hEnd.style.left = (f1 * 100).toFixed(3) + "%";
    e.region.style.visibility = hasWindow ? "visible" : "hidden";
    e.hStart.style.visibility = hasWindow ? "visible" : "hidden";
    e.hEnd.style.visibility = hasWindow ? "visible" : "hidden";

    e.tStart.textContent = fmtSec(s.startSec);
    e.tEnd.textContent = fmtSec(s.endSec);
    e.tLen.textContent = lengthLabel(s.startSec, s.endSec, s.tempoBpm);

    var edited =
      Math.abs(s.startSec - s.original.startSec) > 0.001 ||
      Math.abs(s.endSec - s.original.endSec) > 0.001;
    e.bReset.disabled = !edited;
    e.bSave.disabled = !edited || !s.buffer;

    drawWaveform(s);
  }

  function drawWaveform(s) {
    var canvas = s.els.canvas;
    if (!canvas || !canvas.getContext) return;
    var rect = canvas.getBoundingClientRect();
    if (!(rect.width > 0) || !(rect.height > 0)) return;
    var dpr = window.devicePixelRatio || 1;
    var w = Math.round(rect.width * dpr);
    var h = Math.round(rect.height * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    var g = canvas.getContext("2d");
    if (!g) return;
    g.clearRect(0, 0, w, h);
    if (!s.peaks || !s.peaks.length) return;

    // Peak bars, mirrored around the midline (desktop Canvas rendering).
    var midY = h / 2;
    var barW = w / s.peaks.length;
    g.fillStyle = "rgba(255,255,255,0.35)";
    for (var i = 0; i < s.peaks.length; i++) {
      var ph = Math.max(1 * dpr, s.peaks[i] * (h - 8 * dpr));
      g.fillRect(i * barW, midY - ph / 2, Math.max(0.5, barW - 0.5 * dpr), ph);
    }

    // Original-boundary reference ticks (faint, desktop parity).
    g.fillStyle = "rgba(255,255,255,0.25)";
    var t0 = fraction(s, s.original.startSec) * w;
    var t1 = fraction(s, s.original.endSec) * w;
    g.fillRect(t0, 0, dpr, h);
    g.fillRect(t1, 0, dpr, h);
  }

  // ---------- export ----------

  window.JamnChopEdit = {
    open: open,
    close: close,
    _internals: {
      barSeconds: barSeconds,
      minLengthSec: minLengthSec,
      wholeBars: wholeBars,
      clampStart: clampStart,
      clampEnd: clampEnd,
      snapLengthToBars: snapLengthToBars,
      computeWindow: computeWindow,
      initialRegion: initialRegion,
      lengthLabel: lengthLabel,
      computePeaks: computePeaks,
    },
  };
})();
