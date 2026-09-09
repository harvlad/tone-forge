/**
 * JamnQuantize — a single, system-wide pad Quantize setting shared by every
 * web surface (Jam Pads / kit.js, Launchpad / lpview.js, …).
 *
 * Before this existed each surface kept its own `state.quantize`, so changing
 * Quantize on the Launchpad left the Jam Pads on their own stale value and
 * vice-versa. There is one musical intent — "when do triggered pads start" —
 * so there should be one value. This module is that value.
 *
 * Value is one of "off" | "beat" | "bar" (default "bar"). It is permissive
 * about the exact string so a surface that offers extra grids (lpview's
 * "phrase") can still flow through and round-trip; consumers that only render
 * the three-way control simply show no active highlight for a grid they don't
 * offer. Persisted in localStorage under `jamn.quantize` so the choice
 * survives reloads, and mirrored across browser tabs via the `storage` event.
 *
 * API (window.JamnQuantize):
 *   get()          → current value (string)
 *   set(value)     → update + persist + notify subscribers (no-op if unchanged)
 *   subscribe(fn)  → fn(value) on every external/internal change;
 *                    returns an unsubscribe function.
 */
(function () {
  "use strict";

  var KEY = "jamn.quantize";
  var DEFAULT = "bar";
  var subs = [];
  var cur;

  function load() {
    try {
      var v = window.localStorage.getItem(KEY);
      if (typeof v === "string" && v) return v;
    } catch (_) {}
    return DEFAULT;
  }

  cur = load();

  function notify(v) {
    // Snapshot: a subscriber could unsubscribe during iteration.
    var list = subs.slice();
    for (var i = 0; i < list.length; i++) {
      try {
        list[i](v);
      } catch (_) {}
    }
  }

  function get() {
    return cur;
  }

  function set(value) {
    var v = typeof value === "string" && value ? value : DEFAULT;
    if (v === cur) return;
    cur = v;
    try {
      window.localStorage.setItem(KEY, v);
    } catch (_) {}
    notify(v);
  }

  function subscribe(fn) {
    if (typeof fn !== "function") return function () {};
    subs.push(fn);
    return function () {
      var i = subs.indexOf(fn);
      if (i !== -1) subs.splice(i, 1);
    };
  }

  // Cross-tab sync: `storage` fires only in *other* documents, so this never
  // re-fires for the tab that made the change — no feedback loop.
  try {
    window.addEventListener("storage", function (e) {
      if (!e || e.key !== KEY) return;
      var v = typeof e.newValue === "string" && e.newValue ? e.newValue : DEFAULT;
      if (v === cur) return;
      cur = v;
      notify(v);
    });
  } catch (_) {}

  window.JamnQuantize = { get: get, set: set, subscribe: subscribe };
})();
