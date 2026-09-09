/* lp-hw.js — physical Novation Launchpad mirror for the merged web
 * "Launchpad" surface (kit.js, window.JamnKit, #view-kit). This is the
 * single hardware-mirror owner: it LED-paints the physical pads to match
 * the on-screen kit grid and routes hardware pad presses into the SAME
 * trigger path as an on-screen tap.
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

  function kitViewActive() {
    var v = document.getElementById("view-kit");
    return !!(v && v.classList.contains("active"));
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
      // drop the cache so the whole grid repaints from scratch.
      S.ledCache = [];
      S.lastCols = 0;
    }
    S.wasConnected = connected;
    S.state = connected ? "attached" : "unavailable";
    if (!connected) return;

    if (!kitViewActive()) {
      // Off-surface: keep our LEDs dark instead of showing a stale grid;
      // the driver's own mode painter owns the device there.
      if (S.ledCache.length) blankAll();
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
    var isOn = status === 0x90 && (data[2] || 0) > 0;
    var isOff = status === 0x80 || (status === 0x90 && (data[2] || 0) === 0);
    if (!isOn && !isOff) return;
    if (!kitViewActive()) return;      // other surfaces own the grid then
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
    S.lastCols = 0;
    ledTick();
  }

  function detach() {
    if (!S) return;
    var s = S;
    try { blankAll(); } catch (_) {}
    S = null;
    if (s.timer) clearInterval(s.timer);
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
    _internals: { hwIndexForPad: hwIndexForPad, padForHwNote: padForHwNote, gridCols: gridCols },
  };
})();
