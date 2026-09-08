/* kit-hw.js — Launchpad Pro MK3 hardware mirror for the Jam Pads kit
 * (kit.js). Classic script: defines window.JamnKitHW = { attach, detach,
 * status }. Nothing runs until attach() is called.
 *
 * Division of labor:
 *   - LED writes + port lifecycle go through the existing driver,
 *     window.Launchpad (launchpad.js): enable() binds ports + enters
 *     Programmer Mode, paintButton/blankButton are its public per-LED
 *     RGB API (launchpad.js paintButton, "r,g,b are 0..127"), and its
 *     internal onstatechange handler re-binds on hot-plug while enabled
 *     (launchpad.js _onStateChange). We deliberately do NOT replicate
 *     the SysEx path — the driver is page-global on jam.html, so it is
 *     callable directly.
 *   - Pad-press INPUT cannot come from the driver: its single-slot
 *     onPadPress/onCcMessage callbacks are claimed by jam.js at boot
 *     (jam.js Launchpad.init call), and init() only fills EMPTY slots.
 *     So this module opens its own MIDIAccess (no SysEx needed for
 *     note input) and uses addEventListener('midimessage') on the same
 *     input port — addEventListener coexists with the driver's
 *     `input.onmidimessage = ...` property handler, so neither side
 *     clobbers the other.
 *
 * Grid mapping — kit pad i (0..15, row-major from the TOP-LEFT, the DOM
 * order kit.js renders) onto the hardware's lower-left 4x4:
 *   Programmer-mode numbering is padIdx = row*10 + col + 1 with row 1..8
 *   BOTTOM-up (launchpad.js header comment). The lower-left 4x4 corner
 *   is the same real estate the web driver's own drum mode uses
 *   ("Drum pads on the bottom-left 4×4", launchpad.js drum stub), so
 *   kit row 0 lands on hardware row 4 and kit row 3 on hardware row 1 —
 *   the block reads top-to-bottom exactly like the on-screen grid.
 *   NB: jam-desktop's LaunchpadController lays kits row-major 8-wide
 *   from the top-left instead; the corner-4x4 placement here follows
 *   the explicit product mandate + the web driver's drum-mode corner
 *   precedent, and keeps rows visually congruent with kit.js.
 *
 * Pad states mirror kit.js's own UI classes: idle = dim colorHint,
 * armed = software-pulsed colorHint (the driver exposes no public
 * LED_PULSE spec, only static RGB paintButton — so we animate
 * brightness ourselves), playing = bright colorHint. Rather than
 * wrapping engine.onstate (a single-slot property kit.js already owns,
 * kit.js attachEngineState) we poll the pad elements' is-armed /
 * is-playing classes at LED cadence — kit.js's rAF keeps those classes
 * authoritative from engine.onstate + padProgress, so the hardware can
 * never disagree with the screen, and a kit.js remount costs nothing.
 *
 * Hardware press → the pad's own <button>: we dispatch synthetic
 * pointerdown/pointerup on the kit pad element. kit.js exposes no
 * public trigger, and calling engine.trigger() directly would bypass
 * its Tap/Loop/Latch mode logic and armed/playing bookkeeping — the
 * pointer path is the exact code the mouse takes (kit.js wirePad).
 *
 * Ableton Link: jam.js consumes /api/link/events for its stem-rate
 * follow (jam.js wireLinkFollow) but exposes no window-level hook, so
 * we open our own EventSource on the same backend SSE fan-out (multi-
 * client by design; X-Accel-Buffering fix commit 8e1be51a). Payload:
 * {v, active, bpm, beat, peers, ts, version, server_ts} where `beat`
 * is the Link beat counter at helper send time — we extrapolate bar
 * phase from it. If the pad engine grows setTransport() (parallel
 * work), we feature-detect it and hand over
 * {isPlaying, tempoBpm, barAnchorSongTime} in the kit AudioContext
 * timebase; absent = silent no-op, engine keeps its own lock grid.
 *
 * Known shared-surface caveats (accepted, by design):
 *   - launchpad.js mode painters own the full 8x8; if jam.js repaints
 *     (chord change while a song plays in another view) our corner is
 *     overwritten until the next LED tick redraws it. Mirroring is
 *     gated on the Jam Pads view being active to keep the fight rare.
 *   - Synthetic pointer events are not user gestures: if the page has
 *     never been clicked, the kit AudioContext may still be suspended
 *     and the first hardware press will be silent. One click/tap on
 *     the page arms audio for good.
 */
(function () {
  "use strict";

  // Port discovery constants replicated from launchpad.js (DEVICE_NAME_HINTS
  // / PORT_PREFERENCE + _matchesLaunchpad/_portPreferenceRank) — needed only
  // for our INPUT listener; output stays inside the driver.
  var DEVICE_NAME_HINTS = ["launchpad pro mk3", "lppromk3", "lp pro mk3"];
  var PORT_PREFERENCE = ["midi", "live"];

  var PAD_COUNT = 16;
  var ACCENT = { r: 139, g: 92, b: 246 }; // kit.js ACCENT fallback
  var LED_TICK_MS = 90;      // LED cadence; also the armed-pulse frame rate
  var PULSE_PERIOD_MS = 900; // soft sine pulse, roughly the MK3's own tempo
  var DIM_IDLE = 0.12;       // idle pads glow their hint without shouting

  // ---------- pad index mapping ----------

  /** kit padIdx (0..15, row-major from top-left) → Programmer-Mode LED
   * index in the lower-left 4x4. Row math per launchpad.js header:
   * padIdx = row*10 + col + 1, row 1..8 bottom-up. */
  function hwIndexForPad(i) {
    var kitRow = Math.floor(i / 4); // 0 = top of the 4x4 block
    var kitCol = i % 4;
    return (4 - kitRow) * 10 + kitCol + 1;
  }

  /** Inverse: hardware note (Programmer pad index) → kit padIdx, or -1
   * when the note is outside our 4x4 block. */
  function padForHwNote(note) {
    var tens = Math.floor(note / 10);
    var ones = note % 10;
    if (tens < 1 || tens > 4 || ones < 1 || ones > 4) return -1;
    return (4 - tens) * 4 + (ones - 1);
  }

  // ---------- module state ----------

  var S = null; // null = detached/inert

  function lp() { return window.Launchpad || null; }

  function kitViewActive() {
    var v = document.getElementById("view-kit");
    return !!(v && v.classList.contains("active"));
  }

  function kitPadEls() {
    var root = document.getElementById("kit-root");
    if (!root) return [];
    return Array.prototype.slice.call(
      root.querySelectorAll(".kit-grid .kit-pad"), 0, PAD_COUNT);
  }

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

  // ---------- LED mirroring ----------

  /** Scale a 0..255 tint into the driver's 0..127 SysEx range with a
   * brightness factor, quantized so the change-gate cache works. */
  function scaled(tint, k) {
    return {
      r: Math.max(0, Math.min(127, Math.round((tint.r >> 1) * k))),
      g: Math.max(0, Math.min(127, Math.round((tint.g >> 1) * k))),
      b: Math.max(0, Math.min(127, Math.round((tint.b >> 1) * k))),
    };
  }

  function paintPad(i, rgb) {
    var cache = S.ledCache[i];
    if (cache && cache.r === rgb.r && cache.g === rgb.g && cache.b === rgb.b) return;
    S.ledCache[i] = rgb;
    lp().paintButton(hwIndexForPad(i), rgb.r, rgb.g, rgb.b);
  }

  function blankBlock() {
    var driver = lp();
    if (!driver || !driver.isConnected()) return;
    for (var i = 0; i < PAD_COUNT; i++) {
      if (S.ledCache[i] && (S.ledCache[i].r || S.ledCache[i].g || S.ledCache[i].b)) {
        driver.blankButton(hwIndexForPad(i));
      }
      S.ledCache[i] = { r: 0, g: 0, b: 0 };
    }
  }

  function ledTick() {
    if (!S) return;
    var driver = lp();
    var connected = !!(driver && driver.isConnected());
    if (connected && !S.wasConnected) {
      // Hot-plug (the driver re-bound via its own statechange handler):
      // drop the cache so the whole block repaints from scratch.
      S.ledCache = [];
    }
    S.wasConnected = connected;
    // Keep the coarse state string honest across hot-plug/unplug.
    S.state = connected ? "attached" : "unavailable";
    if (!connected) return;

    if (!kitViewActive()) {
      // Off-surface: keep the corner dark instead of showing a stale kit.
      blankBlock();
      return;
    }
    var els = kitPadEls();
    // Armed-pulse brightness: 0.25..0.85 soft sine, framed by the tick.
    var ph = (Date.now() % PULSE_PERIOD_MS) / PULSE_PERIOD_MS;
    var pulseK = 0.55 + 0.3 * Math.sin(ph * 2 * Math.PI);

    for (var i = 0; i < PAD_COUNT; i++) {
      var el = els[i];
      if (!el || el.classList.contains("is-empty") || el.classList.contains("is-skeleton")) {
        paintPad(i, { r: 0, g: 0, b: 0 });
        continue;
      }
      var tint = tintForEl(el);
      var rgb;
      if (el.classList.contains("is-playing")) rgb = scaled(tint, 1.0);
      else if (el.classList.contains("is-armed")) rgb = scaled(tint, pulseK);
      else rgb = scaled(tint, DIM_IDLE);
      paintPad(i, rgb);
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

  function matchesLaunchpad(port) {
    if (!port || !port.name) return false;
    var n = port.name.toLowerCase();
    return DEVICE_NAME_HINTS.some(function (h) { return n.indexOf(h) !== -1; });
  }

  function preferenceRank(port) {
    var n = (port.name || "").toLowerCase();
    for (var i = 0; i < PORT_PREFERENCE.length; i++) {
      if (n.indexOf(PORT_PREFERENCE[i]) !== -1) return i;
    }
    return PORT_PREFERENCE.length;
  }

  function bindInput() {
    if (!S || !S.access) return;
    var candidates = [];
    S.access.inputs.forEach(function (p) { if (matchesLaunchpad(p)) candidates.push(p); });
    candidates.sort(function (a, b) { return preferenceRank(a) - preferenceRank(b); });
    var next = candidates[0] || null;
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
    var padIdx = padForHwNote(data[1]);
    if (padIdx < 0) return;              // outside our 4x4 — not ours
    if (!kitViewActive()) return;        // other surfaces own the grid then
    var el = kitPadEls()[padIdx];
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
   * beat to "now" (beat counter + elapsed wall time x bpm/60 — relay-grade
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
    if (!S) return { state: "detached" };
    var driver = lp();
    return {
      state: S.state,
      connected: !!(driver && driver.isConnected()),
      device: driver && driver.getStatus ? (driver.getStatus().deviceName || null) : null,
      link: S.link ? {
        active: S.link.active, bpm: S.link.bpm, peers: S.link.peers,
      } : { active: false, bpm: 0, peers: 0 },
    };
  }

  async function attach() {
    if (S) return status();
    var driver = lp();
    // No driver script / no WebMIDI: resolve inert, never throw.
    if (!driver || !driver.isSupported || !driver.isSupported()) {
      return { state: "unavailable", reason: "webmidi_unsupported" };
    }
    var st = null;
    try {
      // Driver connect path: requests MIDI+SysEx access, binds the MK3
      // port pair, enters Programmer Mode, and keeps _enabled=true so
      // its statechange handler re-binds on hot-plug for us.
      st = await driver.enable();
    } catch (_) { st = null; }
    if (!st || st.supported === false || st.error === "permission_denied") {
      // Permission denied (or hard failure): stay fully inert — no
      // timers, no SSE, nothing to leak.
      return { state: "unavailable", reason: (st && st.error) || "enable_failed" };
    }

    S = {
      state: driver.isConnected() ? "attached" : "unavailable",
      access: null,
      input: null,
      onMidi: onMidi,
      onStateChange: null,
      ledCache: [],
      wasConnected: false,
      lastEngine: null,
      link: null,
      linkEs: null,
      timer: 0,
    };

    // Own access for note INPUT only (see header). Permission is already
    // granted at this point (the driver holds sysex access), so this
    // resolves without a second prompt.
    try {
      S.access = await navigator.requestMIDIAccess();
      bindInput();
      S.onStateChange = function () { if (S) bindInput(); };
      try { S.access.addEventListener("statechange", S.onStateChange); } catch (_) {}
    } catch (_) {
      // Input-less mirroring still has value (LEDs follow the screen);
      // presses just have to come from the mouse.
      S.access = null;
    }

    S.timer = setInterval(ledTick, LED_TICK_MS);
    openLink();
    // Even when no device is present right now we stay armed: the
    // driver's hot-plug rebind + our ledTick connection edge light the
    // grid the moment the MK3 appears.
    return status();
  }

  function detach() {
    if (!S) return;
    var s = S;
    try { blankBlock(); } catch (_) {}
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
    // enable-checkbox lifecycle and other surfaces may be using the
    // device; we only ever painted our 4x4, and we just blanked it.
  }

  window.JamnKitHW = {
    attach: attach,
    detach: detach,
    status: status,
    // Pure mapping helpers exposed for DOM-free smoke tests.
    _internals: { hwIndexForPad: hwIndexForPad, padForHwNote: padForHwNote },
  };
})();
