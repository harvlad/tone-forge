/* remix.js — one-tap Remix transforms for the web Jam Pads surface.
 *
 * Web parity for the native Remix sheets, now as a MODAL SHEET matching
 * the desktop instead of the old inline bar:
 *   mobile-ios  Sources/ToneForgeMobile/Views/Jam/RemixSheet.swift
 *   jam-desktop Sources/JamDesktop/RemixSheetView.swift
 * over the same backend endpoints RemixClients.swift wraps:
 *
 *   Kit        GET /api/song/{id}/kit?kind=auto|drums|flip  SamplePack (+seq)
 *   Humanize   GET /api/song/{id}/groove               16 per-slot delays (step fractions)
 *   Re-Drum    GET /api/song/{id}/redrum-candidates    ranked kit donors
 *              GET /api/song/{id}/redrum?kit=…         replacement drums stem WAV
 *   Pack       GET /api/song/{id}/instrument-pack      .sfz zip download
 *
 * Presentation: a centered modal titled "✦ Remix" with a Done button,
 * opened from a small "✦ Remix" trigger rendered where the host mounts
 * #remix-root. Sections mirror the desktop sheet exactly:
 *   Pads    : Auto Kit / Drum Kit / Flip   (checkmark on the active kit)
 *   Feel    : Humanize                      (checkmark when on)
 *   Re-Drum : Original drums / Tightened / donor songs (spinner while applying)
 *   Export  : Instrument Pack (.sfz zip)
 * Dismiss: Done button, Escape, or backdrop click.
 *
 * This module owns the fetches + UI; everything that touches audio goes
 * through the host ctx (jam.js) so there is exactly one playback layer,
 * mirroring how the native sheets drive AppState/SessionController:
 *
 *   window.JamnRemix.mount(container, ctx)   renders the trigger, owns modal
 *   window.JamnRemix.open(ctx)               opens the modal (ctx optional)
 *   window.JamnRemix.close()                 dismisses the modal
 *   window.JamnRemix.unmount()
 *
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

  /* remix.css is not linked from jam.html (host files are off-limits to
   * this module), so inject it once. Idempotent across mounts/pages. */
  function ensureStylesheet() {
    if (document.getElementById('remix-css')) return;
    var link = document.createElement('link');
    link.id = 'remix-css';
    link.rel = 'stylesheet';
    link.href = '/static/remix.css?v=1';
    (document.head || document.documentElement).appendChild(link);
  }

  function setError(msg) {
    if (!state) return;
    state.errorMsg = msg || '';
    render();
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

  /** Which kit kind is currently loaded on the pads (auto|drums|flip|…),
   * so the matching Pads row shows a checkmark — the web analogue of the
   * native lastKitKind. */
  function activeKitKind() {
    try {
      var JK = window.JamnKit;
      if (JK && typeof JK.kind === 'function') return JK.kind();
    } catch (_) {}
    return null;
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

  /** Load a kit onto the pads (Pads section: auto|drums|flip). Kit mounts
   * are synchronous fire-and-forget through the host hook (window.JamnKit
   * .mount), so there is no async busy state — the checkmark comes from
   * activeKitKind() on the next render, mirroring the native sheets. */
  function doLoadKit(kind, msg) {
    if (!state || state.busy) return;
    setError(null);
    try {
      state.ctx.loadKit(kind);
      setApplied(kind, msg);
    } catch (e) {
      setApplied(null, '');
      setError('Kit load failed: ' + ((e && e.message) || e));
    }
  }

  var KIT_MSG = {
    auto:
      'Applied: Auto Kit — the song’s best loops are on the pads, '
      + 'color-coded. Tap the pads to play them.',
    drums:
      'Applied: Drum Kit — the song’s kick, snare and hats are on the pads '
      + 'as clean one-shots. Tap the pads to play them.',
    // The flip is SILENT until the user plays it: the song mix is
    // untouched, and the web sequencer never auto-starts (unlike iOS).
    // Say so, or the transform reads as a no-op.
    flip:
      'Applied: Flip — new kit loading onto the pads; the beat is armed '
      + 'in the Sequencer. Open Sequencer and press play to hear it.',
  };

  /** Humanize toggle; fetches the groove template on first use and
   * caches it for the song (RemixClient.fetchGroove + toggleHumanize). */
  // Humanize only bends SEQUENCER step timing (a few ms per step) — it
  // never touches the song mix. The message below spells that out; the
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

  /** Re-Drum candidates load once per song (the native sheet loads them
   * in .task when the sheet opens — mirrored in openModal()). */
  function loadCandidates() {
    if (!state || state.candidatesLoading || state.candidatesLoaded) return;
    var entryId = state.entryId;
    state.candidatesLoading = true;
    render();
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

  function loadBorrowCandidates() {
    if (!state) return;
    var entryId = state.entryId;
    var stem = state.borrowStem;
    fetch(api(entryId, 'borrow-candidates') + '?stem=' + encodeURIComponent(stem))
      .then(function (r) { return r.ok ? r.json() : { candidates: [] }; })
      .catch(function () { return { candidates: [] }; })
      .then(function (data) {
        if (!state || state.entryId !== entryId || state.borrowStem !== stem) return;
        state.borrowCandidates = ((data && data.candidates) || []).slice(0, 6);
        state.borrowLoaded = true;
        render();
      });
  }

  function applyBorrow(donor) {
    if (!state || state.borrowBusyDonor) return;
    setError(null);
    state.borrowBusyDonor = donor;
    render();
    var stem = state.borrowStem;
    Promise.resolve(state.ctx.loadBorrow(donor, stem))
      .then(function () {
        if (!state) return;
        state.borrowBusyDonor = null;
        setApplied('borrow',
          'Applied: Borrow — real ' + (stem === 'drums' ? 'beat' : stem)
          + ' loops on the pads, locked to this song’s tempo.' + hearItNote());
      })
      .catch(function (e) {
        if (!state) return;
        state.borrowBusyDonor = null;
        setError((e && e.message) || 'Borrow failed');
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
        // Pads follow ONLY when the user is already on the drum kit —
        // force-switching whatever kit/mode was loaded to drums on every
        // Re-Drum stomped the user's context (reported on all platforms).
        var onDrumKit = false;
        try {
          var JK = window.JamnKit;
          onDrumKit = !!(JK && typeof JK.kind === 'function' && JK.kind() === 'drums');
        } catch (_) {}
        if (onDrumKit) {
          try {
            if (kit.indexOf('song:') === 0) {
              state.ctx.loadDonorDrumKit(kit.slice(5));
            } else {
              state.ctx.loadKit('drums');
            }
          } catch (e) {
            try { console.warn('[remix] pads follow-up failed:', e); } catch (_) {}
          }
        }
        // The swap restarts audio only when the song is PLAYING;
        // paused, it's inaudible until the next Play — say which.
        var what = kit === 'self'
          ? 'drums re-triggered from this song’s own tightened kit'
          : 'drums swapped to “' + donorLabel(kit) + '”';
        setApplied('redrum',
          'Applied: Re-Drum — ' + what
          + (onDrumKit ? ', in the song mix and on the pads.'
                       : ', in the song mix.') + hearItNote());
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
  // DOM — trigger button + modal

  /** A single section row: icon, name + subtitle, and an aside that shows
   * a spinner while busy, else a checkmark when active. Matches the
   * native row() helper (RemixSheetView.row / RemixSheet's HStack). */
  function row(opts) {
    var b = el('button', 'remix-row');
    b.type = 'button';
    if (opts.title) b.title = opts.title;
    b.disabled = !!opts.disabled;

    b.appendChild(el('span', 'remix-row-icon', opts.icon || ''));

    var body = el('div', 'remix-row-body');
    body.appendChild(el('div', 'remix-row-name', opts.name));
    if (opts.subtitle) body.appendChild(el('div', 'remix-row-sub', opts.subtitle));
    b.appendChild(body);

    var aside = el('div', 'remix-row-aside');
    if (opts.busy) {
      if (opts.busyLabel) aside.appendChild(el('span', null, opts.busyLabel));
      aside.appendChild(el('span', 'remix-spinner'));
    } else if (opts.checked) {
      aside.appendChild(el('span', 'remix-check', '✓'));
    }
    b.appendChild(aside);

    if (opts.onClick && !opts.disabled) b.addEventListener('click', opts.onClick);
    return b;
  }

  function section(title) {
    var s = el('div', 'remix-section');
    if (title) s.appendChild(el('div', 'remix-section-header', title));
    return s;
  }

  /** Build (or rebuild) the modal body from current state. Cheap enough
   * to redraw whole on each render — the row list is short, and the
   * Escape/backdrop dismiss listeners live on the backdrop, not the
   * rebuilt rows, so nothing here breaks dismissal. */
  function renderModal() {
    if (!state || !state.modalOpen || !state.els) return;
    var body = state.els.body;
    body.innerHTML = '';

    var busy = state.busy; // a blocking fetch: humanize | redrum | pack
    var activeKit = activeKitKind();

    // ---- Pads
    var pads = section('Pads');
    pads.appendChild(row({
      name: 'Auto Kit', icon: '🪄',
      subtitle: 'The song’s best loops, color-coded',
      title: 'The song’s best loops on the pads, color-coded',
      checked: activeKit === 'auto',
      disabled: !!busy,
      onClick: function () { doLoadKit('auto', KIT_MSG.auto); },
    }));
    pads.appendChild(row({
      name: 'Drum Kit', icon: '🥁',
      subtitle: 'Its kick, snare and hats as clean one-shots',
      title: 'The song’s kick, snare and hats as clean one-shots',
      checked: activeKit === 'drums',
      disabled: !!busy,
      onClick: function () { doLoadKit('drums', KIT_MSG.drums); },
    }));
    pads.appendChild(row({
      name: 'Flip', icon: '🔀',
      subtitle: 'A new beat built from the song’s own DNA',
      title: 'A new beat built from the song’s own DNA — pads load here, the pattern lands in the Sequencer',
      checked: activeKit === 'flip',
      disabled: !!busy,
      onClick: function () { doLoadKit('flip', KIT_MSG.flip); },
    }));
    body.appendChild(pads);

    // ---- Feel
    var feel = section('Feel');
    feel.appendChild(row({
      name: 'Humanize', icon: '〰️',
      subtitle: 'Sequences swing with this song’s own timing',
      title: 'Sequences swing with this song’s own timing',
      busy: busy === 'humanize',
      checked: state.humanizeOn,
      disabled: !!busy && busy !== 'humanize',
      onClick: doHumanize,
    }));
    body.appendChild(feel);

    // ---- Re-Drum
    var redrum = section('Re-Drum — keep the groove, swap the kit');
    var redrumLocked = !!state.redrumBusyKit; // any donor rendering

    if (state.redrumActiveKit) {
      redrum.appendChild(row({
        name: 'Original drums', icon: '↩︎',
        subtitle: 'Back to the song’s real drums',
        busy: state.redrumBusyKit === 'original',
        busyLabel: state.redrumBusyKit === 'original' ? 'Restoring…' : null,
        disabled: redrumLocked && state.redrumBusyKit !== 'original',
        onClick: clearRedrum,
      }));
    }

    redrum.appendChild(row({
      name: 'Tightened (own kit)', icon: '🔁',
      subtitle: 'Re-trigger this song’s own cleaned kit',
      busy: state.redrumBusyKit === 'self',
      busyLabel: state.redrumBusyKit === 'self' ? 'Rendering…' : null,
      checked: state.redrumActiveKit === 'self' && state.redrumBusyKit !== 'self',
      disabled: redrumLocked && state.redrumBusyKit !== 'self',
      onClick: function () { applyRedrum('self'); },
    }));

    if (state.candidatesLoading || (!state.candidatesLoaded && !state.candidates.length)) {
      var loading = el('div', 'remix-note');
      loading.appendChild(el('span', 'remix-spinner'));
      loading.appendChild(el('span', null, 'Finding kit donors…'));
      redrum.appendChild(loading);
    } else if (state.candidatesLoaded && !state.candidates.length) {
      redrum.appendChild(el('div', 'remix-note',
        'Analyze more songs to unlock cross-song kits.'));
    }

    state.candidates.forEach(function (c) {
      var kit = 'song:' + c.entryId;
      redrum.appendChild(row({
        name: c.name || c.entryId, icon: '🔁',
        subtitle: 'This song’s groove on that song’s drums',
        title: 'This song’s groove on that song’s drums — first use renders on the server, give it a few seconds',
        busy: state.redrumBusyKit === kit,
        busyLabel: state.redrumBusyKit === kit ? 'Rendering…' : null,
        checked: state.redrumActiveKit === kit && state.redrumBusyKit !== kit,
        disabled: redrumLocked && state.redrumBusyKit !== kit,
        onClick: function () { applyRedrum(kit); },
      }));
    });
    body.appendChild(redrum);

    // ---- Borrow (real loops from other songs)
    var borrow = section('Borrow — real loops from your other songs');
    var stems = [['drums', 'Beat'], ['bass', 'Bass'], ['other', 'Chords']];
    var picker = el('div', 'remix-stem-picker');
    stems.forEach(function (st) {
      var b = el('button', 'remix-stem-btn'
        + (state.borrowStem === st[0] ? ' is-on' : ''), st[1]);
      b.type = 'button';
      b.onclick = function () {
        if (state.borrowStem === st[0]) return;
        state.borrowStem = st[0];
        state.borrowLoaded = false; state.borrowCandidates = [];
        loadBorrowCandidates(); render();
      };
      picker.appendChild(b);
    });
    borrow.appendChild(picker);

    if (!state.borrowLoaded) {
      var bl = el('div', 'remix-note');
      bl.appendChild(el('span', 'remix-spinner'));
      bl.appendChild(el('span', null, 'Finding compatible loops…'));
      borrow.appendChild(bl);
    } else if (!state.borrowCandidates.length) {
      borrow.appendChild(el('div', 'remix-note',
        state.borrowStem === 'drums'
          ? 'Analyze more songs to borrow beats.'
          : 'No key-compatible songs yet.'));
    }
    state.borrowCandidates.forEach(function (c) {
      var sub = state.borrowStem === 'drums'
        ? Math.round(c.tempo) + ' bpm'
        : (c.key || '?') + ' · ' + Math.round(c.tempo) + ' bpm';
      borrow.appendChild(row({
        name: c.name || c.entryId, icon: '🎚️',
        subtitle: sub + (state.borrowStem !== 'drums' && c.harmonic >= 0.9
          ? ' · key match' : ''),
        title: 'Real loops from this song, stretched to your tempo, on the pads',
        busy: state.borrowBusyDonor === c.entryId,
        busyLabel: state.borrowBusyDonor === c.entryId ? 'Rendering…' : null,
        disabled: !!state.borrowBusyDonor && state.borrowBusyDonor !== c.entryId,
        onClick: function () { applyBorrow(c.entryId); },
      }));
    });
    body.appendChild(borrow);

    // ---- Export
    var exp = section('Export');
    exp.appendChild(row({
      name: 'Instrument Pack (.sfz)', icon: '🎹',
      subtitle: 'Drums on keys, bass + stab chromatic — downloads via browser',
      title: 'The song as a playable sampler patch (.sfz zip)',
      busy: busy === 'pack',
      busyLabel: busy === 'pack' ? 'Rendering…' : null,
      disabled: !!busy && busy !== 'pack',
      onClick: doInstrumentPack,
    }));
    body.appendChild(exp);

    // ---- Pinned "Applied: …" + error, below the scroll region.
    state.els.applied.textContent = state.appliedMsg || '';
    state.els.applied.hidden = !state.appliedMsg;
    state.els.error.textContent = state.errorMsg || '';
    state.els.error.hidden = !state.errorMsg;
  }

  function render() {
    if (!state) return;
    if (state.els && state.els.trigger) {
      state.els.trigger.classList.toggle('is-open', !!state.modalOpen);
    }
    renderModal();
  }

  function buildTrigger() {
    var root = state.container;
    root.innerHTML = '';
    var b = el('button', 'remix-trigger');
    b.type = 'button';
    b.title = 'Remix — one-tap transforms for this song';
    b.setAttribute('aria-haspopup', 'dialog');
    b.appendChild(el('span', 'remix-trigger-mark', '✦'));
    b.appendChild(el('span', null, 'Remix'));
    b.addEventListener('click', openModal);
    root.appendChild(b);
    state.els.trigger = b;
  }

  function openModal() {
    if (!state || state.modalOpen) return;
    state.modalOpen = true;

    var backdrop = el('div', 'remix-backdrop');
    var modal = el('div', 'remix-modal');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');
    modal.setAttribute('aria-label', 'Remix');
    modal.tabIndex = -1;

    var header = el('div', 'remix-header');
    var title = el('div', 'remix-modal-title');
    title.appendChild(el('span', 'remix-mark', '✦'));
    title.appendChild(el('span', null, 'Remix'));
    header.appendChild(title);
    var done = el('button', 'remix-done', 'Done');
    done.type = 'button';
    done.addEventListener('click', closeModal);
    header.appendChild(done);
    modal.appendChild(header);

    var body = el('div', 'remix-body');
    modal.appendChild(body);

    // "Applied: …" line — the transforms land on surfaces that may be
    // silent right now (paused mix, idle sequencer), so success must be
    // stated, not inferred from the audio. role=status → screen readers.
    var applied = el('div', 'remix-applied');
    applied.setAttribute('role', 'status');
    applied.hidden = true;
    modal.appendChild(applied);

    var error = el('div', 'remix-modal-error');
    error.hidden = true;
    modal.appendChild(error);

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    state.els.backdrop = backdrop;
    state.els.modal = modal;
    state.els.body = body;
    state.els.applied = applied;
    state.els.error = error;

    // Backdrop click (outside the card) dismisses, like the native sheet.
    backdrop.addEventListener('click', function (ev) {
      if (ev.target === backdrop) closeModal();
    });
    // Escape dismisses; capture so it wins over page-level handlers.
    state.onKeyDown = function (ev) {
      if (ev.key === 'Escape') { ev.preventDefault(); closeModal(); }
    };
    document.addEventListener('keydown', state.onKeyDown, true);

    // Candidates load when the sheet opens (native .task parity).
    loadCandidates();
    loadBorrowCandidates();

    render();
    try { modal.focus(); } catch (_) {}
  }

  function closeModal() {
    if (!state || !state.modalOpen) return;
    state.modalOpen = false;
    if (state.onKeyDown) {
      document.removeEventListener('keydown', state.onKeyDown, true);
      state.onKeyDown = null;
    }
    if (state.els.backdrop && state.els.backdrop.parentNode) {
      state.els.backdrop.parentNode.removeChild(state.els.backdrop);
    }
    state.els.backdrop = null;
    state.els.modal = null;
    state.els.body = null;
    state.els.applied = null;
    state.els.error = null;
    render(); // clears trigger .is-open
  }

  // ------------------------------------------------------------------
  // Public surface

  function initState(container, ctx) {
    return {
      container: container,
      ctx: ctx,
      entryId: ctx.entry.id,
      busy: null,
      humanizeOn: false,
      grooveTemplate: null,
      redrumActiveKit: null,
      redrumBusyKit: null,
      candidates: [],
      candidatesLoaded: false,
      candidatesLoading: false,
      borrowStem: 'drums',
      borrowCandidates: [],
      borrowLoaded: false,
      borrowBusyDonor: null,
      appliedKey: null,
      appliedMsg: '',
      errorMsg: '',
      modalOpen: false,
      onKeyDown: null,
      els: {},
    };
  }

  window.JamnRemix = {
    // Renders the "✦ Remix" trigger into the host container and owns the
    // modal — the host mount site (#remix-root) needs no change.
    mount: function (container, ctx) {
      if (!container || !ctx || !ctx.entry || !ctx.entry.id) return;
      window.JamnRemix.unmount();
      ensureStylesheet();
      state = initState(container, ctx);
      buildTrigger();
      render();
    },
    // Open the modal. Optional ctx lets a host open with a fresh context
    // (e.g. a newly loaded song) without a full remount.
    open: function (ctx) {
      if (ctx && ctx.entry && ctx.entry.id && state && ctx.entry.id !== state.entryId) {
        var container = state.container;
        closeModal();
        state = initState(container, ctx);
        buildTrigger();
      }
      if (!state) return;
      openModal();
    },
    close: function () { closeModal(); },
    unmount: function () {
      if (!state) return;
      closeModal();
      try { state.container.innerHTML = ''; } catch (_) {}
      state = null;
    },
  };
})();
