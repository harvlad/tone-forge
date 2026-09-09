/* audio-context.js — the page's ONE AudioContext, plus the iOS unlock rules.
 *
 * Every surface used to mint its own context (kit.js ×2, sequencer, chopedit,
 * contribute, arrangement, jam.js ×4). On desktop that is merely wasteful.
 * On iOS Safari it is the bug: WebKit caps a page at four concurrent
 * AudioContexts, and past the cap `new AudioContext()` hands back a context
 * that never reaches 'running'. Opening a song, then the Auto Kit, then the
 * sequencer blows the cap — the UI animates from requestAnimationFrame while
 * nothing reaches the speaker. `PadEngine`'s own doc comment says the context
 * is BORROWED; this module is what it borrows from.
 *
 * Two more iOS-only rules live here because every caller got them wrong:
 *
 *   1. Safari has a fourth, non-standard context state: 'interrupted'. It is
 *      entered on a phone call, Siri, a route change (AirPods in/out), and a
 *      backgrounded tab. Guards written as `state === 'suspended'` — which is
 *      what every surface had — skip it, so the context is never resumed and
 *      the page plays silence. Treat anything that is not 'running' as
 *      needing a resume.
 *
 *   2. Without `navigator.audioSession.type`, iOS files a WebAudio-only page
 *      under the ambient session: the hardware ringer switch mutes it and any
 *      other app ducks it. 'playback' is the category a music app wants
 *      (Safari 16.4+; a no-op elsewhere).
 *
 * Classic script, no build step — load it BEFORE any consumer.
 */
(function () {
  "use strict";

  var ctx = null;
  var sessionApplied = false;

  function applyAudioSession() {
    if (sessionApplied) return;
    try {
      var s = navigator.audioSession;
      if (s && s.type !== "playback") s.type = "playback";
      sessionApplied = true;
    } catch (_) { /* not Safari 16.4+, or the setter is guarded */ }
  }

  function onStateChange() {
    // Re-assert the session on every transition: iOS resets it across an
    // interruption, and a re-muted ringer switch is indistinguishable from
    // a dead context to the user.
    sessionApplied = false;
    applyAudioSession();
    if (ctx && ctx.state !== "running") {
      // Best effort — outside a gesture iOS just rejects, and the next
      // gesture-driven unlock() picks it up.
      try { ctx.resume().catch(function () {}); } catch (_) {}
    }
  }

  /** The shared context. Created on first call; null if Web Audio is absent. */
  function context() {
    if (ctx) return ctx;
    var AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    try {
      // 'interactive' is the smallest render buffer the browser will give
      // us — the monitor and the pads both need it, and Safari's default
      // ('balanced') adds 20-40 ms of round-trip on top of the OS layer.
      ctx = new AC({ latencyHint: "interactive" });
    } catch (_) {
      try { ctx = new AC(); } catch (_) { return null; }
    }
    applyAudioSession();
    try {
      ctx.addEventListener("statechange", onStateChange);
    } catch (_) {
      // Older Safari exposes only the property setter.
      try { ctx.onstatechange = onStateChange; } catch (_) {}
    }
    return ctx;
  }

  /** Resume if the context is in ANY non-running state ('suspended' or
   *  Safari's 'interrupted'). Safe to call from a gesture handler — it does
   *  no async work before resume(), so the activation token survives.
   *  Never creates a context: unlocking one nobody asked for would spin up
   *  a hardware audio unit on a page that may never play. */
  function unlock() {
    if (!ctx) return Promise.resolve(false);
    applyAudioSession();
    if (ctx.state === "running") return Promise.resolve(true);
    try {
      return Promise.resolve(ctx.resume()).then(
        function () { return ctx.state === "running"; },
        function () { return false; },
      );
    } catch (_) {
      return Promise.resolve(false);
    }
  }

  /** True when `c` is the page-wide context, i.e. NOT yours to close. */
  function isShared(c) { return !!c && c === ctx; }

  /** close() a context unless it's the shared one. Returns true if closed. */
  function release(c) {
    if (!c || c === ctx) return false;
    try {
      if (c.state !== "closed") c.close();
    } catch (_) {}
    return true;
  }

  // Belt-and-braces unlock on any user gesture. Capture phase so a handler
  // that stops propagation (the pad grid calls preventDefault) can't starve
  // it, and passive so it never delays a scroll. Cheap: a no-op string
  // compare once the context is running.
  ["pointerdown", "touchend", "mousedown", "keydown"].forEach(function (t) {
    try {
      document.addEventListener(t, unlock, { capture: true, passive: true });
    } catch (_) {
      document.addEventListener(t, unlock, true);
    }
  });

  // Returning to the tab is when an 'interrupted' context is recoverable.
  try {
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) unlock();
    });
  } catch (_) {}

  window.JamnAudio = {
    context: context,
    unlock: unlock,
    isShared: isShared,
    release: release,
    // Triage from the console: __jam.state.ctx === JamnAudio.peek()
    peek: function () { return ctx; },
  };
})();
