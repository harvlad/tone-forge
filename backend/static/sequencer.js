/* sequencer.js — MPC-style step sequencer surface, the web twin of the
 * native pattern editor (mobile-ios PatternEditorView + SequencerClock,
 * jam-desktop SequencerPanelView). Classic script: defines
 * window.JamnSequencer = { mount, unmount }. The host calls
 * JamnSequencer.mount(container, ctx) with:
 *
 *   ctx = {
 *     engine,        // PadEngine (padengine.js): trigger/release/stopAll/
 *                    //   loopLengthSeconds/padProgress
 *     pads,          // kit pads array ({padIdx, name, colorHint, ...})
 *     audioContext,  // the engine's AudioContext (scheduling clock)
 *     tempoBpm,      // song tempo (native: pattern syncs to song BPM)
 *     analysisId,    // persistence key scope
 *   }
 *
 * Semantics mirrored from native:
 *   * 16-step default grid, switchable 16/32; steps are 16th notes —
 *     stepDuration = 60/bpm/4 (SequencerPattern.stepDuration).
 *   * Steps store velocity, 0 = off (SequencerStep); UI toggles 0/1.
 *   * Swing 0..0.5 delays odd steps by swing × stepDuration
 *     (SequencerClock swing).
 *   * Quantized launch: when loops are already running, the pattern start
 *     queues to the engine's shared lock grid — the same
 *     anchor + loopLengthSeconds boundaries + 0.08 s grace the pads use
 *     (SessionController.toggleSequencerPlayback / PadEngine._lockLaunchTime).
 *   * Steps fire engine.trigger(padIdx, {loop:false}) — one-shots through
 *     the same voice pool as pad presses, so a sequencer hit self-chokes
 *     that pad's ringing voice exactly like a manual retrigger (and a
 *     latched loop on a sequenced pad is replaced, mirroring the native
 *     shared-pool behavior).
 *   * 4 pattern slots A–D, persisted as one versioned JSON blob under
 *     localStorage "jamn.seq.<analysisId>" (SequencerPatternStore: one
 *     blob, storeVersion tag, corrupt blobs replaced on next write).
 *
 * Scheduling is a WebAudio lookahead loop: a ~25 ms setInterval walks a raw
 * (unwrapped) step counter and, for every step whose time lands inside the
 * next ~100 ms of audioContext.currentTime, arms a setTimeout that calls
 * engine.trigger at the step moment. Active rows are read at FIRE time so
 * edits during playback take effect on the very next step.
 */

(function () {
  "use strict";

  // ---------- constants ----------

  /** Scheduler poll interval (ms). */
  var SCHEDULE_INTERVAL_MS = 25;
  /** How far ahead of audioContext.currentTime steps are armed (s). */
  var LOOKAHEAD_SEC = 0.1;
  /** Lock-grid boundary grace, matching PadEngine.LOOP_LOCK_GRACE_SEC. */
  var LOCK_GRACE_SEC = 0.08;
  /** Allowed step counts (native PatternStepCount minus 8 per spec). */
  var STEP_COUNTS = [16, 32];
  var DEFAULT_STEP_COUNT = 16;
  var SLOT_IDS = ["A", "B", "C", "D"];
  var STORE_VERSION = 1;

  // ---------- pure helpers (exported via _internals, node-testable) ----------

  /** One 16th-note step in seconds (SequencerPattern.stepDuration). */
  function stepDurationSec(bpm) {
    if (!(bpm > 0)) return 0.125; // 120 BPM fallback, like SequencerClock
    return 60.0 / bpm / 4.0;
  }

  /**
   * Absolute time of a raw (unwrapped) step. Swing delays odd steps by
   * swing × stepDur; step counts are even so raw parity == wrapped parity
   * across loops (same argument as SequencerClock.tick).
   */
  function stepTimeSec(startSec, rawStep, stepDur, swing) {
    var t = startSec + rawStep * stepDur;
    if (rawStep % 2 === 1 && swing > 0) t += Math.min(swing, 0.5) * stepDur;
    return t;
  }

  /**
   * Pattern start time aligned to the engine's lock grid: boundaries at
   * multiples of loopLen from the anchor; within the grace window after a
   * boundary, start immediately (PadEngine._lockLaunchTime).
   * anchor == null (no loop ever launched) → start now.
   */
  function nextLockAlignedStart(now, anchor, loopLen, grace) {
    if (anchor == null || !(loopLen > 0)) return now;
    var g = grace == null ? LOCK_GRACE_SEC : grace;
    var elapsed = now - anchor;
    if (elapsed < 0) return anchor; // anchor itself still pending
    var phase = elapsed % loopLen;
    if (phase <= g) return now;
    return anchor + (Math.floor(elapsed / loopLen) + 1) * loopLen;
  }

  /**
   * Lookahead window selection: all raw steps from `nextRaw` whose
   * (swing-adjusted) time is < horizonSec. Returns the fire events and the
   * advanced counter. Pure — the scheduler owns the mutable counter.
   * @returns {{events: Array<{raw:number, step:number, timeSec:number}>, nextRaw: number}}
   */
  function collectDueSteps(state, horizonSec) {
    var events = [];
    var raw = state.nextRaw;
    var guard = 0;
    for (;;) {
      var t = stepTimeSec(state.startSec, raw, state.stepDur, state.swing);
      if (t >= horizonSec) break;
      events.push({ raw: raw, step: raw % state.stepCount, timeSec: t });
      raw += 1;
      // A stalled tab (or bpm=0 bug) must not spin forever in one tick.
      if (++guard > 4096) break;
    }
    return { events: events, nextRaw: raw };
  }

  /** Resize a velocity array, preserving the prefix (SequencerTrack.resize). */
  function resizeSteps(steps, n) {
    var out = [];
    for (var i = 0; i < n; i++) out.push(i < steps.length ? steps[i] : 0);
    return out;
  }

  /** colorHint "#RRGGBB" → {r,g,b}; accent fallback (kit.js parseColor). */
  function parseColor(hint) {
    var fallback = { r: 139, g: 92, b: 246 };
    if (typeof hint === "number" && isFinite(hint)) {
      return { r: (hint >> 16) & 255, g: (hint >> 8) & 255, b: hint & 255 };
    }
    if (typeof hint !== "string") return fallback;
    var s = hint.trim().replace(/^#/, "");
    if (!/^[0-9a-fA-F]{6}$/.test(s)) return fallback;
    return {
      r: parseInt(s.slice(0, 2), 16),
      g: parseInt(s.slice(2, 4), 16),
      b: parseInt(s.slice(4, 6), 16),
    };
  }

  /** Fresh empty pattern (native SequencerPattern defaults, web row shape). */
  function emptyPattern() {
    return { stepCount: DEFAULT_STEP_COUNT, swing: 0, rows: {} };
  }

  /**
   * Validate/repair one pattern blob. Unknown shapes come back as a clean
   * empty pattern — the store never throws on a corrupt blob, it replaces
   * it on next write (SequencerPatternStore behavior).
   */
  function normalizePattern(raw) {
    var p = emptyPattern();
    if (!raw || typeof raw !== "object") return p;
    if (STEP_COUNTS.indexOf(raw.stepCount) !== -1) p.stepCount = raw.stepCount;
    var sw = Number(raw.swing);
    if (isFinite(sw)) p.swing = Math.max(0, Math.min(0.5, sw));
    if (raw.rows && typeof raw.rows === "object") {
      for (var key in raw.rows) {
        if (!Object.prototype.hasOwnProperty.call(raw.rows, key)) continue;
        var arr = raw.rows[key];
        if (!Array.isArray(arr)) continue;
        var padIdx = parseInt(key, 10);
        if (!isFinite(padIdx) || padIdx < 0) continue;
        var steps = [];
        var any = false;
        for (var i = 0; i < p.stepCount; i++) {
          var v = Number(arr[i]);
          v = isFinite(v) ? Math.max(0, Math.min(1, v)) : 0;
          steps.push(v);
          if (v > 0) any = true;
        }
        if (any) p.rows[String(padIdx)] = steps;
      }
    }
    return p;
  }

  /** Parse the persisted store blob → {activeSlot, slots:{A..D}}. */
  function normalizeStore(json) {
    var out = { activeSlot: "A", slots: {} };
    var raw = null;
    if (typeof json === "string" && json) {
      try { raw = JSON.parse(json); } catch (_) { raw = null; }
    } else if (json && typeof json === "object") {
      raw = json;
    }
    for (var i = 0; i < SLOT_IDS.length; i++) {
      var id = SLOT_IDS[i];
      out.slots[id] = normalizePattern(raw && raw.slots ? raw.slots[id] : null);
    }
    if (raw && SLOT_IDS.indexOf(raw.activeSlot) !== -1) {
      out.activeSlot = raw.activeSlot;
    }
    return out;
  }

  /** Serialize the store for localStorage: only non-empty rows survive. */
  function serializeStore(store) {
    var slots = {};
    for (var i = 0; i < SLOT_IDS.length; i++) {
      var id = SLOT_IDS[i];
      var p = store.slots[id] || emptyPattern();
      var rows = {};
      for (var key in p.rows) {
        if (!Object.prototype.hasOwnProperty.call(p.rows, key)) continue;
        var steps = p.rows[key];
        var any = false;
        for (var j = 0; j < steps.length; j++) {
          if (steps[j] > 0) { any = true; break; }
        }
        if (any) rows[key] = steps;
      }
      slots[id] = { stepCount: p.stepCount, swing: p.swing, rows: rows };
    }
    return JSON.stringify({
      v: STORE_VERSION,
      activeSlot: store.activeSlot,
      slots: slots,
    });
  }

  function storageKey(analysisId) {
    return "jamn.seq." + (analysisId || "default");
  }

  // ---------- mount state (one surface at a time, like kit.js) ----------

  var current = null;

  function mount(container, ctx) {
    try {
      if (current) unmount();
      if (!container || !ctx || !ctx.engine || !ctx.audioContext) return;
      var s = {
        container: container,
        ctx: ctx,
        engine: ctx.engine,
        ac: ctx.audioContext,
        pads: ctx.pads || [],
        tempoBpm: ctx.tempoBpm > 0 ? ctx.tempoBpm : 120,
        store: null,
        pattern: null, // alias of store.slots[store.activeSlot]
        showUsedOnly: false,
        // transport
        playing: false,
        startSec: 0,
        nextRaw: 0,
        schedTimer: null,
        fireTimers: [],
        raf: null,
        litStep: -1,
        // dom
        cellEls: {}, // padIdx → [cell]
        rowEls: {},
        colCells: [], // step → [cell] for playhead sweep
        slotBtns: {},
        stepBtns: {},
        playBtn: null,
        swingEl: null,
        swingLabel: null,
        usedBtn: null,
      };
      current = s;
      loadStore(s);
      build(s);
      render(s);
    } catch (e) {
      // Surface errors are non-fatal to the host page.
      if (window.console) console.error("[sequencer] mount failed", e);
    }
  }

  function unmount() {
    var s = current;
    if (!s) return;
    current = null;
    stopPlayback(s);
    try { s.container.innerHTML = ""; } catch (_) {}
    try { s.container.classList.remove("seq-surface"); } catch (_) {}
  }

  // ---------- persistence ----------

  function loadStore(s) {
    var json = null;
    try { json = localStorage.getItem(storageKey(s.ctx.analysisId)); } catch (_) {}
    s.store = normalizeStore(json);
    s.pattern = s.store.slots[s.store.activeSlot];
  }

  function persist(s) {
    try {
      localStorage.setItem(storageKey(s.ctx.analysisId), serializeStore(s.store));
    } catch (_) {
      /* private-mode Safari etc. — pattern still lives in memory */
    }
  }

  // ---------- pattern edits ----------

  function rowSteps(s, padIdx) {
    var key = String(padIdx);
    if (!s.pattern.rows[key]) {
      s.pattern.rows[key] = resizeSteps([], s.pattern.stepCount);
    }
    return s.pattern.rows[key];
  }

  function toggleStep(s, padIdx, step) {
    var steps = rowSteps(s, padIdx);
    steps[step] = steps[step] > 0 ? 0 : 1;
    persist(s);
    renderCell(s, padIdx, step);
  }

  function setStepCount(s, n) {
    if (STEP_COUNTS.indexOf(n) === -1 || n === s.pattern.stepCount) return;
    s.pattern.stepCount = n;
    for (var key in s.pattern.rows) {
      if (!Object.prototype.hasOwnProperty.call(s.pattern.rows, key)) continue;
      s.pattern.rows[key] = resizeSteps(s.pattern.rows[key], n);
    }
    persist(s);
    render(s);
  }

  function selectSlot(s, id) {
    if (SLOT_IDS.indexOf(id) === -1 || id === s.store.activeSlot) return;
    s.store.activeSlot = id;
    s.pattern = s.store.slots[id];
    persist(s);
    render(s);
  }

  // ---------- transport / scheduling ----------

  function togglePlay(s) {
    if (s.playing) {
      stopPlayback(s);
      renderTransport(s);
      return;
    }
    if (s.ac.state === "suspended") {
      try { s.ac.resume(); } catch (_) {}
    }
    var now = s.ac.currentTime;
    // Quantized launch: when the engine's lock grid exists (a loop has
    // launched), align the pattern start to the next lock boundary so the
    // steps land on the same grid the loops use — the web analogue of
    // "queue the start on the next bar downbeat" (SessionController).
    var anchor = anyLoopRunning(s) ? s.engine._lockAnchor : null;
    s.startSec = nextLockAlignedStart(now, anchor, s.engine.loopLengthSeconds, LOCK_GRACE_SEC);
    s.nextRaw = 0;
    s.litStep = -1;
    s.playing = true;
    s.schedTimer = setInterval(function () { schedulerTick(s); }, SCHEDULE_INTERVAL_MS);
    schedulerTick(s); // don't wait a full interval for step 0
    s.raf = requestAnimationFrame(function frame() {
      updatePlayhead(s);
      if (s.playing) s.raf = requestAnimationFrame(frame);
    });
    renderTransport(s);
  }

  function stopPlayback(s) {
    s.playing = false;
    if (s.schedTimer != null) { clearInterval(s.schedTimer); s.schedTimer = null; }
    for (var i = 0; i < s.fireTimers.length; i++) clearTimeout(s.fireTimers[i]);
    s.fireTimers = [];
    if (s.raf != null) { cancelAnimationFrame(s.raf); s.raf = null; }
    lightColumn(s, -1);
  }

  /** True when any pad voice is loop-running (drives lock-grid alignment). */
  function anyLoopRunning(s) {
    for (var i = 0; i < s.pads.length; i++) {
      var p = s.engine.padProgress(s.pads[i].padIdx);
      if (p != null) return true;
    }
    return false;
  }

  function schedulerTick(s) {
    if (!s.playing) return;
    var now = s.ac.currentTime;
    var res = collectDueSteps(
      {
        nextRaw: s.nextRaw,
        startSec: s.startSec,
        stepDur: stepDurationSec(s.tempoBpm),
        swing: s.pattern.swing,
        stepCount: s.pattern.stepCount,
      },
      now + LOOKAHEAD_SEC
    );
    s.nextRaw = res.nextRaw;
    for (var i = 0; i < res.events.length; i++) armStep(s, res.events[i], now);
    // Prune fired timers so the list doesn't grow across a long session.
    if (s.fireTimers.length > 256) s.fireTimers = s.fireTimers.slice(-64);
  }

  /**
   * Arm one step with a setTimeout at its moment. Rows are read at fire
   * time (not arm time) so toggles during playback affect the next step.
   * engine.trigger starts at currentTime, so the timeout IS the trigger
   * clock — the lookahead only bounds how late a stalled interval can be.
   */
  function armStep(s, ev, now) {
    var delayMs = Math.max(0, (ev.timeSec - now) * 1000);
    var id = setTimeout(function () {
      if (!s.playing || current !== s) return;
      var step = ev.raw % s.pattern.stepCount; // honor live stepCount switch
      for (var key in s.pattern.rows) {
        if (!Object.prototype.hasOwnProperty.call(s.pattern.rows, key)) continue;
        var steps = s.pattern.rows[key];
        if (step < steps.length && steps[step] > 0) {
          s.engine.trigger(parseInt(key, 10), { loop: false });
        }
      }
    }, delayMs);
    s.fireTimers.push(id);
  }

  // ---------- DOM ----------

  function build(s) {
    var root = s.container;
    root.classList.add("seq-surface");
    root.innerHTML = "";

    // --- transport row ---
    var bar = el("div", "seq-transport");

    var play = el("button", "seq-play");
    play.type = "button";
    play.addEventListener("click", function () { togglePlay(s); });
    s.playBtn = play;
    bar.appendChild(play);

    // slot picker A–D
    var slots = el("div", "seq-seg seq-slots");
    SLOT_IDS.forEach(function (id) {
      var b = el("button", "seq-seg-btn");
      b.type = "button";
      b.textContent = id;
      b.addEventListener("click", function () { selectSlot(s, id); });
      s.slotBtns[id] = b;
      slots.appendChild(b);
    });
    bar.appendChild(slots);

    // step count 16/32
    var stepSeg = el("div", "seq-seg");
    STEP_COUNTS.forEach(function (n) {
      var b = el("button", "seq-seg-btn");
      b.type = "button";
      b.textContent = String(n);
      b.addEventListener("click", function () { setStepCount(s, n); });
      s.stepBtns[n] = b;
      stepSeg.appendChild(b);
    });
    bar.appendChild(stepSeg);

    // swing
    var swingWrap = el("label", "seq-swing");
    var swingText = el("span", "seq-swing-name");
    swingText.textContent = "Swing";
    var swing = document.createElement("input");
    swing.type = "range";
    swing.min = "0";
    swing.max = "50";
    swing.step = "1";
    swing.addEventListener("input", function () {
      s.pattern.swing = Math.max(0, Math.min(0.5, Number(swing.value) / 100));
      persist(s);
      renderTransport(s);
    });
    var swingLabel = el("span", "seq-swing-val");
    swingWrap.appendChild(swingText);
    swingWrap.appendChild(swing);
    swingWrap.appendChild(swingLabel);
    s.swingEl = swing;
    s.swingLabel = swingLabel;
    bar.appendChild(swingWrap);

    // tempo (song BPM — native pattern syncs to song tempo by default)
    var tempo = el("span", "seq-tempo");
    tempo.textContent = Math.round(s.tempoBpm) + " BPM";
    bar.appendChild(tempo);

    // used-rows collapse
    var used = el("button", "seq-toggle");
    used.type = "button";
    used.textContent = "Used";
    used.addEventListener("click", function () {
      s.showUsedOnly = !s.showUsedOnly;
      render(s);
    });
    s.usedBtn = used;
    bar.appendChild(used);

    root.appendChild(bar);

    // --- grid ---
    s.gridEl = el("div", "seq-grid");
    root.appendChild(s.gridEl);
  }

  function el(tag, cls) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    return e;
  }

  function rowIsUsed(s, padIdx) {
    var steps = s.pattern.rows[String(padIdx)];
    if (!steps) return false;
    for (var i = 0; i < steps.length; i++) if (steps[i] > 0) return true;
    return false;
  }

  /** Full rebuild of the grid for the active pattern. */
  function render(s) {
    var grid = s.gridEl;
    grid.innerHTML = "";
    s.cellEls = {};
    s.rowEls = {};
    s.colCells = [];
    var n = s.pattern.stepCount;
    for (var c = 0; c < n; c++) s.colCells.push([]);
    grid.style.setProperty("--seq-steps", String(n));

    var pads = s.pads.slice();
    var anyUsed = pads.some(function (p) { return rowIsUsed(s, p.padIdx); });
    // "Used" collapse: only rows with active steps — but an empty pattern
    // must still show something to edit, so fall back to all rows.
    var visible = (s.showUsedOnly && anyUsed)
      ? pads.filter(function (p) { return rowIsUsed(s, p.padIdx); })
      : pads;

    visible.forEach(function (pad) {
      var row = el("div", "seq-row");
      var tint = parseColor(pad.colorHint);
      row.style.setProperty("--pad-tint", tint.r + "," + tint.g + "," + tint.b);

      var label = el("button", "seq-row-label");
      label.type = "button";
      label.textContent = pad.name || "Pad " + (pad.padIdx + 1);
      label.title = "Preview";
      // Row-label press previews the pad (native track-name preview).
      label.addEventListener("click", function () {
        s.engine.trigger(pad.padIdx, { loop: false });
      });
      row.appendChild(label);

      var cells = el("div", "seq-row-cells");
      var steps = s.pattern.rows[String(pad.padIdx)] || [];
      var cellList = [];
      for (var i = 0; i < n; i++) {
        (function (step) {
          var cell = el("button", "seq-cell");
          cell.type = "button";
          if (step % 4 === 0) cell.classList.add("is-beat");
          if (steps[step] > 0) cell.classList.add("is-on");
          cell.setAttribute("aria-label", (pad.name || "pad") + " step " + (step + 1));
          cell.addEventListener("click", function () { toggleStep(s, pad.padIdx, step); });
          cells.appendChild(cell);
          cellList.push(cell);
          s.colCells[step].push(cell);
        })(i);
      }
      s.cellEls[pad.padIdx] = cellList;
      row.appendChild(cells);
      s.rowEls[pad.padIdx] = row;
      grid.appendChild(row);
    });

    renderTransport(s);
  }

  function renderCell(s, padIdx, step) {
    var cells = s.cellEls[padIdx];
    if (!cells || !cells[step]) return;
    var steps = s.pattern.rows[String(padIdx)] || [];
    cells[step].classList.toggle("is-on", steps[step] > 0);
  }

  function renderTransport(s) {
    if (s.playBtn) {
      var armed = s.playing && s.ac.currentTime < s.startSec - 1e-3;
      s.playBtn.textContent = s.playing ? (armed ? "Armed" : "Stop") : "Play";
      s.playBtn.classList.toggle("is-playing", s.playing && !armed);
      s.playBtn.classList.toggle("is-armed", armed);
      s.playBtn.setAttribute("aria-pressed", String(s.playing));
    }
    SLOT_IDS.forEach(function (id) {
      var b = s.slotBtns[id];
      if (b) b.classList.toggle("is-on", s.store.activeSlot === id);
    });
    STEP_COUNTS.forEach(function (nn) {
      var b = s.stepBtns[nn];
      if (b) b.classList.toggle("is-on", s.pattern.stepCount === nn);
    });
    if (s.swingEl) s.swingEl.value = String(Math.round(s.pattern.swing * 100));
    if (s.swingLabel) s.swingLabel.textContent = Math.round(s.pattern.swing * 200) + "%";
    if (s.usedBtn) s.usedBtn.classList.toggle("is-on", s.showUsedOnly);
  }

  // ---------- playhead ----------

  function updatePlayhead(s) {
    if (!s.playing) return;
    var now = s.ac.currentTime;
    if (now < s.startSec) {
      // Waiting for the quantize boundary — no column lit yet.
      lightColumn(s, -1);
      renderTransport(s);
      return;
    }
    var dur = stepDurationSec(s.tempoBpm);
    var raw = Math.floor((now - s.startSec) / dur);
    var step = raw % s.pattern.stepCount;
    if (step !== s.litStep) {
      lightColumn(s, step);
      if (s.litStep === -1) renderTransport(s); // armed → playing flip
      s.litStep = step;
    }
  }

  function lightColumn(s, step) {
    if (s.litStep >= 0 && s.colCells[s.litStep]) {
      s.colCells[s.litStep].forEach(function (c) { c.classList.remove("is-playhead"); });
    }
    if (step >= 0 && s.colCells[step]) {
      s.colCells[step].forEach(function (c) { c.classList.add("is-playhead"); });
    }
    if (step < 0) s.litStep = -1;
  }

  // ---------- export ----------

  window.JamnSequencer = {
    mount: mount,
    unmount: unmount,
    _internals: {
      stepDurationSec: stepDurationSec,
      stepTimeSec: stepTimeSec,
      nextLockAlignedStart: nextLockAlignedStart,
      collectDueSteps: collectDueSteps,
      resizeSteps: resizeSteps,
      parseColor: parseColor,
      emptyPattern: emptyPattern,
      normalizePattern: normalizePattern,
      normalizeStore: normalizeStore,
      serializeStore: serializeStore,
      storageKey: storageKey,
      SCHEDULE_INTERVAL_MS: SCHEDULE_INTERVAL_MS,
      LOOKAHEAD_SEC: LOOKAHEAD_SEC,
      LOCK_GRACE_SEC: LOCK_GRACE_SEC,
      SLOT_IDS: SLOT_IDS,
      STEP_COUNTS: STEP_COUNTS,
    },
  };
})();
