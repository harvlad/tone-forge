/* kit-hw.js — SUPERSEDED.
 *
 * This module used to be the Launchpad Pro MK3 hardware mirror for the
 * "Jam Pads" kit, painting only the bottom-left 4×4. When the standalone
 * lpview.js chop grid and the Jam Pads kit merged into one "Launchpad"
 * surface (kit.js, #view-kit), the mirror was consolidated into lp-hw.js
 * (window.JamnLpHW), which follows the kit's own 16↔64 toggle (16 → the
 * top-left 4×4, 64 → the full 8×8) and is the single mirror owner.
 *
 * The include for this file was removed from jam.html and jam.js no longer
 * references window.JamnKitHW. The file is kept only so any stale reference
 * degrades to a harmless no-op instead of a hard error; do not re-wire it.
 */
(function () {
  "use strict";
  if (window.JamnKitHW) return; // never clobber a live mirror
  var noop = function () {};
  window.JamnKitHW = {
    attach: function () { return Promise.resolve({ state: "superseded" }); },
    detach: noop,
    status: function () { return { state: "superseded" }; },
  };
})();
