/* lp-hw.js — physical Novation Launchpad mirror for the merged web
 * "Launchpad" surface (kit.js, window.JamnKit, #view-kit). This is the
 * single hardware-mirror owner: it LED-paints the physical pads to match
 * the on-screen kit grid and routes hardware pad presses into the SAME
 * trigger path as an on-screen tap.
 *
 * It ALSO owns the MK3's FUNCTION BUTTONS while this surface is up —
 * the full desktop D-036 assignment (transport, One-Shot/Follow/Latch
 * select, loop-lock, 16/64 arrows, Session=sequencer panel,
 * Chord=Instant Groove, Record Arm, layer mutes, Stop Clip, pattern
 * select, sequencer play/stop, section jumps) with state LEDs on a
 * separate cache/blank path. See the "function buttons" section below.
 *
 * History: this file used to mirror the standalone lpview.js chop grid
 * (window.JamnLaunchpad, #view-launchpad) as a fixed 8×8; a separate
 * kit-hw.js did a partial bottom-left 4×4 mirror of the Jam Pads kit.
 * Those two surfaces merged into one "Launchpad" built on the kit engine,
 * so this module now drives off the KIT and supersedes kit-hw.js — one
 * mirror, one owner. It follows the kit's own 16↔64 toggle:
 *   - 64 mode → the full 8×8, kit pad i (0..63, row-major from TOP-LEFT)
 *     onto the whole grid.
 *   - 16 mode → the TOP-LEFT 4×4, kit pad i (0..15, row-major from
 *     top-left) onto the top-left 4×4 block.
 * Both use the same formula, programmerPadIndex(7 - row, col), just with
 * a 4- vs 8-wide stride — see hwIndexForPad. The 7-row flip maps the
 * on-screen TOP row to the hardware's top row (programmer numbering has
 * row 0 at the BOTTOM).
 *
 * Reuse over reinvention (task mandate): every model-specific detail
 * comes from the proven driver window.Launchpad (launchpad.js), never a
 * hand-rolled note map / SysEx here — that is exactly what differs across
 * Mini MK3 / X / Pro MK3 / classic:
 *   - device detection + Programmer-Mode entry + hot-plug rebind:
 *     Launchpad.enable() (requestMIDIAccess({sysex:true}), binds the port
 *     pair, sends the enter-Programmer-Mode SysEx, keeps _enabled=true so
 *     the driver's own onstatechange re-binds on replug). This is the call
 *     that triggers Chrome's MIDI permission prompt.
 *   - per-pad RGB LED SysEx: Launchpad.paintButton(index, r,g,b) /
 *     blankButton(index) — index in programmer-mode numbering, r/g/b 0..127.
 *   - programmer-mode note map: Launchpad.programmerPadIndex(row,col) /
 *     rowColForProgrammerIndex(index) (row 0 = BOTTOM, col 0 = left).
 *   - input-port selection: Launchpad.findInputPort(access).
 *
 * Pad-press INPUT can't come through the driver: its single-slot
 * onPadPress callback is claimed by jam.js at boot, and the driver's own
 * _onMidi interprets presses against ITS mode (song / chord / instrument),
 * not the kit grid. So this module opens its OWN MIDIAccess for note input
 * and uses addEventListener('midimessage') on the same physical port; that
 * coexists with the driver's `input.onmidimessage = ...` property handler,
 * so neither side clobbers the other.
 *
 * LED authority: rather than wrapping engine.onstate (a single-slot
 * property kit.js already owns) we POLL the kit pad elements' --pad-tint +
 * is-armed / is-playing / is-empty / is-skeleton classes at LED cadence.
 * kit.js's rAF keeps those authoritative from engine.onstate + padProgress,
 * so the hardware can never disagree with the screen, a kit remount costs
 * nothing, and every grid change (mount, Auto Kit / Drum Kit reload, pad
 * source swap, 16↔64 toggle) is picked up automatically on the next tick —
 * no explicit repaint plumbing into kit.js internals.
 *
 * Hardware press → the pad's own <button>: we dispatch synthetic
 * pointerdown/pointerup on the kit pad element. kit.js exposes no public
 * trigger, and calling engine.trigger() directly would bypass its
 * Tap/Loop/Latch mode logic + armed/playing bookkeeping — the pointer path
 * is the exact code the mouse takes (kit.js wirePad).
 *
 * Coexistence with jam.js's own song-mode Launchpad panel: we deliberately
 * do NOT call Launchpad.disable() on detach (jam.js owns that enable-
 * checkbox lifecycle); on leaving the surface we blank our block and hand
 * the grid back via Launchpad.repaint() (jam.js does this). We attach only
 * while the kit surface is active, so the driver's own modes own the grid
 * everywhere else — that surface split IS the owner arbitration.
 */
(function () {
  "use strict";

  var ACCENT = { r: 139, g: 92, b: 246 }; // kit.js ACCENT fallback
  var LED_TICK_MS = 90;      // LED cadence; also the armed-pulse frame rate
  var PULSE_PERIOD_MS = 900; // soft sine pulse, roughly the MK3's own tempo
  var DIM_IDLE = 0.12;       // idle pads glow their hint without shouting

  var S = null; // null = detached/inert

  function lp() { return window.Launchpad || null; }

  function driverConnected() {
    var d = lp();
    return !!(d && typeof d.isConnected === "function" && d.isConnected());
  }

  /** The kit grid owns hardware presses + LEDs while the merged pad
   * surface is the active view AND showing its SAMPLES tab, OR while
   * the Sequencer view is up (it drives the SAME kit engine — on
   * hardware the sequencer is a PANEL over the pad surface, desktop
   * Session button, so the mirror + function buttons stay live across
   * the toggle). jam.js stamps the active tab as data-pad-surface on
   * #view-kit (absent = legacy markup = samples). On the Notes/Chords
   * tabs the driver (window.Launchpad) owns the device: its mode paints
   * the grid and its own _onMidi turns presses into synth voices —
   * routing those presses into kit pads here would re-create the
   * loop+synth double-fire. */
  function samplesGridActive() {
    var sq = document.getElementById("view-sequencer");
    if (sq && sq.classList.contains("active")) return true;
    var v = document.getElementById("view-kit");
    if (!v || !v.classList.contains("active")) return false;
    return (v.getAttribute("data-pad-surface") || "samples") === "samples";
  }

  // ---------- grid geometry (follows the kit's 16↔64 toggle) ----------

  /** Current on-screen grid width: 8 (64-pad 8×8) or 4 (16-pad 4×4).
   * Reads the kit's own DOM so the mirror follows the toggle without any
   * cross-module state: kit.js stamps `kit-grid-64` on the grid for 64
   * mode; the tile count is the belt-and-braces fallback. */
  function gridCols() {
    var root = document.getElementById("kit-root");
    if (!root) return 4;
    var grid = root.querySelector(".kit-grid");
    if (!grid) return 4;
    if (grid.classList.contains("kit-grid-64")) return 8;
    var n = grid.querySelectorAll(".kit-pad").length;
    return n > 16 ? 8 : 4;
  }

  function padCountFor(cols) { return cols * cols === 64 ? 64 : cols * cols; }

  function kitPadEls(cols) {
    var root = document.getElementById("kit-root");
    if (!root) return [];
    return Array.prototype.slice.call(
      root.querySelectorAll(".kit-grid .kit-pad"), 0, padCountFor(cols));
  }

  // ---------- pad index mapping (delegated to the driver) ----------

  /** kit padIdx (row-major from top-left) → programmer-mode LED index.
   * `cols` is 4 (top-left 4×4) or 8 (full 8×8); 7-row flips the on-screen
   * top row onto the hardware's top row (programmer row 0 = bottom). */
  function hwIndexForPad(i, cols) {
    var d = lp();
    if (!d || typeof d.programmerPadIndex !== "function") return -1;
    var row = Math.floor(i / cols), col = i % cols;
    return d.programmerPadIndex(7 - row, col);
  }

  /** Inverse: hardware note (programmer pad index) → kit padIdx, or -1
   * when the note is a ring button / outside the active cols×cols block. */
  function padForHwNote(note, cols) {
    var d = lp();
    if (!d || typeof d.rowColForProgrammerIndex !== "function") return -1;
    var rc = d.rowColForProgrammerIndex(note);
    if (!rc) return -1;
    var row = 7 - rc.row, col = rc.col;
    if (row < 0 || row >= cols || col < 0 || col >= cols) return -1;
    return row * cols + col;
  }

  // ---------- function buttons (desktop D-036 map, web port) ----------
  //
  // Every round button the MK3 puts around the 8×8 grid, mapped to the
  // SAME assignment desktop pinned in LaunchpadControlSurface (D-036) —
  // parity rule 4: identical semantics on identical buttons. Actions
  // fire on press only (value > 0); Shift (90) stays reserved, ▲▼
  // (80/70) stay with jam.js's octave painter off this surface and are
  // deliberately unmapped here (desktop: unmapped/off), Setup/logo
  // untouched, tap tempo parked.
  //
  // LEDs ride a path SEPARATE from the grid mirror: their own cache
  // (S.ctlCache, keyed by CC), their own blanking list (CTL_CCS), and a
  // repaint from scratch on every reconnect edge — the web analogue of
  // desktop's ControlButtonLightTransport controlLedCache. CC 20's LED
  // is NOT painted here: the driver's own play-button painter
  // (launchpad.js _paintPlayButton, cache + reconnect repaint) already
  // owns that address; two painters on one LED flicker.

  /** CC 2–7 in track-control order — desktop layerRow truncated to the
   * six free cells (Record Arm + Stop Clip bookend the row). */
  var LAYER_ROW = ["DRUMS", "BASS", "CHORDS", "SYNTH", "LEAD", "TEXTURE"];

  /** Momentary press-flash duration (Stop Clip / Chord), desktop
   * flashDuration. */
  var FLASH_MS = 180;

  /** Every CC this module may paint — the blanking + cache domain.
   * Excludes 20 (driver's play LED), 70/80 (jam.js octave arrows),
   * 90 (Shift reserved) and 94/96–98 (unmapped, never addressed). */
  var CTL_CCS = [
    1, 2, 3, 4, 5, 6, 7, 8,           // track control row
    10, 30, 40, 50, 60,               // left column (minus ▷ 20)
    91, 92, 93, 95,                   // top row
    19, 29, 39, 49, 59, 69, 79, 89,   // scene column
    101, 102, 103, 104, 105, 106, 107, 108, // track select
  ];

  // Palette: desktop LaunchpadControlSurface.Color hex values halved
  // into the driver's 0..127 SysEx range (same channel scaling the
  // grid mirror uses), so both platforms light the same colors.
  var CTL = {
    bright:   { r: 127, g: 127, b: 127 },
    dim:      { r: 15, g: 15, b: 15 },
    green:    { r: 0, g: 127, b: 0 },
    greenDim: { r: 5, g: 20, b: 5 },
    red:      { r: 127, g: 0, b: 0 },
    redDim:   { r: 32, g: 4, b: 4 },
    amber:    { r: 127, g: 95, b: 0 },
    amberDim: { r: 25, g: 17, b: 5 },
    lockOn:   { r: 122, g: 79, b: 5 },  // 0xF59E0B — the quantize accent
    lockDim:  { r: 30, g: 19, b: 1 },
    section:  { r: 32, g: 32, b: 32 },
    pattern:  { r: 16, g: 16, b: 16 },
    off:      { r: 0, g: 0, b: 0 },
  };

  /** Scale a 0..127 color by k (software pulse — paintButton is
   * static-only, so pulsing LEDs breathe at the LED tick like the
   * armed-pad pulse). */
  function ctlScaled(c, k) {
    return {
      r: Math.max(0, Math.min(127, Math.round(c.r * k))),
      g: Math.max(0, Math.min(127, Math.round(c.g * k))),
      b: Math.max(0, Math.min(127, Math.round(c.b * k))),
    };
  }

  /**
   * The D-036 assignment table as a pure CC → function map (the web
   * twin of LaunchpadControlSurface.function(for:)). Returns null for
   * reserved/unmapped buttons. Exposed via _internals for the node
   * suite — this IS the contract the desktop tests pin.
   */
  function ccFunction(cc) {
    if (cc === 20) return { kind: "playPause" };
    if (cc === 10) return { kind: "globalStop" };
    if (cc === 30) return { kind: "selectMode", mode: "one" };
    if (cc === 40) return { kind: "selectMode", mode: "follow" };
    if (cc === 50) return { kind: "selectMode", mode: "latch" };
    if (cc === 60) return { kind: "loopLockToggle" };
    if (cc === 91) return { kind: "gridSize", count: 16 };
    if (cc === 92) return { kind: "gridSize", count: 64 };
    if (cc === 93) return { kind: "sequencerPanelToggle" };
    if (cc === 95) return { kind: "instantGroove" };
    if (cc === 1) return { kind: "recordToggle" };
    if (cc === 8) return { kind: "stopAllPads" };
    if (cc >= 2 && cc <= 7) return { kind: "layerToggle", category: LAYER_ROW[cc - 2] };
    if (cc >= 101 && cc <= 108) return { kind: "patternSelect", index: cc - 101 };
    if (cc === 89) return { kind: "sequencerPlayStop" };
    if (cc >= 19 && cc <= 79 && cc % 10 === 9) {
      // Scene buttons top→bottom = song order: CC 79 (under 89) is
      // block 0, CC 19 (bottom) is block 6.
      return { kind: "sectionJump", index: 7 - Math.floor(cc / 10) };
    }
    return null; // 90 Shift reserved; 70/80, 94, 96–98 free; tap tempo parked
  }

  /**
   * Pure LED frame for the function buttons (the web twin of
   * controlLightFrame). `st` is a plain state snapshot (see
   * collectControlState); `pulseK` is the shared soft-pulse factor.
   * Returns {cc: {r,g,b 0..127}} for every CTL_CCS address — absent
   * keys mean off. CC 20 is deliberately not emitted (driver-owned).
   */
  function controlFrame(st, pulseK) {
    var k = typeof pulseK === "number" ? pulseK : 1;
    var f = {};

    // ○ Record/Capture MIDI = global stop: always-available dim red.
    f[10] = CTL.redDim;

    // Trigger-mode select: selected lit, others dim.
    f[30] = st.mode === "one" ? CTL.bright : CTL.dim;
    f[40] = st.mode === "follow" ? CTL.bright : CTL.dim;
    f[50] = st.mode === "latch" ? CTL.bright : CTL.dim;

    // Loop lock (web: the shared quantize setting != off).
    f[60] = st.loopLocked ? CTL.lockOn : CTL.lockDim;

    // Grid size arrows (◄ 16, ► 64).
    f[91] = st.padCount === 16 ? CTL.bright : CTL.dim;
    f[92] = st.padCount === 64 ? CTL.bright : CTL.dim;

    // Session = sequencer panel; Chord = Instant Groove (press flash).
    f[93] = st.seqOpen ? CTL.bright : CTL.dim;
    f[95] = st.flash95 ? CTL.amber : CTL.amberDim;

    // Record Arm: red pulse while recording, dim red idle. (Web's
    // recorder is one-tap arm+start — no distinct "armed" stage.)
    f[1] = st.recording ? ctlScaled(CTL.red, k) : CTL.redDim;

    // Stop Clip: amber flash on press, else dim amber.
    f[8] = st.flash8 ? CTL.amber : CTL.amberDim;

    // Layer toggles: sounding layer pulses its category accent,
    // available-but-silent dim, empty dark.
    for (var i = 0; i < LAYER_ROW.length; i++) {
      var cc = i + 2;
      var info = st.layers && st.layers[i];
      if (!info || !info.present || !info.color) {
        f[cc] = CTL.off;
        continue;
      }
      // colorHint is 0..255; halve into SysEx range like the grid.
      var accent = { r: info.color.r >> 1, g: info.color.g >> 1, b: info.color.b >> 1 };
      f[cc] = info.active
        ? ctlScaled(accent, k)
        : { r: accent.r >> 2, g: accent.g >> 2, b: accent.b >> 2 };
    }

    // Pattern select: stored slot dim, the active one bright (pulsing
    // while the sequencer runs). Slots null = pane closed = dark (the
    // buttons can't act then — see the hw namespace note).
    for (var slot = 0; slot < 8; slot++) {
      var pcc = 101 + slot;
      var s = st.slots && st.slots[slot];
      if (!s) { f[pcc] = CTL.off; continue; }
      if (s.active) {
        f[pcc] = st.seqPlaying ? ctlScaled(CTL.bright, k) : CTL.bright;
      } else {
        f[pcc] = s.hasContent ? CTL.pattern : CTL.off;
      }
    }

    // Sequencer play/stop (top scene arrow).
    f[89] = st.seqPlaying ? ctlScaled(CTL.green, k) : CTL.greenDim;

    // Section blocks: lit where a block exists, pulse on the block
    // under the playhead.
    for (var b = 0; b < 7; b++) {
      var scc = (7 - b) * 10 + 9;
      if (!st.sections || b >= st.sections.count) {
        f[scc] = CTL.off;
      } else {
        f[scc] = st.sections.active === b ? ctlScaled(CTL.bright, k) : CTL.section;
      }
    }

    return f;
  }

  /** Live state snapshot for controlFrame. Every read is feature-
   * checked — a missing module reads as its dark/neutral state. */
  function collectControlState() {
    var hwk = window.JamnKit && window.JamnKit.hw;
    var seq = window.JamnSequencer && window.JamnSequencer.hw;
    var hooks = window.JamnKitHooks;
    var now = Date.now();
    var layers = [];
    for (var i = 0; i < LAYER_ROW.length; i++) {
      var info = null;
      try { info = hwk && hwk.layerInfo ? hwk.layerInfo(LAYER_ROW[i]) : null; } catch (_) {}
      layers.push(info);
    }
    var recording = false;
    try {
      recording = !!(window.JamnRecordings && window.JamnRecordings.isRecording
        && window.JamnRecordings.isRecording());
    } catch (_) {}
    var sections = { count: 0, active: -1 };
    try { if (hwk && hwk.sectionInfo) sections = hwk.sectionInfo(); } catch (_) {}
    var loopLocked = false;
    try {
      loopLocked = !!(window.JamnQuantize
        && typeof window.JamnQuantize.get === "function"
        && window.JamnQuantize.get() !== "off");
    } catch (_) {}
    return {
      mode: hwk && hwk.triggerMode ? hwk.triggerMode() : null,
      loopLocked: loopLocked,
      padCount: hwk && hwk.padCount ? hwk.padCount() : null,
      seqOpen: !!(hooks && typeof hooks.isSequencerOpen === "function"
        && hooks.isSequencerOpen()),
      seqPlaying: !!(seq && seq.isPlaying && seq.isPlaying()),
      slots: seq && seq.slotInfo ? seq.slotInfo() : null,
      recording: recording,
      layers: layers,
      sections: sections,
      flash8: (S.flashUntil[8] || 0) > now,
      flash95: (S.flashUntil[95] || 0) > now,
    };
  }

  function flash(cc) {
    if (S) S.flashUntil[cc] = Date.now() + FLASH_MS;
  }

  /** Toggle the shared quantize between off and the last musical grid
   * (default bar) — the web reading of desktop loopLockEnabled. Going
   * through JamnQuantize keeps the kit's segmented control (and any
   * other subscriber) in lockstep for free. */
  function toggleLoopLock() {
    var q = window.JamnQuantize;
    if (!q || typeof q.get !== "function" || typeof q.set !== "function") return;
    var cur = q.get();
    if (cur === "off") {
      q.set((S && S.lastQuant) || "bar");
    } else {
      if (S) S.lastQuant = cur; // re-lock restores the SAME grid unit
      q.set("off");
    }
  }

  /** Dispatch one function-button press (the impure half of ccFunction,
   * mirroring LaunchpadControlSurface.handle). Wholly fenced: a host
   * fault must never break the MIDI stream or the grid mirror. */
  function handleCc(cc, value) {
    if (!S || !(value > 0)) return;    // press only; releases ignored
    if (!samplesGridActive()) return;  // off-surface: driver/jam.js own CCs
    var fn = ccFunction(cc);
    if (!fn) return;
    var hw = window.JamnKit && window.JamnKit.hw;
    var host = window.JamnKitHost || null;
    var hooks = window.JamnKitHooks || null;
    var seq = window.JamnSequencer && window.JamnSequencer.hw;
    try {
      switch (fn.kind) {
        case "playPause":
          if (host && typeof host.isPlaying === "function" && host.isPlaying()) {
            if (host.pauseSong) host.pauseSong();
          } else if (host && host.playSong) {
            host.playSong();
          }
          break;
        case "globalStop":
          // The everything-stop: pads, song AND sequencer (CC 8 is the
          // pads-only one; desktop twin is stopEverything()). The
          // sequencer clock must stop too, or the running pattern
          // re-triggers pads one step after they were silenced.
          if (hw) hw.stopAllPads();
          if (host && host.pauseSong) host.pauseSong();
          if (seq && typeof seq.isPlaying === "function" && seq.isPlaying()) {
            seq.togglePlay();
          }
          break;
        case "selectMode":
          if (hw) hw.setTriggerMode(fn.mode);
          break;
        case "loopLockToggle":
          toggleLoopLock();
          break;
        case "gridSize":
          if (hw) hw.setPadCount(fn.count);
          break;
        case "sequencerPanelToggle":
          if (hooks && typeof hooks.toggleSequencer === "function") hooks.toggleSequencer();
          break;
        case "instantGroove":
          // Inert on an empty grid — no groove, no flash (desktop guard).
          if (hw && hw.instantGroove()) flash(95);
          break;
        case "recordToggle":
          if (window.JamnRecordings
              && typeof window.JamnRecordings.toggleRecord === "function") {
            window.JamnRecordings.toggleRecord();
          }
          break;
        case "stopAllPads":
          if (hw) { hw.stopAllPads(); flash(8); }
          break;
        case "layerToggle":
          if (hw) hw.toggleLayer(fn.category);
          break;
        case "patternSelect":
          if (seq) seq.selectSlot(fn.index);
          break;
        case "sequencerPlayStop":
          if (seq) seq.togglePlay();
          break;
        case "sectionJump":
          if (hw) hw.sectionJump(fn.index);
          break;
      }
    } catch (_) { /* fenced: see docstring */ }
    paintControls(); // press feedback lands NOW, not at the next tick
  }

  /** Paint the function-button LEDs, diffed against their own cache
   * (grid mirror untouched — separate path, separate cache). */
  function paintControls() {
    if (!S) return;
    var d = lp();
    if (!d || !driverConnected() || !samplesGridActive()) return;
    var st;
    try { st = collectControlState(); } catch (_) { return; }
    var ph = (Date.now() % PULSE_PERIOD_MS) / PULSE_PERIOD_MS;
    var pulseK = 0.55 + 0.3 * Math.sin(ph * 2 * Math.PI);
    var frame = controlFrame(st, pulseK);
    for (var i = 0; i < CTL_CCS.length; i++) {
      var cc = CTL_CCS[i];
      var rgb = frame[cc] || CTL.off;
      var cache = S.ctlCache[cc];
      if (cache && cache.r === rgb.r && cache.g === rgb.g && cache.b === rgb.b) continue;
      S.ctlCache[cc] = rgb;
      try {
        if (rgb.r || rgb.g || rgb.b) d.paintButton(cc, rgb.r, rgb.g, rgb.b);
        else d.blankButton(cc);
      } catch (_) {}
    }
  }

  /** True when any function LED is cached as painted. */
  function ctlPainted() {
    if (!S) return false;
    for (var k in S.ctlCache) {
      if (Object.prototype.hasOwnProperty.call(S.ctlCache, k)) return true;
    }
    return false;
  }

  /** Blank every function LED this module may have painted and drop
   * the cache (standdown / reconnect / teardown edges). */
  function blankControls() {
    var d = lp();
    if (!d || !driverConnected()) return;
    for (var i = 0; i < CTL_CCS.length; i++) {
      try { d.blankButton(CTL_CCS[i]); } catch (_) {}
    }
    if (S) S.ctlCache = {};
  }

  // ---------- LED mirroring ----------

  /** Pad tint straight from the element's --pad-tint custom property
   * (kit.js sets "r,g,b" 0..255 from colorHint) so hardware and screen
   * can never disagree about a pad's color. */
  function tintForEl(el) {
    var raw = el && el.style ? el.style.getPropertyValue("--pad-tint") : "";
    if (raw) {
      var parts = raw.split(",").map(function (x) { return parseInt(x, 10); });
      if (parts.length === 3 && parts.every(function (n) { return isFinite(n); })) {
        return { r: parts[0], g: parts[1], b: parts[2] };
      }
    }
    return ACCENT;
  }

  /** Scale a 0..255 tint into the driver's 0..127 SysEx range with a
   * brightness factor, quantized so the change-gate cache works. */
  function scaled(tint, k) {
    return {
      r: Math.max(0, Math.min(127, Math.round((tint.r >> 1) * k))),
      g: Math.max(0, Math.min(127, Math.round((tint.g >> 1) * k))),
      b: Math.max(0, Math.min(127, Math.round((tint.b >> 1) * k))),
    };
  }

  function paintPad(i, cols, rgb) {
    var d = lp();
    if (!d || !driverConnected()) return;
    var cache = S.ledCache[i];
    if (cache && cache.r === rgb.r && cache.g === rgb.g && cache.b === rgb.b) return;
    S.ledCache[i] = rgb;
    d.paintButton(hwIndexForPad(i, cols), rgb.r, rgb.g, rgb.b);
  }

  /** Blank the ENTIRE 8×8 and drop the cache. Used on connect edge and on
   * a 16↔64 change, where a pad's index→hardware mapping shifts and stale
   * LEDs from the previous layout must not linger. */
  function blankAll() {
    var d = lp();
    if (!d || !driverConnected()) return;
    for (var r = 0; r < 8; r++) {
      for (var c = 0; c < 8; c++) {
        try { d.blankButton(d.programmerPadIndex(r, c)); } catch (_) {}
      }
    }
    S.ledCache = [];
  }

  function ledTick() {
    if (!S) return;
    var connected = driverConnected();
    if (connected && !S.wasConnected) {
      // Hot-plug (the driver re-bound via its own statechange handler):
      // drop BOTH caches so the grid AND the function LEDs repaint from
      // scratch (the reconnect-repaint half of the control-LED contract).
      S.ledCache = [];
      S.ctlCache = {};
      S.lastCols = 0;
    }
    S.wasConnected = connected;
    S.state = connected ? "attached" : "unavailable";
    if (!connected) return;

    if (!samplesGridActive()) {
      // Off-surface (other view, or the Notes/Chords tab): drop our LEDs
      // ONCE on the edge and hand the device straight back to the
      // driver's own mode painter — blankAll alone would wipe whatever
      // the driver painted with nothing left to repaint it. Connected is
      // already true here (checked above), so blankAll really clears the
      // cache and this edge fires exactly once per standdown.
      if (S.ledCache.length || ctlPainted()) {
        blankAll();
        blankControls();
        var dd = lp();
        if (dd && typeof dd.repaint === "function") {
          try { dd.repaint(); } catch (_) {}
        }
      }
      return;
    }

    var cols = gridCols();
    if (cols !== S.lastCols) {
      // 16↔64 toggle (or first paint): the padIdx→hardware map just
      // changed, so wipe every LED before repainting the new layout.
      blankAll();
      S.lastCols = cols;
    }

    var count = padCountFor(cols);
    var els = kitPadEls(cols);
    // Armed-pulse brightness: soft sine, framed by the tick.
    var ph = (Date.now() % PULSE_PERIOD_MS) / PULSE_PERIOD_MS;
    var pulseK = 0.55 + 0.3 * Math.sin(ph * 2 * Math.PI);

    for (var i = 0; i < count; i++) {
      var el = els[i];
      if (!el || el.classList.contains("is-empty") || el.classList.contains("is-skeleton")) {
        paintPad(i, cols, { r: 0, g: 0, b: 0 });
        continue;
      }
      var tint = tintForEl(el);
      var rgb;
      if (el.classList.contains("is-playing")) rgb = scaled(tint, 1.0);
      else if (el.classList.contains("is-armed")) rgb = scaled(tint, pulseK);
      else rgb = scaled(tint, DIM_IDLE);
      paintPad(i, cols, rgb);
    }

    // Function-button LEDs: separate path + cache, same tick cadence
    // (the tick doubles as the soft-pulse clock for record/layers/
    // sections, exactly like the armed-pad pulse above).
    paintControls();

    // The engine is recreated on every kit.js remount — re-push the Link
    // transport whenever the identity changes so a fresh engine doesn't
    // silently lose bar alignment.
    var engine = window.JamnKit && window.JamnKit.engine && window.JamnKit.engine();
    if (engine !== S.lastEngine) {
      S.lastEngine = engine || null;
      pushLinkTransport();
    }
  }

  // ---------- hardware pad input (own MIDIAccess, listener-only) ----------

  function bindInput() {
    if (!S || !S.access) return;
    var d = lp();
    var next = (d && typeof d.findInputPort === "function") ? d.findInputPort(S.access) : null;
    if (next === S.input) return;
    if (S.input) {
      try { S.input.removeEventListener("midimessage", S.onMidi); } catch (_) {}
    }
    S.input = next;
    if (S.input) {
      try { S.input.addEventListener("midimessage", S.onMidi); } catch (_) {}
    }
  }

  function onMidi(evt) {
    if (!S) return;
    var data = evt.data;
    if (!data || data.length < 2) return;
    var status = data[0] & 0xf0;
    if (status === 0xb0) {
      // Function-button CCs (D-036 map). Branches BEFORE the note path
      // so the audible press chain stays exactly as audited — zero
      // added work between Note On and engine.trigger.
      handleCc(data[1] | 0, data[2] | 0);
      return;
    }
    var isOn = status === 0x90 && (data[2] || 0) > 0;
    var isOff = status === 0x80 || (status === 0x90 && (data[2] || 0) === 0);
    if (!isOn && !isOff) return;
    if (!samplesGridActive()) return;  // driver owns presses off the samples grid
    var cols = gridCols();
    var padIdx = padForHwNote(data[1], cols);
    if (padIdx < 0) return;            // ring button / outside the block
    var el = kitPadEls(cols)[padIdx];
    if (!el || el.disabled) return;
    // Same path as a mouse press (kit.js wirePad): synthetic pointer
    // events run padDown/padUp with full Tap/Loop/Latch semantics.
    // kit.js's setPointerCapture(try/catch) tolerates the synthetic id.
    if (typeof PointerEvent !== "function") return;
    try {
      el.dispatchEvent(new PointerEvent(isOn ? "pointerdown" : "pointerup",
        { bubbles: true, cancelable: true }));
    } catch (_) {}
  }

  // ---------- Ableton Link (backend SSE relay) ----------

  function openLink() {
    if (!S || typeof EventSource !== "function") return;
    try {
      var es = new EventSource("/api/link/events");
      S.linkEs = es;
      es.onmessage = function (evt) {
        if (!S) return;
        var data = null;
        try { data = JSON.parse(evt.data); } catch (_) { return; }
        S.link = {
          active: !!data.active,
          bpm: Number(data.bpm) || 0,
          beat: Number(data.beat) || 0,
          peers: Number(data.peers) || 0,
          serverTs: Number(data.server_ts) || 0,
          recvEpoch: Date.now() / 1000,
        };
        pushLinkTransport();
      };
      // EventSource auto-reconnects; no manual retry (same stance as jam.js).
    } catch (_) { /* SSE unsupported: Link stays inactive */ }
  }

  /** Feature-detected engine transport hand-off. Extrapolates the Link
   * beat to "now" (beat counter + elapsed wall time × bpm/60 — relay-grade
   * accuracy, same caveat jam.js documents), then anchors the PREVIOUS
   * 4/4 bar boundary in the kit AudioContext timebase so the engine can
   * quantize loop launches to Link bars via anchor + k*barSec. */
  function pushLinkTransport() {
    if (!S) return;
    var engine = window.JamnKit && window.JamnKit.engine && window.JamnKit.engine();
    if (!engine || typeof engine.setTransport !== "function") return;
    var link = S.link;
    try {
      if (!link || !link.active || !(link.bpm > 0)) {
        engine.setTransport(null); // Link gone: engine falls back to its own grid
        return;
      }
      var ctx = window.JamnKit.audioContext && window.JamnKit.audioContext();
      if (!ctx) return;
      var beatSec = 60 / link.bpm;
      var elapsed = Date.now() / 1000 - (link.serverTs || link.recvEpoch);
      var beatNow = link.beat + Math.max(0, elapsed) / beatSec;
      var phaseInBar = ((beatNow % 4) + 4) % 4; // beats since the bar line
      engine.setTransport({
        isPlaying: function () { return true; }, // Link has no stop state here
        tempoBpm: link.bpm,
        barAnchorSongTime: ctx.currentTime - phaseInBar * beatSec,
      });
    } catch (_) { /* engine mid-teardown — next SSE/message retries */ }
  }

  // ---------- public API ----------

  function status() {
    if (!S) return { state: "detached", connected: false, device: null };
    var d = lp();
    var connected = driverConnected();
    return {
      state: S.state,
      connected: connected,
      device: connected && d && d.getStatus ? (d.getStatus().deviceName || null) : null,
      link: S.link ? {
        active: S.link.active, bpm: S.link.bpm, peers: S.link.peers,
      } : { active: false, bpm: 0, peers: 0 },
    };
  }

  async function attach() {
    if (S) return status();
    var d = lp();
    // No driver script / no WebMIDI: resolve inert, never throw.
    if (!d || typeof d.isSupported !== "function" || !d.isSupported()) {
      return { state: "unavailable", reason: "webmidi_unsupported", connected: false, device: null };
    }
    var st = null;
    try {
      // Driver connect path: prompts for MIDI+SysEx, binds the port pair,
      // enters Programmer Mode, keeps _enabled=true so its statechange
      // handler re-binds on hot-plug for us. Permission-prompt trigger.
      st = await d.enable();
    } catch (_) { st = null; }
    if (!st || st.supported === false || st.error === "permission_denied") {
      // Permission denied / hard failure: stay fully inert — no timers,
      // no SSE, nothing to leak.
      return { state: "unavailable", reason: (st && st.error) || "enable_failed", connected: false, device: null };
    }

    S = {
      state: driverConnected() ? "attached" : "unavailable",
      access: null,
      input: null,
      onMidi: onMidi,
      onStateChange: null,
      ledCache: [],
      ctlCache: {},   // function-button LEDs — own cache, own blank list
      flashUntil: {}, // cc → epoch-ms end of a momentary press flash
      lastQuant: "bar", // loop-lock re-arm target (last non-off grid)
      wasConnected: false,
      lastCols: 0,
      lastEngine: null,
      link: null,
      linkEs: null,
      timer: 0,
    };

    // Own MIDIAccess for note INPUT only. Permission is already granted
    // (the driver holds sysex access), so this resolves without a second
    // prompt. Input-less mirroring still lights the LEDs; presses just
    // have to come from the mouse then.
    try {
      S.access = await navigator.requestMIDIAccess();
      if (!S) return { state: "detached", connected: false, device: null };
      bindInput();
      S.onStateChange = function () { if (S) bindInput(); };
      try { S.access.addEventListener("statechange", S.onStateChange); } catch (_) {}
    } catch (_) {
      S.access = null;
    }

    S.timer = setInterval(ledTick, LED_TICK_MS);
    // Blank the device when this tab goes away. WebMIDI does NOT reset a
    // Launchpad's LEDs on tab close, so without this the grid we painted
    // lingers on the hardware into the NEXT session ("those pads were already
    // lit before I got to web"). pagehide fires on close/navigate/bfcache;
    // the synchronous blank SysEx flushes before teardown. best-effort.
    S.onPageHide = function () {
      try { blankAll(); } catch (_) {}
      try { blankControls(); } catch (_) {}
    };
    try { window.addEventListener("pagehide", S.onPageHide); } catch (_) {}
    openLink();
    // Even with no device present now we stay armed: the driver's hot-plug
    // rebind + our ledTick connect edge light the grid the moment the
    // Launchpad appears.
    return status();
  }

  /** Force a repaint from the kit's current grid. The ledTick poll already
   * picks up every grid change on its own, so this is a convenience for
   * callers that want an immediate refresh (no-op when detached). */
  function repaint() {
    if (!S) return;
    S.ledCache = [];
    S.ctlCache = {};
    S.lastCols = 0;
    ledTick();
  }

  function detach() {
    if (!S) return;
    var s = S;
    try { blankAll(); } catch (_) {}
    try { blankControls(); } catch (_) {}
    S = null;
    if (s.timer) clearInterval(s.timer);
    if (s.onPageHide) {
      try { window.removeEventListener("pagehide", s.onPageHide); } catch (_) {}
    }
    if (s.linkEs) { try { s.linkEs.close(); } catch (_) {} }
    if (s.input) { try { s.input.removeEventListener("midimessage", s.onMidi); } catch (_) {} }
    if (s.access && s.onStateChange) {
      try { s.access.removeEventListener("statechange", s.onStateChange); } catch (_) {}
    }
    // Clear any Link transport we handed the engine so the lock grid
    // returns to free-running.
    try {
      var engine = window.JamnKit && window.JamnKit.engine && window.JamnKit.engine();
      if (engine && typeof engine.setTransport === "function") engine.setTransport(null);
    } catch (_) {}
    // Deliberately NOT calling Launchpad.disable(): jam.js owns the
    // enable-checkbox lifecycle and its song-mode panel may be using the
    // device; we only ever painted our block, and we just blanked it.
    // jam.js follows detach() with Launchpad.repaint() to restore the
    // driver's own mode colors.
  }

  window.JamnLpHW = {
    attach: attach,
    detach: detach,
    repaint: repaint,
    status: status,
    // Pure mapping helpers exposed for DOM-free smoke tests.
    _internals: {
      hwIndexForPad: hwIndexForPad,
      padForHwNote: padForHwNote,
      gridCols: gridCols,
      // Function-button surface (D-036 web port) — the node suite pins
      // the CC assignment table + the LED state transitions.
      ccFunction: ccFunction,
      controlFrame: controlFrame,
      LAYER_ROW: LAYER_ROW,
      CTL_CCS: CTL_CCS,
      CTL: CTL,
      FLASH_MS: FLASH_MS,
    },
  };
})();
