/* remix.js — one-tap Remix transforms for the web Jam Pads surface.
 *
 * Web parity for the native Remix sheets:
 *   mobile-ios  Sources/ToneForgeMobile/Views/Jam/RemixSheet.swift
 *   jam-desktop Sources/JamDesktop/RemixSheetView.swift
 * over the same backend endpoints RemixClients.swift wraps:
 *
 *   Flip       GET /api/song/{id}/kit?kind=flip        SamplePack + defaultSequence
 *   Humanize   GET /api/song/{id}/groove               16 per-slot delays (step fractions)
 *   Re-Drum    GET /api/song/{id}/redrum-candidates    ranked kit donors
 *              GET /api/song/{id}/redrum?kit=…         replacement drums stem WAV
 *   Pack       GET /api/song/{id}/instrument-pack      .sfz zip download
 *
 * This module owns the fetches + row UI; everything that touches audio
 * goes through the host ctx (jam.js) so there is exactly one playback
 * layer, mirroring how RemixSheet drives AppState/SessionController:
 *
 *   window.JamnRemix.mount(container, ctx)
 *     ctx.entry              full /api/history/{id} entry (needs .id)
 *     ctx.loadKit(kind)      mount the song's kit on the pads (auto|drums|flip)
 *     ctx.loadDonorDrumKit(donorId)  donor song's drum kit on the pads
 *     ctx.swapDrums(arrayBuffer) → Promise  decode + swap the drums stem
 *                            in the song mix, preserving position + mix
 *     ctx.restoreDrums() → Promise   back to the song's real drums (A/B)
 *     ctx.setGrooveOffsets(offsets|null)   sequencer humanize template
 *     ctx.isSongPlaying() → bool  OPTIONAL: song transport state, for the
 *                            playing-aware "Applied:" line (falls back to
 *                            window.JamnKitHost.isPlaying, then false)
 *   window.JamnRemix.unmount()
 *
 * Per-row busy flags (never one global spinner painted on every row —
 * the same field-reported confusion the native sheets fixed) and a
 * single error line; nothing here blocks jamming.
 */
(function () {
  'use strict';

  var state = null; // one mount at a time, like kit.js/sequencer.js

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function api(entryId, leaf, query) {
    return '/api/song/' + encodeURIComponent(entryId) + '/' + leaf + (query || '');
  }

  function setError(msg) {
    if (!state || !state.errEl) return;
    state.errEl.textContent = msg || '';
    state.errEl.hidden = !msg;
  }

  /** Visible "Applied: …" line + row highlight. Every transform here
   * lands on a surface that may not be SOUNDING right now (paused song
   * mix, idle sequencer), which field reports read as "remix did
   * nothing" — so each success says exactly what changed and where to
   * hear it, instead of relying on the audio to announce itself. */
  function setApplied(key, msg) {
    if (!state) return;
    state.appliedKey = key || null;
    state.appliedMsg = msg || '';
    render();
  }

  /** Is the song transport actually playing? ctx hook when the host
   * provides one; otherwise the kit-host transport contract that jam.js
   * already publishes for kit.js. Unknown → false (safe: worst case we
   * tell an already-listening user to press Play). */
  function songPlaying() {
    try {
      if (state && state.ctx && typeof state.ctx.isSongPlaying === 'function') {
        return !!state.ctx.isSongPlaying();
      }
      if (window.JamnKitHost && typeof window.JamnKitHost.isPlaying === 'function') {
        return !!window.JamnKitHost.isPlaying();
      }
    } catch (_) {}
    return false;
  }

  /** Suffix steering the user to where the change becomes audible. */
  function hearItNote() {
    return songPlaying() ? '' : ' Press Play on the song to hear it.';
  }

  function setBusy(key, on) {
    if (!state) return;
    state.busy = on ? key : null;
    // A new in-flight transform obsoletes the previous applied line —
    // stale "Applied: …" next to a spinner reads as a finished action.
    if (on) { state.appliedKey = null; state.appliedMsg = ''; }
    render();
  }

  // ------------------------------------------------------------------
  // Transforms (same semantics as AppState/SessionController)

  /** Flip: a new beat from the song's own DNA. The kit mount stages the
   * manifest's defaultSequence into the sequencer store (kit.js hook),
   * so the beat is armed in the Sequencer pane after the pads land. */
  function doFlip() {
    if (!state || state.busy) return;
    setError(null);
    try {
      state.ctx.loadKit('flip');
      // The flip is SILENT until the user plays it: the song mix is
      // untouched, and the web sequencer never auto-starts (unlike iOS).
      // Say so, or the transform reads as a no-op.
      setApplied('flip',
        'Applied: Flip — new kit loading onto the pads; the beat is armed '
        + 'in the Sequencer. Open Sequencer and press play to hear it.');
    } catch (e) {
      setApplied(null, '');
      setError('Flip failed: ' + e);
    }
  }

  /** Humanize toggle; fetches the groove template on first use and
   * caches it for the song (RemixClient.fetchGroove + toggleHumanize). */
  // Humanize only bends SEQUENCER step timing (a few ms per step) — it
  // never touches the song mix. Both messages below spell that out; the
  // silent-failure path (sequencer module absent → jam.js optional-
  // chains the hook into a no-op while the toggle lights up) becomes a
  // real error instead.
  var HUMANIZE_ON_MSG =
    'Applied: Humanize on — sequencer steps now swing with this song’s '
    + 'own micro-timing. Subtle by design; audible while a sequence plays.';

  function doHumanize() {
    if (!state || state.busy) return;
    setError(null);
    if (state.humanizeOn) {
      state.humanizeOn = false;
      try { state.ctx.setGrooveOffsets(null); } catch (_) {}
      setApplied(null, 'Humanize off — sequencer timing back to the grid.');
      return;
    }
    if (!window.JamnSequencer) {
      setError('Humanize needs the Sequencer (module missing on this page).');
      return;
    }
    if (state.grooveTemplate) {
      state.humanizeOn = true;
      try { state.ctx.setGrooveOffsets(state.grooveTemplate); } catch (_) {}
      setApplied('humanize', HUMANIZE_ON_MSG);
      return;
    }
    var entryId = state.entryId;
    setBusy('humanize', true);
    fetch(api(entryId, 'groove'))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!state || state.entryId !== entryId) return;
        var offsets = data && data.groove && data.groove.offsetsSteps;
        if (!Array.isArray(offsets) || !offsets.length) {
          throw new Error('no groove template');
        }
        state.grooveTemplate = offsets;
        state.humanizeOn = true;
        try { state.ctx.setGrooveOffsets(offsets); } catch (_) {}
        setApplied('humanize', HUMANIZE_ON_MSG);
      })
      .catch(function (e) {
        setError('Humanize unavailable: ' + ((e && e.message) || e));
      })
      .then(function () {
        if (state && state.entryId === entryId) setBusy('humanize', false);
      });
  }

  /** Expand/collapse the Re-Drum donor row; candidates load once per
   * song (RemixSheet loads them in .task on open). */
  function toggleRedrumRow() {
    if (!state) return;
    state.redrumOpen = !state.redrumOpen;
    render();
    if (state.redrumOpen && !state.candidatesLoaded && !state.candidatesLoading) {
      loadCandidates();
    }
  }

  function loadCandidates() {
    var entryId = state.entryId;
    state.candidatesLoading = true;
    fetch(api(entryId, 'redrum-candidates'))
      .then(function (r) { return r.ok ? r.json() : { candidates: [] }; })
      .catch(function () { return { candidates: [] }; })
      .then(function (data) {
        if (!state || state.entryId !== entryId) return;
        state.candidates = ((data && data.candidates) || []).slice(0, 6);
        state.candidatesLoaded = true;
        state.candidatesLoading = false;
        render();
      });
  }

  /** Donor display name for the applied line ("song:<id>" → its title). */
  function donorLabel(kit) {
    if (kit === 'self') return 'Tightened (own kit)';
    var id = kit.indexOf('song:') === 0 ? kit.slice(5) : kit;
    for (var i = 0; i < state.candidates.length; i++) {
      var c = state.candidates[i];
      if (c && c.entryId === id) return c.name || id.slice(0, 8);
    }
    return id.slice(0, 8);
  }

  /** Apply Re-Drum: swap the song-mix drums stem for the rendered
   * replacement, then land the kit on the PADS too (a stem-only swap
   * left native users hunting for where the new drums lived).
   * kit is "self" | "song:<entryId>"; first render per (song, kit)
   * pair happens server-side (~10-60 s). */
  function applyRedrum(kit) {
    if (!state || state.busy) return;
    var entryId = state.entryId;
    setError(null);
    state.redrumBusyKit = kit;
    setBusy('redrum', true);
    fetch(api(entryId, 'redrum', '?kit=' + encodeURIComponent(kit)))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.arrayBuffer();
      })
      .then(function (ab) {
        if (!state || state.entryId !== entryId) return null;
        return state.ctx.swapDrums(ab);
      })
      .then(function () {
        if (!state || state.entryId !== entryId) return;
        state.redrumActiveKit = kit;
        // Pads follow the mix (applyRedrum on iOS/desktop). A failure
        // here is non-fatal (the mix swap already landed) but must not
        // vanish — the pads staying stale is exactly the confusion the
        // pads-follow exists to prevent.
        try {
          if (kit.indexOf('song:') === 0) {
            state.ctx.loadDonorDrumKit(kit.slice(5));
          } else {
            state.ctx.loadKit('drums');
          }
        } catch (e) {
          try { console.warn('[remix] pads follow-up failed:', e); } catch (_) {}
        }
        // The swap restarts audio only when the song is PLAYING;
        // paused, it's inaudible until the next Play — say which.
        var what = kit === 'self'
          ? 'drums re-triggered from this song’s own tightened kit'
          : 'drums swapped to “' + donorLabel(kit) + '”';
        setApplied('redrum',
          'Applied: Re-Drum — ' + what
          + ', in the song mix and on the pads.' + hearItNote());
      })
      .catch(function (e) {
        setError('Re-Drum failed: ' + ((e && e.message) || e));
      })
      .then(function () {
        if (state && state.entryId === entryId) {
          state.redrumBusyKit = null;
          setBusy('redrum', false);
        }
      });
  }

  /** Back to the song's real drums (A/B), mirroring clearRedrum. */
  function clearRedrum() {
    if (!state || state.busy || !state.redrumActiveKit) return;
    var entryId = state.entryId;
    setError(null);
    state.redrumBusyKit = 'original';
    setBusy('redrum', true);
    Promise.resolve()
      .then(function () { return state.ctx.restoreDrums(); })
      .then(function () {
        if (!state || state.entryId !== entryId) return;
        state.redrumActiveKit = null;
        setApplied(null,
          'Original drums restored in the song mix.' + hearItNote());
      })
      .catch(function (e) {
        setError('Restore failed: ' + ((e && e.message) || e));
      })
      .then(function () {
        if (state && state.entryId === entryId) {
          state.redrumBusyKit = null;
          setBusy('redrum', false);
        }
      });
  }

  /** Instrument Pack: fetch the .sfz zip and hand it to the browser's
   * download path (desktop parity: the zip lands in Downloads). Fetched
   * as a blob (not a bare navigation) so busy/error states are real —
   * the render runs server-side in a worker and can take seconds. */
  function doInstrumentPack() {
    if (!state || state.busy) return;
    var entryId = state.entryId;
    setError(null);
    setBusy('pack', true);
    fetch(api(entryId, 'instrument-pack'))
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.blob();
      })
      .then(function (blob) {
        if (!state || state.entryId !== entryId) return;
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url;
        a.download = 'Instrument-' + entryId.slice(0, 8) + '.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        // Revoke on a delay — Safari cancels the download if the URL
        // disappears before the save sheet commits.
        setTimeout(function () { URL.revokeObjectURL(url); }, 10000);
        setApplied('pack',
          'Applied: Instrument Pack — .sfz zip saved to your downloads.');
      })
      .catch(function (e) {
        setError('Instrument Pack unavailable: ' + ((e && e.message) || e));
      })
      .then(function () {
        if (state && state.entryId === entryId) setBusy('pack', false);
      });
  }

  // ------------------------------------------------------------------
  // DOM

  function mainButton(label, title, key, onClick) {
    var b = el('button', 'tab-control-btn remix-btn', label);
    b.type = 'button';
    b.title = title;
    b.dataset.remix = key;
    b.addEventListener('click', onClick);
    return b;
  }

  function donorButton(label, kit, title) {
    var b = el('button', 'tab-control-btn remix-btn', label);
    b.type = 'button';
    b.title = title;
    b.dataset.redrumKit = kit;
    b.addEventListener('click', function () { applyRedrum(kit); });
    return b;
  }

  function build() {
    var root = state.container;
    root.innerHTML = '';
    var bar = el('div', 'remix-bar');
    bar.appendChild(el('span', 'remix-title', 'Remix'));

    var group = el('div', 'tab-control-group remix-group');
    group.setAttribute('role', 'group');
    group.setAttribute('aria-label', 'Remix transforms');
    group.appendChild(mainButton('Flip', 'A new beat built from the song’s own DNA — pads load here, the pattern lands in the Sequencer', 'flip', doFlip));
    group.appendChild(mainButton('Humanize', 'Sequences swing with this song’s own timing', 'humanize', doHumanize));
    group.appendChild(mainButton('Re-Drum', 'Keep this song’s groove, play it on another song’s drums — in the mix and on the pads', 'redrum', toggleRedrumRow));
    group.appendChild(mainButton('Instrument Pack', 'The song as a playable sampler patch (.sfz zip)', 'pack', doInstrumentPack));
    bar.appendChild(group);

    var err = el('span', 'remix-error');
    err.hidden = true;
    bar.appendChild(err);
    state.errEl = err;

    // "Applied: …" line — the transforms land on surfaces that may be
    // silent right now (paused mix, idle sequencer), so success must be
    // stated, not inferred from the audio.
    var status = el('span', 'remix-note remix-status');
    status.hidden = true;
    status.setAttribute('role', 'status'); // screen readers announce it
    bar.appendChild(status);
    state.statusEl = status;

    var donors = el('div', 'remix-donors');
    donors.hidden = true;
    bar.appendChild(donors);
    state.donorsEl = donors;

    root.appendChild(bar);
    render();
  }

  function render() {
    if (!state || !state.container) return;
    var btns = state.container.querySelectorAll('.remix-btn[data-remix]');
    for (var i = 0; i < btns.length; i++) {
      var b = btns[i];
      var key = b.dataset.remix;
      var busy = state.busy === key
        || (key === 'redrum' && state.busy === 'redrum');
      b.classList.toggle('is-busy', busy);
      b.disabled = !!state.busy && !busy;
      if (key === 'humanize') b.classList.toggle('is-active', state.humanizeOn);
      if (key === 'redrum') {
        b.classList.toggle('is-active',
          state.redrumOpen || !!state.redrumActiveKit);
      }
      if (key === 'flip' || key === 'pack') {
        b.classList.toggle('is-active', state.appliedKey === key);
      }
    }
    if (state.statusEl) {
      state.statusEl.textContent = state.appliedMsg || '';
      state.statusEl.hidden = !state.appliedMsg;
    }
    renderDonors();
  }

  function renderDonors() {
    var wrap = state.donorsEl;
    if (!wrap) return;
    wrap.hidden = !state.redrumOpen;
    if (!state.redrumOpen) return;
    wrap.innerHTML = '';
    if (state.redrumActiveKit) {
      var orig = el('button', 'tab-control-btn remix-btn', 'Original drums');
      orig.type = 'button';
      orig.title = 'Back to the song’s real drums';
      orig.dataset.redrumKit = '__original__';
      orig.addEventListener('click', clearRedrum);
      wrap.appendChild(orig);
    }
    wrap.appendChild(donorButton('Tightened (own kit)', 'self',
      'Re-trigger this song’s own cleaned kit'));
    if (state.candidatesLoading || (!state.candidatesLoaded && !state.candidates.length)) {
      wrap.appendChild(el('span', 'remix-note', 'Finding kit donors…'));
    } else if (state.candidatesLoaded && !state.candidates.length) {
      wrap.appendChild(el('span', 'remix-note',
        'Analyze more songs to unlock cross-song kits.'));
    }
    state.candidates.forEach(function (c) {
      wrap.appendChild(donorButton(c.name || c.entryId, 'song:' + c.entryId,
        'This song’s groove on that song’s drums — first use renders on the server, give it a few seconds'));
    });
    // Busy/active decoration on donor rows.
    var rows = wrap.querySelectorAll('.remix-btn');
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var kit = r.dataset.redrumKit;
      var busyKit = state.redrumBusyKit === kit
        || (kit === '__original__' && state.redrumBusyKit === 'original');
      r.classList.toggle('is-busy', busyKit);
      r.disabled = !!state.redrumBusyKit && !busyKit;
      r.classList.toggle('is-active',
        kit === state.redrumActiveKit && !busyKit);
      if (busyKit) r.textContent = r.textContent + ' — rendering…';
    }
  }

  // ------------------------------------------------------------------
  // Public surface

  window.JamnRemix = {
    mount: function (container, ctx) {
      if (!container || !ctx || !ctx.entry || !ctx.entry.id) return;
      window.JamnRemix.unmount();
      state = {
        container: container,
        ctx: ctx,
        entryId: ctx.entry.id,
        busy: null,
        humanizeOn: false,
        grooveTemplate: null,
        redrumOpen: false,
        redrumActiveKit: null,
        redrumBusyKit: null,
        candidates: [],
        candidatesLoaded: false,
        candidatesLoading: false,
        appliedKey: null,
        appliedMsg: '',
        errEl: null,
        statusEl: null,
        donorsEl: null,
      };
      build();
    },
    unmount: function () {
      if (!state) return;
      try { state.container.innerHTML = ''; } catch (_) {}
      state = null;
    },
  };
})();
