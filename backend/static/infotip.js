// infotip.js — Ableton-style hover descriptions for every control.
//
// One delegated listener + a central registry (generated from a full sweep
// of the surface's control-creation code) instead of per-control wiring:
// controls are matched at hover time by selector, disambiguated by their
// visible text when siblings share a class (e.g. the Tap|Loop|Latch
// segment). Elements the registry doesn't know fall back to their title=
// attribute, restyled through the same tip so the browser's delayed native
// tooltip never double-renders. Touch pointers are ignored — hover is a
// pointer-input concept; touch surfaces carry their own affordances.
(function () {
  "use strict";
  var REGISTRY = [{"m":".jc-tab","t":"Opens the Voice recorder — record a short vocal phrase; takes cap at 8 s and never leave this device.","l":"Voice"},{"m":".jc-tab","t":"Opens the Beat recorder — tap or clap a rhythm over the click and see the detected hits.","l":"Beat"},{"m":".jc-tab","t":"Opens the Sample recorder — record or import a sound, trim it, and send it to a pad.","l":"Sample"},{"m":".jc-btn--primary","t":"Starts recording from the mic; auto-stops when the take reaches its time cap.","l":"Record"},{"m":".jc-btn--danger","t":"Stops the recording early and processes the take for review.","l":"Stop"},{"m":".jc-btn--ghost","t":"Plays the take; press again to stop playback.","l":"Play"},{"m":".jc-btn--ghost","t":"Stops playback of the take.","l":"Stop"},{"m":".jc-btn--primary","t":"Saves the take on this device — takes never leave the browser.","l":"Keep"},{"m":".jc-btn--ghost","t":"Throws the take away without saving it.","l":"Discard"},{"m":".jc-name-row .jc-input","t":"Names the take before you save or download it."},{"m":".jc-btn--ghost","t":"Downloads this saved take as a 16-bit mono WAV file.","l":"WAV"},{"m":".jc-btn--danger","t":"Deletes this saved take from this device.","l":"Delete"},{"m":".jc-input--bpm","t":"Sets the metronome tempo, 60–200 BPM; follows the loaded song's tempo when it has one."},{"m":"#jc-click-toggle","t":"Plays a metronome click while you record; turn off to capture without the click."},{"m":"label[for=\"jc-click-toggle\"]","t":"Plays a metronome click while you record; turn off to capture without the click.","l":"Click"},{"m":".jc-btn--ghost","t":"Clears the failed take so you can record another.","l":"Try Again"},{"m":".jc-btn--ghost","t":"Opens a file picker; imported audio is clipped to the first 8 seconds.","l":"Import audio file"},{"m":".jc-wave--trim","t":"Drag to move the nearer trim handle and set where the sample starts and ends."},{"m":".jc-btn--primary","t":"Sends the trimmed sample to the open song's Jam Pads grid.","l":"Send to pad"},{"m":".jc-btn--ghost","t":"Downloads the trimmed slice as a WAV file.","l":"Download WAV"},{"m":".jc-btn--ghost","t":"Saves the trimmed sample on this device.","l":"Keep"},{"m":".seq-play","t":"Starts or stops the pattern; while loops are running, the start waits for the next loop boundary."},{"m":".seq-slots .seq-seg-btn","t":"Switches between the four saved pattern slots (A–D) for this song."},{"m":".seq-seg:not(.seq-slots) .seq-seg-btn","t":"Sets a 16-step pattern — one bar of 16th notes at the song tempo.","l":"16"},{"m":".seq-seg:not(.seq-slots) .seq-seg-btn","t":"Sets a 32-step pattern — two bars; existing steps are kept.","l":"32"},{"m":".seq-swing input","t":"Swings the pattern by delaying every other 16th step; takes effect immediately during playback."},{"m":".seq-toggle","t":"Shows only rows that have active steps; tap again to show all pads.","l":"Used"},{"m":".seq-row-label","t":"Plays this pad once as a preview; its steps run on the row to the right."},{"m":".seq-cell","t":"Toggles the pad on this step; edits land on the very next pass during playback."},{"m":".remix-trigger","t":"Opens the Remix sheet: kits, feel, Re-Drum, Borrow and export transforms for this song."},{"m":".remix-done","t":"Closes the Remix sheet."},{"m":".remix-row","t":"Loads the song’s best loops onto the pads, color-coded by stem.","l":"Auto Kit"},{"m":".remix-row","t":"Loads the song’s kick, snare and hats onto the pads as clean one-shots.","l":"Drum Kit"},{"m":".remix-row","t":"Builds a new beat from the song’s own material: pads load here, the pattern is armed in the Sequencer.","l":"Flip"},{"m":".remix-row","t":"Applies this song’s own micro-timing to sequencer steps; tap again to return to the grid.","l":"Humanize"},{"m":".remix-row","t":"Restores the song’s real drums in the mix, undoing Re-Drum.","l":"Original drums"},{"m":".remix-row","t":"Re-triggers the song’s groove from its own cleaned-up kit in the mix.","l":"Tightened (own kit)"},{"m":".remix-section:nth-of-type(3) .remix-row","t":"Plays this song’s groove on that song’s drums; the first use renders on the server for a few seconds."},{"m":".remix-stem-btn","t":"Lists other songs whose beat you can borrow, tempo-matched to this song.","l":"Beat"},{"m":".remix-stem-btn","t":"Lists harmonically compatible songs whose bassline you can borrow.","l":"Bass"},{"m":".remix-stem-btn","t":"Lists harmonically compatible songs whose chords you can borrow.","l":"Chords"},{"m":".remix-section:nth-of-type(4) .remix-row","t":"Loads both songs’ sections onto the pads — this song on top, that one below — locked to this tempo."},{"m":".remix-row","t":"Renders the song as a playable .sfz sampler patch and downloads the zip.","l":"Instrument Pack (.sfz)"},{"m":".chopedit-wave","t":"Drag to move the nearest region edge; on release the length snaps to whole bars at the song tempo."},{"m":".chopedit-handle--start","t":"Sets where the chop starts; drag it, and the region length bar-snaps on release."},{"m":".chopedit-handle--end","t":"Sets where the chop ends; drag it, and the region length bar-snaps on release."},{"m":".chopedit-preserve","t":"Keeps the pad’s loop length — trimmed audio plays at its original spot, silence fills the rest."},{"m":".chopedit-preserve input","t":"Keeps the pad’s loop length — trimmed audio plays at its original spot, silence fills the rest."},{"m":".chopedit-btn","t":"Previews the selected region as a seamless loop.","l":"Play"},{"m":".chopedit-btn","t":"Stops the region preview.","l":"Stop"},{"m":".chopedit-btn","t":"Returns both edges to the pad’s original boundaries.","l":"Reset"},{"m":".chopedit-btn","t":"Closes the editor without changing the pad.","l":"Cancel"},{"m":".chopedit-btn--accent","t":"Applies the new boundaries to the pad and closes the editor; Enter also saves."},{"m":".kit-seg-btn","t":"Switches the kit to the pad grid view.","l":"Grid"},{"m":".kit-seg-btn","t":"Switches to the layer rack — one row per category with its active loop and swap chips.","l":"Layers"},{"m":".kit-seg-btn","t":"Sets the surface to 16 pads, the native 4×4 layout.","l":"16"},{"m":".kit-seg-btn","t":"Sets the surface to a compact 8×8 grid of 64 pads; unfilled slots stay empty.","l":"64"},{"m":".kit-seg-btn","t":"Fires pads immediately on press, ignoring the quantize grid.","l":"Off"},{"m":".kit-seg-btn","t":"Quantizes pad launches to the next beat.","l":"Beat"},{"m":".kit-seg-btn","t":"Quantizes pad launches to the next bar.","l":"Bar"},{"m":".kit-seg-btn","t":"Plays pads as one-shots — a press fires the whole slice once.","l":"Tap"},{"m":".kit-seg-btn","t":"Loops a pad while you hold it; releasing stops it.","l":"Loop"},{"m":".kit-seg-btn","t":"Latches loops so they keep playing after you release the pad; tap again to stop.","l":"Latch"},{"m":".kit-groove","t":"Starts the single best loop of each category at once, locked to the quantize grid."},{"m":".kit-chop .kit-select:nth-of-type(1) .kit-select-el","t":"Picks which stem (mix, drums, bass…) the Load button slices into chops.","l":"Stem"},{"m":".kit-chop .kit-select:nth-of-type(2) .kit-select-el","t":"Picks the slice grid for chops — beat, phrase, onset, chord, section, or drum-bundle.","l":"Slices"},{"m":".kit-chop-load","t":"Slices the chosen stem and bakes the chops onto the pads, replacing the current kit.","l":"Load"},{"m":".kit-borrow-btn","t":"Opens a picker to borrow real loops from your other analyzed songs, tempo- and key-matched."},{"m":".kit-session-toggle","t":"Enables a session key/tempo target — added parts conform to it; your song still plays true."},{"m":".kit-session-root","t":"Sets the root note of the session key that borrowed parts conform to."},{"m":".kit-session-qual","t":"Sets the session key quality — major or minor."},{"m":".kit-session-bpm","t":"Sets the session tempo (40–240 BPM) that borrowed parts conform to."},{"m":".kit-play","t":"Plays or pauses the song from the current position."},{"m":".kit-stop","t":"Stops every pad voice; song playback keeps running.","l":"Stop All"},{"m":".kit-kill","t":"Stops every pad voice and stops the song.","l":"Kill All"},{"m":".kit-arr-rec","t":"Records which pads you play in each section, live while the song plays."},{"m":".kit-arr-play","t":"Replays the captured pads hands-free — pads come in and out per section."},{"m":".kit-arr-clear","t":"Forgets the captured arrangement for this song.","l":"Clear"},{"m":".kit-pad","t":"Plays this slice; hold loops it in Loop mode. Long-press or right-click opens the pad menu."},{"m":".kit-layer-play","t":"Starts the category's best loop, or stops the layer if one is playing."},{"m":".kit-layer-chip","t":"Swaps the category's playing layer to this loop, quantized to the grid."},{"m":".kit-wedge","t":"Forces this pad to loop; it latches on until stopped.","l":"Loop"},{"m":".kit-wedge","t":"Forces this pad to play as a one-shot instead of looping.","l":"One-shot"},{"m":".kit-wedge","t":"Opens the pad's effects editor — delay, filter, and gain.","l":"Effects"},{"m":".kit-wedge","t":"Opens the chop editor to re-slice this pad's region from the stem.","l":"Chop"},{"m":".kit-wedge","t":"Opens the Sequencer view with this pad's track highlighted.","l":"Sequence"},{"m":".kit-wedge","t":"Adds this pad to the sequencer as its own track.","l":"To Sequence"},{"m":".kit-wedge","t":"Opens the sound picker to swap this pad's sample from a pack or another pad.","l":"Add sound"},{"m":".kit-wedge","t":"Stops this pad's voice now.","l":"Stop pad"},{"m":".kit-wedge","t":"Stops every other sounding pad so only this one keeps playing.","l":"Solo"},{"m":".kit-wedge","t":"Removes this pad from the kit; Undo stays available for 5 seconds.","l":"Delete"},{"m":".kit-wedge","t":"Undoes this pad's overrides — effects, loop force, chop edits, gates, and swapped sound.","l":"Reset"},{"m":".kit-radial-hub","t":"Closes the pad menu without changing anything."},{"m":".kit-fx-close","t":"Closes the effects editor."},{"m":".kit-fx-row","t":"Sets the delay time in seconds; new values apply on the pad's next trigger.","l":"Delay time"},{"m":".kit-fx-row","t":"Sets how much delay output feeds back, making echoes repeat longer.","l":"Feedback"},{"m":".kit-fx-row","t":"Blends the delayed signal into the pad's output.","l":"Delay mix"},{"m":".kit-fx-row","t":"Sets the resonant low-pass filter cutoff, from 100 Hz to fully open.","l":"Cutoff"},{"m":".kit-fx-row","t":"Boosts the filter around the cutoff, up to 24 dB.","l":"Resonance"},{"m":".kit-fx-row","t":"Sets the pad's level, up to 200%.","l":"Gain"},{"m":".kit-fx-reset","t":"Clears this pad's effects back to neutral.","l":"Neutral"},{"m":".kit-pick-back","t":"Returns to the pack list."},{"m":".kit-pick-close","t":"Closes the sound picker."},{"m":".kit-pick-tab","t":"Lists the server's curated sample packs to take a sound from.","l":"Curated packs"},{"m":".kit-pick-tab","t":"Lists this kit's other pads so you can copy a sound across.","l":"This song"},{"m":".kit-pick-row","t":"Previews the sound on hover; click assigns it to the pad (or opens the pack)."},{"m":".kit-pick-browse","t":"Opens the full Packs view."},{"m":".kit-borrow-close","t":"Closes the borrow picker."},{"m":".kit-borrow-part","t":"Lists donor songs whose drum loops you can borrow as the beat.","l":"Beat"},{"m":".kit-borrow-part","t":"Lists key-compatible songs whose bass loops you can borrow.","l":"Bass"},{"m":".kit-borrow-part","t":"Lists key-compatible songs whose chord loops you can borrow.","l":"Chords"},{"m":".kit-borrow-part","t":"Lists key-compatible songs whose vocal melody loops you can borrow.","l":"Melody"},{"m":".kit-borrow-cand","t":"Loads this song's loops onto the pads, matched to your tempo and key (or the session target)."},{"m":".kit-toast-act","t":"Restores the just-deleted pad.","l":"Undo"},{"m":".jamn-pack-card","t":"Loads this kit onto the Jam pads — the song's performance kit or the curated pack."},{"m":".jamn-pack-chip","t":"Loads this song's one-shot drum kit on the pads instead of the full performance kit.","l":"Drums"},{"m":"#jamn-side-toggle","t":"Opens and closes the sidebar menu."},{"m":".jamn-pill","t":"Opens the upload screen to start a new song.","l":"Intake"},{"m":".jamn-pill","t":"Opens the analysis queue — every song currently being processed.","l":"Band Room"},{"m":".jamn-pill","t":"Opens section-by-section practice for the loaded song.","l":"Rehearsal"},{"m":".jamn-pill","t":"Opens the Jam Pads play surface for the loaded song.","l":"Perform"},{"m":".jamn-tool[data-tool=\"launchpad\"]","t":"Opens the Jam Pads grid — the merged Launchpad surface."},{"m":".jamn-tool[data-tool=\"sequencer\"]","t":"Opens the step sequencer."},{"m":".jamn-tool[data-tool=\"play\"]","t":"Plays or pauses the loaded song."},{"m":".jamn-tool[data-tool=\"stop\"]","t":"Stops everything — the song, the sequencer, and all sounding pads."},{"m":".jamn-tool[data-tool=\"melody\"]","t":"Opens the pads in melody-guide mode; dimmed when the song has no melody line."},{"m":".jamn-tool[data-tool=\"beat\"]","t":"Opens Beat Capture — tap a rhythm into a pattern."},{"m":".jamn-tool[data-tool=\"remix\"]","t":"Opens Remix — one-tap transforms like Flip, Humanize and Re-Drum."},{"m":".jamn-tool[data-tool=\"record\"]","t":"Opens your recordings."},{"m":".jamn-tool[data-tool=\"synth\"]","t":"Opens Jam Pads — the wavetable synth pad surface."},{"m":".jamn-tool[data-tool=\"packs\"]","t":"Opens sample packs."},{"m":"#jamn-tool-session","t":"Opens the Connect / Session panel showing pairing status."},{"m":"#session-pop-close","t":"Closes the Connect / Session panel."},{"m":"#header-connect-pill","t":"Pairs with the Connect desktop helper; once paired, opens the monitor volume control."},{"m":"#connect-gain","t":"Sets how loud Connect monitors your instrument — keep at 0 unless you're on headphones."},{"m":"#connect-btn","t":"Launches and pairs the Connect desktop helper; re-sends the matched tone when already paired."},{"m":".connect-restart-btn","t":"Retries the Connect link, preferring the direct local connection over the cloud relay.","l":"Reconnect"},{"m":".connect-restart-btn","t":"Asks the helper supervisor to relaunch Connect.","l":"Try restarting Connect"},{"m":".connect-launch-btn","t":"Launches the installed Connect app so it can pair with this page."},{"m":".connect-install-link","t":"Downloads Connect for low-latency monitoring (macOS)."},{"m":".connect-launcher-link","t":"Opens the Connect helper via its pairing link — use this if auto-launch didn't fire."},{"m":"#jamn-side-brand","t":"Returns to the welcome / upload screen."},{"m":".jamn-side-item","t":"Opens the Contribute popup to record a voice sample.","l":"Voice"},{"m":".jamn-side-item","t":"Opens the Contribute popup on Beat — tap a rhythm in.","l":"Beat"},{"m":".jamn-side-item","t":"Opens the Contribute popup to capture a sample.","l":"Sample"},{"m":".jamn-side-item","t":"Opens the Jam Pads grid.","l":"Launchpad"},{"m":".jamn-side-item","t":"Opens the guitar fretboard stage.","l":"Guitar"},{"m":".jamn-side-item","t":"Opens the step sequencer.","l":"Sequencer"},{"m":".jamn-side-item","t":"Opens your recordings.","l":"Recordings"},{"m":".jamn-side-item","t":"Opens sample packs.","l":"Packs"},{"m":".jamn-side-item","t":"Opens the stems mixer.","l":"Mixer"},{"m":"#jamn-side-lib-add","t":"Opens the upload screen to add a song."},{"m":"#jamn-lib-add","t":"Opens the upload screen to add a song."},{"m":".jamn-lib-row","t":"Loads this song and opens it on the pads."},{"m":"#account-modal-cancel","t":"Closes the sign-in dialog."},{"m":"#account-email-form button[type=\"submit\"]","t":"Emails you a passwordless sign-in link — it expires in 15 minutes.","l":"Email me a link"},{"m":"#account-modal-sent button","t":"Closes the dialog — finish signing in from the link in your email.","l":"Done"},{"m":"#cc-tracks-open","t":"Opens a list of Creative-Commons demo tracks you can jam with right away."},{"m":".cc-track-row","t":"Imports this demo track and queues it for analysis."},{"m":"#cc-tracks-close","t":"Closes the demo-track picker."},{"m":".onboarding-option","t":"Selects the gear you play through so your monitoring path can be tuned for it."},{"m":"#onboarding-input-change","t":"Shows the input picker to choose a different audio interface."},{"m":"#onboarding-input-select","t":"Picks which audio input Jamn should use."},{"m":"#onboarding-submit","t":"Saves your device choice; change it later in settings."},{"m":".dropzone","t":"Opens the file picker to choose an audio file — MP3, WAV, M4A and more."},{"m":"#upload-attest","t":"Confirms you own or control the rights to this audio — required before upload."},{"m":"#upload-submit","t":"Uploads the song and queues it for analysis in the Band Room."},{"m":"#engine-start-btn","t":"Starts the local analysis engine — usually takes 5–15 seconds."},{"m":"#engine-stop-btn","t":"Shuts down the local analysis engine."},{"m":"#engine-download-link","t":"Downloads the Mac companion app that runs deep analysis."},{"m":"#bandroom-clear","t":"Removes finished and failed cards from the queue."},{"m":"#bandroom-back-intake","t":"Returns to the upload screen."},{"m":".br-card-dismiss","t":"Hides this card from the queue."},{"m":".br-start","t":"Loads the finished analysis and opens it on the pads."},{"m":"#rehearsal-back-to-bandroom","t":"Leaves rehearsal for the Band Room; shows your session summary first if you practiced."},{"m":"#rehearsal-skip-to-jam","t":"Skips practice and goes straight to the pads."},{"m":".rehearsal-section-row","t":"Selects this section to practice; long-press a locked row to unlock it early."},{"m":".rehearsal-variation-toggle","t":"Shows or hides this pattern's individual returns so you can practice one pass."},{"m":".rehearsal-group-heading--extras","t":"Expands or collapses the Extras sections."},{"m":".best-rep-button","t":"Plays the last perfect run you saved for this section."},{"m":".best-rep-download-button","t":"Downloads this saved best rep as a WAV file."},{"m":"#rehearsal-play","t":"Plays or pauses the practice section."},{"m":"#rehearsal-loop","t":"Loops the current section so it repeats while you practice."},{"m":".rehearsal-speed-btn","t":"Slows practice playback to half speed; external MIDI clock follows.","l":"0.5×"},{"m":".rehearsal-speed-btn","t":"Slows practice playback to three-quarter speed; external MIDI clock follows.","l":"0.75×"},{"m":".rehearsal-speed-btn","t":"Returns practice playback to full speed.","l":"1×"},{"m":"#rehearsal-next","t":"Jumps to the next part to learn."},{"m":"#session-summary-keep","t":"Dismisses the summary and keeps practicing."},{"m":"#session-summary-jam","t":"Ends practice and continues to the jam."},{"m":"#session-summary-best-rep","t":"Plays the saved recording of your best run this session."},{"m":"#session-summary-skill-map","t":"Opens your cross-song skill map."},{"m":"#skill-map-close","t":"Closes the skill map."},{"m":"#warmup-close","t":"Closes the warm-up."},{"m":".bestrep-optin-cta button","t":"Saves your best reps automatically from now on.","l":"Yes"},{"m":".bestrep-optin-cta button","t":"Skips saving this time — you'll be asked again on the next perfect run.","l":"Not now"},{"m":".bestrep-optin-cta button","t":"Turns off best-rep saving.","l":"Never"},{"m":"#t-play","t":"Plays or pauses the song from the current position."},{"m":"#t-stop","t":"Stops playback and returns to the start."},{"m":"#t-loop","t":"Clears the active section loop."},{"m":"#t-click","t":"Toggles a click track locked to the song's detected beats."},{"m":"#waveform-canvas","t":"Seeks — click anywhere on the waveform to jump playback there."},{"m":".mixer-tab","t":"Shows the per-stem level faders.","l":"Levels"},{"m":".mixer-tab","t":"Shows the master FX panel — EQ, compressor, reverb and delay on the song bus.","l":"FX"},{"m":"#slot-right > summary","t":"Collapses or expands the stem rack."},{"m":".stem-row .gain","t":"Sets this channel's playback level."},{"m":".stem-row .mute-btn","t":"Silences this channel; tap again to unmute."},{"m":".stem-row .solo-btn","t":"Solos this channel — every channel not soloed is muted."},{"m":".fx-preset-chip","t":"Applies this master FX preset; matches the same preset in the desktop app."},{"m":".fx-slider","t":"Adjusts this FX parameter; editing a knob switches the preset to Custom."},{"m":".fx-reset-btn","t":"Resets all master FX to neutral."},{"m":".plugin-dl-btn","t":"Downloads the jamn Kit AU / VST3 plugin for macOS.","l":"macOS"},{"m":".plugin-dl-btn","t":"Downloads the jamn Kit VST3 plugin for Windows.","l":"Windows"},{"m":"#contribute-modal-close","t":"Closes the Contribute popup and releases the microphone."}];

  var DELAY_MS = 350;
  var tipEl = null;
  var timer = 0;
  var current = null;

  function ensureTip() {
    if (tipEl) return tipEl;
    var style = document.createElement("style");
    style.textContent =
      "#jamn-infotip{position:fixed;z-index:9999;max-width:280px;" +
      "background:#111318;color:#d7dae0;border:1px solid #2a2e37;" +
      "border-radius:8px;padding:7px 10px;font:12px/1.45 -apple-system," +
      "BlinkMacSystemFont,'Segoe UI',sans-serif;pointer-events:none;" +
      "box-shadow:0 6px 24px rgba(0,0,0,.45);opacity:0;transition:opacity .12s;}" +
      "#jamn-infotip.on{opacity:1;}";
    document.head.appendChild(style);
    tipEl = document.createElement("div");
    tipEl.id = "jamn-infotip";
    tipEl.setAttribute("role", "tooltip");
    document.body.appendChild(tipEl);
    return tipEl;
  }

  function textOf(el) {
    return (el.textContent || "").trim();
  }

  function lookup(start) {
    // Walk up from the hovered node; first registry hit wins. Label
    // entries require the element's visible text to match exactly.
    for (var el = start; el && el !== document.body; el = el.parentElement) {
      var fallback = null;
      for (var i = 0; i < REGISTRY.length; i++) {
        var r = REGISTRY[i];
        var hit = false;
        try { hit = el.matches && el.matches(r.m); } catch (_) {}
        if (!hit) continue;
        if (r.l != null) {
          if (textOf(el) === r.l) return { el: el, tip: r.t };
        } else if (!fallback) {
          fallback = { el: el, tip: r.t };
        }
      }
      if (fallback) return fallback;
      // Registry miss: reuse the element's own title as the tip (and stash
      // it so the native tooltip doesn't double-render).
      if (el.hasAttribute && el.hasAttribute("title")) {
        var t = el.getAttribute("title");
        if (t) {
          el.setAttribute("data-jamn-title", t);
          el.removeAttribute("title");
        }
        var stored = el.getAttribute("data-jamn-title");
        if (stored) return { el: el, tip: stored };
      }
    }
    return null;
  }

  function place(target) {
    var r = target.getBoundingClientRect();
    var el = ensureTip();
    var pad = 8;
    var x = Math.min(Math.max(r.left, pad), window.innerWidth - el.offsetWidth - pad);
    var y = r.bottom + 8;
    if (y + el.offsetHeight + pad > window.innerHeight) {
      y = r.top - el.offsetHeight - 8;
    }
    el.style.left = x + "px";
    el.style.top = Math.max(pad, y) + "px";
  }

  function show(hit) {
    var el = ensureTip();
    el.textContent = hit.tip;
    el.classList.add("on");
    current = hit.el;
    place(hit.el);
  }

  function hide() {
    if (timer) { clearTimeout(timer); timer = 0; }
    current = null;
    if (tipEl) tipEl.classList.remove("on");
  }

  document.addEventListener("pointerover", function (ev) {
    if (ev.pointerType === "touch") return;
    if (timer) clearTimeout(timer);
    var t = ev.target;
    timer = setTimeout(function () {
      timer = 0;
      if (!document.contains(t)) return;
      var hit = lookup(t);
      if (hit) show(hit); else hide();
    }, DELAY_MS);
  }, true);
  document.addEventListener("pointerout", function (ev) {
    if (current && ev.target && ev.target.contains && ev.target.contains(current)) hide();
    else if (!current) hide();
  }, true);
  // Any interaction or scroll dismisses — the tip must never sit over a
  // control the user is actively working.
  document.addEventListener("pointerdown", hide, true);
  document.addEventListener("scroll", hide, true);
  // Keyboard accessibility: focus shows the same tip.
  document.addEventListener("focusin", function (ev) {
    var hit = lookup(ev.target);
    if (hit) show(hit);
  });
  document.addEventListener("focusout", hide);
})();
