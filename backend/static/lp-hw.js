/* lp-hw.js — physical Novation Launchpad mirror for the WEB Launchpad
 * surface (lpview.js, window.JamnLaunchpad — the sidebar "Launchpad"
 * 8×8 chop grid). This is the full 8×8 twin: it LED-paints all 64
 * hardware pads to match the on-screen chop grid and routes hardware
 * pad presses into the SAME trigger path as an on-screen tap.
 *
 * Classic script: defines window.JamnLpHW = { attach, detach, repaint,
 * status }. Nothing runs until attach() is called (on lpview mount).
 *
 * Reuse over reinvention (task mandate): every model-specific detail
 * comes from the proven driver window.Launchpad (launchpad.js), never
 * from a hand-rolled note map / SysEx here — that is exactly what
 * differs across Mini MK3 / X / Pro MK3 / classic:
 *   - device detection + Programmer-Mode entry + hot-plug rebind:
 *     Launchpad.enable() — requestMIDIAccess({sysex:true}), binds the
 *     MK3 MIDI port pair, sends the enter-Programmer-Mode SysEx, and
 *     keeps _enabled=true so the driver's own onstatechange re-binds on
 *     replug. This is the call that triggers Chrome's MIDI permission
 *     prompt.
 *   - per-pad RGB LED SysEx: Launchpad.paintButton(index, r,g,b) /
 *     blankButton(index) — index in programmer-mode numbering, r/g/b in
 *     the 0..127 SysEx range.
 *   - programmer-mode note map: Launchpad.programmerPadIndex(row,col) /
 *     rowColForProgrammerIndex(index) (row 0 = BOTTOM, col 0 = left).
 *   - input-port selection: Launchpad.findInputPort(access).
 *
 * Pad-press INPUT can't come through the driver: its single-slot
 * onPadPress callback is claimed by jam.js at boot, and the driver's
 * own _onMidi interprets presses against ITS mode (song / instrument /
 * contribute), not lpview's chop grid. So — exactly like kit-hw.js —
 * this module opens its OWN MIDIAccess for note input and uses
 * addEventListener('midimessage') on the same physical port; that
 * coexists with the driver's `input.onmidimessage = ...` property
 * handler, so neither side clobbers the other.
 *
 * Grid map — lpview pad idx (0..63, row-major from the TOP-LEFT, the
 * DOM order lpview renders) → hardware. lpview row 0 is the TOP row;
 * programmer numbering has row 0 at the BOTTOM, so hardware row =
 * 7 - lpRow. Concretely lpview idx 0 (top-left) → programmer pad 81,
 * idx 63 (bottom-right) → programmer pad 18. All 64 mapping math is
 * delegated to the driver helpers so the numbering can never drift.
 *
 * KitHW coexistence: window.JamnKitHW mirrors the Jam Pads 4×4 and this
 * mirrors the sidebar Launchpad 8×8. Both call Launchpad.enable() (the
 * driver's enable is idempotent — repeated calls just re-bind) and both
 * open their own listener-only MIDIAccess, so they never fight over the
 * SysEx channel. They also never own the device at the same time: KitHW
 * attaches only on the Jam Pads view, this attaches only on the
 * Launchpad view, and jam.js's showView wrapper detaches whichever left.
 * That surface split IS the owner arbitration — no extra shared flag is
 * needed. Like KitHW we deliberately do NOT call Launchpad.disable() on
 * detach (jam.js owns the enable-checkbox lifecycle for its own song-mode
 * Launchpad panel); we hand the grid back via Launchpad.repaint().
 */
(function () {
  "use strict";

  var PAD_COUNT = 64;
  // Connect-edge + status poll cadence. NOT an animation loop: the grid
  // is repainted explicitly by lpview on every renderGrid, and here only
  // on a (re)connect edge. A (re)connect matters because the driver's
  // enable()/hot-plug rebind repaints ITS OWN mode over the whole 8×8
  // (an off-mode driver clears the grid) via a deferred output.open()
  // callback — so we must repaint OURS once that has settled. 250ms is
  // comfortably after Chrome resolves the port open.
  var TICK_MS = 250;
  // Press feedback. paintButton is static-RGB only (no hardware pulse in
  // the public API), so we flash the pad white on note-on and let the
  // note-off repaint restore its resting colour.
  var FEEDBACK_RGB = { r: 127, g: 127, b: 127 };

  var S = null; // null = detached / inert

  function lp() { return window.Launchpad || null; }

  function driverConnected() {
    var d = lp();
    return !!(d && typeof d.isConnected === "function" && d.isConnected());
  }

  // ---------- pad index mapping (delegated to the driver) ----------

  /** lpview idx (0..63, row-major from top-left) → programmer-mode LED
   * index (11..88). Reuses Launchpad.programmerPadIndex so the numbering
   * comes from the code that already drives real hardware. */
  function hwIndexForPad(idx) {
    var d = lp();
    if (!d || typeof d.programmerPadIndex !== "function") return -1;
    var lpRow = Math.floor(idx / 8), lpCol = idx % 8;
    return d.programmerPadIndex(7 - lpRow, lpCol); // 7-row: lpview top = hw top
  }

  /** Inverse: hardware note (programmer pad index) → lpview idx, or -1
   * when the note is a ring button / outside the 8×8. */
  function padForHwNote(note) {
    var d = lp();
    if (!d || typeof d.rowColForProgrammerIndex !== "function") return -1;
    var rc = d.rowColForProgrammerIndex(note);
    if (!rc) return -1;
    var lpRow = 7 - rc.row, lpCol = rc.col;
    if (lpRow < 0 || lpRow > 7 || lpCol < 0 || lpCol > 7) return -1;
    return lpRow * 8 + lpCol;
  }

  // ---------- LED painting ----------

  /** 0..255 channel → 0..127 SysEx range (same >>1 downscale kit-hw uses
   * so hardware brightness matches the Jam Pads mirror). */
  function to7(v) {
    v = (v | 0) >> 1;
    return v < 0 ? 0 : (v > 127 ? 127 : v);
  }

  /** The 0..127 RGB for lpview pad `i`, or null for an empty pad (off).
   * lpview supplies the 0..255 colour via the padColor accessor — the
   * exact same padFill()/categoryColor RGB the on-screen tile uses. */
  function colorForPad(i) {
    if (!S || typeof S.padColor !== "function") return null;
    var c = null;
    try { c = S.padColor(i); } catch (_) { c = null; }
    if (!c) return null;
    return { r: to7(c.r), g: to7(c.g), b: to7(c.b) };
  }

  function paintPad(i, rgb) {
    var d = lp();
    if (!d || !driverConnected()) return;
    var cache = S.ledCache[i];
    if (rgb) {
      if (cache && cache.r === rgb.r && cache.g === rgb.g && cache.b === rgb.b) return;
      S.ledCache[i] = rgb;
      d.paintButton(hwIndexForPad(i), rgb.r, rgb.g, rgb.b);
    } else {
      if (cache && cache.r === 0 && cache.g === 0 && cache.b === 0) return;
      S.ledCache[i] = { r: 0, g: 0, b: 0 };
      d.blankButton(hwIndexForPad(i));
    }
  }

  function repaintAll() {
    if (!S || !driverConnected()) return;
    for (var i = 0; i < PAD_COUNT; i++) paintPad(i, colorForPad(i));
  }

  function repaintPad(i) {
    if (!S || !driverConnected()) return;
    paintPad(i, colorForPad(i));
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
    var vel = data[2] || 0;
    // Note-On w/ vel>0 = press; Note-Off (0x80) or Note-On vel 0 =
    // release. Same shape the driver's _onMidi decodes (the MK3 sends
    // Note-On vel 0 for releases in Programmer Mode).
    var isOn = status === 0x90 && vel > 0;
    var isOff = status === 0x80 || (status === 0x90 && vel === 0);
    if (!isOn && !isOff) return;
    var idx = padForHwNote(data[1]);
    if (idx < 0) return; // ring button / outside the 8×8 — not ours
    if (isOn) {
      // Immediate press feedback: flash white, cache it so the release
      // repaint restores the resting colour.
      var d = lp();
      if (d && driverConnected()) {
        S.ledCache[idx] = { r: FEEDBACK_RGB.r, g: FEEDBACK_RGB.g, b: FEEDBACK_RGB.b };
        try { d.paintButton(hwIndexForPad(idx), FEEDBACK_RGB.r, FEEDBACK_RGB.g, FEEDBACK_RGB.b); } catch (_) {}
      }
      if (typeof S.onPress === "function") {
        try { S.onPress(idx); } catch (_) {}
      }
    } else {
      if (typeof S.onRelease === "function") {
        try { S.onRelease(idx); } catch (_) {}
      }
      repaintPad(idx); // restore the pad's resting colour after the flash
    }
  }

  // ---------- connect-edge + status poll ----------

  function tick() {
    if (!S) return;
    var connected = driverConnected();
    if (connected && !S.wasConnected) {
      // (Re)connect: the driver just (re)bound its ports and repainted
      // its OWN mode over the full grid. Drop our cache and repaint ours
      // so the chop grid wins on the surface we own.
      S.ledCache = [];
      repaintAll();
    }
    if (connected !== S.wasConnected) {
      S.wasConnected = connected;
      notifyStatus();
    }
  }

  function notifyStatus() {
    if (S && typeof S.onStatusChange === "function") {
      try { S.onStatusChange(); } catch (_) {}
    }
  }

  // ---------- public API ----------

  function status() {
    if (!S) return { state: "detached", connected: false, device: null };
    var d = lp();
    var connected = driverConnected();
    var device = (connected && d && typeof d.getStatus === "function")
      ? (d.getStatus().deviceName || null) : null;
    return { state: connected ? "attached" : "unavailable", connected: connected, device: device };
  }

  async function attach(opts) {
    if (S) return status();
    var d = lp();
    // No driver script / no WebMIDI: resolve inert, never throw.
    if (!d || typeof d.isSupported !== "function" || !d.isSupported()) {
      return { state: "unavailable", reason: "webmidi_unsupported", connected: false, device: null };
    }
    S = {
      padColor: (opts && opts.padColor) || null,
      onPress: (opts && opts.onPress) || null,
      onRelease: (opts && opts.onRelease) || null,
      onStatusChange: (opts && opts.onStatusChange) || null,
      access: null,
      input: null,
      onMidi: onMidi,
      onStateChange: null,
      ledCache: [],
      // false so the FIRST tick sees the rising edge and repaints AFTER
      // the driver's deferred enter-Programmer-Mode repaint has settled
      // (see TICK_MS). Painting synchronously here would race that repaint
      // and get wiped.
      wasConnected: false,
      timer: 0,
    };

    // Driver connect path: prompts for MIDI+SysEx, binds the MK3 port
    // pair, enters Programmer Mode, keeps _enabled=true so its own
    // statechange handler re-binds on hot-plug for us. This is the
    // permission-prompt trigger.
    var st = null;
    try { st = await d.enable(); } catch (_) { st = null; }
    if (!S) return { state: "detached", connected: false, device: null }; // detached mid-await
    if (!st || st.supported === false || st.error === "permission_denied") {
      // Permission denied / hard failure: stay attached but LED-inert.
      // Keep polling so a later grant/replug still lights the grid.
      S.timer = setInterval(tick, TICK_MS);
      notifyStatus();
      return { state: "unavailable", reason: (st && st.error) || "enable_failed", connected: false, device: null };
    }

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

    S.timer = setInterval(tick, TICK_MS);
    // Surface the device name immediately (the pill can update before the
    // first LED repaint); the grid lights on the first tick's rising edge.
    notifyStatus();
    return status();
  }

  /** Repaint the full 8×8 from lpview's current pad colours. lpview calls
   * this after every renderGrid (Load / Auto Kit / Drum Kit / Stem /
   * Slices all funnel through renderGrid). No-op when detached / no
   * device. */
  function repaint() {
    if (!S) return;
    repaintAll();
  }

  function detach() {
    if (!S) return;
    var s = S;
    // Hand the device back to the driver's own mode painter (off → clears
    // the grid). We never called enable's counterpart disable() — jam.js
    // owns that lifecycle — so repaint() is the clean release that stops
    // our stale chop colours lingering on the hardware.
    try {
      var d = lp();
      if (d && typeof d.repaint === "function") d.repaint();
    } catch (_) {}
    S = null;
    if (s.timer) clearInterval(s.timer);
    if (s.input) {
      try { s.input.removeEventListener("midimessage", s.onMidi); } catch (_) {}
    }
    if (s.access && s.onStateChange) {
      try { s.access.removeEventListener("statechange", s.onStateChange); } catch (_) {}
    }
  }

  window.JamnLpHW = {
    attach: attach,
    detach: detach,
    repaint: repaint,
    status: status,
    // Pure mapping helpers exposed for DOM-free smoke tests (lpview pattern).
    _internals: { hwIndexForPad: hwIndexForPad, padForHwNote: padForHwNote },
  };
})();
