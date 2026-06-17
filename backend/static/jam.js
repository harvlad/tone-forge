/*
 * jam.js
 *
 * ToneForge Jam — single-page state machine.
 *
 * State model:
 *   intake     : user has not pasted a URL yet
 *   bandroom   : analysis in flight; stems land progressively
 *   perform    : analysis complete; user is in the session
 *
 * Audio playback strategy (browser-only, pre-Connect):
 *   - Each stem is loaded as an HTMLAudioElement so we can stream it.
 *   - All elements share a single play/pause clock, kept in sync by
 *     issuing seeks rather than fighting browser-imposed drift.
 *   - A simple per-stem GainNode (via Web Audio API) handles mute/solo.
 *   - This is intentionally crude. Sample-accurate sync is Connect's job.
 *
 * Out of scope here:
 *   - Mic input / tone matching (Connect)
 *   - Session persistence (later)
 *   - Score / progress tracking (Learn product)
 */

(() => {
  'use strict';

  // -------------------------------------------------------- DOM helpers
  const $ = (id) => document.getElementById(id);
  const views = {
    intake: $('view-intake'),
    bandroom: $('view-bandroom'),
    perform: $('view-perform'),
  };
  function showView(name) {
    Object.values(views).forEach(v => v.classList.remove('active'));
    views[name].classList.add('active');
    // perform v2: the 1440px wide layout + fixed playback bar only
    // apply on the perform view. Intake / band-room keep the legacy
    // 1100px max-width to stay byte-identical.
    document.body.classList.toggle('perform-active', name === 'perform');
  }

  // -------------------------------------------------------- constants
  const LOCAL_ENGINE_URL = 'http://127.0.0.1:7777';
  const ENGINE_POLL_MS = 3000;

  // -------------------------------------------------------- state
  const state = {
    userInstrument: 'guitar',
    sourceUrl: null,
    analysisId: null,
    fullResult: null,
    // Map: stemName -> {
    //   url, buffer, gainNode, source, muted, lastGain
    // }
    // buffer: decoded AudioBuffer (full stem, decoded once)
    // gainNode: persistent GainNode for that stem (mute/solo/level)
    // source: the current AudioBufferSourceNode (one-shot; recreated on play/seek)
    stems: new Map(),
    // Section pills (from analysis); array of { name, startSec, endSec, el }
    sections: [],
    // Chord ribbon pills (from analysis); array of
    // { symbol, startSec, endSec, el, leftPx, widthPx }. Built once per
    // session in buildChordRibbon(); leftPx/widthPx are cached on
    // construction so updateChordPlayhead can run in O(log n) per RAF
    // tick without re-measuring DOM.
    chords: [],
    // Raw chord arrays from the analysis result, cached so the
    // beat-snap toggle can switch between them at render time without
    // a refetch. `fixed` is the Phase 5 WCSR-best, fixed-window grid;
    // `snapped` is the same regions with boundaries snapped to nearest
    // beat (Phase 6 hybrid). `snapped` is null when the analysis lane
    // didn't produce a beat-snapped version (e.g. no beats detected).
    rawChords: { fixed: [], snapped: null },
    // User preference: render the beat-snapped chord array when
    // available. Persisted to localStorage under
    // CHORD_BEAT_SNAP_PREF_KEY. Falsy by default — WCSR floor is
    // higher with the fixed-window array, so that's the safer default
    // for guitarists tracking chord changes against the tab.
    chordBeatSnap: false,
    // Chord diagram queue + tab lane (Phase C of the chord-diagram
    // feature). View mode is persisted to localStorage under
    // CHORD_VIEW_MODE_PREF_KEY. The shape registry is the parsed
    // chord_shapes.json, fetched once on first render; null until
    // loaded. The renderer module is dynamically imported on first
    // need (same lazy load pattern) and pinned here so subsequent
    // renders skip the import promise resolution.
    chordViewMode: 'diagrams',   // 'diagrams' | 'tab' | 'both'
    chordShapeRegistry: null,
    chordDiagramModule: null,
    // Per-symbol voicing preference, populated from localStorage on
    // boot. Shape: { "C:maj": 0, "G:maj": 1, ... } where the int is
    // the index into listVoicings(symbol, registry) — 0 is the
    // curated open voicing, 1+ are algorithmic barre alternates.
    // Selections persist across sessions (§4 of the chord-guidance
    // UX directive: "Persist selection for the session").
    voicingPrefs: {},
    // Advanced overlays toggle state (§8). All default OFF; persisted
    // to localStorage under CHORD_OVERLAYS_KEY.
    chordOverlays: { nashville: false, roman: false, scales: false },
    // Lead-line MIDI notes for the tab lane. Sourced from
    // result.midi.notes (bundle.user_midi.notes). Cached at
    // analysis-complete so the tab renderer can slice [t0, t1)
    // without touching the network.
    leadMidiNotes: [],
    // Loop window in seconds or null
    loop: null,
    // Web Audio context (created lazily on first play)
    ctx: null,
    masterGain: null,
    isPlaying: false,
    duration: 0,
    // Playback timeline derived from the Web Audio clock:
    //   currentTime(audio_clock) = ctx.currentTime - playClockAnchor + playOffset
    playClockAnchor: 0,
    playOffset: 0,
    // Local-engine availability: 'unknown' | 'off' | 'starting' | 'on'
    engineStatus: 'unknown',
    engineStartInFlight: false,
    // Click track — driven by result.beat_times (in seconds).
    beatTimes: [],
    clickEnabled: false,
    clickGain: null,           // master gain node for click
    clickScheduler: null,      // setInterval handle for look-ahead scheduling
    clickNextBeatIdx: 0,       // next beat index to schedule
    // V2 preset matches per stem (keyed by legacy stem name).
    presetMatches: {},
    // Tone-card state. `rec` is the raw wire-form ToneRecommendation
    // dict from `result.tone` (see backend/tone_forge/tone/guitar_catalog.py
    // `to_wire_dict`). Dismissed is set when the user closes the card; we
    // remember it so re-renders within the same session don't pop it back.
    tone: {
      rec: null,
      dismissed: false,
    },
    // Detected key from analysis result, parsed once per jam.
    //   { root: int 0-11, scale: 'Major'|'Minor', pitchClasses: Set<int> }
    songKey: null,
    // Learning-assistance settings, persisted to localStorage.
    settings: {
      listenEnabled: false,
      feedbackView: 'cents', // 'cents' | 'rolling' | 'full'
    },
    // Live guitar monitor — the play-along loop's input path.
    // Routes the connected audio interface directly to ctx.destination
    // so the player hears themselves through JAM while the song
    // plays. Distinct from `state.listen` (tuner / pitch detection):
    // the tuner only ever taps the mic through an analyser; the
    // monitor mixes the input into the output bus. The two own
    // separate MediaStreams so they can run independently.
    //
    // Audio graph when enabled:
    //   MediaStreamSource -> monitor.gainNode -> ctx.destination
    //                    \--> meterAnalyser (analysis-only tap)
    //
    // gain values:
    //   - `gain` is the current slider value [0, 4]. Range goes above
    //     unity (1.0) because HX Stomp at line level + Web Audio
    //     getUserMedia is consistently quiet through the laptop's
    //     internal speakers; users need real make-up gain.
    //   - `muted` zeroes the gainNode without losing the slider value
    //   - clipping is detected on the analyser tap pre-gain so the
    //     "your input is hot" warning is independent of monitor gain.
    monitor: {
      enabled: false,
      stream: null,
      sourceNode: null,
      gainNode: null,
      meterAnalyser: null,
      meterBuffer: null,
      rafHandle: null,
      deviceId: '',       // '' = system default
      deviceLabel: '',
      gain: 1.0,
      muted: false,
      // Phase 2 (Audio-Ownership Pivot): browser-monitor demotion.
      // When Connect.app is installed but not paired, the toggle is
      // gated behind a "Launch Connect for low-latency monitoring"
      // affordance, and a small fallback link lets the user opt
      // INTO the browser monitor anyway. This flag stays true once
      // the user opts in, for the rest of the page session. We
      // intentionally do NOT persist it — the assumption is that
      // re-opening JAM is a clean slate and Connect should be the
      // default again. When `paired`, this flag is moot (Connect
      // owns the device regardless).
      fallbackMode: false,
      // Clip indicator hold: timestamp (perf.now ms) until which the
      // CLIP overlay stays lit. Reset by the meter tick.
      clipUntil: 0,
      // Devices enumerate with empty labels until the page has been
      // granted mic permission at least once. We flip this after the
      // first successful getUserMedia so the select can refresh with
      // real labels.
      devicesEnumerated: false,
      // Smoothed peak amplitude for the displayed bar height. Tracked
      // on `state.monitor` (not a closure local) so a restart on a
      // device change can reset it without leaking state.
      lastDisplayedPeak: 0,
      // Stored reference to the track 'ended' listener so we can
      // remove it before our own _stopMonitor() calls t.stop().
      // Safari has been observed to fire 'ended' synchronously on
      // explicit stop, which without this guard re-entered _stopMonitor
      // on a half-torn-down stream and contributed to a tab crash
      // alongside the in-flight UserMediaPermissionsController query.
      trackEndedHandler: null,
      // Re-entry guard for _stopMonitor.
      _stopping: false,
    },
    // Song master-bus mixer channel. Mirrors state.masterGain in the
    // same way state.monitor mirrors monitor.gainNode. Tracked
    // separately so the song mute toggle remembers the user's
    // pre-mute gain value across mute/unmute (and across solo-induced
    // auto-mute) without reading it back out of the live gain node.
    songMix: {
      gain: 1.0,
      muted: false,
    },
    // Mic-capture pipeline (built lazily when listening is enabled).
    listen: {
      stream: null,
      sourceNode: null,
      analyser: null,
      buffer: null,        // reusable Float32Array
      rafHandle: null,
      // Rolling intonation samples: array of {t_ms, cents, inKey}.
      // Trimmed to last 5 s in the update loop.
      history: [],
      // Onset detection state:
      //   rmsBaseline — slow-moving floor for the input level
      //   lastOnsetMs — most recent onset detection time (refractory)
      //   onsets      — rolling list of {t_audio, offset_ms} for last 10 s
      rmsBaseline: 0,
      lastOnsetMs: 0,
      onsets: [],
      lastOffsetMs: null,
    },
    // TTFJ instrumentation. t0 captured on intake-form submit, marks pushed
    // at each pipeline boundary, stages set used to ignore duplicate progress
    // events for the same stage.
    ttfj: {
      t0: 0,
      marks: [],
      stages: new Set(),
    },
    // Connect-bridge WebSocket state. Lazy: opened on first push attempt.
    connectBridge: {
      ws: null,
      sessionId: null,
      status: 'idle', // 'idle' | 'connecting' | 'open' | 'closed'
      queue: [],      // messages waiting for the socket to open
      reconnectMs: 1000,
      lastPreset: null,
      lastGain: 0.0,  // monitor gain last requested; replayed on reconnect
      peers: 0,       // other clients on the same channel (Connect helper)
      // request_id -> { chainId, displayName, t0 } for in-flight
      // apply_chain frames. Populated by applyToneChain(), drained by
      // the WS 'ack' handler so the UI can confirm a successful push.
      // Without this, Apply silently fires-and-forgets and the user
      // can't tell whether the helper got the message.
      pendingApplies: new Map(),
      // Most recently applied monitor chain id. Used to suppress the
      // "applied" toast when the chain hasn't actually changed (e.g.
      // double-clicking Apply on the same suggestion).
      lastAppliedChainId: null,
      // Per-connection latch: the server emits 0-3 frames carrying
      // `replayed: true` immediately after join() when there is cached
      // state on the channel (preset_push, set_gain, apply_chain). The
      // user-visible signal we want is a single "Reconnected" toast
      // when the helper / browser re-attaches to a channel with state,
      // not three flashes back-to-back. This flag is cleared in
      // ws.onopen (every fresh connection re-arms it) and flipped on
      // the first replayed frame so the toast fires exactly once per
      // (re)connect cycle.
      reconnectToastShown: false,

      // ----- v2 fields (Audio-Ownership Pivot) -----
      //
      // Mirrors of the new Connect → Browser snapshot frames. Phase
      // 4 commit A wires the receive path; subsequent phases consume
      // them for UI (latency comparison card, install probe button,
      // four-state status pill).
      //
      // connectState   : last connect_state frame from Connect.app
      //                  (engine state + device + monitor + dsp).
      // connectLatencyMs : last latency_report.estimated_round_trip_ms.
      // connectMeter   : last input_meter sample, smoothed downstream.
      // installed      : /api/connect/installed result (Phase 6).
      // installedPath  : filesystem path to the bundle, when known.
      // installedVersion : CFBundleShortVersionString, when known.
      // launching      : true while a launch attempt is in-flight
      //                  (Phase 1: drives the "Connect Starting"
      //                  status pill for up to 5s).
      // launchingUntilMs : performance.now() expiry of the launching
      //                    flag; cleared early on first peer join.
      connectState: null,
      connectLatencyMs: null,
      // connectLatencyReport: full latency_report payload (kept for
      // the dual-latency tooltip's input/output/buffer breakdown).
      connectLatencyReport: null,
      // connectMeasuredLatencyMs: ground-truth round-trip from a
      // LatencyProbe impulse-loopback run, only present after the
      // user clicked "Measure". Estimated vs. measured are shown
      // side-by-side; measured wins the "Recommended" tag when
      // both are present and confidence is "high".
      connectMeasuredLatencyMs: null,
      connectMeasuredConfidence: null,
      // measureInFlight: true between sending measure_latency and
      // receiving the next latency_report. Drives the button's
      // disabled state + a "Measuring…" label.
      measureInFlight: false,
      connectMeter: { peak: -120, rms: -120, t: 0 },
      installed: null,
      installedPath: null,
      installedVersion: null,
      launching: false,
      launchingUntilMs: 0,
    },
    // perform v2 waveform timeline. Peaks are computed once from the
    // decoded stem AudioBuffers after prepareStemAudio resolves.
    //   peaks      : Float32Array (length = targetSamples, range [0,1])
    //   durationSec: anchor duration the peaks were taken against
    // Re-drawn every RAF inside tickClock(); also re-drawn on debounced
    // window resize so the canvas matches the bar's actual pixel width.
    waveform: null,
  };

  // Records a timing mark relative to TTFJ t0. No-op if t0 is unset.
  function markTtfj(label, extra) {
    if (!state.ttfj.t0) return;
    const elapsedMs = Math.round(performance.now() - state.ttfj.t0);
    const entry = { label, elapsedMs };
    if (extra && typeof extra === 'object') Object.assign(entry, extra);
    state.ttfj.marks.push(entry);
    console.log(`[ttfj] +${elapsedMs}ms ${label}`, extra || '');
  }

  // Resets TTFJ state and stamps t0 at the moment the user submitted the URL.
  function resetTtfj() {
    state.ttfj.t0 = performance.now();
    state.ttfj.marks = [];
    state.ttfj.stages = new Set();
  }

  // Emits a final summary table to console + stashes on window for inspection.
  function reportTtfj(totalMs) {
    const summary = {
      total_ms: totalMs,
      stages: state.ttfj.marks,
      source_url: state.sourceUrl,
    };
    window.__tfjTtfj = summary;
    console.log(`[ttfj] === TTFJ = ${totalMs}ms ===`);
    try { console.table(state.ttfj.marks); } catch {}
  }

  // ---- Connect bridge (WebSocket → backend hub) ---------------------------
  // The browser pushes tone-preset payloads via /ws/connect-bridge. The
  // backend caches the latest preset and forwards it to the Connect desktop
  // app when that joins the same session channel. The push is fire-and-
  // forget from the browser's perspective.

  function ensureConnectBridge() {
    const cb = state.connectBridge;
    if (cb.ws && (cb.status === 'open' || cb.status === 'connecting')) return;
    if (!cb.sessionId) cb.sessionId = newSessionId();
    cb.status = 'connecting';
    const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${scheme}//${window.location.host}/ws/connect-bridge`;
    let ws;
    try { ws = new WebSocket(url); }
    catch (e) { console.warn('[connect] WebSocket construction failed:', e); cb.status = 'closed'; return; }
    cb.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: 'hello',
        role: 'browser',
        session_id: cb.sessionId,
        // Wire-protocol version (mirrors CONNECT_BRIDGE_PROTOCOL_VERSION
        // on the server and ConnectProtocol.version in Swift). Bump in
        // lockstep with those when adding required fields or changing
        // semantics of an existing message type.
        //
        // v2 (Audio-Ownership Pivot): additive over v1. JAM now
        // emits session_data + transport_state and receives
        // connect_state + latency_report + input_meter. Existing v1
        // frame shapes unchanged.
        protocol_version: 2,
      }));
      cb.status = 'open';
      cb.reconnectMs = 1000;
      // Re-arm the "Reconnected" toast for this connection lifecycle.
      // Without this, a second reconnect in the same tab would skip
      // the toast because the prior flag was still set.
      cb.reconnectToastShown = false;
      renderConnectStatus();
      // Flush any preset pushes that were queued before the socket opened.
      for (const msg of cb.queue) {
        try { ws.send(JSON.stringify(msg)); } catch {}
      }
      cb.queue = [];
    };
    ws.onmessage = (ev) => {
      let data;
      try { data = JSON.parse(ev.data); } catch { return; }
      // First frame on a (re)connection that carries `replayed: true`
      // fires a single "Reconnected" toast. The server-side replay
      // batch can contain up to three frames (preset_push, set_gain,
      // apply_chain) back-to-back; the latch in cb.reconnectToastShown
      // dedupes them so the user sees one signal, not three flashes.
      // Pinned at the server end by test_connect_bridge_replay.py.
      if (data && data.replayed === true && !cb.reconnectToastShown) {
        cb.reconnectToastShown = true;
        flashConnectStatus('Reconnected', true, 1500);
      }
      if (data.type === 'hello_ack') {
        // Server confirms it speaks our protocol version. `joined`
        // follows; nothing to do here beyond logging.
        console.log(`[connect] hello_ack (server protocol v${data.protocol_version})`);
      } else if (data.type === 'version_mismatch') {
        // Server is older than us, or we're past what it supports.
        // Stop reconnecting — looping would just rehit the rejection.
        // In practice this happens when an older backend is paired with
        // a newer jam.js (e.g. cached static asset after deploy).
        console.warn(`[connect] version_mismatch: server requires v${data.required}, we sent v2`);
        cb.lastPreset = null;  // suppress auto-reconnect in onclose
        try { ws.close(); } catch {}
      } else if (data.type === 'joined') {
        cb.peers = data.peers || 0;
        // Phase 1: helper is on the channel — pop the "Connect
        // Starting" grace window early so the pill flips straight
        // to "Connect Connected" without lingering in the
        // intermediate state for the rest of the 5s.
        if (cb.peers > 0) _clearLaunchingTimer();
        renderConnectStatus();
        console.log(`[connect] joined session ${data.session_id} (peers=${data.peers})`);
      } else if (data.type === 'peer_left') {
        // Server detected a dead peer on its last broadcast attempt
        // and is telling us the new survivor count. Without this our
        // "paired" badge would stay green until the next reconnect
        // tick (up to 30s of stale UI).
        cb.peers = Number(data.peers) || 0;
        renderConnectStatus();
        console.log(`[connect] peer_left (peers=${cb.peers}, reason=${data.reason || 'unknown'})`);
      } else if (data.type === 'set_gain') {
        // Server replayed the cached gain (e.g. after we reconnected).
        // Sync local state and slider so the UI matches.
        const g = Math.max(0, Math.min(1, Number(data.gain) || 0));
        cb.lastGain = g;
        renderConnectGain();
      } else if (data.type === 'ack') {
        // Server confirmed receipt of one of our requests. If it
        // matches a pending apply_chain, flash a success toast and
        // update the visible "matched tone" label so the user can
        // see something changed.
        const pending = data.request_id
          ? cb.pendingApplies.get(data.request_id)
          : null;
        if (pending) {
          cb.pendingApplies.delete(data.request_id);
          const alreadyOnIt = cb.lastAppliedChainId === pending.chainId;
          cb.lastAppliedChainId = pending.chainId;
          flashConnectStatus(
            alreadyOnIt
              ? `${pending.displayName} re-applied`
              : `${pending.displayName} applied`,
            true,
          );
          // Surface the applied chain in the YOUR SLOT card so the
          // page reflects the user's choice. The backend's automatic
          // preset-match logic (pushPresetToConnect) may also fire
          // later for the same song; if it does, that path will
          // overwrite this label, which is the right precedence.
          const userToneName = document.getElementById('user-tone-name');
          if (userToneName) userToneName.textContent = pending.displayName;
          const userToneMeta = document.getElementById('user-tone-meta');
          if (userToneMeta) {
            userToneMeta.textContent = 'Monitor chain pushed to Connect';
          }
          console.log(
            `[connect] apply_chain ack ${pending.chainId} `
            + `(${Math.round(performance.now() - pending.t0)}ms)`,
          );
        }
      } else if (data.type === 'set_auto_update') {
        // The user toggled the Sparkle auto-update preference in
        // another tab (or the server replayed the persisted value
        // on a fresh Connect join). Reflect the new state into the
        // settings checkbox so all open tabs converge. Idempotent —
        // setting `checked` to its current value is a no-op and
        // does not fire a `change` event, so we won't loop back to
        // the server. Pinned at the server end by
        // test_connect_bridge_set_auto_update.py.
        if (typeof data.enabled === 'boolean') {
          const cbEl = document.getElementById('setting-auto-update-enabled');
          if (cbEl) cbEl.checked = data.enabled;
          console.log(
            `[connect] set_auto_update enabled=${data.enabled} `
            + `(replayed=${data.replayed === true})`,
          );
        }
      } else if (data.type === 'device_lost') {
        // Connect's AudioEngine exhausted its reconfig retry budget
        // (interface unplugged and not plugged back in, or driver
        // gave up). The helper is alive on the WS but its audio
        // path is permanently broken until restart. Surface a
        // longer-lived toast so the user knows the helper itself
        // needs attention — a normal reconnection won't fix this.
        // Pinned at the server end by
        // test_connect_bridge_device_lost.py.
        console.warn(`[connect] device_lost (reason=${data.reason || 'unknown'})`);
        flashConnectStatus(
          'Audio input lost — reconnect your interface and restart Connect.',
          false,
          8000,
        );
      } else if (data.type === 'ping') {
        // Server liveness probe. The backend reaps the socket if it
        // doesn't see any frame within ~40s (30s recv + 10s pong
        // window) — see tone_forge_api.py:567-599. We must answer
        // immediately or get dropped, and because the browser's
        // auto-reconnect path is gated on `cb.lastPreset` being set
        // (see ws.onclose below), a single missed pong on a fresh
        // perform view leaves the UI stuck at "Connect: offline"
        // until the user reloads. Best-effort send.
        try { ws.send(JSON.stringify({ type: 'pong' })); } catch {}
      } else if (data.type === 'pong') {
        // The server doesn't send unsolicited pongs, but the wire
        // protocol allows them as a defense-in-depth keepalive. No-op.
      } else if (data.type === 'error') {
        // Surface the failure visibly, not just in the console. If
        // we can pin it to an in-flight apply, name the chain in the
        // toast so the user knows which click failed.
        console.warn('[connect] server error:', data.message);
        const pending = data.request_id
          ? cb.pendingApplies.get(data.request_id)
          : null;
        if (pending) {
          cb.pendingApplies.delete(data.request_id);
          flashConnectStatus(
            `Couldn't apply ${pending.displayName}: ${data.message || data.code || 'unknown error'}`,
            false,
            5000,
          );
        } else if (cb.pendingApplies.size > 0) {
          // Server didn't echo a request_id but we know an apply is
          // in flight. Clear the queue and show a generic message —
          // safer than silently leaving "Applying…" up forever.
          cb.pendingApplies.clear();
          flashConnectStatus(
            `Apply failed: ${data.message || data.code || 'unknown error'}`,
            false,
            5000,
          );
        }
      } else if (data.type === 'connect_state') {
        // v2 (Audio-Ownership Pivot): engine snapshot from Connect.app.
        // Phase 4 commit A only stores it; consumers in Phases 1/2/5
        // read state.connectBridge.connectState to drive the four-
        // state status pill, the audio-input-card collapse, and the
        // ownership banner. Storing the raw payload (not destructured)
        // keeps the v2 fields { state, device, monitor, dsp }
        // available without a second wire pass.
        cb.connectState = data;
        // Phase 3: the "Recommended" tag on the dual-latency card
        // tracks paired-and-running, which depends on the connect_state
        // we just stored. Re-render the comparison so the tag flips
        // when Connect's engine transitions to .running.
        renderLatencyComparison();
      } else if (data.type === 'latency_report') {
        // v2: measured engine latency. Stash the round-trip estimate
        // for the dual-latency card (Phase 3) plus the full payload
        // (input/output/buffer ms) so the row's tooltip can break
        // the floor down for the curious user.
        const rt = Number(data.estimated_round_trip_ms);
        cb.connectLatencyMs = Number.isFinite(rt) ? rt : null;
        // measured_round_trip_ms is the ground-truth from a
        // LatencyProbe impulse-loopback run; only present after the
        // user triggered a "Measure" via measure_latency. Stash
        // alongside the floor estimate so the UI can show both.
        const measured = Number(data.measured_round_trip_ms);
        cb.connectMeasuredLatencyMs = Number.isFinite(measured) ? measured : null;
        cb.connectMeasuredConfidence = typeof data.measurement_confidence === 'string'
          ? data.measurement_confidence
          : null;
        cb.measureInFlight = false;
        cb.connectLatencyReport = data;
        renderLatencyComparison();
      } else if (data.type === 'input_meter') {
        // v2: input level for JAM's VU meter. High-rate (~20 Hz at
        // source); smoothing happens at the render layer in a later
        // phase. Store last sample only.
        const peak = Number(data.peak_dbfs);
        const rms = Number(data.rms_dbfs);
        cb.connectMeter = {
          peak: Number.isFinite(peak) ? peak : -120,
          rms: Number.isFinite(rms) ? rms : -120,
          t: performance.now(),
        };
      }
    };
    ws.onclose = () => {
      cb.ws = null;
      cb.status = 'closed';
      cb.peers = 0;
      renderConnectStatus();
      // Exponential backoff up to 30s. Only auto-reconnect if we have a
      // preset to keep alive; idle browser doesn't need to keep hammering.
      if (cb.lastPreset) {
        const delay = Math.min(cb.reconnectMs, 30000);
        cb.reconnectMs = Math.min(cb.reconnectMs * 2, 30000);
        setTimeout(ensureConnectBridge, delay);
      }
    };
    ws.onerror = (e) => {
      console.warn('[connect] ws error:', e);
    };
    renderConnectStatus();
  }

  // Reflects the current bridge state in the user-card Connect button.
  // The button doubles as a status indicator so the player can see at a
  // glance whether the desktop helper is paired.
  function renderConnectStatus() {
    const btn = document.getElementById('connect-btn');
    if (!btn) return;
    const cb = state.connectBridge;
    let label;
    const paired = cb.status === 'open' && cb.peers > 0;
    // Phase 1: four-state vocabulary.
    //   Connected → peers > 0 (helper actually on the channel)
    //   Starting  → user just clicked Launch (5s grace window)
    //   Error     → WS closed AND auto-reconnect exhausted
    //                 (cb.reconnectMs caps at 30000)
    //   Offline   → everything else (idle / waiting / connecting)
    // The class names stay legacy ('connected' only) so the existing
    // CSS for the gain slider reveal at line 614+ keeps working.
    const launching = !!cb.launching
      && performance.now() < cb.launchingUntilMs;
    const errored = cb.status === 'closed' && cb.reconnectMs >= 30000;
    if (paired) {
      label = `Connect Connected (${cb.peers})`;
      btn.classList.add('connected');
    } else if (launching) {
      label = 'Connect Starting…';
      btn.classList.remove('connected');
    } else if (errored) {
      label = 'Connect Error — click for help';
      btn.classList.remove('connected');
    } else {
      label = 'Connect Offline';
      btn.classList.remove('connected');
    }
    btn.textContent = label;

    // Reveal the monitor-gain slider only once the helper is actually
    // paired. Showing it before then would imply the slider does
    // something — it doesn't, the server has no peer to broadcast to.
    const wrap = document.getElementById('connect-gain-wrap');
    if (wrap) wrap.hidden = !paired;

    // Reconcile the browser-side monitor toggle with the pairing
    // state. When paired, Connect owns the input device and the
    // monitor must yield (otherwise both clients fight for the HX
    // Stomp and the helper supervisor flaps). Defined further down
    // alongside the monitor lifecycle; safe to call here because
    // function declarations are hoisted within this IIFE.
    if (typeof _syncMonitorVsConnect === 'function') _syncMonitorVsConnect();
    // Phase 3: the dual-latency card's "Recommended" tag tracks
    // _isConnectPaired(), which we just updated. Re-render so peer
    // join / peer leave flips the tag without waiting for the next
    // connect_state frame.
    if (typeof renderLatencyComparison === 'function') renderLatencyComparison();
    // Phase 5: ownership-boundary disclosure summary mirrors the
    // same state. Keeps the audio-input-card collapsed/expanded
    // default + badge in sync with install/launch/pair transitions.
    if (typeof renderAudioInputCard === 'function') renderAudioInputCard();

    // Inline status line. We're always on the perform view by the time
    // anyone reads this, so the message reflects post-analysis state —
    // never "analyze a song" (we already did).
    const statusEl = document.getElementById('connect-status');
    if (statusEl) {
      let text = '';
      let ok = false;
      // showLauncherLink: render a visible "Open Connect helper →"
      // anchor below the status text when we're waiting for the helper
      // to join. The synthesised anchor.click() inside the button
      // handler works in Chrome but Safari silently drops
      // programmatically-clicked custom-scheme URLs. A real
      // user-clicked <a href="toneforge://…"> is the universal escape
      // hatch — every browser treats it as a direct user gesture.
      let showLauncherLink = false;
      if (paired && cb.lastPreset) {
        text = 'Tone preset synced with Connect.';
        ok = true;
      } else if (paired && cb.lastAppliedChainId) {
        text = 'Monitor chain synced with Connect.';
        ok = true;
      } else if (paired) {
        // Companion to the user-tone headline at jam.js:1402-1413 —
        // if a recommendation exists and hasn't been dismissed yet
        // it IS a tone match, just not yet pushed to Connect. Don't
        // contradict the visible Apply card.
        const hasPending = state.tone
          && state.tone.rec
          && !state.tone.dismissed;
        text = hasPending
          ? 'Paired — recommendation ready (click Apply).'
          : 'Paired — no tone match for this song.';
      } else if (launching) {
        // Phase 1: user just clicked Launch; we're inside the
        // 5s grace window. The pill above already says
        // "Connect Starting…" — the body just confirms.
        text = 'Starting Connect…';
        showLauncherLink = !!cb.sessionId;
      } else if (cb.status === 'connecting') {
        text = 'Connecting to the desktop helper…';
      } else if (cb.status === 'open') {
        text = 'Waiting for the desktop helper to join.';
        showLauncherLink = !!cb.sessionId;
      } else if (errored) {
        text = 'Connect Error — restart it from the tray menu.';
      } else if (cb.status === 'closed') {
        text = 'Connect Offline. Restart it from the tray menu.';
      }
      statusEl.textContent = text;
      // Phase 6: install/launch CTA. Lives ABOVE the existing
      // deep-link / restart affordances so it's the user's primary
      // action when not paired. Renders one of three states:
      //   installed===true  && !paired → "Launch Connect" button
      //   installed===false              → "Install Connect" link
      //   installed===null               → nothing (probe pending)
      if (!paired && cb.installed === true) {
        const btn6 = document.createElement('button');
        btn6.type = 'button';
        btn6.textContent = 'Launch Connect';
        btn6.className = 'connect-launch-btn';
        btn6.addEventListener('click', launchConnect);
        statusEl.appendChild(document.createElement('br'));
        statusEl.appendChild(btn6);
        if (cb.installedVersion) {
          const ver = document.createElement('span');
          ver.className = 'connect-launch-version';
          ver.textContent = ` v${cb.installedVersion}`;
          statusEl.appendChild(ver);
        }
      } else if (!paired && cb.installed === false) {
        const installLink = document.createElement('a');
        installLink.href = CONNECT_INSTALL_URL;
        installLink.target = '_blank';
        installLink.rel = 'noopener';
        installLink.textContent = 'Install Connect for low-latency monitoring';
        installLink.className = 'connect-install-link';
        statusEl.appendChild(document.createElement('br'));
        statusEl.appendChild(installLink);
      }
      if (showLauncherLink) {
        const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${scheme}//${window.location.host}/ws/connect-bridge`;
        const deepLink =
          'toneforge://pair'
          + `?session=${encodeURIComponent(cb.sessionId)}`
          + `&ws=${encodeURIComponent(wsUrl)}`;
        const launcher = document.createElement('a');
        launcher.href = deepLink;
        launcher.textContent = 'Open Connect helper →';
        launcher.className = 'connect-launcher-link';
        // Phase 1: clicking the user-gesture deeplink should also
        // arm the "Connect Starting" pill so the UX matches the
        // synthetic-click path inside launchConnect(). Without this
        // the pill would stay on "Connect Offline" until the helper
        // joins, which can read as nothing-happening for several
        // seconds.
        launcher.addEventListener('click', () => {
          if (typeof _setLaunchingTimer === 'function') {
            _setLaunchingTimer(5000);
          }
        });
        // Space the link onto its own line so it's distinguishable
        // from the status copy above it.
        statusEl.appendChild(document.createElement('br'));
        statusEl.appendChild(launcher);
        // Second affordance: ask the *supervisor* to respawn the
        // helper. This is the §3D "Try restarting Connect" CTA — used
        // when the supervisor exhausted its 4-attempt auto-restart
        // budget (`backend/local_engine/connect_bridge.py:307-313`)
        // and the user needs to manually re-arm it. The deep link
        // above launches the helper via macOS LaunchServices; this
        // button asks the local-engine supervisor to spawn the
        // already-known child. Both are idempotent — try one, then
        // the other.
        const restartBtn = document.createElement('button');
        restartBtn.type = 'button';
        restartBtn.textContent = 'Try restarting Connect';
        restartBtn.className = 'connect-restart-btn';
        restartBtn.addEventListener('click', restartConnectHelper);
        statusEl.appendChild(document.createElement('br'));
        statusEl.appendChild(restartBtn);
      }
      // Phase 6: even with empty `text` (e.g. status='idle' before the
      // bridge has been opened) we want the install/launch CTA visible
      // so first-time users see "Install Connect" without having to
      // click anything. statusEl.children includes the appended button/
      // anchor regardless of text content.
      statusEl.hidden = !text && statusEl.children.length === 0;
      statusEl.classList.toggle('ok', ok);
    }
    // v2 header pill mirrors every status transition. Guarded for the
    // initial pre-DOM call by renderConnectPill's own null check.
    try { renderConnectPill(); } catch (_) {}
  }

  // Click handler for the "Try restarting Connect" CTA rendered inside
  // renderConnectStatus(). POSTs to the local-engine supervisor's
  // restart endpoint (defined in backend/local_engine/server.py:238).
  // The supervisor's restart() is stop+start; both reset the
  // _restart_attempts budget, so the next crash gets a full 4
  // attempts again. We flash a toast for either outcome — the WS
  // 'joined' handler is what actually confirms the helper came back
  // (peers > 0 → "paired"), so this toast is just acknowledgement of
  // the click.
  let _restartInFlight = false;
  async function restartConnectHelper() {
    if (_restartInFlight) return;
    _restartInFlight = true;
    flashConnectStatus('Restarting Connect…', false, 4000);
    try {
      const resp = await fetch(
        `${LOCAL_ENGINE_URL}/api/connect/restart`,
        { method: 'POST' },
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json().catch(() => ({}));
      if (data && data.running) {
        flashConnectStatus(
          'Connect supervisor respawned the helper.',
          true,
          2500,
        );
      } else {
        // Supervisor returned, but the child isn't running — usually
        // means binary discovery failed (no installed Connect.app).
        // Surface last_error if present so the user has a clue.
        const err = (data && data.last_error)
          ? `: ${data.last_error}`
          : '';
        flashConnectStatus(
          `Couldn't start Connect${err}`,
          false,
          5000,
        );
      }
    } catch (err) {
      console.warn('[connect] restart failed:', err);
      flashConnectStatus(
        `Restart failed: ${err.message || 'local engine unreachable'}`,
        false,
        5000,
      );
    } finally {
      _restartInFlight = false;
    }
  }

  // ---- Phase 6: install probe + launch helper ------------------------
  //
  // probeConnectInstalled() runs once at page load and hits
  // `/api/connect/installed` (filesystem-only, no AVFoundation). The
  // result drives `renderConnectStatus()`'s install/launch CTA: when
  // we can see the bundle on disk we surface "Launch Connect"; when
  // we can't we surface "Install Connect" pointing at GitHub Releases.
  //
  // Failure is silent on purpose: a 5xx from the probe must NOT block
  // JAM startup. We just stay in "installed: null" — neither CTA
  // renders, and the pre-Phase-6 behaviour (no install affordance,
  // status pill only) prevails.
  //
  // GitHub Releases tag for the Connect.app DMG. Pinned to /latest so
  // users always get the most recent build that build_release.sh
  // produced; never hard-codes a version JAM might out-grow.
  const CONNECT_INSTALL_URL =
    'https://github.com/harvlad/tone-forge/releases/latest';

  async function probeConnectInstalled() {
    const cb = state.connectBridge;
    try {
      const resp = await fetch('/api/connect/installed');
      if (!resp.ok) {
        console.warn('[connect] install probe HTTP', resp.status);
        return;
      }
      const data = await resp.json();
      cb.installed = !!data.installed;
      cb.installedPath = data.path || null;
      cb.installedVersion = data.version || null;
      renderConnectStatus();
    } catch (err) {
      // No-op: stays at installed=null, no CTA rendered. This is the
      // pre-Phase-6 UX, which is correct behaviour for "we don't know".
      console.warn('[connect] install probe failed:', err);
    }
  }

  // launchConnect() POSTs /api/connect/launch (Launch Services lookup
  // by bundle id, falling back to `open <path>`), then fires the
  // toneforge:// deeplink so Connect.app's URL handler can pair with
  // this browser. Both steps are best-effort: if `open` fails (e.g.
  // Connect.app isn't installed yet) the deeplink simply won't have
  // anything to receive it, and the user can fall back to the
  // browser monitor via Phase 2's affordance.
  //
  // The deeplink synthesis duplicates the inline anchor in
  // renderConnectStatus() — see jam.js:670-685 — because we want a
  // *programmatic* path here (button → POST → deeplink) without
  // requiring the user to click a second link after clicking the
  // primary "Launch Connect" button. Safari may drop the synthetic
  // anchor.click() on the floor; that's fine, the visible deeplink
  // anchor in the existing status block stays as the universal
  // escape hatch.
  // Phase 1: arm the "Connect Starting" status pill for `ms`
  // milliseconds (default 5s). The pill consumes
  // `cb.launching === true && performance.now() < cb.launchingUntilMs`
  // — see renderConnectStatus() / renderConnectPill(). Cleared early
  // in the WS `joined` handler the moment peers > 0, so the user
  // doesn't see a misleading "Starting…" once Connect is actually
  // paired. Idempotent: re-calling extends the deadline rather than
  // queueing a second timer.
  let _launchingTimer = null;
  function _setLaunchingTimer(ms = 5000) {
    const cb = state.connectBridge;
    cb.launching = true;
    cb.launchingUntilMs = performance.now() + ms;
    if (_launchingTimer) clearTimeout(_launchingTimer);
    _launchingTimer = setTimeout(() => {
      _launchingTimer = null;
      const cb2 = state.connectBridge;
      if (!cb2) return;
      // Defensive: only clear if the deadline really has passed. A
      // later _setLaunchingTimer() call could have pushed the
      // deadline out while this timer was queued.
      if (performance.now() >= cb2.launchingUntilMs) {
        cb2.launching = false;
        renderConnectStatus();
      }
    }, ms + 100);
    renderConnectStatus();
  }

  // Phase 1: called from the `joined` WS handler when peers > 0.
  // Pops the launching flag so the status pill flips from
  // "Connect Starting" → "Connect Connected" without waiting for
  // the 5s grace timer to expire.
  function _clearLaunchingTimer() {
    if (_launchingTimer) {
      clearTimeout(_launchingTimer);
      _launchingTimer = null;
    }
    const cb = state.connectBridge;
    if (cb) {
      cb.launching = false;
      cb.launchingUntilMs = 0;
    }
  }

  let _launchInFlight = false;
  async function launchConnect() {
    if (_launchInFlight) return;
    _launchInFlight = true;
    const cb = state.connectBridge;
    try {
      // Step 1: POST /api/connect/launch. Backend tries
      // `open -b com.toneforge.connect` then `open <path>`; both are
      // idempotent. Status 202 always (Accepted, polling for paired
      // state lives in the WS join handler).
      try {
        await fetch('/api/connect/launch', { method: 'POST' });
      } catch (err) {
        console.warn('[connect] launch POST failed:', err);
      }
      // Step 2: fire the toneforge:// deeplink so Connect.app's URL
      // handler pairs with this browser. Requires a session id (the
      // bridge channel) and the WS URL the helper should join.
      if (cb.sessionId) {
        try {
          const scheme =
            window.location.protocol === 'https:' ? 'wss:' : 'ws:';
          const wsUrl =
            `${scheme}//${window.location.host}/ws/connect-bridge`;
          const deepLink =
            'toneforge://pair'
            + `?session=${encodeURIComponent(cb.sessionId)}`
            + `&ws=${encodeURIComponent(wsUrl)}`;
          const a = document.createElement('a');
          a.href = deepLink;
          a.style.display = 'none';
          document.body.appendChild(a);
          a.click();
          a.remove();
        } catch (err) {
          console.warn('[connect] deeplink synth failed:', err);
        }
      }
      // Phase 1: arm the "Connect Starting" pill for the standard
      // 5s grace window. _setLaunchingTimer() also triggers a
      // renderConnectStatus() so the pill flips immediately.
      _setLaunchingTimer(5000);
    } finally {
      _launchInFlight = false;
    }
  }

  // ---- Phase 5: ownership-boundary disclosure ------------------------
  //
  // renderAudioInputCard() owns the <details id="audio-input-card">
  // summary state. The card's default `open` attribute is set
  // optimistically in jam.html to true (expanded) so users on
  // browsers without JS — or before the install probe lands —
  // still see the controls. Once we learn whether Connect.app is
  // installed we collapse it: the contract is that JAM does NOT
  // own the audio path when Connect is installed, so the browser-
  // monitor controls become a fallback affordance rather than the
  // primary surface.
  //
  // The "collapse on first install probe" behaviour fires exactly
  // once per page session — once the user has explicitly clicked
  // the disclosure (toggling it open or closed manually), we
  // record that on `state.connectBridge._inputCardUserToggled`
  // and never auto-collapse again. Without that latch, every
  // renderConnectStatus() call (joined, peer_left, gain replay,
  // etc.) would clobber the user's choice.
  let _audioInputCardWiredUp = false;
  function renderAudioInputCard() {
    const card = document.getElementById('audio-input-card');
    const badge = document.getElementById('audio-input-card-badge');
    if (!card) return;
    if (!_audioInputCardWiredUp) {
      _audioInputCardWiredUp = true;
      card.addEventListener('toggle', () => {
        const cb = state.connectBridge;
        if (cb) cb._inputCardUserToggled = true;
      });
    }
    const cb = state.connectBridge;
    const paired = cb && cb.status === 'open' && cb.peers > 0;
    const installed = cb && cb.installed === true;
    // Auto-collapse semantics: collapse when Connect is installed
    // (whether paired or not) so the disclosure reflects "Connect
    // owns this". Stays open if Connect isn't installed — the
    // browser monitor is the primary path. Once the user has
    // touched the chevron we leave it alone.
    if (cb && !cb._inputCardUserToggled) {
      const shouldBeOpen = !installed;
      if (card.open !== shouldBeOpen) card.open = shouldBeOpen;
    }
    // Tiny status badge in the summary. Mirrors the four-state
    // vocabulary at a glance without filling the summary with a
    // full status pill.
    if (badge) {
      badge.classList.remove(
        'is-paired',
        'is-installed',
        'is-fallback',
        'is-missing',
      );
      if (paired) {
        badge.textContent = 'Connect';
        badge.classList.add('is-paired');
        badge.title = 'Connect.app is paired — owns the audio path.';
      } else if (installed) {
        badge.textContent = 'Connect installed';
        badge.classList.add('is-installed');
        badge.title = 'Connect.app is installed but not paired. '
          + 'Click "Launch Connect" to pair.';
      } else if (state.monitor && state.monitor.fallbackMode) {
        badge.textContent = 'Browser fallback';
        badge.classList.add('is-fallback');
        badge.title = 'Using the browser monitor as a fallback.';
      } else {
        badge.textContent = 'Browser only';
        badge.classList.add('is-missing');
        badge.title = 'Connect.app isn\'t installed — install it for '
          + 'low-latency monitoring.';
      }
    }
  }

  // Format the gain slider's readout so users see what they're doing.
  // The readout sits next to the slider; the slider itself stays
  // value-driven from state.connectBridge.lastGain so re-renders
  // (e.g. after server replay) sync the UI.
  function renderConnectGain() {
    const slider = document.getElementById('connect-gain');
    const readout = document.getElementById('connect-gain-readout');
    if (!slider || !readout) return;
    const g = state.connectBridge.lastGain || 0;
    slider.value = String(g);
    if (g <= 0.001) {
      readout.textContent = 'muted';
    } else {
      readout.textContent = Math.round(g * 100) + '%';
    }
  }

  // Channel ID for the Connect bridge. Defaults to the well-known
  // "default" channel so the Connect helper can pair with no setup —
  // both sides just say session_id="default". Override via
  // ?session=<name> in the URL for multi-user scenarios.
  function newSessionId() {
    try {
      const fromUrl = new URLSearchParams(window.location.search).get('session');
      if (fromUrl) return fromUrl;
    } catch {}
    return 'default';
  }

  // Fire-and-forget push. If the socket isn't open yet, queue and connect.
  function pushPresetToConnect(preset) {
    if (!preset || typeof preset !== 'object') return;
    const cb = state.connectBridge;
    cb.lastPreset = preset;
    const msg = { type: 'preset_push', preset };
    sendOrQueueBridgeMessage(msg);
  }

  // Set the Connect helper's input monitor gain. Server clamps to
  // [0, 1] and caches; reconnects re-receive the cached value.
  function setConnectGain(gain) {
    const cb = state.connectBridge;
    const clamped = Math.max(0, Math.min(1, Number(gain) || 0));
    cb.lastGain = clamped;
    sendOrQueueBridgeMessage({ type: 'set_gain', gain: clamped });
  }

  // Audio-Ownership Pivot follow-up: ask Connect to run a real
  // impulse-loopback LatencyProbe. Audible — caller is responsible
  // for user confirmation. Idempotent: re-firing while a probe is
  // in flight is a no-op on the Connect side (latencyProbeInFlight
  // guard in AudioEngine) and the second send here is harmless.
  function triggerLatencyMeasurement() {
    const cb = state.connectBridge;
    if (!cb || cb.status !== 'open' || !(cb.peers > 0)) return;
    if (cb.measureInFlight) return;
    cb.measureInFlight = true;
    // Render flips the button to "Measuring…" immediately so the
    // user gets feedback even before the impulse plays.
    if (typeof renderLatencyComparison === 'function') renderLatencyComparison();
    sendOrQueueBridgeMessage({ type: 'measure_latency', v: 2 });
    // Safety: if Connect never answers (engine down, helper killed
    // mid-probe), clear the in-flight flag after a generous timeout
    // so the button comes back.
    setTimeout(() => {
      if (state.connectBridge && state.connectBridge.measureInFlight) {
        state.connectBridge.measureInFlight = false;
        if (typeof renderLatencyComparison === 'function') renderLatencyComparison();
      }
    }, 5000);
  }

  // Audio-Ownership Pivot follow-up: hand stem URLs to Connect so
  // it can take over playback from the browser's Web Audio path.
  // Idempotent: re-firing with the same stem set is harmless on the
  // Connect side (it unloads then reloads). Gated on a paired
  // Connect — silent no-op otherwise so a JAM tab with no helper
  // running doesn't pile up messages on the queue.
  function pushStemsToConnect() {
    const cb = state.connectBridge;
    if (!cb || cb.status !== 'open' || !(cb.peers > 0)) return;
    const stems = [];
    for (const [id, stem] of state.stems.entries()) {
      if (!stem || !stem.url) continue;
      stems.push({
        id: String(id),
        url: String(stem.url),
        display_name: stem.displayName || String(id),
      });
    }
    if (stems.length === 0) return;
    sendOrQueueBridgeMessage({ type: 'load_stems', v: 2, stems });
  }

  // Audio-Ownership Pivot Phase 4 (D4): push the post-analysis song
  // metadata to Connect so a future SessionStore consumer can drive
  // an in-Connect tuner / countdown / chord HUD overlay. Today's
  // Connect ignores unknown keys, so this is a no-op on the helper
  // side until SessionStore is wired up; the rails go through now
  // so the wire format does not churn.
  //
  // Called once per song-load from the post-analysis populate point
  // (where state.beatTimes / state.songKey / state.rawChords /
  // state.sections all become valid). Server caches the frame and
  // replays it to a late-joining Connect helper on join, so a user
  // who fires JAM analysis before launching Connect.app still sees
  // the song metadata once the helper attaches.
  function pushSessionDataToConnect() {
    const cb = state.connectBridge;
    if (!cb || cb.status !== 'open' || !(cb.peers > 0)) return;
    const chords = (state.rawChords && (state.rawChords.snapped || state.rawChords.fixed)) || [];
    const sections = Array.isArray(state.sections)
      ? state.sections.map((s) => ({
          name: s.name || 'section',
          // jam.js stores sections in seconds (startSec/endSec). Wire
          // contract is start_s/end_s to match the rest of the v2
          // schema.
          start_s: Number(s.startSec) || 0,
          end_s: Number(s.endSec) || 0,
        }))
      : null;
    // state.songKey is the parsed pitch-class set (see
    // parseDetectedKey). For wire we send {root, scale} where root is
    // 0-11 and scale is "Major"/"Minor"; null when the analysis did
    // not produce a defensible key.
    let keyPayload = null;
    if (state.songKey && typeof state.songKey === 'object') {
      const root = (typeof state.songKey.root === 'number') ? state.songKey.root : null;
      const scale = state.songKey.scale || state.songKey.mode || null;
      if (root !== null && scale) {
        keyPayload = { root, scale };
      }
    }
    const bpm = (typeof state.tempo === 'number' && state.tempo > 0) ? state.tempo : null;
    const npTitleEl = document.getElementById('np-title');
    sendOrQueueBridgeMessage({
      type: 'session_data',
      v: 2,
      session_id: state.analysisId || null,
      song: {
        id: state.analysisId || null,
        title: (npTitleEl && npTitleEl.textContent) || null,
      },
      bpm,
      key: keyPayload,
      chord_progression: chords,
      section_markers: sections,
      loop_markers: null,
    });
  }

  // Audio-Ownership Pivot follow-up: mirror play/pause/seek state
  // to Connect so its AudioEngine drives the stems. Cheap (one
  // small JSON frame per transport edge); JAM's tickClock does NOT
  // call this — only the edges (play, pause, seek) do.
  function pushTransportToConnect() {
    const cb = state.connectBridge;
    if (!cb || cb.status !== 'open' || !(cb.peers > 0)) return;
    const playing = !!state.isPlaying;
    const positionS = playing
      ? (typeof currentPlayTime === 'function' ? currentPlayTime() : state.playOffset || 0)
      : (state.playOffset || 0);
    sendOrQueueBridgeMessage({
      type: 'transport_state',
      v: 2,
      playing,
      position_s: positionS,
    });
  }

  function sendOrQueueBridgeMessage(msg) {
    const cb = state.connectBridge;
    if (cb.ws && cb.status === 'open') {
      try { cb.ws.send(JSON.stringify(msg)); }
      catch (e) { console.warn('[connect] send failed:', e); cb.queue.push(msg); }
    } else {
      cb.queue.push(msg);
      ensureConnectBridge();
    }
  }

  function escapeHtml(s) {
    if (s == null) return '';
    return String(s)
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  // -------------------------------------------------------- tone card
  //
  // Tier-aware rendering for the monitor-chain ToneRecommendation.
  // Three visual states map 1:1 to the backend tiers:
  //
  //   high     — pre-applied (we still show "Apply" so the user can
  //              confirm even when we auto-pushed); badge: "High match"
  //   medium   — suggested + alternates; badge: "Suggested match"
  //   low /
  //   unknown  — fallback rationale; badge: "Default chain"
  //
  // The rec passed in is the wire dict produced by
  // `guitar_catalog.to_wire_dict`. Hides the card entirely when null
  // so legacy responses (no `tone` field) leave the slot empty.
  function renderToneCard(rec) {
    const card = $('tone-card');
    if (!card) return;
    if (!rec || state.tone.dismissed) {
      card.hidden = true;
      return;
    }
    const tier = String(rec.tier || 'unknown').toLowerCase();
    const badge = $('tone-tier-badge');
    const title = $('tone-card-title');
    const rationale = $('tone-card-rationale');
    const alternates = $('tone-card-alternates');
    const alternatesList = $('tone-card-alternates-list');

    // Pick the human title. apply.chain_id is always present; match
    // gives us a friendlier chain name when there was a real hit.
    const matchName = rec.match && rec.match.chain_id
      ? toneChainDisplayName(rec.match.chain_id)
      : null;
    const applyName = toneChainDisplayName(rec.apply && rec.apply.chain_id);
    title.textContent = matchName || applyName || 'Default chain';

    // Tier badge text + colour class. CSS handles the palette; we
    // just toggle the modifier.
    const badgeText = ({
      high: 'High match',
      medium: 'Suggested',
      low: 'Low confidence',
      unknown: 'Default',
    })[tier] || 'Default';
    badge.textContent = badgeText;
    badge.className = 'tone-tier-badge tone-tier-badge--' + tier;

    rationale.textContent = rec.rationale || '';

    // Alternates row — only shown when we have any.
    const alts = Array.isArray(rec.alternates) ? rec.alternates : [];
    if (alts.length) {
      alternatesList.innerHTML = '';
      for (const alt of alts) {
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'tone-alternate-chip';
        chip.textContent = toneChainDisplayName(alt.chain_id);
        chip.title = `Distance ${Number(alt.distance || 0).toFixed(2)}`;
        chip.addEventListener('click', () => applyToneChain(alt.chain_id));
        alternatesList.appendChild(chip);
      }
      alternates.hidden = false;
    } else {
      alternates.hidden = true;
    }
    card.hidden = false;
  }

  // Best-effort human label from a chain id. The catalog ids follow
  // the `tfc.<slug>` convention; strip the prefix and title-case.
  function toneChainDisplayName(chainId) {
    if (!chainId) return '';
    const s = String(chainId).replace(/^tfc\./, '');
    return s.split(/[_\-]/).map(p => p ? p[0].toUpperCase() + p.slice(1) : p).join(' ');
  }

  // Push an apply_chain frame over the Connect WS bridge. The backend
  // (`tone_forge_api._handle_apply_chain`) is the source of truth for
  // the positive-label log: it emits `log_applied` after the broadcast
  // succeeds. We don't double-log from the browser.
  //
  // Tracks the request_id in cb.pendingApplies so the WS ack handler
  // can flash a "<chain> applied" toast and update the visible tone
  // label. Before this, Apply was fire-and-forget — the user clicked
  // and got zero visible signal even on a fully successful push.
  function applyToneChain(chainId) {
    if (!chainId) return;
    const cb = state.connectBridge;
    const rid = 'apply_' + Date.now().toString(36);
    const displayName = toneChainDisplayName(chainId) || chainId;
    cb.pendingApplies.set(rid, {
      chainId,
      displayName,
      t0: performance.now(),
    });
    // Pessimistic UX: tell the user we're trying. If the WS is closed,
    // sendOrQueueBridgeMessage queues + reopens the socket, so the
    // "Applying…" message will sit until the socket reconnects and the
    // ack comes back. If the helper is just gone, the message will be
    // overwritten 2.2s later by the regular renderConnectStatus tick.
    flashConnectStatus(`Applying ${displayName}…`);
    console.log(`[connect] apply_chain ${chainId} request_id=${rid}`);
    sendOrQueueBridgeMessage({
      type: 'apply_chain',
      chain_id: chainId,
      request_id: rid,
    });
  }

  // Dismiss handler. POSTs a negative-label event and hides the card
  // for the remainder of this jam (until a new song is loaded).
  async function dismissToneCard(reason) {
    const rec = state.tone.rec;
    state.tone.dismissed = true;
    const card = $('tone-card');
    if (card) card.hidden = true;
    if (!rec || !rec.apply || !rec.apply.chain_id) return;
    try {
      await fetch('/api/tone/ignored', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chain_id: rec.apply.chain_id,
          reason: reason || 'card_closed',
          session_id: state.analysisId || null,
          source_url: state.sourceUrl || null,
        }),
      });
    } catch (err) {
      // Telemetry only — never block the UI on a failed log.
      console.warn('[tone] ignored log failed:', err);
    }
  }

  // -------------------------------------------------------- intake form
  $('intake-form').addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (state.engineStatus !== 'on') return;
    const url = $('intake-url').value.trim();
    if (!url) return;
    const reason = invalidVideoUrl(url);
    if (reason) {
      const errEl = $('intake-error');
      if (errEl) {
        errEl.textContent = reason;
        errEl.hidden = false;
      } else {
        alert(reason);
      }
      return;
    }
    const errEl = $('intake-error');
    if (errEl) errEl.hidden = true;
    state.sourceUrl = url;
    state.userInstrument = $('intake-instrument').value;
    resetTtfj();
    markTtfj('submit', { url });
    enterBandRoom();
  });

  // Reject URLs that yt-dlp can't extract from. Returns a user-facing
  // error string when the URL is invalid, or null when it's good.
  function invalidVideoUrl(url) {
    let parsed;
    try { parsed = new URL(url); }
    catch { return 'That doesn\'t look like a URL. Paste a YouTube video link.'; }
    const host = parsed.hostname.replace(/^www\./, '').toLowerCase();
    const isYouTube = host === 'youtube.com' || host === 'm.youtube.com' || host === 'youtu.be' || host === 'music.youtube.com';
    if (!isYouTube) {
      return 'Only YouTube video URLs are supported right now.';
    }
    // Search results pages can't be downloaded.
    if (parsed.pathname === '/results' || parsed.searchParams.has('search_query')) {
      return 'That\'s a YouTube search-results page. Click into a video and copy its URL.';
    }
    // Channel / playlist landing pages without a video ID.
    if (parsed.pathname.startsWith('/@') || parsed.pathname.startsWith('/channel/') || parsed.pathname.startsWith('/c/')) {
      return 'That\'s a channel URL. Open a video on the channel and copy its URL.';
    }
    // youtu.be/<id>, /watch?v=<id>, /shorts/<id>, /embed/<id>
    const hasVideoId =
      (host === 'youtu.be' && parsed.pathname.length > 1)
      || (parsed.pathname === '/watch' && parsed.searchParams.has('v'))
      || parsed.pathname.startsWith('/shorts/')
      || parsed.pathname.startsWith('/embed/');
    if (!hasVideoId) {
      return 'Couldn\'t find a video ID in that URL. Use a full YouTube video link.';
    }
    return null;
  }

  // -------------------------------------------------------- local engine
  // The local engine is the Demucs-on-Apple-Silicon helper running on
  // :7777. Deep analysis requires it, so the Jam intake gates the submit
  // button on whether the engine is reachable. We poll /health and offer a
  // one-click start that hits the backend's spawn endpoint.
  function setEngineState(status, title, detail) {
    state.engineStatus = status;
    const banner = $('engine-banner');
    banner.classList.remove(
      'engine-banner--unknown',
      'engine-banner--off',
      'engine-banner--starting',
      'engine-banner--on',
    );
    banner.classList.add(`engine-banner--${status}`);
    if (title != null) $('engine-banner-title').textContent = title;
    if (detail != null) $('engine-banner-detail').textContent = detail;

    const startBtn = $('engine-start-btn');
    const stopBtn = $('engine-stop-btn');
    if (status === 'off') {
      startBtn.style.display = '';
      startBtn.disabled = false;
      startBtn.textContent = 'Start engine';
      if (stopBtn) stopBtn.style.display = 'none';
    } else if (status === 'starting') {
      startBtn.style.display = '';
      startBtn.disabled = true;
      startBtn.textContent = 'Starting…';
      if (stopBtn) stopBtn.style.display = 'none';
    } else if (status === 'on') {
      startBtn.style.display = 'none';
      if (stopBtn) {
        stopBtn.style.display = '';
        stopBtn.disabled = false;
        stopBtn.textContent = 'Stop engine';
      }
    } else {
      // 'unknown' or anything else — hide both, banner copy carries the state.
      startBtn.style.display = 'none';
      if (stopBtn) stopBtn.style.display = 'none';
    }

    // Gate the submit button: only enabled when the engine is reachable.
    $('intake-submit').disabled = status !== 'on';
  }

  async function checkEngine() {
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 1500);
      const resp = await fetch(`${LOCAL_ENGINE_URL}/health`, {
        method: 'GET',
        cache: 'no-store',
        signal: ctrl.signal,
      });
      clearTimeout(t);
      if (resp.ok) {
        if (state.engineStatus !== 'on') {
          setEngineState('on', 'Local engine ready', 'Stems will be separated on this machine.');
        }
        return true;
      }
      throw new Error(`HTTP ${resp.status}`);
    } catch (_err) {
      // If we're mid-start, keep that messaging.
      if (state.engineStartInFlight) return false;
      if (state.engineStatus !== 'off') {
        setEngineState(
          'off',
          'Local engine not running',
          'Start it once to enable stem separation and deep analysis.',
        );
      }
      return false;
    }
  }

  $('engine-start-btn').addEventListener('click', async () => {
    if (state.engineStartInFlight) return;
    state.engineStartInFlight = true;
    setEngineState('starting', 'Starting local engine…', 'This usually takes 5–15 seconds.');
    try {
      const resp = await fetch('/api/local-engine/start', { method: 'POST' });
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}${txt ? ': ' + txt : ''}`);
      }
    } catch (err) {
      console.error('[jam] engine start failed:', err);
      state.engineStartInFlight = false;
      setEngineState('off', 'Could not start engine', err.message || 'See server logs for details.');
      return;
    }
    // Poll /health aggressively until it answers or we give up after ~30s.
    const deadline = Date.now() + 30_000;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1000));
      const ok = await checkEngine();
      if (ok) {
        state.engineStartInFlight = false;
        return;
      }
    }
    state.engineStartInFlight = false;
    setEngineState(
      'off',
      'Engine did not respond',
      'The starter ran but /health is still unreachable. Check the server logs.',
    );
  });

  // Stop button — same pattern studio.html uses: POST shutdown directly
  // to the local engine. The connection drops as the engine dies, which
  // is expected; the next /health poll will flip the banner to 'off'.
  $('engine-stop-btn').addEventListener('click', async () => {
    const btn = $('engine-stop-btn');
    btn.disabled = true;
    btn.textContent = 'Stopping…';
    try {
      await fetch(`${LOCAL_ENGINE_URL}/api/engine/shutdown`, { method: 'POST' });
    } catch (_err) {
      // Expected — the socket closes as the engine exits.
    }
    // Give the process a moment to die before re-polling.
    setTimeout(() => {
      checkEngine();
    }, 800);
  });

  // Run once on load, then keep polling so the banner reflects reality
  // if the engine is started or killed externally.
  checkEngine();
  setInterval(() => {
    if (!state.engineStartInFlight) checkEngine();
  }, ENGINE_POLL_MS);

  $('bandroom-cancel').addEventListener('click', () => {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    showView('intake');
  });

  // Tone card buttons. Bound once at startup; the per-jam state lives
  // on `state.tone`.
  const _toneApplyBtn = $('tone-apply-btn');
  if (_toneApplyBtn) {
    _toneApplyBtn.addEventListener('click', () => {
      const rec = state.tone.rec;
      const chainId = rec && rec.apply && rec.apply.chain_id;
      if (chainId) applyToneChain(chainId);
    });
  }
  const _toneDismissBtn = $('tone-dismiss-btn');
  if (_toneDismissBtn) {
    _toneDismissBtn.addEventListener('click', () => {
      dismissToneCard('card_closed');
    });
  }

  $('perform-back').addEventListener('click', () => {
    stopAllStems();
    // Drop decoded buffers so we don't hold ~340 MB across jams.
    for (const stem of state.stems.values()) {
      stem.buffer = null;
      if (stem.gainNode) {
        try { stem.gainNode.disconnect(); } catch (_) {}
      }
    }
    state.stems.clear();
    state.sections = [];
    state.chords = [];
    state.rawChords = { fixed: [], snapped: null };
    // Empty the ribbon DOM so stale pills from the prior song don't
    // bleed into the next one before buildChordRibbon repopulates.
    const _ribbonEl = document.getElementById('chord-ribbon');
    const _stripEl = document.getElementById('chord-ribbon-strip');
    if (_stripEl) _stripEl.innerHTML = '';
    if (_ribbonEl) _ribbonEl.hidden = true;
    // Hide the beat-snap toggle row until the next analysis result
    // brings a fresh `chords_beat_snapped` array.
    const _snapRow = document.getElementById('chord-snap-row');
    if (_snapRow) _snapRow.hidden = true;
    // Reset the chord guidance (now+next) + lead-tab lane + view-mode
    // row so stale content from the prior session doesn't bleed into
    // the next. The view-mode preference itself is intentionally NOT
    // reset (it's a user preference that should survive across jams,
    // same as the beat-snap toggle).
    const _guidance = document.getElementById('chord-guidance');
    if (_guidance) {
      _guidance.hidden = true;
      const _nowSym = document.getElementById('chord-now-symbol');
      const _nowDia = document.getElementById('chord-now-diagram');
      const _countdown = document.getElementById('chord-countdown');
      const _transition = document.getElementById('chord-transition');
      if (_nowSym) _nowSym.textContent = '';
      if (_nowDia) _nowDia.innerHTML = '';
      if (_countdown) _countdown.textContent = '';
      if (_transition) { _transition.textContent = ''; _transition.hidden = true; }
      // Up-next preview cards — clear text + diagram, hide cards.
      for (let i = 1; i <= 2; i++) {
        const _card = document.getElementById(`chord-up-next-${i}`);
        const _sym = document.getElementById(`chord-up-next-${i}-symbol`);
        const _dia = document.getElementById(`chord-up-next-${i}-diagram`);
        if (_card) { _card.hidden = true; _card.onclick = null; }
        if (_sym) _sym.textContent = '';
        if (_dia) _dia.innerHTML = '';
      }
    }
    const _tabLane = document.getElementById('chord-tab-queue');
    if (_tabLane) {
      _tabLane.hidden = true;
      const _tabBody = document.getElementById('chord-tab-body');
      if (_tabBody) _tabBody.innerHTML = '';
    }
    const _overlaysRow = document.getElementById('chord-overlays-row');
    if (_overlaysRow) _overlaysRow.hidden = true;
    // Clear any open voicing popover so it doesn't leak between jams.
    const _vp = document.getElementById('chord-voicing-popover');
    if (_vp) _vp.remove();
    state.leadMidiNotes = [];
    state.duration = 0;
    state.playOffset = 0;
    state.analysisId = null;
    $('t-play').textContent = 'Play';
    // perform v2: stop mic capture if it was running; clear waveform
    // peaks + canvas; reset tone-card state; hide the header monitor
    // popover. We intentionally do NOT touch state.connectBridge —
    // P2k Safari pairing must survive a "New jam" click so the
    // helper stays paired across songs.
    try { stopListening(); } catch (_) {}
    state.waveform = null;
    try {
      const _wfCanvas = document.getElementById('waveform-canvas');
      if (_wfCanvas) {
        const _wfCtx = _wfCanvas.getContext('2d');
        if (_wfCtx) _wfCtx.clearRect(0, 0, _wfCanvas.width, _wfCanvas.height);
      }
    } catch (_) {}
    state.tone = { rec: null, dismissed: false };
    const _toneCard = document.getElementById('tone-card');
    if (_toneCard) _toneCard.hidden = true;
    const _monitorPop = document.getElementById('header-connect-monitor-pop');
    if (_monitorPop) _monitorPop.hidden = true;
    const _connectPill = document.getElementById('header-connect-pill');
    if (_connectPill) _connectPill.setAttribute('aria-expanded', 'false');
    document.body.classList.remove('perform-active');
    // Reset the URL to the bare /jam path. onAnalysisComplete() pushes
    // /jam/:id and the Connect-bridge pairing flow appends
    // ?session=<id>; both should disappear when the user explicitly
    // starts a new jam so a reload doesn't bounce them back into the
    // dead analysis or rejoin a stale Connect channel.
    try {
      window.history.replaceState(null, '', '/jam');
    } catch {}
    showView('intake');
  });

  // ---------------------------------------------- band-room: kick off analysis
  function enterBandRoom() {
    $('bandroom-title').textContent = 'Setting up your band…';
    $('bandroom-status').textContent = 'Downloading audio';
    $('bandroom-bar').style.width = '5%';
    // Reset slot states.
    // Remove any guitar-split slots injected by a previous jam, then
    // reset the static slot states back to Waiting.
    document.querySelectorAll('.bandroom-stage .slot[data-stem^="guitar_"]').forEach(el => el.remove());
    document.querySelectorAll('.slot[data-stem]').forEach(el => {
      if (el.dataset.stem === 'user') return;
      el.classList.remove('ready');
      el.querySelector('.slot-state').textContent = 'Waiting';
    });
    showView('bandroom');
    startSseAnalysis();
  }

  // ---------------------------------------------- SSE analysis
  function startSseAnalysis() {
    // The existing /api/analyze-url-stream endpoint accepts JSON via POST,
    // not EventSource. We fall back to fetch + manual SSE parsing.
    const body = {
      url: state.sourceUrl,
      source_kind: 'auto',
      platform: 'auto',
      fast_mode: false,
      analysis_mode: 'deep',     // forces stem separation
      use_local_engine: true,    // deep requires local engine
      extract_midi: true,
    };

    const controller = new AbortController();
    state.abortController = controller;

    fetch('/api/analyze-url-stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: controller.signal,
    }).then(async (resp) => {
      if (!resp.ok) {
        const txt = await resp.text().catch(() => '');
        throw new Error(`HTTP ${resp.status}: ${txt}`);
      }
      markTtfj('sse_open');
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let firstByteSeen = false;
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (!firstByteSeen) {
          firstByteSeen = true;
          markTtfj('sse_first_byte');
        }
        buffer += decoder.decode(value, { stream: true });
        // SSE events are separated by blank lines
        let idx;
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          handleSseBlock(block);
        }
      }
    }).catch((err) => {
      if (err.name === 'AbortError') return;
      console.error('[jam] analyze stream failed:', err);
      $('bandroom-status').textContent = 'Analysis failed: ' + (err.message || err);
    });
  }

  function handleSseBlock(block) {
    // Backend (analyze-url-stream) embeds the event type in JSON rather than
    // emitting an `event:` header, e.g.
    //   data: {"type": "progress", "message": "...", "percent": 12, "stage": "..."}
    //   data: {"type": "result",   "data": {...}}
    //   data: {"type": "error",    "message": "..."}
    // We also tolerate the standard SSE "event: foo" form for the upload route.
    let eventName = 'message';
    const dataLines = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
    }
    const dataStr = dataLines.join('\n');
    let payload = null;
    if (dataStr) {
      try { payload = JSON.parse(dataStr); } catch (e) { payload = { raw: dataStr }; }
    }
    const resolvedName = (payload && payload.type) || eventName;
    onSseEvent(resolvedName, payload);
  }

  function onSseEvent(name, data) {
    if (name === 'progress' && data) {
      if (typeof data.percent === 'number') {
        const pct = Math.min(100, Math.max(2, Math.round(data.percent)));
        $('bandroom-bar').style.width = pct + '%';
      } else if (typeof data.progress === 'number') {
        const pct = Math.min(100, Math.max(2, Math.round(data.progress * 100)));
        $('bandroom-bar').style.width = pct + '%';
      }
      const label = data.stage ? humanStage(data.stage) : data.message;
      if (label) $('bandroom-status').textContent = label;
      // Mark each pipeline stage the first time we see it.
      if (data.stage && !state.ttfj.stages.has(data.stage)) {
        state.ttfj.stages.add(data.stage);
        markTtfj(`stage:${data.stage}`, { percent: data.percent });
      }
      return;
    }

    if (name === 'stems_partial' && data) {
      markTtfj('stems_partial_received');
      const records = Array.isArray(data.stem_records) && data.stem_records.length
        ? data.stem_records
        : legacyStemsToRecords(data.stems || {});
      applyStemRecords(records);
      // Kick off audio fetch + decode immediately, in parallel with the
      // rest of the analysis pipeline. Don't await — when onAnalysisComplete
      // later calls prepareStemAudio() it'll skip already-decoded stems.
      prepareStemAudio()
        .then(() => markTtfj('partial_audio_ready', { stems: state.stems.size }))
        .catch((err) => console.warn('[jam] partial decode failed:', err));
      return;
    }

    if (name === 'result' && data) {
      markTtfj('result_received');
      const result = data.data || data;
      onAnalysisComplete(result);
      return;
    }

    if (name === 'error') {
      const msg = (data && (data.message || data.error || data.detail)) || 'unknown';
      console.error('[jam] sse error:', data);
      $('bandroom-status').textContent = 'Error: ' + msg;
    }
  }

  function humanStage(stage) {
    const map = {
      download: 'Downloading audio',
      separate: 'Separating stems',
      separation: 'Separating stems',
      stems: 'Separating stems',
      midi: 'Extracting MIDI',
      analyze: 'Analyzing tone',
      tone: 'Building your tone',
      preset: 'Matching presets',
      complete: 'Ready',
    };
    return map[stage.toLowerCase()] || stage;
  }

  // Roles whose band-room slot is hardcoded in jam.html. When a record
  // has any other role (or any new pan-split sub-role like LEAD/
  // RHYTHM/TEXTURE), markStemReady will inject a slot for it.
  const HARDCODED_BANDROOM_ROLES = new Set(['drums', 'bass', 'vocals', 'keys']);

  // Per-role icon used when injecting a band-room slot.
  const ROLE_ICON = {
    drums: '🥁', bass: '🎸', vocals: '🎤', keys: '🎹',
    lead: '🎸', rhythm: '🎸', texture: '🎸', harmonic: '🎸',
    unknown: '🎶',
  };

  function ensureBandroomSlot(id) {
    const stem = state.stems.get(id);
    if (!stem) return null;
    let slot = document.querySelector(`.slot[data-stem="${cssEscape(id)}"]`);
    if (slot) return slot;
    // Match the static slot by role (e.g. role=drums uses the hardcoded
    // drums tile). Only inject a new tile if no matching hardcoded one
    // exists for this role.
    if (HARDCODED_BANDROOM_ROLES.has(stem.role)) {
      slot = document.querySelector(`.slot[data-stem="${stem.role}"]`);
      if (slot) return slot;
    }
    const stage = document.querySelector('.bandroom-stage');
    if (!stage) return null;
    slot = document.createElement('div');
    slot.className = 'slot';
    slot.dataset.stem = id;
    slot.innerHTML = `
      <div class="slot-icon">${ROLE_ICON[stem.role] || '🎶'}</div>
      <div class="slot-name">${stem.displayName}</div>
      <div class="slot-state">Waiting</div>
    `;
    stage.appendChild(slot);
    return slot;
  }

  function markStemReady(id) {
    const slot = ensureBandroomSlot(id);
    if (!slot) return;
    if (slot.classList.contains('ready')) return;
    slot.classList.add('ready');
    slot.querySelector('.slot-state').textContent = 'Ready';
  }

  function cssEscape(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, '_'); }

  // Merge a stem_records list into state.stems. Preserves decoded buffers
  // and gain nodes for stems whose id is in both the old and new sets, so
  // an early `stems_partial` event followed by a final `result` doesn't
  // re-fetch what's already in memory. Stems no longer present (e.g.
  // `demucs.other` replaced by pan-split children) are disconnected and
  // dropped.
  function applyStemRecords(records) {
    const nextIds = new Set();
    for (const rec of records) {
      const id = rec.id;
      if (!id) continue;
      nextIds.add(id);
      const existing = state.stems.get(id);
      if (existing) {
        // Update metadata in case the final result refined the role /
        // display name; keep audio state untouched.
        existing.role = rec.role;
        existing.displayName = rec.display_name || rec.displayName || existing.displayName;
        existing.url = rec.audio_url || rec.audioUrl || existing.url;
        existing.parentId = rec.parent_id || rec.parentId || existing.parentId;
        existing.provider = rec.provider || existing.provider;
        existing.confidence = rec.confidence ?? existing.confidence;
      } else {
        state.stems.set(id, {
          id,
          role: rec.role,
          displayName: rec.display_name || rec.displayName || id,
          url: rec.audio_url || rec.audioUrl,
          parentId: rec.parent_id || rec.parentId || null,
          provider: rec.provider || 'unknown',
          confidence: rec.confidence ?? 1.0,
          buffer: null,
          gainNode: null,
          source: null,
          muted: false,
          lastGain: 1.0,
          loadPromise: null,
        });
      }
      markStemReady(id);
    }
    // Drop stems that aren't in the new set anymore (e.g. demucs.other
    // when pan-split children take over).
    for (const id of [...state.stems.keys()]) {
      if (nextIds.has(id)) continue;
      const stem = state.stems.get(id);
      if (stem && stem.gainNode) {
        try { stem.gainNode.disconnect(); } catch {}
      }
      state.stems.delete(id);
      // Also remove its band-room slot if it was rendered.
      const slot = document.querySelector(`.bandroom-stage .slot[data-stem="${cssEscape(id)}"]`);
      if (slot) slot.remove();
    }
  }

  // ---------------------------------------------- transition to performance
  async function onAnalysisComplete(result) {
    state.fullResult = result;
    // The streaming endpoint attaches the persisted identifier under
    // ``history_id`` (see tone_forge_api.py); the deep-link / bundle
    // path uses ``analysis_id`` / ``id``. Accept all three so a fresh
    // analysis updates the URL bar the same way a reloaded one does.
    state.analysisId =
      result.analysis_id || result.id || result.history_id || null;

    // Push the analysis id into the URL so a reload restores the jam
    // via the /jam/:id deep-link path (see maybeDeepLink IIFE below).
    // replaceState — we don't want a back-button entry per analysis.
    if (state.analysisId && !/^\/jam\/[^\/]+$/.test(window.location.pathname)) {
      try {
        window.history.replaceState(null, '', `/jam/${state.analysisId}`);
      } catch {}
    }

    // Stem records — the provider-agnostic Stem[] from the session
    // engine (see backend/tone_forge/stem_model.py). Each record carries
    // its own id / role / displayName so this file is no longer coupled
    // to Demucs-shaped names like "other" / "guitar_lead".
    //
    // Legacy fallback: older server builds only emit ``stems_paths``
    // (name-keyed dict). Convert it to the same record shape so
    // downstream code can stay uniform.
    const stemRecords = Array.isArray(result.stem_records) && result.stem_records.length
      ? result.stem_records
      : legacyStemsToRecords(result.stems_paths || {});

    // Merge instead of clear+rebuild so any stems already decoded from an
    // earlier `stems_partial` event keep their AudioBuffers.
    applyStemRecords(stemRecords);
    // Drive the progress bar all the way to 100 so the user sees the
    // band-room hit "Ready" before we cross-fade to the perform view.
    $('bandroom-bar').style.width = '100%';
    $('bandroom-status').textContent = 'Ready';

    // Title / meta
    //
    // Tempo / key live in different fields depending on which analysis
    // pipeline produced the result. Walk a small list of known locations.
    // Treat a tempo of exactly 60.0 as "no real estimate" — that's the
    // ensemble-extractor default when it can't lock onto a beat.
    const rawTempo =
        result.tempo_bpm
        ?? result.tempo
        ?? result.midi?.tempo
        ?? result.midi_stats?.tempo
        ?? null;
    const tempo = rawTempo && Math.abs(rawTempo - 60.0) > 0.01 ? rawTempo : null;
    const key = result.detected_key
      || result.key
      || result.descriptor?.detected_key
      || (result.synth && result.synth.descriptor && result.synth.descriptor.detected_key)
      || '—';
    const title = result.title || result.source_name || result.source_title || extractYouTubeId(state.sourceUrl) || 'Untitled jam';
    $('np-title').textContent = title;
    $('np-meta').textContent = `${tempo ? Math.round(tempo) + ' bpm' : '— bpm'} · ${key}`;

    // Parse the key string into a pitch-class set for intonation scoring.
    state.songKey = parseDetectedKey(key);
    applyFeedbackView();
    // Refresh the perform-view "Audio in" row label.
    try { refreshAudioInName(); } catch (_) {}
    if (state.settings.listenEnabled) startListening().catch(err => {
      console.warn('Mic capture failed on result load:', err);
    });

    // Click track — store beat times for the look-ahead scheduler.
    state.beatTimes = Array.isArray(result.beat_times) ? result.beat_times : [];
    // Disable click button if the engine didn't return a beat grid.
    const clickBtn = $('t-click');
    if (clickBtn) {
      clickBtn.disabled = state.beatTimes.length === 0;
      clickBtn.textContent = state.beatTimes.length === 0 ? 'Click: n/a' : 'Click: off';
    }

    // Tone — pull the user-instrument's match (role-aware lookup).
    state.presetMatches = result.preset_matches || {};
    const userMatch = findUserPresetMatch(state.presetMatches);
    if (userMatch) {
      $('user-tone-name').textContent = userMatch.preset_name || 'Matched tone';
      $('user-tone-meta').textContent = formatPresetMatchMeta(userMatch);
      // Push the matched preset to the Connect bridge so the desktop app
      // can swap its amp-sim chain. Fire-and-forget — Connect may not be
      // running yet.
      pushPresetToConnect({
        analysis_id: state.analysisId,
        source_url: state.sourceUrl,
        instrument: state.userInstrument,
        match: userMatch,
      });
    } else if (result.tone) {
      // The legacy preset_matches table didn't hit, but the new
      // ToneRecommendation pipeline did. Reflect the recommendation
      // in the headline so it doesn't contradict the card body below,
      // which is about to render via renderToneCard().
      const recName = (result.tone.match && result.tone.match.chain_id
        && toneChainDisplayName(result.tone.match.chain_id))
        || (result.tone.apply && result.tone.apply.chain_id
          && toneChainDisplayName(result.tone.apply.chain_id))
        || 'Recommended tone';
      $('user-tone-name').textContent = recName;
      $('user-tone-meta').textContent = 'Apply below to push to Connect';
    } else {
      $('user-tone-name').textContent = 'No tone match yet';
      $('user-tone-meta').textContent = 'Default monitoring preset will be used';
    }

    // Phase-1 monitor-chain Tone card. `result.tone` is the wire form
    // of a ToneRecommendation. Renders the tier/title/rationale and
    // wires Apply -> Connect bridge / Dismiss -> /api/tone/ignored.
    state.tone.dismissed = false;
    state.tone.rec = result.tone || null;
    renderToneCard(state.tone.rec);

    // Build the stem rack rows
    buildStemRack();
    buildSectionBar(result.sections || []);
    // JAM Alpha visual reference. Top-level result.chords is the
    // shape AnalysisResult.to_dict() emits; empty/missing renders
    // nothing (ribbon stays hidden), so legacy responses without a
    // chord lane don't show an empty strip.
    //
    // Phase 6: result.chords_beat_snapped is the same regions with
    // boundaries snapped to nearest beat. Cache both and let the
    // toggle pick at render time.
    state.rawChords = {
      fixed: result.chords || [],
      snapped: result.chords_beat_snapped || null,
    };
    // Phase 4 (D4): once every post-analysis field is populated
    // (songKey, beatTimes, sections via buildSectionBar, rawChords
    // above), push the session-data v2 frame to Connect. Gated on
    // paired-Connect inside pushSessionDataToConnect; server caches
    // it for late-joiners. Fire-and-forget — no UI dependency on
    // Connect ack.
    try { pushSessionDataToConnect(); } catch (_) {}
    // Cache lead-line MIDI notes for the tab lane. Preference order:
    //   1. result.midi_stems[guitar|other|piano] — the live-analyze
    //      path carries per-stem MIDI keyed by stem name. We prefer
    //      guitar (lead-flavour on most rock catalogue); 'other' (the
    //      demucs catch-all) and 'piano' are fallbacks so legacy
    //      pipelines without a guitar stem still surface something.
    //   2. result.midi.notes — bundle-restore path: user_midi.notes
    //      (whatever stem the user's selected role mapped to).
    // An empty array makes the tab lane render its "(no lead notes
    // for this region)" placeholder, which is the correct degenerate
    // behaviour rather than throwing.
    state.leadMidiNotes = _pickLeadMidiNotes(result);
    syncChordSnapToggleVisibility();
    buildChordRibbon(activeChordArray());
    syncChordViewModeRowVisibility();
    syncChordOverlaysVisibility();
    // Prime the guidance + tab panels at index 0 so the user sees a
    // populated NOW PLAYING / NEXT UP / LEAD PART surface immediately
    // after analysis completes — without this, both panels stay empty
    // until the playhead crosses the first chord boundary.
    if (Array.isArray(state.chords) && state.chords.length > 0) {
      _onActiveChordChanged(0);
    }

    // Preload audio so press-play is instant
    await prepareStemAudio();
    markTtfj('audio_ready', { stems: state.stems.size });

    showView('perform');
    // The browser is the session "owner"; open the Connect bridge so
    // the helper has a channel to join even if no preset has been
    // pushed yet. The push has already happened above on success.
    primeConnectBridge();
    const totalMs = Math.round(performance.now() - state.ttfj.t0);
    markTtfj('playback_ready');
    reportTtfj(totalMs);
  }

  function extractYouTubeId(url) {
    if (!url) return null;
    const m = url.match(/(?:v=|youtu\.be\/)([A-Za-z0-9_-]{6,})/);
    return m ? m[1] : null;
  }

  function buildStemRack() {
    const rack = $('stem-rack');
    rack.innerHTML = '';

    // Two synthetic mixer-channel rows precede the per-stem rows:
    //   * Song master  -- routes state.masterGain
    //   * Guitar input -- routes state.monitor.gainNode (mirrors the
    //                     standalone monitor panel above)
    // Both participate in the shared solo group via _applyAllSoloMutes.
    // The Guitar Input row carries `.mixer-channel-divider` so the CSS
    // can draw a visual separator between the master channels and the
    // per-stem channels.
    rack.appendChild(_buildMixerChannelRow('song', 'Song', false));
    rack.appendChild(_buildMixerChannelRow('guitar', 'Guitar input', true));

    // Order by stem.role using the global ROLE_ORDER. Unknown roles
    // sort to the end. Ties (multiple guitar parts with the same role
    // role) preserve insertion order.
    const sorted = [...state.stems.entries()].sort(([, a], [, b]) => {
      const ai = ROLE_ORDER.indexOf(a.role);
      const bi = ROLE_ORDER.indexOf(b.role);
      return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
    });
    for (const [id, stem] of sorted) {
      const row = document.createElement('div');
      row.className = 'stem-row';
      const isUser = stemIsUserInstrument(stem);
      const match = findPresetMatchForStem(stem, state.presetMatches || {});
      const matchHtml = match
        ? `<div class="stem-match" title="${formatPresetMatchMeta(match)}">${escapeHtml(match.preset_name || '')}</div>`
        : '';
      row.innerHTML = `
        <div class="name ${isUser ? 'original-instrument' : ''}">${escapeHtml(stem.displayName)}${matchHtml}</div>
        <input type="range" class="gain" min="0" max="1" step="0.01" value="${isUser ? 0.7 : 1.0}" data-stem="${id}" />
        <button class="ghost mute-btn" data-stem="${id}">Mute</button>
        <button class="ghost solo-btn" data-stem="${id}">Solo</button>
      `;
      rack.appendChild(row);
    }
    // Stem-row controls
    rack.querySelectorAll('.gain[data-stem]').forEach(el =>
      el.addEventListener('input', () => setStemGain(el.dataset.stem, parseFloat(el.value)))
    );
    rack.querySelectorAll('.mute-btn[data-stem]').forEach(btn =>
      btn.addEventListener('click', () => toggleMute(btn.dataset.stem, btn))
    );
    rack.querySelectorAll('.solo-btn[data-stem]').forEach(btn =>
      btn.addEventListener('click', () => toggleSolo(btn.dataset.stem, btn))
    );
    // Channel-row controls (song / guitar) — share toggleMute/Solo
    // but route gain changes to the appropriate setter so the master
    // node and monitor node each get the slider value live.
    rack.querySelectorAll('.gain[data-channel]').forEach(el =>
      el.addEventListener('input', () => {
        const v = parseFloat(el.value);
        if (!isFinite(v)) return;
        if (el.dataset.channel === 'song') setSongGain(v);
        else if (el.dataset.channel === 'guitar') _setMonitorGain(v);
      })
    );
    rack.querySelectorAll('.mute-btn[data-channel]').forEach(btn =>
      btn.addEventListener('click', () => toggleMute(btn.dataset.channel, btn))
    );
    rack.querySelectorAll('.solo-btn[data-channel]').forEach(btn =>
      btn.addEventListener('click', () => toggleSolo(btn.dataset.channel, btn))
    );
  }

  // Build one synthetic mixer-channel row (Song master or Guitar
  // input). Rendered using the same .stem-row layout as the per-stem
  // rows so the CSS grid stays uniform. Min/max/step on the gain
  // slider matches the underlying node: master is [0, 1.5] and
  // monitor is [0, 1.5]; both default to the live state value when
  // present so a re-render mid-session doesn't snap the fader.
  function _buildMixerChannelRow(channelId, displayName, withDivider) {
    const row = document.createElement('div');
    row.className = `stem-row mixer-channel-row mixer-channel-${channelId}`;
    if (withDivider) row.classList.add('mixer-channel-divider');
    let initialGain, muted;
    if (channelId === 'song') {
      initialGain = state.songMix.gain;
      muted = state.songMix.muted;
    } else {
      // 'guitar' — mirrors the monitor panel
      initialGain = state.monitor.gain;
      muted = state.monitor.muted;
    }
    row.innerHTML = `
      <div class="name mixer-channel-name">${escapeHtml(displayName)}</div>
      <input type="range" class="gain" min="0" max="1.5" step="0.01" value="${initialGain.toFixed(2)}" data-channel="${channelId}" />
      <button class="ghost mute-btn" data-channel="${channelId}">${muted ? 'Muted' : 'Mute'}</button>
      <button class="ghost solo-btn" data-channel="${channelId}">Solo</button>
    `;
    return row;
  }

  function buildSectionBar(sections) {
    const bar = $('section-bar');
    bar.innerHTML = '';
    state.sections = sections.map((s) => {
      const pill = document.createElement('button');
      pill.className = 'section-pill';
      // Sections come back as {type, start_time, end_time} from the backend
      // (see ArrangementSection.to_dict in tone_forge/analysis/sections.py).
      // Tolerate the older {name, start, end} shape as a fallback.
      const label = s.type || s.name || s.label || 'Section';
      const start = secondsOf(s.start_time ?? s.start ?? s.start_sec ?? s.startSec);
      const end = secondsOf(s.end_time ?? s.end ?? s.end_sec ?? s.endSec);
      pill.textContent = `${label} ${formatTime(start)}`;
      pill.addEventListener('click', () => toggleSectionLoop({ name: label, startSec: start, endSec: end, el: pill }));
      bar.appendChild(pill);
      return { name: label, startSec: start, endSec: end, el: pill };
    });
    // Fallback: when section detection returns nothing (some short
    // clips or noisy mixes), still give the user a single full-song
    // pill so the loop UX (click pill → A/B region loop) is reachable.
    if (!state.sections.length) {
      const dur = secondsOf(state.duration) || 0;
      if (dur > 0) {
        const pill = document.createElement('button');
        pill.className = 'section-pill';
        pill.textContent = `Full song ${formatTime(0)}`;
        pill.addEventListener('click', () => toggleSectionLoop({ name: 'Full song', startSec: 0, endSec: dur, el: pill }));
        bar.appendChild(pill);
        state.sections = [{ name: 'Full song', startSec: 0, endSec: dur, el: pill }];
      } else {
        bar.innerHTML = '<div style="color: var(--text-dim); font-size:13px;">Section detection unavailable for this song</div>';
      }
    }
  }

  // ---------------------------------------------- chord ribbon (JAM Alpha)
  //
  // Beat-snap toggle: switches the rendered chord ribbon between the
  // fixed-window grid (result.chords) and the beat-snapped grid
  // (result.chords_beat_snapped). Both arrays are produced by the
  // analysis pipeline; the toggle is a render-time choice persisted
  // to localStorage so the user's preference survives reloads.
  const CHORD_BEAT_SNAP_PREF_KEY = 'toneforge.jam.chordBeatSnap';
  try {
    state.chordBeatSnap = localStorage.getItem(CHORD_BEAT_SNAP_PREF_KEY) === '1';
  } catch (_) {
    // localStorage may throw in private-mode Safari; default stays
    // false which matches the safer (higher-WCSR) view.
  }

  function activeChordArray() {
    const raw = state.rawChords || { fixed: [], snapped: null };
    if (state.chordBeatSnap && Array.isArray(raw.snapped) && raw.snapped.length > 0) {
      return raw.snapped;
    }
    return raw.fixed || [];
  }

  function syncChordSnapToggleVisibility() {
    const row = document.getElementById('chord-snap-row');
    const cb = document.getElementById('chord-snap-toggle');
    if (!row || !cb) return;
    const raw = state.rawChords || { fixed: [], snapped: null };
    const hasSnapped = Array.isArray(raw.snapped) && raw.snapped.length > 0;
    const hasFixed = Array.isArray(raw.fixed) && raw.fixed.length > 0;
    // Only surface the toggle when there's a meaningful choice: both
    // a chord lane to show AND a beat-snapped alternative.
    row.hidden = !(hasFixed && hasSnapped);
    cb.checked = !!state.chordBeatSnap;
  }

  // Wire the toggle once at module load. The handler flips the
  // preference, persists it, and re-renders the ribbon from the
  // cached arrays without touching the network.
  (function wireChordSnapToggle() {
    const cb = document.getElementById('chord-snap-toggle');
    if (!cb) return;
    cb.checked = !!state.chordBeatSnap;
    cb.addEventListener('change', () => {
      state.chordBeatSnap = !!cb.checked;
      try { localStorage.setItem(CHORD_BEAT_SNAP_PREF_KEY, state.chordBeatSnap ? '1' : '0'); } catch (_) {}
      buildChordRibbon(activeChordArray());
    });
  })();

  // -------------------------------------- chord diagrams + lead tab (Phase C)
  // View mode persisted to localStorage so the user's choice survives
  // reloads. Default is 'diagrams' — the common case for a guitarist
  // who wants visual fingerings under the chord ribbon.
  const CHORD_VIEW_MODE_PREF_KEY = 'toneforge.jam.chordViewMode';
  const VALID_CHORD_VIEW_MODES = new Set(['diagrams', 'tab', 'both']);
  const VOICING_PREFS_KEY = 'toneforge.jam.voicings';
  const CHORD_OVERLAYS_KEY = 'toneforge.jam.overlays';
  try {
    const stored = localStorage.getItem(CHORD_VIEW_MODE_PREF_KEY);
    if (stored && VALID_CHORD_VIEW_MODES.has(stored)) {
      state.chordViewMode = stored;
    }
    const voicingStr = localStorage.getItem(VOICING_PREFS_KEY);
    if (voicingStr) {
      const parsed = JSON.parse(voicingStr);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        state.voicingPrefs = parsed;
      }
    }
    const overlayStr = localStorage.getItem(CHORD_OVERLAYS_KEY);
    if (overlayStr) {
      const parsed = JSON.parse(overlayStr);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
        state.chordOverlays = {
          nashville: !!parsed.nashville,
          roman: !!parsed.roman,
          scales: !!parsed.scales,
        };
      }
    }
  } catch (_) {
    // localStorage may throw in private-mode Safari; default stays
    // 'diagrams'.
  }

  // Walk the analysis result and return the best lead-line MIDI
  // notes array we can find. See the caller comment in
  // onAnalysisComplete for the preference order.
  function _pickLeadMidiNotes(r) {
    if (!r) return [];
    const stems = r.midi_stems;
    if (stems && typeof stems === 'object') {
      for (const key of ['guitar', 'other', 'piano']) {
        const stem = stems[key];
        if (!stem) continue;
        if (Array.isArray(stem.notes) && stem.notes.length > 0) {
          return stem.notes;
        }
        // Some per-stem extractor outputs nest notes inside ``content``
        // (PrettyMIDI dict shape from tone_forge_api.py:2055).
        if (stem.content && Array.isArray(stem.content.notes) && stem.content.notes.length > 0) {
          return stem.content.notes;
        }
      }
    }
    if (r.midi && Array.isArray(r.midi.notes)) {
      return r.midi.notes;
    }
    return [];
  }

  // Lazy-import the renderer module + shape registry. Idempotent —
  // resolves to the cached pair after the first successful load. If
  // either resource fails (404, parse error) we return null and the
  // queue render falls back to symbol-only text in each slot.
  async function ensureChordDiagramAssets() {
    if (state.chordDiagramModule && state.chordShapeRegistry) {
      return { mod: state.chordDiagramModule, registry: state.chordShapeRegistry };
    }
    try {
      if (!state.chordDiagramModule) {
        state.chordDiagramModule = await import('/static/chord_diagrams.js');
      }
      if (!state.chordShapeRegistry) {
        state.chordShapeRegistry = await state.chordDiagramModule.loadChordShapes();
      }
      return { mod: state.chordDiagramModule, registry: state.chordShapeRegistry };
    } catch (err) {
      try { console.warn('[jam] chord diagram assets failed to load', err); } catch (_) {}
      return null;
    }
  }

  // Hide / show the diagram queue + tab lane according to the
  // current view mode. Both lanes are also gated on whether chord
  // data is present at all — when state.chords is empty (or the
  // ribbon is hidden), nothing chord-derived should be visible.
  function syncChordViewModeRowVisibility() {
    // Old radio toggle ("Now+Next / Lead tab / Both") removed per the
    // chord-guidance UX directive (§3, §9): both panels are always
    // visible when chord data exists. The LEAD PART panel renders an
    // explanatory placeholder ("No lead phrase detected …") rather
    // than hiding itself, so the surface is never blank.
    const guidance = document.getElementById('chord-guidance');
    const tab = document.getElementById('chord-tab-queue');
    if (!guidance || !tab) return;
    const hasChords = Array.isArray(state.chords) && state.chords.length > 0;
    guidance.hidden = !hasChords;
    tab.hidden = !hasChords;
  }

  // Wire the view-mode radios. Mirrors wireChordSnapToggle: handler
  // updates state, persists to localStorage, re-applies visibility,
  // and forces a queue / tab re-render so the visible lane reflects
  // the active chord immediately rather than waiting for the next
  // playhead tick.
  (function wireChordViewModeRadios() {
    const row = document.getElementById('chord-view-mode-row');
    if (!row) return;
    const radios = row.querySelectorAll('input[name="chord-view-mode"]');
    radios.forEach((r) => {
      r.addEventListener('change', () => {
        if (!r.checked) return;
        if (!VALID_CHORD_VIEW_MODES.has(r.value)) return;
        state.chordViewMode = r.value;
        try { localStorage.setItem(CHORD_VIEW_MODE_PREF_KEY, state.chordViewMode); } catch (_) {}
        syncChordViewModeRowVisibility();
        // Force a re-render at the current active index so the lane
        // populates immediately rather than waiting for the chord
        // boundary to cross (which on a long pill could be many
        // seconds away).
        const idx = _chordLastActiveIdx >= 0
          ? _chordLastActiveIdx
          : 0;
        _renderChordGuidance(idx);
        _renderLeadTabLane(idx);
      });
    });
  })();

  // Render the NOW PLAYING focal card. The prior NEXT UP peripheral
  // row was removed because the upcoming chords are already visible
  // in the ribbon strip above; the countdown + transition hint that
  // used to live in the NEXT card are now updated in the now-block's
  // footer via _updateChordCountdown / _updateChordTransitionHint.
  function _renderChordGuidance(activeIdx) {
    const guidance = document.getElementById('chord-guidance');
    if (!guidance) return;
    if (!Array.isArray(state.chords) || state.chords.length === 0) return;

    const nowSymbolEl = document.getElementById('chord-now-symbol');
    const nowDiagramEl = document.getElementById('chord-now-diagram');
    if (!nowSymbolEl || !nowDiagramEl) return;

    const chords = state.chords;
    const nowChord = (activeIdx >= 0 && activeIdx < chords.length) ? chords[activeIdx] : null;
    const nowSymbol = nowChord ? (nowChord.symbol || '?') : '—';

    // Update the now-block text label immediately so the chord name
    // refreshes even before the SVG renderer has loaded.
    nowSymbolEl.textContent = nowSymbol;
    // Overlays (§8) render synchronously off state.chordOverlays so
    // their labels update at the same moment as the chord symbol.
    _applyOverlaysToNowBlock(nowSymbol);

    // Asset load is async; if it isn't ready yet, fire-and-forget
    // and let the next chord-change tick repaint. We still update
    // the text label so the user always sees the current chord.
    ensureChordDiagramAssets().then((assets) => {
      // NOW PLAYING — large diagram, no in-SVG title (the symbol text
      // above is the title surface). highlighted=true so .is-active
      // classes on SVG children pick up the accent palette.
      nowDiagramEl.innerHTML = '';
      // The NOW PLAYING diagram is a click target (§4) — wire the
      // cursor + handler once per render so the voicing picker opens
      // anchored to chord-now-block.
      nowDiagramEl.classList.add('is-clickable');
      nowDiagramEl.title = `${nowSymbol} — click to change voicing`;
      nowDiagramEl.onclick = () => {
        if (nowSymbol && nowSymbol !== '—') _openVoicingPicker(nowSymbol);
      };
      if (nowChord) {
        if (assets && assets.mod && assets.registry) {
          const shape = _resolveShapeForSymbol(nowSymbol);
          const svg = assets.mod.renderChordDiagramSVG(
            nowSymbol, shape,
            { highlighted: true, hideTitle: true },
          );
          if (svg) nowDiagramEl.appendChild(svg);
          else {
            const txt = document.createElement('div');
            txt.className = 'chord-diagram-empty';
            txt.textContent = nowSymbol;
            nowDiagramEl.appendChild(txt);
          }
        } else {
          const txt = document.createElement('div');
          txt.className = 'chord-diagram-empty';
          txt.textContent = nowSymbol;
          nowDiagramEl.appendChild(txt);
        }
      }

      // UP NEXT / THEN — two preview cards for activeIdx+1, +2. Each
      // is a click-to-seek <button>; rendered with the same registry
      // + voicing prefs as NOW PLAYING but at the non-highlighted
      // (dim) palette since they're peripheral. Hidden when there's
      // no further chord at that index (end of song).
      _renderUpNextSlot(1, chords, activeIdx + 1, assets);
      _renderUpNextSlot(2, chords, activeIdx + 2, assets);
    });

    // Transition guidance (best-effort one-liner) — sits below the
    // countdown. Updated on every active-chord change since the
    // shape pair only changes at chord boundaries.
    _updateChordTransitionHint(activeIdx);
  }

  // Render one of the two up-next preview cards (slotNumber in {1, 2}).
  // Hides the card when `chordIdx` is out of range (end of song).
  // Wires a click-to-seek handler on each render so the closure
  // captures the current target chord's startSec.
  function _renderUpNextSlot(slotNumber, chords, chordIdx, assets) {
    const cardEl = document.getElementById(`chord-up-next-${slotNumber}`);
    const symbolEl = document.getElementById(`chord-up-next-${slotNumber}-symbol`);
    const diagramEl = document.getElementById(`chord-up-next-${slotNumber}-diagram`);
    if (!cardEl || !symbolEl || !diagramEl) return;

    if (chordIdx < 0 || chordIdx >= chords.length) {
      cardEl.hidden = true;
      symbolEl.textContent = '';
      diagramEl.innerHTML = '';
      cardEl.onclick = null;
      return;
    }

    const chord = chords[chordIdx];
    const symbol = chord.symbol || '?';
    cardEl.hidden = false;
    symbolEl.textContent = symbol;
    cardEl.title = `Jump to ${symbol} (${formatTime(chord.startSec)})`;
    const _seekTo = chord.startSec;
    cardEl.onclick = () => seekAll(_seekTo);

    diagramEl.innerHTML = '';
    if (assets && assets.mod && assets.registry) {
      const shape = _resolveShapeForSymbol(symbol);
      const svg = assets.mod.renderChordDiagramSVG(
        symbol, shape,
        { highlighted: false, hideTitle: true },
      );
      if (svg) {
        diagramEl.appendChild(svg);
      } else {
        const txt = document.createElement('div');
        txt.className = 'chord-diagram-empty';
        txt.textContent = symbol;
        diagramEl.appendChild(txt);
      }
    } else {
      const txt = document.createElement('div');
      txt.className = 'chord-diagram-empty';
      txt.textContent = symbol;
      diagramEl.appendChild(txt);
    }
  }

  // Find the section that contains time `t`. Falls back to null when
  // no sections are defined; callers should degrade to the per-chord
  // window in that case.
  function _findSectionAt(t) {
    if (!Array.isArray(state.sections) || state.sections.length === 0) return null;
    for (const s of state.sections) {
      const s0 = typeof s.startSec === 'number' ? s.startSec : null;
      const s1 = typeof s.endSec === 'number' ? s.endSec : null;
      if (s0 === null || s1 === null) continue;
      if (t >= s0 && t < s1) return s;
    }
    return null;
  }

  // Tiny one-line transition hints comparing the curated shape of the
  // current chord against the next chord. The full transition-hint
  // problem is rich (which fingers move, hand position, voice
  // leading) — this is a best-effort heuristic that covers the most
  // common useful cases without overpromising.
  function _computeTransitionHint(nowSymbol, nextSymbol) {
    if (!nowSymbol || !nextSymbol || nowSymbol === nextSymbol) return '';
    if (!state.chordDiagramModule || !state.chordShapeRegistry) return '';
    const mod = state.chordDiagramModule;
    const reg = state.chordShapeRegistry;
    let nowShape = null;
    let nextShape = null;
    try {
      nowShape = mod.lookupShape(nowSymbol, reg);
      nextShape = mod.lookupShape(nextSymbol, reg);
    } catch (_) { return ''; }
    if (!nowShape || !nextShape) return '';
    const a = nowShape.frets;
    const b = nextShape.frets;
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== 6 || b.length !== 6) return '';

    // Same shape shifted: every non-muted string differs by the same
    // positive offset. Common for barre-chord movements.
    const deltas = [];
    let sameStructure = true;
    for (let i = 0; i < 6; i++) {
      const aMute = a[i] < 0;
      const bMute = b[i] < 0;
      if (aMute !== bMute) { sameStructure = false; break; }
      if (aMute) continue;
      deltas.push(b[i] - a[i]);
    }
    if (sameStructure && deltas.length > 0
        && deltas.every((d) => d === deltas[0]) && deltas[0] !== 0) {
      const sign = deltas[0] > 0 ? '+' : '';
      const word = Math.abs(deltas[0]) === 1 ? 'fret' : 'frets';
      return `Same shape ${sign}${deltas[0]} ${word}`;
    }

    // Single-string-change hint: only one fretted string differs.
    let changedStrings = 0;
    for (let i = 0; i < 6; i++) {
      if (a[i] !== b[i]) changedStrings++;
    }
    if (changedStrings === 1) return 'Move one finger only';
    if (changedStrings === 2) return 'Two-finger move';

    // Barre hint when next shape uses a barre.
    if (nextShape.barre && typeof nextShape.barre.fret === 'number') {
      return `Barre at fret ${nextShape.barre.fret}`;
    }

    return '';
  }

  function _updateChordTransitionHint(activeIdx) {
    const el = document.getElementById('chord-transition');
    if (!el) return;
    if (!Array.isArray(state.chords) || activeIdx < 0
        || activeIdx >= state.chords.length - 1) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    const now = state.chords[activeIdx];
    const next = state.chords[activeIdx + 1];
    // The transition module loads async — if it hasn't resolved yet,
    // try again after the current ensureChordDiagramAssets promise.
    if (!state.chordDiagramModule || !state.chordShapeRegistry) {
      ensureChordDiagramAssets().then(() => {
        const hint = _computeTransitionHint(now.symbol, next.symbol);
        if (!hint) { el.hidden = true; el.textContent = ''; return; }
        el.textContent = hint;
        el.hidden = false;
      });
      return;
    }
    const hint = _computeTransitionHint(now.symbol, next.symbol);
    if (!hint) { el.hidden = true; el.textContent = ''; return; }
    el.textContent = hint;
    el.hidden = false;
  }

  // Countdown to next chord change. Updated from the RAF tick at
  // ~10Hz via _chordCountdownLastTextTs throttle, so we don't churn
  // the DOM 60 times per second for a value that only changes every
  // 100ms in user-visible precision.
  let _chordCountdownLastText = '';
  function _updateChordCountdown(t, activeIdx) {
    const el = document.getElementById('chord-countdown');
    if (!el) return;
    if (!Array.isArray(state.chords) || activeIdx < 0
        || activeIdx >= state.chords.length) {
      if (_chordCountdownLastText !== '') {
        el.textContent = '';
        _chordCountdownLastText = '';
      }
      return;
    }
    const cur = state.chords[activeIdx];
    const next = state.chords[activeIdx + 1];
    let text;
    if (!next) {
      text = 'Last chord';
    } else {
      const remaining = Math.max(0, cur.endSec - t);
      // 1-decimal precision matches the directive example "1.8s".
      text = `Next change in ${remaining.toFixed(1)}s`;
    }
    if (text !== _chordCountdownLastText) {
      el.textContent = text;
      _chordCountdownLastText = text;
    }
  }

  // -------------------------------------- voicing picker (§4)
  // Returns the user's preferred shape for `symbol`, falling back to
  // the curated lookup (voicing index 0). The renderer module +
  // registry must already be loaded — callers ensure this via
  // ensureChordDiagramAssets().
  function _resolveShapeForSymbol(symbol) {
    if (!state.chordDiagramModule || !state.chordShapeRegistry) return null;
    const mod = state.chordDiagramModule;
    const reg = state.chordShapeRegistry;
    const list = (typeof mod.listVoicings === 'function')
      ? mod.listVoicings(symbol, reg)
      : null;
    if (Array.isArray(list) && list.length > 0) {
      const key = `${symbol}`;
      const idx = Number.isInteger(state.voicingPrefs[key])
        ? state.voicingPrefs[key]
        : 0;
      const clamped = Math.max(0, Math.min(idx, list.length - 1));
      return list[clamped].shape;
    }
    // Fallback to legacy lookup if listVoicings missing for some
    // reason (older bundled module on a stale cache).
    try { return mod.lookupShape(symbol, reg); } catch (_) { return null; }
  }

  function _persistVoicingPref() {
    try {
      localStorage.setItem(VOICING_PREFS_KEY, JSON.stringify(state.voicingPrefs));
    } catch (_) {}
  }

  // Toggle the voicing popover under the NOW PLAYING diagram. The
  // popover is built on demand and parented to chord-now-block so it
  // anchors visually beneath the diagram without needing a portal /
  // fixed positioner. Re-clicking the same chord closes the popover.
  let _voicingPopoverSymbol = null;
  function _openVoicingPicker(symbol) {
    const host = document.getElementById('chord-now-block');
    if (!host) return;
    const existing = document.getElementById('chord-voicing-popover');
    if (existing) existing.remove();
    if (_voicingPopoverSymbol === symbol) {
      _voicingPopoverSymbol = null;
      return;
    }
    _voicingPopoverSymbol = symbol;

    if (!state.chordDiagramModule || !state.chordShapeRegistry) return;
    const list = state.chordDiagramModule.listVoicings(symbol, state.chordShapeRegistry);
    if (!Array.isArray(list) || list.length === 0) {
      _voicingPopoverSymbol = null;
      return;
    }

    const pop = document.createElement('div');
    pop.id = 'chord-voicing-popover';
    pop.className = 'chord-voicing-popover';
    pop.setAttribute('role', 'menu');
    pop.setAttribute('aria-label', `Voicing options for ${symbol}`);

    const header = document.createElement('div');
    header.className = 'chord-voicing-header';
    header.textContent = `${symbol} voicings`;
    pop.appendChild(header);

    const selectedIdx = Number.isInteger(state.voicingPrefs[symbol])
      ? state.voicingPrefs[symbol]
      : 0;

    list.forEach((entry, idx) => {
      const opt = document.createElement('button');
      opt.type = 'button';
      opt.className = 'chord-voicing-option';
      if (idx === selectedIdx) opt.classList.add('is-selected');
      opt.setAttribute('role', 'menuitemradio');
      opt.setAttribute('aria-checked', idx === selectedIdx ? 'true' : 'false');
      opt.textContent = entry.name;
      opt.addEventListener('click', () => {
        state.voicingPrefs[symbol] = idx;
        _persistVoicingPref();
        // Close popover and re-render to reflect the new selection.
        _voicingPopoverSymbol = null;
        pop.remove();
        if (_chordLastActiveIdx >= 0) _renderChordGuidance(_chordLastActiveIdx);
      });
      pop.appendChild(opt);
    });

    host.appendChild(pop);

    // Dismiss on outside click. Defer registering the listener by one
    // tick so the click that opened the popover doesn't immediately
    // close it.
    setTimeout(() => {
      const onDocClick = (ev) => {
        if (!pop.contains(ev.target)
            && !ev.target.closest('#chord-now-diagram')) {
          pop.remove();
          _voicingPopoverSymbol = null;
          document.removeEventListener('click', onDocClick);
        }
      };
      document.addEventListener('click', onDocClick);
    }, 0);
  }

  // -------------------------------------- advanced overlays (§8)
  // Parse a chord symbol into { rootPc, quality }. The chord_diagrams
  // module exports normalizeSymbol but it's only available after the
  // async import — for overlays we want a synchronous fast path so
  // the labels appear without waiting for the module to resolve.
  // Falls back to null on garbage input.
  const _PC_NAME_TO_PC = {
    'C': 0, 'C#': 1, 'Db': 1, 'D': 2, 'D#': 3, 'Eb': 3,
    'E': 4, 'F': 5, 'F#': 6, 'Gb': 6, 'G': 7, 'G#': 8, 'Ab': 8,
    'A': 9, 'A#': 10, 'Bb': 10, 'B': 11,
  };
  function _parseChordSymbolLocal(symbol) {
    if (!symbol || typeof symbol !== 'string') return null;
    const m = symbol.match(/^([A-G][#b]?)(.*)$/);
    if (!m) return null;
    const rootPc = _PC_NAME_TO_PC[m[1]];
    if (rootPc == null) return null;
    const tail = (m[2] || '').toLowerCase();
    let quality = 'maj';
    if (/^m(?!aj)/.test(tail) || /^min/.test(tail)) quality = 'min';
    else if (/dim/.test(tail)) quality = 'dim';
    else if (/aug/.test(tail)) quality = 'aug';
    else if (/^7/.test(tail)) quality = '7';
    else if (/m7/.test(tail)) quality = 'm7';
    else if (/maj7/.test(tail)) quality = 'maj7';
    else if (/sus2/.test(tail)) quality = 'sus2';
    else if (/sus4/.test(tail)) quality = 'sus4';
    else if (/^5/.test(tail)) quality = '5';
    return { rootPc, quality };
  }

  // Compute the scale degree (1-7) of the chord root relative to the
  // song key. Returns null when key isn't known or the chord doesn't
  // sit on a scale tone (e.g. chromatic / borrowed chords).
  function _scaleDegree(chordRootPc, songKey) {
    if (!songKey || chordRootPc == null) return null;
    const intervals = songKey.scale === 'Minor'
      ? [0, 2, 3, 5, 7, 8, 10]
      : [0, 2, 4, 5, 7, 9, 11];
    const diff = (chordRootPc - songKey.root + 12) % 12;
    const idx = intervals.indexOf(diff);
    return idx >= 0 ? idx + 1 : null; // 1-based
  }

  function _nashvilleLabel(symbol) {
    if (!state.songKey) return '';
    const parsed = _parseChordSymbolLocal(symbol);
    if (!parsed) return '';
    const deg = _scaleDegree(parsed.rootPc, state.songKey);
    if (!deg) return '';
    const isMinor = parsed.quality === 'min' || parsed.quality === 'm7' || parsed.quality === 'dim';
    return isMinor ? `${deg}m` : `${deg}`;
  }

  // Roman numerals, uppercase for major-family, lowercase for minor;
  // diminished gets the ° symbol, augmented the + symbol.
  const _ROMAN_UPPER = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII'];
  function _romanLabel(symbol) {
    if (!state.songKey) return '';
    const parsed = _parseChordSymbolLocal(symbol);
    if (!parsed) return '';
    const deg = _scaleDegree(parsed.rootPc, state.songKey);
    if (!deg) return '';
    const base = _ROMAN_UPPER[deg - 1];
    if (parsed.quality === 'dim') return `${base.toLowerCase()}°`;
    if (parsed.quality === 'aug') return `${base}+`;
    const isMinor = parsed.quality === 'min' || parsed.quality === 'm7';
    return isMinor ? base.toLowerCase() : base;
  }

  // Scale-suggestion strings by quality. Concise, one-line, generic
  // enough to apply across keys (the renderer prepends the chord
  // root). Goal is a "try this" hint, not music-theory exhaustive.
  const _SCALE_HINTS = {
    'maj': 'Major / Major pentatonic',
    'min': 'Natural minor / Minor pentatonic',
    'm7':  'Dorian / Minor pentatonic',
    'maj7': 'Major / Lydian',
    '7':   'Mixolydian',
    'dim': 'Whole-half diminished',
    'aug': 'Whole-tone',
    'sus2': 'Major pentatonic',
    'sus4': 'Mixolydian / Major pentatonic',
    '5':   'Major / Minor pentatonic',
  };
  function _scaleSuggestion(symbol) {
    const parsed = _parseChordSymbolLocal(symbol);
    if (!parsed) return '';
    const hint = _SCALE_HINTS[parsed.quality];
    if (!hint) return '';
    // Use the chord root name, not the song key, since soloists
    // typically frame scales against the chord they're playing over.
    const rootName = symbol.match(/^([A-G][#b]?)/)?.[1] || '';
    return rootName ? `${rootName} ${hint}` : hint;
  }

  function _applyOverlaysToNowBlock(symbol) {
    const nash = document.getElementById('chord-now-nashville');
    const rom = document.getElementById('chord-now-roman');
    const sc = document.getElementById('chord-now-scale');
    if (nash) {
      const txt = state.chordOverlays.nashville ? _nashvilleLabel(symbol) : '';
      nash.textContent = txt;
      nash.hidden = !txt;
    }
    if (rom) {
      const txt = state.chordOverlays.roman ? _romanLabel(symbol) : '';
      rom.textContent = txt;
      rom.hidden = !txt;
    }
    if (sc) {
      const txt = state.chordOverlays.scales ? _scaleSuggestion(symbol) : '';
      sc.textContent = txt;
      sc.hidden = !txt;
    }
  }

  function _persistChordOverlays() {
    try { localStorage.setItem(CHORD_OVERLAYS_KEY, JSON.stringify(state.chordOverlays)); } catch (_) {}
  }

  // Reflect the overlays state on the checkbox UI and toggle the
  // visibility of the row itself. Called on analysis-complete and on
  // perform-back cleanup.
  function syncChordOverlaysVisibility() {
    const row = document.getElementById('chord-overlays-row');
    if (!row) return;
    const hasChords = Array.isArray(state.chords) && state.chords.length > 0;
    row.hidden = !hasChords;
    const n = document.getElementById('chord-overlay-nashville');
    const r = document.getElementById('chord-overlay-roman');
    const s = document.getElementById('chord-overlay-scales');
    if (n) n.checked = !!state.chordOverlays.nashville;
    if (r) r.checked = !!state.chordOverlays.roman;
    if (s) s.checked = !!state.chordOverlays.scales;
  }

  (function wireChordOverlayCheckboxes() {
    const wire = (id, key) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.addEventListener('change', () => {
        state.chordOverlays[key] = !!el.checked;
        _persistChordOverlays();
        if (_chordLastActiveIdx >= 0) _renderChordGuidance(_chordLastActiveIdx);
      });
    };
    wire('chord-overlay-nashville', 'nashville');
    wire('chord-overlay-roman', 'roman');
    wire('chord-overlay-scales', 'scales');
  })();

  // Render the LEAD PART / RIFF lane for the active section's time
  // window — widened from per-chord so an intro phrase that spans
  // multiple chords appears as one cohesive riff. Falls back to the
  // per-chord window when section info is missing.
  function _renderLeadTabLane(activeIdx) {
    const lane = document.getElementById('chord-tab-queue');
    const body = document.getElementById('chord-tab-body');
    if (!lane || !body) return;
    if (!Array.isArray(state.chords) || state.chords.length === 0) return;
    if (activeIdx < 0 || activeIdx >= state.chords.length) return;

    const chord = state.chords[activeIdx];
    // Prefer the active section's window so an intro riff that spans
    // 4 chords renders as one phrase. If no section info, fall back
    // to the chord's own [startSec, endSec).
    const section = _findSectionAt(chord.startSec);
    const t0 = section ? section.startSec : chord.startSec;
    const t1 = section ? section.endSec : chord.endSec;

    body.innerHTML = '';
    const notes = Array.isArray(state.leadMidiNotes) ? state.leadMidiNotes : [];
    if (notes.length === 0) {
      const placeholder = document.createElement('div');
      placeholder.className = 'chord-tab-empty';
      placeholder.textContent = 'No lead phrase detected for this region';
      body.appendChild(placeholder);
      return;
    }

    ensureChordDiagramAssets().then((assets) => {
      if (!assets || !assets.mod) return;
      const svg = assets.mod.renderLeadTabSVG(notes, t0, t1, { highlighted: true });
      body.innerHTML = '';
      if (svg) {
        body.appendChild(svg);
      } else {
        const placeholder = document.createElement('div');
        placeholder.className = 'chord-tab-empty';
        placeholder.textContent = 'No lead phrase detected for this region';
        body.appendChild(placeholder);
      }
    });
  }

  // Active-chord-change dispatcher. Called from inside
  // updateChordPlayhead's class-toggle block whenever the active
  // chord index advances. Re-renders both the guidance block and the
  // tab lane, but each render is gated by the current view mode so
  // we don't waste cycles painting hidden DOM.
  function _onActiveChordChanged(newIdx) {
    if (newIdx < 0) return;
    _renderChordGuidance(newIdx);
    _renderLeadTabLane(newIdx);
  }

  // Render the chord lane from result.chords. Wire shape (from
  // AnalysisResult.to_dict + chord_detector):
  //   [{ start_s: number, end_s: number, symbol: string,
  //      confidence: number }, ...]
  //
  // Layout strategy: pill width scales with chord duration so the
  // strip's pixel timeline IS the song's time axis. updateChordPlayhead
  // then translates the strip by `-pxPerSecond * t` to put the
  // currently-sounding chord under the fixed centre playhead. We
  // pre-measure offsetLeft / offsetWidth ONCE here so per-RAF cost is
  // O(log n) binary-search + one style write.
  //
  // Empty / missing input renders nothing and leaves the ribbon hidden,
  // which is the correct degenerate behaviour — a guitarist on a
  // chord-less song should not see a blank strip.
  const CHORD_RIBBON_PX_PER_SEC = 60;
  function buildChordRibbon(chords) {
    const ribbon = $('chord-ribbon');
    const strip = $('chord-ribbon-strip');
    if (!ribbon || !strip) return;
    strip.innerHTML = '';
    state.chords = [];

    if (!Array.isArray(chords) || chords.length === 0) {
      ribbon.hidden = true;
      return;
    }

    // Build pills. Cache start/end/leftPx/widthPx onto state.chords so
    // the playhead update never re-measures.
    //
    // Two layout invariants must mirror jam.css exactly or the active-
    // pill highlight will visually drift off the playhead as leftPx
    // accumulates across many pills:
    //   (1) `.chord-ribbon-strip` has `padding: 0 8px`, so pill 0's
    //       visual left edge sits 8px in from the strip's left edge.
    //       Initialise leftPx = 8 to match.
    //   (2) `.chord-pill` has `min-width: 56px`. The width floor here
    //       MUST equal that minimum — a smaller floor (e.g. 28) makes
    //       JS believe a short pill is narrower than CSS actually
    //       renders it, and the per-pill underestimate compounds over
    //       170 Pub Feed pills into multi-pill drift.
    let leftPx = 8;
    const pxPerSec = CHORD_RIBBON_PX_PER_SEC;
    for (const c of chords) {
      const startSec = secondsOf(c.start_s);
      const endSec = secondsOf(c.end_s);
      const durSec = Math.max(0, endSec - startSec);
      // Skip degenerate chords (zero-length / negative); the detector
      // shouldn't emit them but defensive code is cheap.
      if (!(durSec > 0)) continue;
      const widthPx = Math.max(56, durSec * pxPerSec);
      const pill = document.createElement('div');
      pill.className = 'chord-pill';
      pill.style.width = widthPx + 'px';
      pill.textContent = c.symbol || '?';
      // perform v2: click / Enter / Space on a pill seeks the playhead
      // to that chord's start. Capture startSec per-iteration so the
      // closure binds the right value. seekAll() already nudges the
      // chord ribbon + waveform when paused.
      pill.setAttribute('role', 'button');
      pill.setAttribute('tabindex', '0');
      pill.setAttribute('aria-label', `Seek to ${c.symbol || '?'} at ${formatTime(startSec)}`);
      const _seekTarget = startSec;
      pill.addEventListener('click', () => seekAll(_seekTarget));
      pill.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') {
          ev.preventDefault();
          seekAll(_seekTarget);
        }
      });
      strip.appendChild(pill);
      state.chords.push({
        symbol: c.symbol || '?',
        startSec,
        endSec,
        el: pill,
        leftPx,
        widthPx,
      });
      leftPx += widthPx + 4; // +4 to match flex gap
    }

    if (state.chords.length === 0) {
      ribbon.hidden = true;
      return;
    }
    ribbon.hidden = false;
    // Position at t=0 immediately so the first chord is centred under
    // the playhead before the user hits Play.
    updateChordPlayhead(0);
  }

  // Per-RAF playhead update. Binary-search state.chords by startSec to
  // find the active chord at time `t`, toggle the highlight, and
  // translate the strip so the active pill sits under the centred
  // playhead marker. Called from tickClock() while playing and from
  // seekAll() when paused so the ribbon tracks scrubs too.
  let _chordLastActiveIdx = -1;
  function updateChordPlayhead(t) {
    const strip = $('chord-ribbon-strip');
    if (!strip || state.chords.length === 0) return;

    // Binary search for the chord whose [startSec, endSec) contains t.
    let lo = 0, hi = state.chords.length - 1, idx = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const c = state.chords[mid];
      if (t < c.startSec) hi = mid - 1;
      else if (t >= c.endSec) lo = mid + 1;
      else { idx = mid; break; }
    }

    // Three cases for "no exact hit":
    //   (a) before the first chord       -> pin to first, clamp progress=0
    //   (b) past the last chord          -> pin to last,  clamp progress=1
    //   (c) inside an internal gap       -> interpolate between bracketing
    //       pills' edges so the strip keeps scrolling instead of freezing
    //       off-screen until the next chord starts.
    // Without case (c) the strip would park at the last computed transform
    // (which is typically far left-of-viewport) for the duration of the
    // gap, producing the "saw a chord slide through once and never again"
    // symptom on songs whose detector emitted sparse chord regions.
    let activeCentrePx;
    let highlightIdx = idx;

    if (idx >= 0) {
      const c = state.chords[idx];
      const rawProgress = c.endSec > c.startSec
        ? (t - c.startSec) / (c.endSec - c.startSec)
        : 0;
      const localProgress = Math.max(0, Math.min(1, rawProgress));
      activeCentrePx = c.leftPx + c.widthPx * localProgress;
    } else if (t < state.chords[0].startSec) {
      // (a) Pre-roll: pin to first pill's leading edge.
      const c = state.chords[0];
      highlightIdx = 0;
      activeCentrePx = c.leftPx;
    } else if (t >= state.chords[state.chords.length - 1].endSec) {
      // (b) Post-roll: pin to last pill's trailing edge.
      const c = state.chords[state.chords.length - 1];
      highlightIdx = state.chords.length - 1;
      activeCentrePx = c.leftPx + c.widthPx;
    } else {
      // (c) Internal gap: find the bracketing pair [prev, next] where
      //     prev.endSec <= t < next.startSec, and interpolate between
      //     prev's right edge and next's left edge.
      let prevIdx = 0;
      for (let i = 0; i < state.chords.length - 1; i++) {
        if (state.chords[i].endSec <= t && t < state.chords[i + 1].startSec) {
          prevIdx = i;
          break;
        }
      }
      const prev = state.chords[prevIdx];
      const next = state.chords[prevIdx + 1];
      const gapDur = next.startSec - prev.endSec;
      const gapProgress = gapDur > 0
        ? (t - prev.endSec) / gapDur
        : 0;
      const prevRightPx = prev.leftPx + prev.widthPx;
      const nextLeftPx = next.leftPx;
      activeCentrePx = prevRightPx + (nextLeftPx - prevRightPx) * gapProgress;
      // No pill is "active" during the gap; drop the highlight.
      highlightIdx = -1;
    }

    if (highlightIdx !== _chordLastActiveIdx) {
      if (_chordLastActiveIdx >= 0 && state.chords[_chordLastActiveIdx]) {
        state.chords[_chordLastActiveIdx].el.classList.remove('active');
      }
      if (highlightIdx >= 0 && state.chords[highlightIdx]) {
        state.chords[highlightIdx].el.classList.add('active');
      }
      _chordLastActiveIdx = highlightIdx;
      // Notify the chord-diagram queue + lead-tab lane that the
      // active chord index advanced. Idempotent if the view mode
      // hides both lanes.
      _onActiveChordChanged(highlightIdx);
    }

    // Countdown updates every RAF tick (string-compare gates the
    // actual DOM write so churn is bounded to ~10Hz user-visible
    // precision).
    _updateChordCountdown(t, highlightIdx);

    strip.style.transform = `translateX(${-activeCentrePx}px)`;
  }

  function secondsOf(v) {
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && !isNaN(parseFloat(v))) return parseFloat(v);
    return 0;
  }

  function toggleSectionLoop(section) {
    if (state.loop && state.loop.name === section.name) {
      state.loop = null;
      section.el.classList.remove('active');
      $('loop-status').textContent = 'Loop: off';
      return;
    }
    state.sections.forEach(s => s.el.classList.remove('active'));
    section.el.classList.add('active');
    state.loop = section;
    $('loop-status').textContent = `Loop: ${section.name} (${formatTime(section.startSec)} – ${formatTime(section.endSec)})`;
    // If currently playing, seek to start
    if (state.isPlaying) seekAll(section.startSec);
  }

  // ---------------------------------------------- audio: prepare + transport
  //
  // Playback uses AudioBufferSourceNodes (NOT HTMLAudioElements) so the
  // stems are sample-accurate to each other. With <audio> elements each
  // stem has its own decoder pipeline and starts drift on the order of
  // 10-50 ms — audible as flam on percussive transients.
  //
  // Cost: each stem must be decoded to a full AudioBuffer before play.
  // For a 4-minute 44.1 kHz stereo stem that's ~85 MB × 4 stems ≈ 340 MB.
  // Acceptable on desktop; we'll need a streaming strategy for mobile.
  async function prepareStemAudio() {
    if (!state.ctx) {
      try {
        // latencyHint: 'interactive' asks the browser for the smallest
        // render buffer it will give us. Safari defaults to 'balanced'
        // (larger buffer for power), which adds ~20-40ms of monitor
        // round-trip on top of the OS layer. 'interactive' typically
        // halves baseLatency at the cost of more CPU.
        const Ctx = window.AudioContext || window.webkitAudioContext;
        state.ctx = new Ctx({ latencyHint: 'interactive' });
        state.masterGain = state.ctx.createGain();
        state.masterGain.gain.value = 1.0;
        state.masterGain.connect(state.ctx.destination);
      } catch (e) {
        console.warn('[jam] AudioContext not available:', e);
        return;
      }
    }

    const loaders = [];
    for (const [name, stem] of state.stems.entries()) {
      // Idempotent: skip stems that have already been decoded (e.g. via
      // an earlier stems_partial event that kicked off decode in parallel
      // with the rest of the analysis pipeline).
      if (stem.buffer) continue;
      // Don't queue the same stem twice if decode is already in flight.
      if (stem.loadPromise) {
        loaders.push(stem.loadPromise);
        continue;
      }
      const p = fetch(stem.url)
          .then((r) => {
            if (!r.ok) throw new Error(`HTTP ${r.status} for ${name}`);
            return r.arrayBuffer();
          })
          .then((ab) => state.ctx.decodeAudioData(ab))
          .then((buf) => {
            stem.buffer = buf;
            const initial = nameIsUserInstrument(name) ? 0.7 : 1.0;
            stem.gainNode = state.ctx.createGain();
            stem.gainNode.gain.value = initial;
            stem.lastGain = initial;
            stem.gainNode.connect(state.masterGain);
          })
          .catch((err) => {
            console.error(`[jam] failed to load stem ${name}:`, err, stem.url);
          })
          .finally(() => {
            stem.loadPromise = null;
          });
      stem.loadPromise = p;
      loaders.push(p);
    }
    await Promise.all(loaders);

    // Duration = max of all decoded buffers.
    let maxDur = 0;
    for (const stem of state.stems.values()) {
      if (stem.buffer && stem.buffer.duration > maxDur) maxDur = stem.buffer.duration;
    }
    state.duration = maxDur;
    state.playOffset = 0;
    $('t-time').textContent = `0:00 / ${formatTime(maxDur)}`;
    // Schedule waveform peak computation off the critical path.
    // ~500ms for a 4-stem song; user can hit Play immediately and the
    // bars will appear shortly after.
    setTimeout(() => {
      try { computeWaveformPeaks(); } catch (e) {
        console.warn('[jam] computeWaveformPeaks failed:', e);
      }
    }, 0);
    console.log('[jam] audio prepared:', {
      stems: state.stems.size,
      ctxState: state.ctx.state,
      sampleRate: state.ctx.sampleRate,
      durationSec: maxDur,
    });
    // Audio-Ownership Pivot follow-up: hand the URL list to
    // Connect so its AudioEngine can take over playback. Browser
    // Web Audio above stays as the fallback path. Silent no-op if
    // Connect is not paired — re-fires on every prepareStemAudio
    // call, which is idempotent on the helper side.
    try { pushStemsToConnect(); } catch (e) {
      console.warn('[jam] pushStemsToConnect failed:', e);
    }
  }

  // ----- Stem-model helpers (role-based, provider-agnostic) -----
  //
  // The Jam UI used to substring-match stem names ("other", "guitar_lead")
  // to decide what was "the user's instrument". That coupled the UI to
  // Demucs' naming. The session engine now ships a Stem[] with explicit
  // ``role`` per stem, so all routing here goes through these helpers.

  // Map an intake instrument selection to the roles a stem can have to
  // count as "this is the user's part" (i.e. should be tagged as
  // original-instrument and muteable in one click).
  const INSTRUMENT_ROLE_MAP = {
    guitar: new Set(['lead', 'rhythm', 'texture', 'harmonic']),
    bass: new Set(['bass']),
    keys: new Set(['keys', 'harmonic']),
    vocals: new Set(['vocals']),
    drums: new Set(['drums']),
  };

  // Stable display order; unknown roles fall to the end.
  const ROLE_ORDER = [
    'drums', 'bass', 'keys',
    'harmonic', 'lead', 'rhythm', 'texture',
    'unknown', 'vocals',
  ];

  // Legacy adapter: convert the old name-keyed ``stems_paths`` dict
  // into Stem records. Older servers only ship the dict; this lets us
  // keep one code path on the client.
  function legacyStemsToRecords(stemsDict) {
    const records = [];
    for (const [name, url] of Object.entries(stemsDict)) {
      let role = 'unknown';
      let displayName = name;
      if (name === 'drums') { role = 'drums'; displayName = 'Drums'; }
      else if (name === 'bass') { role = 'bass'; displayName = 'Bass'; }
      else if (name === 'vocals') { role = 'vocals'; displayName = 'Vocals'; }
      else if (name === 'keys' || name === 'piano') { role = 'keys'; displayName = 'Keys'; }
      else if (name === 'guitar' || name === 'other') { role = 'harmonic'; displayName = 'Guitar'; }
      else if (name === 'guitar_lead') { role = 'lead'; displayName = 'Guitar — lead'; }
      else if (name === 'guitar_rhythm') { role = 'rhythm'; displayName = 'Guitar — rhythm'; }
      else if (name === 'guitar_texture') { role = 'texture'; displayName = 'Guitar — texture'; }
      records.push({
        id: `legacy.${name}`,
        role,
        display_name: displayName,
        audio_url: url,
        parent_id: null,
        provider: 'legacy',
        confidence: 0.5,
      });
    }
    return records;
  }

  function rolesForUserInstrument() {
    return INSTRUMENT_ROLE_MAP[state.userInstrument] || new Set();
  }

  function stemIsUserInstrument(stem) {
    if (!stem) return false;
    return rolesForUserInstrument().has(stem.role);
  }

  function userInstrumentStemIds() {
    const out = [];
    for (const [id, stem] of state.stems.entries()) {
      if (stemIsUserInstrument(stem)) out.push(id);
    }
    return out;
  }

  function nameIsUserInstrument(id) {
    return stemIsUserInstrument(state.stems.get(id));
  }

  // -------------------------------------------------- preset-match lookup
  //
  // The backend currently emits `preset_matches` keyed by legacy stem
  // names (`bass`, `guitar`, `guitar_lead`, `guitar_rhythm`, ...). The
  // session engine consumes Stem records keyed by stable provider IDs
  // (`demucs.other.sides`). These helpers bridge the two: given a Stem,
  // they enumerate the legacy keys that may carry its match, then return
  // the first hit.
  const ROLE_TO_LEGACY_KEYS = {
    drums:    [],                  // drums are intentionally skipped server-side
    bass:     ['bass'],
    vocals:   ['vocals'],
    lead:     ['guitar_lead', 'guitar', 'other'],
    rhythm:   ['guitar_rhythm', 'guitar', 'other'],
    texture:  ['guitar_texture', 'guitar', 'other'],
    harmonic: ['guitar', 'other'],
    keys:     ['piano', 'keys'],
    unknown:  [],
  };

  function legacyKeysForStem(stem) {
    if (!stem) return [];
    const keys = [];
    // Trailing segment of the stem id is usually the legacy name
    // (`demucs.bass` -> `bass`, `demucs.other` -> `other`,
    // `demucs.other.sides` -> `sides`). For pan-split children we
    // additionally prefer the role-derived key below.
    if (stem.id) {
      const parts = stem.id.split('.');
      keys.push(parts[parts.length - 1]);
      if (parts.length > 1) keys.push(parts.slice(1).join('_'));
    }
    for (const k of (ROLE_TO_LEGACY_KEYS[stem.role] || [])) keys.push(k);
    return keys;
  }

  function findPresetMatchForStem(stem, matches) {
    if (!stem || !matches) return null;
    for (const k of legacyKeysForStem(stem)) {
      if (matches[k]) return matches[k];
    }
    return null;
  }

  function findUserPresetMatch(matches) {
    if (!matches) return null;
    // Walk the user's stems in role-priority order; return the first
    // stem that has a match.
    const candidates = [...state.stems.values()]
      .filter(stemIsUserInstrument)
      .sort((a, b) => {
        const ai = ROLE_ORDER.indexOf(a.role);
        const bi = ROLE_ORDER.indexOf(b.role);
        return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi);
      });
    for (const stem of candidates) {
      const m = findPresetMatchForStem(stem, matches);
      if (m) return m;
    }
    // Last-ditch: legacy keys for songs where stem_records is empty
    // (legacy adapter path) but matches arrived.
    return (
      matches[state.userInstrument]
      || matches.guitar_lead
      || matches.guitar
      || matches.guitar_rhythm
      || matches.guitar_texture
      || matches.other
      || null
    );
  }

  function formatPresetMatchMeta(match) {
    if (!match) return '';
    const inst = match.instrument || 'Analog';
    if (match.distance == null) return inst;
    const pct = Math.max(0, 100 - match.distance * 10).toFixed(0);
    return `${inst} · match ${pct}%`;
  }

  // Current playhead in seconds (derived from the audio clock).
  function currentPlayTime() {
    if (!state.ctx) return 0;
    if (!state.isPlaying) return state.playOffset;
    return state.playOffset + (state.ctx.currentTime - state.playClockAnchor);
  }

  $('t-play').addEventListener('click', () => {
    if (state.isPlaying) {
      pauseAll();
    } else {
      playAll().catch((err) => console.error('[jam] play failed:', err));
    }
  });
  $('t-stop').addEventListener('click', () => {
    stopAllStems();
    $('t-play').textContent = 'Play';
    $('t-time').textContent = `0:00 / ${formatTime(state.duration)}`;
  });

  $('user-mute-original').addEventListener('click', () => {
    // The user-instrument role may map to multiple stems (e.g. guitar
    // pan-split into lead/rhythm/texture). Mute / unmute them as a group.
    const targets = userInstrumentStemIds();
    if (targets.length === 0) return;
    // Use the first target to decide direction so all stems flip together.
    const newMuted = !isMuted(targets[0]);
    let muted = false;
    for (const t of targets) muted = setStemMuted(t, newMuted);
    const label = state.userInstrument === 'guitar' ? 'guitar' : state.userInstrument;
    $('user-mute-original').textContent = muted ? `Unmute original ${label}` : `Mute original ${label}`;
  });

  let _connectStatusFlashTimer = null;
  function flashConnectStatus(text, ok = false, durationMs = 2200) {
    const statusEl = document.getElementById('connect-status');
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = false;
    statusEl.classList.toggle('ok', !!ok);
    clearTimeout(_connectStatusFlashTimer);
    _connectStatusFlashTimer = setTimeout(renderConnectStatus, durationMs);
  }

  // Briefly pulse the Connect button so a click that produces an
  // otherwise-text-only status flash still has a tactile, on-element
  // signal. Pure UI affordance — no state change, no WS traffic.
  function pulseConnectBtn() {
    const btn = document.getElementById('connect-btn');
    if (!btn) return;
    btn.classList.remove('clicked-pulse');
    // Force reflow so the class re-add restarts the CSS animation.
    void btn.offsetWidth;
    btn.classList.add('clicked-pulse');
  }

  $('connect-btn').addEventListener('click', () => {
    const cb = state.connectBridge;
    const paired = cb.status === 'open' && cb.peers > 0;
    // Visible tactile feedback regardless of branch — the click was
    // received and a handler ran. Branches below decide the textual
    // message; the pulse just removes the "did anything happen?"
    // ambiguity the user hit on the paired+no-preset path.
    pulseConnectBtn();
    if (paired && cb.lastPreset) {
      // Re-push so the helper re-applies the matched tone.
      pushPresetToConnect(cb.lastPreset);
      flashConnectStatus('Re-synced tone preset to Connect.', true, 3500);
      return;
    }
    if (paired) {
      // Paired but no match exists for this song. Acknowledge the click
      // with a longer-lived message that points at the Apply button —
      // the previous 2.2s flash was easy to miss, and the user had no
      // signposting to the actual action (Apply on the Suggested card).
      flashConnectStatus(
        'Already paired. Click "Apply" on the Suggested match above to push a tone.',
        true,
        5000,
      );
      return;
    }
    if (cb.status === 'connecting') {
      flashConnectStatus('Still connecting — give it a sec.', false, 3500);
      return;
    }
    // Not paired (open w/ no peers, or closed). Two things have to
    // happen here:
    //   1. Make sure the browser side of the bridge is alive so the
    //      helper has somewhere to join.
    //   2. Fire the toneforge:// deep link so an installed Connect.app
    //      launches and joins the same session.
    //
    // Launcher choice: an anchor element with a synthetic ``.click()``
    // inside this user-gesture handler. We tried two other patterns
    // first and both failed in real browsers:
    //   * ``window.location.href = deepLink`` (P2i) — on Brave/Chrome
    //     this *navigates the current page* when no handler is
    //     registered, wiping the jam UI and reloading bare ``/jam``.
    //   * Hidden ``<iframe src=deepLink>`` (P2j) — Brave silently
    //     drops iframe-initiated custom-scheme launches, so Connect
    //     never opened even when it was installed and registered.
    // An anchor click is treated by Brave/Chrome/Safari as a direct
    // user action (inheriting the surrounding click's gesture), so
    // the OS handoff fires when a handler is installed; when no
    // handler is registered the click is a silent no-op and the
    // parent document stays put. Same shape pattern Slack / Zoom use.
    ensureConnectBridge();
    const cbAfterKick = state.connectBridge;
    // Safari 26.x has a crash in its banner-management code when a
    // programmatically-clicked anchor targets an unregistered custom
    // scheme (radar via Safari-2026-06-11-010546.ips:
    // -[BannerContainerView uninstallBanner:withAnimation:] nil-derefs
    // through -[__NSArrayM insertObject:atIndex:] on the next
    // didCommitLoadForFrame:). Beyond that, Safari already silently
    // drops the synthetic launch — see the comment above. So on
    // Safari we skip the auto-launch entirely and rely on the visible
    // "Open Connect helper →" link that renderConnectStatus surfaces;
    // a real user-clicked anchor still works on Safari when
    // Connect.app IS installed, and falls through to a graceful
    // "no app" prompt when it isn't, without queueing the broken
    // banner uninstall.
    const isSafari =
      /^((?!chrome|android).)*safari/i.test(navigator.userAgent || '');
    if (cbAfterKick.sessionId && !isSafari) {
      const scheme = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${scheme}//${window.location.host}/ws/connect-bridge`;
      const deepLink =
        'toneforge://pair'
        + `?session=${encodeURIComponent(cbAfterKick.sessionId)}`
        + `&ws=${encodeURIComponent(wsUrl)}`;
      try {
        const a = document.createElement('a');
        a.href = deepLink;
        a.style.display = 'none';
        // ``rel=noopener`` keeps any handler-side window.open semantics
        // from looking back at our document. ``target=_self`` keeps the
        // click from being routed through any popup-blocker path.
        a.rel = 'noopener';
        a.target = '_self';
        document.body.appendChild(a);
        a.click();
        a.remove();
      } catch (e) {
        console.warn('[connect] deep-link launch failed:', e);
      }
    }
    flashConnectStatus(
      isSafari
        ? 'Bridge ready — click "Open Connect helper →" below to pair.'
        : 'Reaching out to the desktop helper…',
    );
  });

  // Open the bridge proactively when the user lands on the perform
  // view so the helper has somewhere to join. The browser is the
  // server of state here — Connect just listens.
  function primeConnectBridge() {
    ensureConnectBridge();
  }

  // Monitor-gain slider. We debounce sends slightly so dragging the
  // thumb doesn't flood the WS — 30 ms is below human "live" feel and
  // well above the network roundtrip on localhost.
  let _connectGainSendTimer = null;
  const _connectGainSlider = document.getElementById('connect-gain');
  if (_connectGainSlider) {
    _connectGainSlider.addEventListener('input', () => {
      const g = Number(_connectGainSlider.value);
      state.connectBridge.lastGain = g;
      renderConnectGain();
      if (_connectGainSendTimer) clearTimeout(_connectGainSendTimer);
      _connectGainSendTimer = setTimeout(() => {
        _connectGainSendTimer = null;
        setConnectGain(g);
      }, 30);
    });
  }

  // Schedule all stems to start at the same absolute audio-clock time.
  // Sample-accurate sync across stems is the entire reason we decoded
  // to AudioBuffers up front.
  async function playAll() {
    if (!state.ctx || !state.stems.size) return;
    if (state.ctx.state === 'suspended') {
      try { await state.ctx.resume(); }
      catch (e) { console.warn('[jam] ctx.resume failed:', e); }
    }

    // If a loop is active and we're outside it, snap to its start.
    if (state.loop) {
      if (state.playOffset < state.loop.startSec || state.playOffset >= state.loop.endSec) {
        state.playOffset = state.loop.startSec;
      }
    }
    // If we're past the end, restart.
    if (state.playOffset >= state.duration - 0.01) state.playOffset = 0;

    // Small lookahead so all .start() calls land BEFORE the scheduled time.
    const startWhen = state.ctx.currentTime + 0.05;
    const startOffset = state.playOffset;

    for (const stem of state.stems.values()) {
      if (!stem.buffer) continue;
      const src = state.ctx.createBufferSource();
      src.buffer = stem.buffer;
      src.connect(stem.gainNode);
      // Clamp offset to buffer duration; .start() throws otherwise.
      const off = Math.min(Math.max(0, startOffset), Math.max(0, stem.buffer.duration - 0.01));
      try {
        src.start(startWhen, off);
      } catch (e) {
        console.warn('[jam] source.start failed:', e);
      }
      stem.source = src;
    }

    state.playClockAnchor = startWhen;
    state.isPlaying = true;
    $('t-play').textContent = 'Pause';
    requestAnimationFrame(tickClock);
    // (Re)arm click scheduler if user has it on. seekAll() also routes
    // through playAll(), so loop boundaries reset the beat index here too.
    if (state.clickEnabled) {
      stopClickScheduler();
      startClickScheduler();
    }
    // Mirror the play edge to Connect so its AudioEngine starts the
    // stems sample-aligned. seekAll() also routes through this, so a
    // seek-while-playing produces a clean stop+restart on the helper.
    try { pushTransportToConnect(); } catch (_) {}
  }

  function stopSources() {
    for (const stem of state.stems.values()) {
      if (stem.source) {
        try { stem.source.stop(); } catch (_) {}
        try { stem.source.disconnect(); } catch (_) {}
        stem.source = null;
      }
    }
  }

  function pauseAll() {
    if (!state.isPlaying) return;
    // Capture current playhead before tearing down the sources.
    state.playOffset = Math.min(state.duration, currentPlayTime());
    stopSources();
    stopClickScheduler();
    state.isPlaying = false;
    $('t-play').textContent = 'Play';
    // Mirror the pause edge to Connect so its AudioEngine stops the
    // stems. Position is captured into state.playOffset above so the
    // helper has the right resume point on the next play.
    try { pushTransportToConnect(); } catch (_) {}
  }

  function stopAllStems() {
    stopSources();
    stopClickScheduler();
    state.playOffset = 0;
    state.isPlaying = false;
  }

  function seekAll(sec) {
    const target = Math.max(0, Math.min(state.duration, sec));
    if (state.isPlaying) {
      stopSources();
      state.playOffset = target;
      // Re-start at the new offset (synchronous from already-resumed ctx).
      playAll().catch((err) => console.error('[jam] seek-play failed:', err));
    } else {
      state.playOffset = target;
      $('t-time').textContent = `${formatTime(target)} / ${formatTime(state.duration)}`;
      // While paused, tickClock isn't running, so the ribbon needs an
      // explicit nudge when the user seeks via a section pill.
      updateChordPlayhead(target);
      try { drawWaveform(); } catch (_) {}
    }
  }

  function tickClock() {
    if (!state.isPlaying) return;
    const t = currentPlayTime();
    $('t-time').textContent = `${formatTime(t)} / ${formatTime(state.duration)}`;
    // JAM Alpha chord ribbon: cheap per-frame update (O(log n) binary
    // search + one style write). No-op when state.chords is empty.
    updateChordPlayhead(t);
    // v2 waveform playhead. Cheap canvas redraw (~1600 bars + 1 line).
    drawWaveform();
    // Looping: jump back to loop start when we cross the end.
    if (state.loop && t >= state.loop.endSec) {
      seekAll(state.loop.startSec);
      return;
    }
    // Natural end of song.
    if (t >= state.duration - 0.02) {
      stopAllStems();
      $('t-play').textContent = 'Play';
      $('t-time').textContent = `${formatTime(state.duration)} / ${formatTime(state.duration)}`;
      return;
    }
    requestAnimationFrame(tickClock);
  }

  // ---------------------------------------------- click track
  //
  // Drives a short square-wave tick at every beat in result.beat_times.
  // Uses the same look-ahead pattern Chris Wilson described in "A Tale of
  // Two Clocks": a setInterval-driven scheduler peeks ~300 ms into the
  // future and schedules sample-accurate oscillator bursts on the audio
  // graph. setInterval itself isn't sample-accurate; the WebAudio start()
  // calls are.
  //
  // Song-time → audio-clock conversion:
  //   currentPlayTime() = playOffset + (ctx.currentTime - playClockAnchor)
  //   => audio_when      = playClockAnchor + (beat_time - playOffset)
  // playOffset is constant during a single playback run; it's only
  // mutated on pause/seek (which call resetClickSchedule anyway).
  const CLICK_LOOKAHEAD_SEC = 0.3;
  const CLICK_TICK_MS = 100;

  function ensureClickGain() {
    if (state.clickGain) return state.clickGain;
    if (!state.ctx || !state.masterGain) return null;
    state.clickGain = state.ctx.createGain();
    // Audible over a full stem mix without overwhelming it. 0.25 was
    // too quiet against drums+bass+guitar; 0.6 sits roughly at -4 dB
    // peak after the envelope which the ear actually catches.
    state.clickGain.gain.value = 0.6;
    state.clickGain.connect(state.masterGain);
    return state.clickGain;
  }

  function scheduleClickTick(when) {
    if (!state.ctx || !state.clickGain) return;
    const osc = state.ctx.createOscillator();
    const env = state.ctx.createGain();
    osc.type = 'square';
    osc.frequency.value = 1500;
    env.gain.setValueAtTime(0, when);
    env.gain.linearRampToValueAtTime(1.0, when + 0.001);
    env.gain.exponentialRampToValueAtTime(0.0001, when + 0.04);
    osc.connect(env);
    env.connect(state.clickGain);
    osc.start(when);
    osc.stop(when + 0.05);
  }

  function clickSchedulerStep() {
    if (!state.isPlaying || !state.clickEnabled) return;
    if (!state.beatTimes.length || !state.ctx) return;
    const now = state.ctx.currentTime;
    const horizon = now + CLICK_LOOKAHEAD_SEC;
    while (state.clickNextBeatIdx < state.beatTimes.length) {
      const bt = state.beatTimes[state.clickNextBeatIdx];
      const audioWhen = state.playClockAnchor + (bt - state.playOffset);
      if (audioWhen < now - 0.01) {
        // Beat is already in the past for this playback run — skip it.
        state.clickNextBeatIdx++;
        continue;
      }
      if (audioWhen > horizon) break;
      scheduleClickTick(audioWhen);
      state.clickNextBeatIdx++;
    }
  }

  // Reset the index to the first beat >= currentPlayTime. Called whenever
  // playback (re)starts or seeks, since playClockAnchor / playOffset
  // change atomically there.
  function resetClickSchedule() {
    const songT = state.playOffset;
    let idx = 0;
    while (idx < state.beatTimes.length && state.beatTimes[idx] < songT - 0.01) idx++;
    state.clickNextBeatIdx = idx;
  }

  function startClickScheduler() {
    if (state.clickScheduler) return;
    ensureClickGain();
    resetClickSchedule();
    state.clickScheduler = setInterval(clickSchedulerStep, CLICK_TICK_MS);
    // Fire one immediate pass so the first beat lands without the
    // setInterval-quantization delay.
    clickSchedulerStep();
  }

  function stopClickScheduler() {
    if (state.clickScheduler) {
      clearInterval(state.clickScheduler);
      state.clickScheduler = null;
    }
  }

  $('t-click').addEventListener('click', () => {
    if (!state.beatTimes.length) return;
    state.clickEnabled = !state.clickEnabled;
    const btn = $('t-click');
    btn.textContent = state.clickEnabled ? 'Click: on' : 'Click: off';
    btn.classList.toggle('click-on', state.clickEnabled);
    if (state.clickEnabled && state.isPlaying) {
      startClickScheduler();
    } else {
      stopClickScheduler();
    }
  });

  // ---------------------------------------------- mixer: stems + master + monitor
  //
  // The mixer surfaces three flavours of channel:
  //   * stem channels      -- one per demucs stem; data-stem="<name>"
  //   * song master        -- routes state.masterGain; data-channel="song"
  //   * guitar input       -- routes state.monitor.gainNode; data-channel="guitar"
  //
  // setStemGain / setStemMuted operate per-stem. setSongGain /
  // setSongMuted and _setMonitorGain / _setMonitorMuted operate on
  // the master + monitor. toggleMute and toggleSolo dispatch on
  // dataset.channel vs dataset.stem so one button click handler
  // serves both row types.
  function setStemGain(name, g) {
    const stem = state.stems.get(name);
    if (!stem || !stem.gainNode) return;
    stem.lastGain = g;
    if (!stem.muted) stem.gainNode.gain.value = g;
  }
  function setStemMuted(name, muted) {
    const stem = state.stems.get(name);
    if (!stem || !stem.gainNode) return false;
    stem.muted = !!muted;
    stem.gainNode.gain.value = stem.muted ? 0.0 : (stem.lastGain ?? 1.0);
    return stem.muted;
  }
  function isMuted(name) {
    const stem = state.stems.get(name);
    return !!(stem && stem.muted);
  }
  function setSongGain(g) {
    state.songMix.gain = g;
    if (state.masterGain && !state.songMix.muted) {
      state.masterGain.gain.value = g;
    }
  }
  function setSongMuted(muted) {
    state.songMix.muted = !!muted;
    if (state.masterGain) {
      state.masterGain.gain.value = state.songMix.muted ? 0 : state.songMix.gain;
    }
    return state.songMix.muted;
  }
  function isSongMuted() { return !!state.songMix.muted; }

  function toggleMute(target, btn) {
    // `target` is either a stem name (data-stem) or the channel id
    // 'song' / 'guitar' (data-channel). Dispatch on the button's
    // dataset so the rack's row-template stays uniform.
    const ch = btn && btn.dataset && btn.dataset.channel;
    let muted;
    if (ch === 'song') {
      muted = setSongMuted(!isSongMuted());
    } else if (ch === 'guitar') {
      _setMonitorMuted(!state.monitor.muted);
      muted = state.monitor.muted;
    } else {
      muted = setStemMuted(target, !isMuted(target));
    }
    btn.textContent = muted ? 'Muted' : 'Mute';
  }

  function toggleSolo(target, btn) {
    // Solo is a global one-of-many across stems + song + guitar:
    // any solo active mutes every non-solo channel; clearing all
    // solos unmutes everything. Matches the legacy stem-only
    // behaviour (channel-level user mutes get overwritten).
    btn.classList.toggle('active');
    _applyAllSoloMutes();
    // Re-sync every Mute button's text since solos can change the
    // underlying mute state out from under the user.
    document.querySelectorAll('.mute-btn').forEach(b => {
      const cb = b.dataset.channel;
      if (cb === 'song') b.textContent = isSongMuted() ? 'Muted' : 'Mute';
      else if (cb === 'guitar') b.textContent = state.monitor.muted ? 'Muted' : 'Mute';
      else if (b.dataset.stem) b.textContent = isMuted(b.dataset.stem) ? 'Muted' : 'Mute';
    });
  }

  function _applyAllSoloMutes() {
    const anySolo = !!document.querySelector('.solo-btn.active');
    // Stems
    for (const [n] of state.stems.entries()) {
      const soloBtn = document.querySelector(`.solo-btn[data-stem="${n}"]`);
      const isSolo = !!(soloBtn && soloBtn.classList.contains('active'));
      if (anySolo) setStemMuted(n, !isSolo);
      else setStemMuted(n, false);
    }
    // Song master
    const songSoloBtn = document.querySelector('.solo-btn[data-channel="song"]');
    const songSoloed = !!(songSoloBtn && songSoloBtn.classList.contains('active'));
    if (anySolo) setSongMuted(!songSoloed);
    else setSongMuted(false);
    // Guitar input
    const guitarSoloBtn = document.querySelector('.solo-btn[data-channel="guitar"]');
    const guitarSoloed = !!(guitarSoloBtn && guitarSoloBtn.classList.contains('active'));
    if (anySolo) _setMonitorMuted(!guitarSoloed);
    else _setMonitorMuted(false);
  }

  // ---------------------------------------------- util
  function formatTime(t) {
    if (!isFinite(t) || t < 0) return '0:00';
    const m = Math.floor(t / 60);
    const s = Math.floor(t % 60).toString().padStart(2, '0');
    return `${m}:${s}`;
  }

  // ---------------------------------------------- deep-link: /jam/:id
  //
  // If the URL carries an analysis id, fetch the canonical SessionBundle
  // and jump straight to the performance view. SessionBundle is the
  // Priority-5 contract emitted by ``GET /api/session/:id`` (see
  // ``backend/tone_forge/session/bundle.py``); ``onAnalysisComplete``
  // still consumes the legacy AnalysisResult-shaped dict that the
  // streaming analyze path produces, so we project the bundle back into
  // that surface before calling it. Future work folds more of the
  // band-room consumers onto bundle fields directly; until then the
  // adapter is the bridge.
  //
  // Older history rows (pre-result persistence) fall back to
  // ``/api/history/:id`` so deep-link doesn't regress for legacy data.
  (function maybeDeepLink() {
    const m = window.location.pathname.match(/^\/jam\/([^\/]+)$/);
    if (!m) return;
    const id = m[1];

    fetch(`/api/session/${id}`)
      .then(r => {
        if (r.ok) return r.json().then(bundle => ({ kind: 'bundle', bundle }));
        if (r.status === 422) {
          // Entry exists but never persisted its analysis blob — fall
          // through to the legacy envelope so deep-link still works.
          return fetch(`/api/history/${id}`).then(r2 => {
            if (!r2.ok) throw new Error('not found');
            return r2.json().then(entry => ({ kind: 'legacy', entry }));
          });
        }
        throw new Error('not found');
      })
      .then(({ kind, bundle, entry }) => {
        const result = kind === 'bundle'
          ? bundleToLegacyResult(bundle)
          : entry;
        state.sourceUrl = result.source_url || null;
        state.userInstrument = result.detected_type || 'guitar';
        showView('bandroom');
        onAnalysisComplete(result);
      })
      .catch(() => {
        // Stay on intake on error.
      });
  })();

  // ---------------------------------------------- SessionBundle adapter
  //
  // Project a SessionBundle (Priority-5 contract) back onto the legacy
  // AnalysisResult-shaped dict that ``onAnalysisComplete`` reads. This
  // is a one-way translation used only on the deep-link path; the
  // streaming Studio path keeps writing AnalysisResult and skips this.
  //
  // The adapter never invents data — empty bundle fields project to
  // ``null`` / ``[]`` so the consumer's existing ``?? null`` fallbacks
  // continue to drive the "no data yet" UI.
  function bundleToLegacyResult(bundle) {
    if (!bundle || typeof bundle !== 'object') return {};
    const audio = bundle.audio || {};
    const understanding = bundle.understanding || {};
    const stems = bundle.stems || {};
    const userMidi = bundle.user_midi || null;

    // Project every stem the bundle actually carries — fixed slots plus
    // the ``extras`` array the assembler now preserves for stems beyond
    // the six-slot contract (``guitar_texture``, ``guitar_texture_2``,
    // ``guitar_rhythm``, ``piano``, ...). Each Stem.id is shaped as
    // ``legacy.<name>``; we strip the prefix to recover the original
    // dict key the rest of the client expects in ``stems_paths``.
    const stemsPaths = {};
    const addStem = (key, stem) => {
      if (!stem || !stem.audio_url) return;
      if (!key) return;
      stemsPaths[key] = stem.audio_url;
    };
    for (const slot of ['drums', 'bass', 'vocals', 'other', 'guitar_left', 'guitar_right']) {
      addStem(slot, stems[slot]);
    }
    const extras = Array.isArray(stems.extras) ? stems.extras : [];
    for (const stem of extras) {
      // Stem.id is "legacy.<name>" from _stem_from_path; fall back to
      // the display_name slugified if the id isn't shaped that way.
      let key = null;
      if (typeof stem.id === 'string' && stem.id.startsWith('legacy.')) {
        key = stem.id.slice('legacy.'.length);
      } else if (typeof stem.display_name === 'string') {
        key = stem.display_name.toLowerCase().replace(/\s+/g, '_');
      }
      addStem(key, stem);
    }

    // Tempo / key / preset_matches / tone: read the legacy sidecar
    // fields first (they survive the persistence round-trip), then
    // fall back to the bundle's structured fields. Using ``??`` so a
    // legitimate 0 doesn't get clobbered into null the way ``||`` did.
    const tempoBpm = (bundle.legacy_tempo_bpm ?? understanding.tempo_bpm) ?? null;
    const detectedKey = bundle.legacy_detected_key ?? understanding.key ?? null;
    const presetMatches = bundle.legacy_preset_matches && typeof bundle.legacy_preset_matches === 'object'
      ? bundle.legacy_preset_matches
      : {};
    const tone = bundle.legacy_tone || null;

    return {
      // Identity
      id: bundle.session_id,
      analysis_id: bundle.session_id,
      // Acquisition
      source_url: audio.source_uri || null,
      source_name: audio.source_title || null,
      source_title: audio.source_title || null,
      duration_sec: audio.duration_s ?? null,
      content_hash: audio.content_hash || null,
      wav_path: audio.wav_path || null,
      // Role
      detected_type: bundle.user_role || 'guitar',
      // Understanding
      tempo_bpm: tempoBpm,
      detected_key: detectedKey,
      sections: understanding.sections || [],
      // JAM Alpha: project chord lane onto the legacy top-level field
      // shape that onAnalysisComplete / buildChordRibbon consume. The
      // bundle assembler persists chords as
      // SongUnderstanding.chords; empty/missing becomes [] so the
      // ribbon stays hidden on legacy sessions without a chord lane.
      chords: understanding.chords || [],
      // Phase 6 hybrid grid: beat-snapped chord boundaries. Null when
      // the upstream analysis didn't produce a snapped array (e.g.
      // beats unavailable). onAnalysisComplete caches both and the
      // beat-snap toggle picks at render time.
      chords_beat_snapped: understanding.chords_beat_snapped || null,
      beat_times: understanding.beats_s || [],
      // Stems
      stems_paths: stemsPaths,
      // MIDI
      midi: userMidi ? {
        notes: userMidi.notes || [],
        overall_confidence: userMidi.overall_confidence ?? 0,
      } : null,
      // Tone — carries the persisted ``to_wire_dict`` payload so
      // renderToneCard can re-render the SUGGESTED chain after refresh.
      tone,
      preset_matches: presetMatches,
    };
  }

  // -------------------------------------------------------- learning assistance
  //
  // Mic-input pitch detection + intonation feedback against the song key.
  //
  // The detector runs an autocorrelation pass on a 2048-sample window at
  // requestAnimationFrame rate (~60Hz). Output is the nearest equal-tempered
  // semitone, the cents offset, and whether that pitch class is in the
  // detected song key. A rolling 5-second history powers the "% in tune"
  // and "% in key" readouts.

  const NOTE_NAMES_SHARP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
  const NOTE_NAMES_FLAT  = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B'];
  const MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11];
  const MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10]; // natural minor
  const NOTE_NAME_TO_PC = {
    'C': 0, 'B#': 0,
    'C#': 1, 'Db': 1,
    'D': 2,
    'D#': 3, 'Eb': 3,
    'E': 4, 'Fb': 4,
    'F': 5, 'E#': 5,
    'F#': 6, 'Gb': 6,
    'G': 7,
    'G#': 8, 'Ab': 8,
    'A': 9,
    'A#': 10, 'Bb': 10,
    'B': 11, 'Cb': 11,
  };

  function parseDetectedKey(s) {
    // Accepts "C Major", "A Minor", "F# Major", "Eb Minor", "—".
    if (!s || typeof s !== 'string' || s === '—') return null;
    const parts = s.trim().split(/\s+/);
    if (parts.length < 2) return null;
    const root = NOTE_NAME_TO_PC[parts[0]];
    if (root == null) return null;
    const scale = /minor/i.test(parts[1]) ? 'Minor' : 'Major';
    const intervals = scale === 'Major' ? MAJOR_INTERVALS : MINOR_INTERVALS;
    const pitchClasses = new Set(intervals.map(i => (root + i) % 12));
    return { root, scale, pitchClasses };
  }

  function freqToMidi(freq) {
    return 69 + 12 * Math.log2(freq / 440);
  }

  function midiToNoteName(midi) {
    const pc = ((Math.round(midi) % 12) + 12) % 12;
    const octave = Math.floor(Math.round(midi) / 12) - 1;
    return `${NOTE_NAMES_SHARP[pc]}${octave}`;
  }

  // Autocorrelation pitch detector adapted from the well-known WebAudio
  // tuner pattern. Returns frequency in Hz, or -1 if no clear pitch.
  function autoCorrelate(buf, sampleRate) {
    const SIZE = buf.length;
    let rms = 0;
    for (let i = 0; i < SIZE; i++) rms += buf[i] * buf[i];
    rms = Math.sqrt(rms / SIZE);
    if (rms < 0.01) return -1; // too quiet
    // Trim silent edges
    const THRESHOLD = 0.2;
    let r1 = 0, r2 = SIZE - 1;
    for (let i = 0; i < SIZE / 2; i++) {
      if (Math.abs(buf[i]) < THRESHOLD) { r1 = i; break; }
    }
    for (let i = 1; i < SIZE / 2; i++) {
      if (Math.abs(buf[SIZE - i]) < THRESHOLD) { r2 = SIZE - i; break; }
    }
    const trimmed = buf.slice(r1, r2);
    const N = trimmed.length;
    if (N < 256) return -1;
    const c = new Array(N).fill(0);
    for (let lag = 0; lag < N; lag++) {
      for (let i = 0; i < N - lag; i++) {
        c[lag] += trimmed[i] * trimmed[i + lag];
      }
    }
    // Find first dip then the next peak
    let d = 0;
    while (d < N - 1 && c[d] > c[d + 1]) d++;
    let maxval = -1, maxpos = -1;
    for (let i = d; i < N; i++) {
      if (c[i] > maxval) { maxval = c[i]; maxpos = i; }
    }
    if (maxpos <= 0) return -1;
    // Parabolic interpolation for sub-sample accuracy
    let T0 = maxpos;
    const x1 = c[T0 - 1], x2 = c[T0], x3 = c[T0 + 1];
    const a = (x1 + x3 - 2 * x2) / 2;
    const b = (x3 - x1) / 2;
    if (a !== 0) T0 = T0 - b / (2 * a);
    return sampleRate / T0;
  }

  // ---- settings persistence ----
  const SETTINGS_KEY = 'tone-forge.jam.settings.v1';

  function loadSettings() {
    try {
      const raw = localStorage.getItem(SETTINGS_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (typeof parsed.listenEnabled === 'boolean') {
        state.settings.listenEnabled = parsed.listenEnabled;
      }
      if (['cents', 'rolling', 'full'].includes(parsed.feedbackView)) {
        state.settings.feedbackView = parsed.feedbackView;
      }
    } catch (e) {
      console.warn('Failed to load jam settings:', e);
    }
  }

  function saveSettings() {
    try {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
    } catch (e) {
      // Ignore — private mode, quota exceeded, etc.
    }
  }

  // ---- mic capture ----

  // Resolve the user's stored audio_input_name (e.g. "Line 6 HX Stomp")
  // to a Web Audio deviceId. enumerateDevices() only returns labels for
  // devices the page has already been granted permission to, so this
  // must run AFTER a successful getUserMedia call.
  async function _findInputDeviceIdByName(preferredName) {
    if (!preferredName) return null;
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      const norm = preferredName.toLowerCase();
      const match = devs.find(d =>
        d.kind === 'audioinput'
        && typeof d.label === 'string'
        && d.label.toLowerCase().includes(norm)
      );
      return match ? match.deviceId : null;
    } catch { return null; }
  }

  // Base capture constraints. AEC is on by default so the laptop's
  // own playback doesn't get scored as the player's input; when we
  // re-acquire on a dedicated hardware input (HX Stomp etc.) AEC is
  // not helpful and we drop it for cleaner pitch detection.
  function _captureConstraints(deviceId, isHardwareInput) {
    const audio = {
      echoCancellation: !isHardwareInput,
      noiseSuppression: false,
      autoGainControl: false,
    };
    if (deviceId) audio.deviceId = { exact: deviceId };
    return { audio };
  }

  async function startListening() {
    if (state.listen.stream) return; // already running
    try {
      // First pass: default device. Required to populate device labels
      // for enumerateDevices() — browsers hide labels until permission
      // has been granted at least once for the page.
      let stream = await navigator.mediaDevices.getUserMedia(
        _captureConstraints(null, false),
      );
      let acquiredVia = 'default';
      // Second pass: if the user has a stored audio_input_name and
      // it resolves to a different deviceId, re-acquire on that
      // device. Failures fall back silently to the default stream.
      //
      // Observability is load-bearing here: previously this whole block
      // was silent on every code path, so a user whose Helix wasn't
      // being matched ("listening to mic instead") had no diagnostic
      // surface and no idea which of {prefs missing, label mismatch,
      // enumerate visibility, exact-id reacquire failure} was hitting
      // them. We log every branch and surface the active device in
      // both `state.listen.deviceLabel` and the tuner status text so
      // the failure mode is visible without DevTools spelunking.
      try {
        const prefs = await fetchDevicePreferences();
        const preferredName = prefs && prefs.audio_input_name;
        if (!preferredName) {
          console.info('[listen] no preferred input stored; using default mic');
        } else {
          const devs = await navigator.mediaDevices.enumerateDevices();
          const inputs = devs.filter(d => d.kind === 'audioinput');
          const labels = inputs.map(d => d.label || '(no label)');
          const targetId = await _findInputDeviceIdByName(preferredName);
          const track = stream.getAudioTracks()[0];
          const currentId = track && track.getSettings
            ? track.getSettings().deviceId
            : null;
          if (!targetId) {
            // The user's preferred device isn't visible to the page.
            // Log the labels we DID see so we can tell whether the
            // mismatch is "device not enumerated at all" (USB lost /
            // hot-plugged after page load) or "labelled differently
            // than the stored audio_input_name" (probe wrote
            // "Line 6 HX Stomp" but Chromium shows "HX Stomp"
            // without the vendor prefix, etc).
            console.warn(
              '[listen] preferred input not visible to browser; using default mic. '
              + `wanted="${preferredName}" visible=${JSON.stringify(labels)}`
            );
          } else if (targetId === currentId) {
            // The default already IS the preferred device. Common when
            // the user has only the HX Stomp connected.
            acquiredVia = 'preferred-as-default';
            console.info(
              `[listen] preferred input "${preferredName}" was already the default; no re-acquire`
            );
          } else {
            // Stop the default stream and re-acquire on the target id.
            // If the re-acquire throws (busy / disconnected mid-flight)
            // we surface that explicitly rather than silently keeping
            // the wrong stream.
            for (const t of stream.getTracks()) t.stop();
            try {
              stream = await navigator.mediaDevices.getUserMedia(
                _captureConstraints(targetId, true),
              );
              acquiredVia = 'preferred-reacquire';
              console.info(
                `[listen] re-acquired on preferred input "${preferredName}" `
                + `(deviceId=${targetId.slice(0, 8)}...)`
              );
            } catch (reacqErr) {
              // Don't strand the user with a silent failure. Fall back
              // to the default mic, but make sure they know.
              console.error(
                `[listen] re-acquire on "${preferredName}" FAILED — falling back to default mic`,
                reacqErr,
              );
              stream = await navigator.mediaDevices.getUserMedia(
                _captureConstraints(null, false),
              );
              acquiredVia = 'reacquire-failed-fallback';
            }
          }
        }
      } catch (e) {
        console.warn('[listen] preferred-device acquire failed; using default:', e);
      }
      // Reuse the playback audio context if one exists.
      const ctx = state.ctx || new (window.AudioContext || window.webkitAudioContext)();
      state.ctx = ctx;
      const sourceNode = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 2048;
      sourceNode.connect(analyser);
      state.listen.stream = stream;
      state.listen.sourceNode = sourceNode;
      state.listen.analyser = analyser;
      state.listen.buffer = new Float32Array(analyser.fftSize);
      state.listen.history = [];
      // Read back the actual device label/id we ended up on. The label
      // can be empty if the OS didn't expose it; in that case we fall
      // back to acquiredVia so the user still sees something useful.
      const finalTrack = stream.getAudioTracks()[0];
      const finalLabel = (finalTrack && finalTrack.label) || '';
      const finalSettings = finalTrack && finalTrack.getSettings ? finalTrack.getSettings() : {};
      state.listen.deviceLabel = finalLabel;
      state.listen.deviceId = finalSettings.deviceId || null;
      state.listen.acquiredVia = acquiredVia;
      console.info(
        `[listen] started — via=${acquiredVia} label="${finalLabel}" `
        + `channels=${finalSettings.channelCount || '?'} sr=${finalSettings.sampleRate || '?'}`
      );
      $('intonation-panel').hidden = false;
      // Surface the active input in the status text so the user can
      // spot a default-mic fallback without opening DevTools.
      const statusLabel = finalLabel || acquiredVia;
      $('into-status').textContent = `Listening — ${statusLabel}`;
      pitchTick();
    } catch (err) {
      $('into-status').textContent = 'Mic permission denied or unavailable';
      console.warn('startListening failed:', err);
      throw err;
    }
  }

  function stopListening() {
    if (state.listen.rafHandle) {
      cancelAnimationFrame(state.listen.rafHandle);
      state.listen.rafHandle = null;
    }
    if (state.listen.sourceNode) {
      try { state.listen.sourceNode.disconnect(); } catch {}
      state.listen.sourceNode = null;
    }
    if (state.listen.stream) {
      for (const track of state.listen.stream.getTracks()) track.stop();
      state.listen.stream = null;
    }
    state.listen.analyser = null;
    state.listen.buffer = null;
    state.listen.history = [];
    $('into-status').textContent = 'Idle';
    $('into-note').textContent = '—';
    $('into-cents').textContent = '0¢';
    $('into-needle').style.left = '50%';
    $('into-needle').classList.remove('in-tune', 'flat', 'sharp');
    $('into-rolling-pct').textContent = '—';
    $('into-in-key').textContent = '—';
    $('into-stable').textContent = '—';
  }

  function pitchTick() {
    const { analyser, buffer } = state.listen;
    if (!analyser || !buffer) return;
    analyser.getFloatTimeDomainData(buffer);
    const sampleRate = state.ctx.sampleRate;
    const freq = autoCorrelate(buffer, sampleRate);
    const now = performance.now();
    if (freq > 0) {
      const midi = freqToMidi(freq);
      const rounded = Math.round(midi);
      const cents = Math.round((midi - rounded) * 100);
      const pc = ((rounded % 12) + 12) % 12;
      const inKey = state.songKey ? state.songKey.pitchClasses.has(pc) : null;
      updateIntonationDisplay(midi, cents, inKey);
      state.listen.history.push({ t_ms: now, cents, inKey });
    } else {
      // Decay the needle visually when no pitch
      $('into-cents').textContent = '—';
    }
    // Onset detection runs on the same buffer (RMS spike vs moving baseline).
    detectOnset(buffer, now);
    // Trim history to last 5 s
    const cutoff = now - 5000;
    while (state.listen.history.length && state.listen.history[0].t_ms < cutoff) {
      state.listen.history.shift();
    }
    // Trim onsets to last 10 s
    const onsetCutoff = now - 10000;
    while (state.listen.onsets.length && state.listen.onsets[0].t_perf < onsetCutoff) {
      state.listen.onsets.shift();
    }
    updateRollingStats();
    updateTimingStats();
    state.listen.rafHandle = requestAnimationFrame(pitchTick);
  }

  // ---- onset detection + grid-offset scoring ----
  // Energy-based onset detector: maintain an EMA of RMS as a baseline,
  // flag an onset when current RMS exceeds baseline * threshold and the
  // refractory period has elapsed since the last onset.
  const ONSET_THRESHOLD = 2.2;     // multiplier over baseline
  const ONSET_REFRACTORY_MS = 90;  // ignore re-triggers within this window
  const ONSET_BASELINE_ALPHA = 0.03;
  const ONSET_MIN_RMS = 0.015;     // ignore room-noise level

  function detectOnset(buf, nowPerf) {
    let sum = 0;
    for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
    const rms = Math.sqrt(sum / buf.length);
    const L = state.listen;
    // Update baseline (slow EMA).
    if (L.rmsBaseline === 0) L.rmsBaseline = rms;
    else L.rmsBaseline = (1 - ONSET_BASELINE_ALPHA) * L.rmsBaseline + ONSET_BASELINE_ALPHA * rms;
    if (rms < ONSET_MIN_RMS) return;
    if (rms < L.rmsBaseline * ONSET_THRESHOLD) return;
    if (nowPerf - L.lastOnsetMs < ONSET_REFRACTORY_MS) return;
    L.lastOnsetMs = nowPerf;
    // Score against the beat grid when playback is active.
    const offsetMs = computeBeatOffsetMs();
    L.lastOffsetMs = offsetMs;
    L.onsets.push({ t_perf: nowPerf, offset_ms: offsetMs });
  }

  function computeBeatOffsetMs() {
    if (!state.beatTimes || !state.beatTimes.length) return null;
    if (!state.isPlaying) return null;
    const songT = currentPlayTime();
    // Binary search for the nearest beat. beatTimes is sorted ascending.
    const bt = state.beatTimes;
    let lo = 0, hi = bt.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (bt[mid] < songT) lo = mid + 1;
      else hi = mid;
    }
    const a = bt[Math.max(0, lo - 1)];
    const b = bt[lo];
    const nearest = Math.abs(songT - a) < Math.abs(songT - b) ? a : b;
    return Math.round((songT - nearest) * 1000);
  }

  function updateTimingStats() {
    const L = state.listen;
    const lastEl = $('into-timing-last');
    const tightEl = $('into-timing-tight');
    const looseEl = $('into-timing-loose');
    if (!lastEl) return;
    if (L.lastOffsetMs == null) {
      lastEl.textContent = '—';
    } else {
      const v = L.lastOffsetMs;
      lastEl.textContent = (v >= 0 ? '+' : '') + v + 'ms';
    }
    const offsets = L.onsets.map(o => o.offset_ms).filter(v => v != null);
    if (!offsets.length) {
      tightEl.textContent = '—';
      looseEl.textContent = '—';
      return;
    }
    let tight = 0, loose = 0;
    for (const v of offsets) {
      if (Math.abs(v) <= 50) tight++;
      if (Math.abs(v) <= 100) loose++;
    }
    tightEl.textContent = Math.round((tight / offsets.length) * 100) + '%';
    looseEl.textContent = Math.round((loose / offsets.length) * 100) + '%';
  }

  function updateIntonationDisplay(midi, cents, inKey) {
    $('into-note').textContent = midiToNoteName(midi);
    $('into-cents').textContent = (cents >= 0 ? '+' : '') + cents + '¢';
    // Needle: clamp ±50 cents to 0–100% horizontal position.
    const clamped = Math.max(-50, Math.min(50, cents));
    const pct = 50 + clamped; // 0..100
    const needle = $('into-needle');
    needle.style.left = pct + '%';
    needle.classList.remove('in-tune', 'flat', 'sharp');
    if (Math.abs(cents) <= 15) needle.classList.add('in-tune');
    else if (cents < 0) needle.classList.add('flat');
    else needle.classList.add('sharp');
    if (inKey === true) $('into-status').textContent = 'In key';
    else if (inKey === false) $('into-status').textContent = 'Out of key';
    else $('into-status').textContent = 'Listening (no key info)';
  }

  function updateRollingStats() {
    const h = state.listen.history;
    if (!h.length) {
      $('into-rolling-pct').textContent = '—';
      $('into-in-key').textContent = '—';
      $('into-stable').textContent = '—';
      return;
    }
    let inTune = 0, inKeyCount = 0, withKey = 0;
    for (const s of h) {
      if (Math.abs(s.cents) <= 15) inTune++;
      if (s.inKey === true) inKeyCount++;
      if (s.inKey != null) withKey++;
    }
    const inTunePct = Math.round((inTune / h.length) * 100);
    $('into-rolling-pct').textContent = inTunePct + '%';
    $('into-stable').textContent = inTunePct + '%';
    if (withKey > 0) {
      $('into-in-key').textContent = Math.round((inKeyCount / withKey) * 100) + '%';
    } else {
      $('into-in-key').textContent = '—';
    }
  }

  function applyFeedbackView() {
    // Hide/show the panel + sub-sections based on settings.
    const panel = $('intonation-panel');
    const rolling = $('into-rolling');
    const full = $('into-full');
    // Legacy panel is now an empty placeholder; keep gating it so any
    // residual visibility CSS stays consistent.
    if (panel) panel.hidden = !state.settings.listenEnabled;
    // perform v2: the visible tuner is #tuner-compact in the left
    // slot. Gate it on listenEnabled — without the mic, the tuner
    // can't say anything meaningful.
    const tuner = document.getElementById('tuner-compact');
    if (tuner) tuner.hidden = !state.settings.listenEnabled;
    if (rolling) rolling.hidden = state.settings.feedbackView === 'cents';
    if (full) full.hidden = state.settings.feedbackView !== 'full';
  }

  // ---- device-discovery onboarding (§8) ----
  // The Jam page is the first surface a new user sees, so it owns the
  // single-question onboarding modal. The persistence + DeviceCaps
  // mapping live server-side (tone_forge_api.py `/api/device/preferences`);
  // this module is purely the UI gate.
  //
  // Display labels mirror the EXECUTION_PLAN.md §8 spec verbatim so the
  // settings "current" line matches what the user originally picked.
  const DEVICE_CLASS_LABELS = {
    interface_only: 'Just my audio interface',
    helix: 'Helix',
    quad_cortex: 'Quad Cortex',
    kemper: 'Kemper',
    fractal: 'Fractal',
    tonex: 'Tonex',
    neural_dsp: 'Neural DSP plugin',
    // The enum has additional values (connect_monitor, no_hardware) that
    // the onboarding screen does not surface; if they show up server-side
    // (e.g. via a future CLI) we still want to render a readable label.
    connect_monitor: 'Connect monitor',
    no_hardware: 'No hardware',
    other: 'Something else',
  };

  function deviceLabelFor(deviceClass) {
    return DEVICE_CLASS_LABELS[deviceClass] || deviceClass || 'Not set';
  }

  function updateDeviceSettingLabel(prefs) {
    const el = $('setting-device-current');
    if (!el) return;
    if (prefs && prefs.device_class) {
      el.textContent = `Currently: ${deviceLabelFor(prefs.device_class)}`;
    } else {
      el.textContent = 'Not set';
    }
  }

  // Reflect the persisted Sparkle auto-update preference into the
  // checkbox. ``null`` (= user has not expressed a preference) maps
  // to ``true`` because Sparkle's built-in default is "checks
  // enabled" when no override exists; that's what would actually
  // happen at runtime, so the UI must show it as checked or the
  // user would see "off" and be lied to.
  function updateAutoUpdateSetting(prefs) {
    const cb = document.getElementById('setting-auto-update-enabled');
    if (!cb) return;
    const persisted = prefs && typeof prefs.auto_update_enabled === 'boolean'
      ? prefs.auto_update_enabled
      : null;
    cb.checked = persisted === null ? true : persisted;
  }

  async function fetchDevicePreferences() {
    try {
      const res = await fetch('/api/device/preferences');
      if (!res.ok) return null;
      return await res.json(); // null when nothing persisted
    } catch (e) {
      console.warn('[onboarding] GET /api/device/preferences failed:', e);
      return null;
    }
  }

  // Latest probe result; populated asynchronously after the modal opens.
  // The submit handler reads from it to compose `audio_input_name`.
  let _devicesProbeResult = null;

  async function fetchDeviceProbe() {
    try {
      const res = await fetch('/api/device/probe');
      if (!res.ok) return null;
      return await res.json();
    } catch (e) {
      console.warn('[onboarding] GET /api/device/probe failed:', e);
      return null;
    }
  }

  // Substring patterns that map a CoreAudio device name to a
  // device_class radio value. Hint surface only — the user still
  // confirms; nothing auto-selects.
  const DEVICE_CLASS_DETECT_PATTERNS = {
    helix: ['helix', 'hx stomp', 'hx effects'],
    quad_cortex: ['quad cortex', 'qcortex'],
    kemper: ['kemper', 'profiler'],
    fractal: ['axe-fx', 'fm3', 'fm9', 'axefx'],
    tonex: ['tonex'],
    neural_dsp: ['neural dsp', 'cortex mobile'],
  };

  function detectedDeviceClasses(devices) {
    // Returns a Set of device_class values whose detection patterns
    // match at least one probed device name.
    const hits = new Set();
    if (!Array.isArray(devices)) return hits;
    for (const d of devices) {
      const name = (d && d.name) ? d.name.toLowerCase() : '';
      if (!name) continue;
      for (const [klass, patterns] of Object.entries(DEVICE_CLASS_DETECT_PATTERNS)) {
        if (patterns.some(p => name.includes(p))) hits.add(klass);
      }
    }
    return hits;
  }

  function clearOnboardingDetectedBadges() {
    for (const badge of document.querySelectorAll('.onboarding-detected-badge')) {
      badge.remove();
    }
  }

  function markOnboardingDetectedClasses(hits) {
    clearOnboardingDetectedBadges();
    if (!hits || hits.size === 0) return;
    for (const klass of hits) {
      const radio = document.querySelector(
        `#onboarding-form input[name="device-class"][value="${klass}"]`,
      );
      if (!radio) continue;
      const label = radio.closest('.onboarding-option');
      if (!label) continue;
      const badge = document.createElement('span');
      badge.className = 'onboarding-detected-badge';
      badge.textContent = 'Detected';
      label.appendChild(badge);
    }
  }

  function populateOnboardingProbeUI(probe) {
    if (!probe || !probe.probe_succeeded) {
      clearOnboardingDetectedBadges();
      return;
    }
    // Per-option "Detected" badges (independent of suggested_input —
    // we can flag a modeler even when no vendor-known interface is
    // present, and vice versa).
    markOnboardingDetectedClasses(detectedDeviceClasses(probe.devices));
    // Global Detected row reflects the suggested *audio interface*
    // (for audio_input_name capture). Only shown when probe gave us
    // a suggested_input.
    if (!probe.suggested_input) return;
    const detectedRow = $('onboarding-input-detected');
    const nameEl = $('onboarding-input-name');
    const select = $('onboarding-input-select');
    if (!detectedRow || !nameEl || !select) return;
    nameEl.textContent = probe.suggested_input.name;
    // Repopulate the <select> from probe.devices, suggested input first.
    select.innerHTML = '';
    const devices = Array.isArray(probe.devices) ? probe.devices : [];
    for (const d of devices) {
      const opt = document.createElement('option');
      opt.value = d.name;
      opt.textContent = d.name;
      if (probe.suggested_input && d.name === probe.suggested_input.name) {
        opt.selected = true;
      }
      select.appendChild(opt);
    }
    detectedRow.hidden = false;
  }

  function showOnboardingModal() {
    const modal = $('onboarding-modal');
    if (!modal) return;
    modal.hidden = false;
    // Focus the first option for keyboard users.
    const first = modal.querySelector('input[name="device-class"]');
    if (first) first.focus();
    // Kick off the probe — do NOT await; the modal is interactive
    // immediately, and the Detected row simply appears later when /
    // if the probe resolves successfully.
    fetchDeviceProbe().then((probe) => {
      _devicesProbeResult = probe;
      populateOnboardingProbeUI(probe);
    });
  }

  function hideOnboardingModal() {
    const modal = $('onboarding-modal');
    if (!modal) return;
    modal.hidden = true;
    // Reset selection so a future re-prompt (after Reset device choice)
    // doesn't keep the prior answer pre-checked.
    const form = $('onboarding-form');
    if (form) form.reset();
    const err = $('onboarding-error');
    if (err) { err.hidden = true; err.textContent = ''; }
    // Hide the probe rows; the next show() will re-fetch and reveal.
    const detectedRow = $('onboarding-input-detected');
    if (detectedRow) detectedRow.hidden = true;
    const picker = $('onboarding-input-picker');
    if (picker) picker.hidden = true;
    _devicesProbeResult = null;
  }

  function chosenAudioInputName() {
    // Returns the audio input name the user has either accepted
    // (Detected row visible) or picked from the dropdown, or null
    // when the probe row never appeared.
    const detectedRow = $('onboarding-input-detected');
    const picker = $('onboarding-input-picker');
    if (picker && !picker.hidden) {
      const select = $('onboarding-input-select');
      return select && select.value ? select.value : null;
    }
    if (detectedRow && !detectedRow.hidden) {
      const nameEl = $('onboarding-input-name');
      return nameEl && nameEl.textContent ? nameEl.textContent : null;
    }
    return null;
  }

  function initOnboardingUI() {
    const form = $('onboarding-form');
    if (!form) return;
    const changeBtn = $('onboarding-input-change');
    if (changeBtn) {
      changeBtn.addEventListener('click', () => {
        const detectedRow = $('onboarding-input-detected');
        const picker = $('onboarding-input-picker');
        if (detectedRow) detectedRow.hidden = true;
        if (picker) picker.hidden = false;
      });
    }
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const selected = form.querySelector('input[name="device-class"]:checked');
      const err = $('onboarding-error');
      if (!selected) {
        if (err) { err.textContent = 'Pick one to continue.'; err.hidden = false; }
        return;
      }
      const submitBtn = $('onboarding-submit');
      if (submitBtn) submitBtn.disabled = true;
      try {
        const reqBody = { device_class: selected.value };
        const inputName = chosenAudioInputName();
        if (inputName) reqBody.audio_input_name = inputName;
        const res = await fetch('/api/device/preferences', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(reqBody),
        });
        if (!res.ok) {
          const errBody = await res.json().catch(() => ({}));
          throw new Error(errBody.detail || `HTTP ${res.status}`);
        }
        const prefs = await res.json();
        updateDeviceSettingLabel(prefs);
        try { refreshAudioInName(); } catch (_) {}
        hideOnboardingModal();
      } catch (e) {
        console.warn('[onboarding] POST /api/device/preferences failed:', e);
        if (err) {
          err.textContent = `Couldn't save your choice (${e.message || e}). Try again.`;
          err.hidden = false;
        }
      } finally {
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  async function resetDeviceChoice() {
    try {
      const res = await fetch('/api/device/preferences', { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      console.warn('[onboarding] DELETE /api/device/preferences failed:', e);
      return;
    }
    updateDeviceSettingLabel(null);
    // Close the popover so the modal isn't competing for attention.
    const popover = $('settings-popover');
    if (popover) popover.hidden = true;
    showOnboardingModal();
  }

  // Probe on startup. Modal shows only when nothing is persisted; the
  // intake view stays interactive underneath in case the user dismisses
  // via the browser back button (the next page load re-prompts).
  (async () => {
    const prefs = await fetchDevicePreferences();
    updateDeviceSettingLabel(prefs);
    updateAutoUpdateSetting(prefs);
    if (prefs === null) showOnboardingModal();
  })();
  initOnboardingUI();

  // Surface-level "Change audio interface" link on the intake page —
  // same flow as the settings popover's Reset button, just more
  // discoverable for users who want to re-pick without digging into
  // settings.
  const intakeChangeBtn = $('intake-change-device');
  if (intakeChangeBtn) {
    intakeChangeBtn.addEventListener('click', () => { resetDeviceChoice(); });
  }

  // Same affordance on the perform view — users mid-session who
  // realize they want a different interface shouldn't have to dig
  // into the settings popover. Reuses the same reset flow, which
  // re-opens the onboarding modal.
  const performChangeBtn = $('perform-change-device');
  if (performChangeBtn) {
    performChangeBtn.addEventListener('click', () => { resetDeviceChoice(); });
  }

  // ---- settings popover wiring ----
  function initSettingsUI() {
    loadSettings();
    const popover = $('settings-popover');
    const listenCb = $('setting-listen-enabled');
    listenCb.checked = state.settings.listenEnabled;
    for (const r of document.querySelectorAll('input[name="feedback-view"]')) {
      r.checked = r.value === state.settings.feedbackView;
    }
    $('perform-settings').addEventListener('click', () => {
      popover.hidden = !popover.hidden;
    });
    // Close the popover on outside click
    document.addEventListener('click', (ev) => {
      if (popover.hidden) return;
      if (popover.contains(ev.target)) return;
      if (ev.target.id === 'perform-settings') return;
      popover.hidden = true;
    });
    listenCb.addEventListener('change', async () => {
      state.settings.listenEnabled = listenCb.checked;
      saveSettings();
      applyFeedbackView();
      if (state.settings.listenEnabled) {
        try { await startListening(); } catch {}
      } else {
        stopListening();
      }
    });
    for (const r of document.querySelectorAll('input[name="feedback-view"]')) {
      r.addEventListener('change', () => {
        if (r.checked) {
          state.settings.feedbackView = r.value;
          saveSettings();
          applyFeedbackView();
        }
      });
    }
    const deviceReset = $('setting-device-reset');
    if (deviceReset) {
      deviceReset.addEventListener('click', () => { resetDeviceChoice(); });
    }

    // Sparkle auto-update toggle. The POST round-trip is "optimistic":
    // we keep the checkbox in its newly-chosen state and only flip it
    // back on a server failure. Rationale: the WS broadcast that the
    // server fires on success will fire `set_auto_update` back at
    // every connected tab (including this one) — if we waited for
    // that round-trip to flip the UI, the local toggle would look
    // unresponsive for ~50ms. The inbound handler is idempotent so a
    // second `checked = X` does no harm.
    const autoUpdateCb = document.getElementById('setting-auto-update-enabled');
    if (autoUpdateCb) {
      autoUpdateCb.addEventListener('change', async () => {
        const desired = autoUpdateCb.checked;
        // We must re-send the existing device_class because the
        // request model requires it; auto-update lives on the same
        // record. fetchDevicePreferences is cheap (single GET) and
        // avoids stashing a stale shadow copy in module state that
        // could drift if the user changed device class in another tab.
        const current = await fetchDevicePreferences();
        if (!current || !current.device_class) {
          // No device record yet — user hit the toggle before
          // finishing onboarding. Revert and bail; the onboarding
          // submit will land the bool with the default of `true`.
          autoUpdateCb.checked = !desired;
          console.warn(
            '[settings] cannot toggle auto-update before device class is set',
          );
          return;
        }
        try {
          const res = await fetch('/api/device/preferences', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              device_class: current.device_class,
              audio_input_name: current.audio_input_name,
              preferred_chain_family: current.preferred_chain_family,
              auto_update_enabled: desired,
            }),
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
        } catch (e) {
          console.warn('[settings] POST auto_update_enabled failed:', e);
          // Roll back so the UI matches server-side truth.
          autoUpdateCb.checked = !desired;
        }
      });
    }
  }

  // =====================================================================
  // Perform v2 wiring — stats disclosure, audio-in, tone send-to-connect,
  // header Connect pill + monitor popover, waveform timeline, t-loop,
  // instrument selector. All elements live in the new perform layout
  // (#perform-grid + #playback-bar). Existing IDs are preserved so the
  // legacy code paths above continue to work unchanged.
  // =====================================================================

  // ---- 3f: stats disclosure -------------------------------------------
  (function initStatsDisclosure() {
    const toggle = document.getElementById('tuner-stats-toggle');
    const body = document.getElementById('tuner-stats-body');
    if (!toggle || !body) return;
    toggle.addEventListener('click', () => {
      const willOpen = !!body.hidden;
      body.hidden = !willOpen;
      toggle.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
      toggle.textContent = willOpen ? 'Hide stats ▴' : 'Show stats ▾';
    });
  })();

  // ---- 3g: audio-in row -----------------------------------------------
  async function refreshAudioInName() {
    const nameEl = document.getElementById('audio-in-name');
    if (!nameEl) return;
    try {
      const prefs = await fetchDevicePreferences();
      const label = (prefs && prefs.audio_input_name)
        ? prefs.audio_input_name
        : 'System default';
      nameEl.textContent = label;
    } catch {
      nameEl.textContent = 'System default';
    }
  }
  (function initAudioInRow() {
    const changeBtn = document.getElementById('audio-in-change');
    if (changeBtn) {
      changeBtn.addEventListener('click', () => { resetDeviceChoice(); });
    }
    // Initial paint — onboarding submit + onAnalysisComplete will
    // refresh it again, but populating it on first paint avoids the
    // "System default" -> real name flicker for returning users.
    refreshAudioInName();
  })();

  // ---- 3h: tone match Send-to-Connect ---------------------------------
  (function initToneSendToConnect() {
    const btn = document.getElementById('tone-send-connect');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const rec = state.tone && state.tone.rec;
      const chainId = rec && rec.apply && rec.apply.chain_id;
      if (chainId) {
        applyToneChain(chainId);
        return;
      }
      if (state.connectBridge && state.connectBridge.lastPreset) {
        try {
          sendOrQueueBridgeMessage({
            type: 'apply_preset',
            preset: state.connectBridge.lastPreset,
          });
          flashConnectStatus(
            'Re-synced tone preset to Connect.', true, 3500,
          );
        } catch (e) {
          flashConnectStatus(
            'Could not reach Connect — is the helper running?',
            false, 3500,
          );
        }
        return;
      }
      flashConnectStatus(
        'No tone match yet — analyze a song first.', false, 3500,
      );
    });
  })();

  // ---- 3i: header Connect pill + monitor popover ----------------------
  function renderConnectPill() {
    const pill = document.getElementById('header-connect-pill');
    if (!pill) return;
    const cb = state.connectBridge;
    pill.classList.remove('connected', 'connecting', 'offline', 'errored');
    // Phase 1: four-state vocabulary, matching the inline status
    // button at jam.js:597-621. `connecting` class reused for the
    // "Starting" state so the existing amber CSS lights up without
    // a new colour token.
    const paired = cb.status === 'open' && cb.peers > 0;
    const launching = !!cb.launching
      && performance.now() < cb.launchingUntilMs;
    const errored = cb.status === 'closed' && cb.reconnectMs >= 30000;
    if (paired) {
      pill.classList.add('connected');
      pill.textContent = `Connect Connected (${cb.peers})`;
    } else if (launching) {
      pill.classList.add('connecting');
      pill.textContent = 'Connect Starting…';
    } else if (errored) {
      pill.classList.add('errored');
      pill.textContent = 'Connect Error';
    } else {
      pill.classList.add('offline');
      pill.textContent = 'Connect Offline';
    }
  }
  (function initHeaderConnectPill() {
    const pill = document.getElementById('header-connect-pill');
    const pop = document.getElementById('header-connect-monitor-pop');
    if (!pill || !pop) return;
    pill.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const willOpen = !!pop.hidden;
      pop.hidden = !willOpen;
      pill.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    document.addEventListener('click', (ev) => {
      if (pop.hidden) return;
      if (pop.contains(ev.target)) return;
      if (ev.target === pill) return;
      pop.hidden = true;
      pill.setAttribute('aria-expanded', 'false');
    });
    renderConnectPill();
  })();

  // Phase 6: kick off the install probe once the DOM is alive. Fires
  // in the background — never blocks anything. Result lands on
  // state.connectBridge.{installed,installedPath,installedVersion}
  // and triggers a renderConnectStatus() so the install/launch CTA
  // appears as soon as the probe returns.
  (function initConnectInstallProbe() {
    if (typeof probeConnectInstalled !== 'function') return;
    // Defer one tick so the rest of the IIFE has a chance to install
    // its DOM listeners; the probe itself is async and won't block.
    setTimeout(() => { probeConnectInstalled(); }, 0);
  })();

  // Audio-Ownership Pivot follow-up: wire the LatencyProbe "Measure"
  // button. The button is in the audio-status card; firing it sends
  // a v2 measure_latency frame to Connect, which plays an audible
  // 1 kHz impulse and replies with measured_round_trip_ms in the
  // next latency_report. Idempotent re-clicks are guarded server-
  // side AND in triggerLatencyMeasurement.
  (function initMeasureLatencyButton() {
    const btn = document.getElementById('audio-latency-measure-btn');
    if (!btn) return;
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      if (typeof triggerLatencyMeasurement === 'function') {
        triggerLatencyMeasurement();
      }
    });
  })();

  // ---- 3k: waveform timeline ------------------------------------------
  function computeWaveformPeaks(targetSamples = 1600) {
    // Mix-down across all decoded stem buffers; track abs-peak per
    // block of (nFrames / targetSamples) samples. Normalize to 1.0.
    const buffers = [];
    let maxFrames = 0;
    let maxDur = 0;
    for (const stem of state.stems.values()) {
      if (!stem.buffer) continue;
      buffers.push(stem.buffer);
      if (stem.buffer.length > maxFrames) maxFrames = stem.buffer.length;
      if (stem.buffer.duration > maxDur) maxDur = stem.buffer.duration;
    }
    if (!buffers.length || maxFrames === 0) return;
    const peaks = new Float32Array(targetSamples);
    const blockSize = Math.max(1, Math.floor(maxFrames / targetSamples));
    // Pre-cache channel data refs to avoid getChannelData() in the
    // hot loop.
    const stemChannels = buffers.map((buf) => {
      const chans = [];
      for (let c = 0; c < buf.numberOfChannels; c++) {
        chans.push(buf.getChannelData(c));
      }
      return { chans, length: buf.length };
    });
    let globalMax = 1e-6;
    for (let b = 0; b < targetSamples; b++) {
      const start = b * blockSize;
      const end = Math.min(start + blockSize, maxFrames);
      let blockPeak = 0;
      for (let i = start; i < end; i++) {
        let mix = 0;
        for (let s = 0; s < stemChannels.length; s++) {
          const sc = stemChannels[s];
          if (i >= sc.length) continue;
          for (let c = 0; c < sc.chans.length; c++) {
            mix += sc.chans[c][i];
          }
        }
        const av = mix < 0 ? -mix : mix;
        if (av > blockPeak) blockPeak = av;
      }
      peaks[b] = blockPeak;
      if (blockPeak > globalMax) globalMax = blockPeak;
    }
    // Normalize.
    const inv = 1.0 / globalMax;
    for (let i = 0; i < peaks.length; i++) peaks[i] *= inv;
    state.waveform = { peaks, durationSec: maxDur };
    drawWaveform();
  }

  function drawWaveform() {
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    const cssW = Math.max(1, Math.floor(rect.width));
    const cssH = Math.max(1, Math.floor(rect.height));
    if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
      canvas.width = cssW * dpr;
      canvas.height = cssH * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const wf = state.waveform;
    if (!wf || !wf.peaks || !wf.peaks.length) return;
    const peaks = wf.peaks;
    const midY = cssH / 2;
    const styles = getComputedStyle(document.documentElement);
    const dimColor = (styles.getPropertyValue('--text-dim') || '#888').trim();
    const accent = (styles.getPropertyValue('--accent') || '#f97316').trim();
    ctx.fillStyle = dimColor;
    const barCount = peaks.length;
    const barW = cssW / barCount;
    for (let i = 0; i < barCount; i++) {
      const h = Math.max(1, peaks[i] * (cssH * 0.9));
      const x = i * barW;
      ctx.fillRect(x, midY - h / 2, Math.max(1, barW - 0.5), h);
    }
    // Playhead.
    const t = currentPlayTime();
    const dur = wf.durationSec || state.duration || 0;
    if (dur > 0) {
      const px = Math.min(cssW - 1, (t / dur) * cssW);
      ctx.fillStyle = accent;
      ctx.fillRect(px, 0, 2, cssH);
    }
  }

  (function initWaveformInteraction() {
    const canvas = document.getElementById('waveform-canvas');
    if (!canvas) return;
    canvas.addEventListener('click', (ev) => {
      const wf = state.waveform;
      if (!wf || !wf.durationSec) return;
      const rect = canvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const frac = Math.max(0, Math.min(1, x / rect.width));
      seekAll(frac * wf.durationSec);
    });
    // Debounced resize.
    let resizeT = null;
    window.addEventListener('resize', () => {
      if (resizeT) clearTimeout(resizeT);
      resizeT = setTimeout(() => { drawWaveform(); }, 100);
    });
  })();

  // ---- 3l: t-loop (clear) ---------------------------------------------
  (function initLoopClearButton() {
    const btn = document.getElementById('t-loop');
    if (!btn) return;
    btn.addEventListener('click', () => {
      const hasActive = !!document.querySelector('#section-bar .section-pill.active');
      if (!hasActive && !state.loop) {
        flashConnectStatus(
          'Tap a section pill to loop it.', false, 2500,
        );
        return;
      }
      state.loop = null;
      const pills = document.querySelectorAll('#section-bar .section-pill');
      for (const p of pills) p.classList.remove('active');
      const ls = document.getElementById('loop-status');
      if (ls) ls.textContent = 'Loop: off';
      btn.textContent = 'Loop: off';
    });
  })();

  // ---- 3m: instrument selector ----------------------------------------
  (function initInstrumentSelector() {
    const sel = document.getElementById('instrument-select');
    if (!sel) return;
    // Mirror the current intake/state choice so the labels stay in sync
    // when entering the perform view.
    if (state.userInstrument) sel.value = state.userInstrument;
    sel.addEventListener('change', () => {
      const next = sel.value;
      if (!next) return;
      state.userInstrument = next;
      try { buildStemRack(); } catch (e) {
        console.warn('[jam] buildStemRack after instrument change failed:', e);
      }
      const muteBtn = document.getElementById('user-mute-original');
      if (muteBtn) {
        const label = next === 'guitar' ? 'guitar'
          : next === 'bass' ? 'bass'
          : next === 'keys' ? 'keys'
          : next;
        // Preserve existing on/off state if the button is already in
        // "Unmute" mode — only the noun changes.
        if (/^Unmute /.test(muteBtn.textContent)) {
          muteBtn.textContent = `Unmute original ${label}`;
        } else {
          muteBtn.textContent = `Mute original ${label}`;
        }
      }
    });
  })();

  initSettingsUI();

  // -------------------------------------------- live guitar monitor
  //
  // Phase 1 of the "karaoke for musicians" play-along loop. The
  // monitor opens an audio-interface input via getUserMedia, routes
  // it through a GainNode to ctx.destination, and runs an analyser
  // tap on the input side for the level meter + clip indicator.
  //
  // Audio graph (active):
  //   MediaStreamSource -> monitor.gainNode -> ctx.destination
  //                    \--> meterAnalyser (analysis-only tap)
  //
  // The monitor reuses the playback AudioContext when one exists
  // (created by prepareStemAudio on first play) so song + monitor
  // share one clock. If the user enables the monitor BEFORE loading
  // a song, the context is created on the fly here.
  //
  // No DSP, no amp sim, no effects. Just direct monitoring. By
  // design we pass the raw input through; tone-matching is a Connect
  // concern.

  const MONITOR_DEVICE_KEY = 'jamMonitorDeviceId';
  const MONITOR_GAIN_KEY   = 'jamMonitorGain';
  const METER_CLIP_THRESHOLD = 0.98;      // amplitude
  const METER_CLIP_HOLD_MS   = 600;       // visual hold for momentary peaks
  const METER_DECAY          = 0.85;      // per-frame falloff

  async function _ensureAudioContextForMonitor() {
    if (state.ctx) return state.ctx;
    try {
      // Same latencyHint rationale as prepareStemAudio(): the monitor
      // path is the one that NEEDS interactive latency, so when the
      // user enables monitoring before pressing play we still get the
      // small-buffer behaviour.
      const Ctx = window.AudioContext || window.webkitAudioContext;
      state.ctx = new Ctx({ latencyHint: 'interactive' });
      state.masterGain = state.ctx.createGain();
      state.masterGain.gain.value = 1.0;
      state.masterGain.connect(state.ctx.destination);
    } catch (e) {
      console.warn('[monitor] AudioContext not available:', e);
      return null;
    }
    return state.ctx;
  }

  async function _populateMonitorDeviceSelect() {
    const sel = $('monitor-device-select');
    if (!sel) return;
    let inputs = [];
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      inputs = devs.filter(d => d.kind === 'audioinput');
    } catch (e) {
      console.warn('[monitor] enumerateDevices failed:', e);
    }
    const prevValue = sel.value;
    sel.innerHTML = '';
    const def = document.createElement('option');
    def.value = '';
    def.textContent = 'System default';
    sel.appendChild(def);
    for (const d of inputs) {
      const opt = document.createElement('option');
      opt.value = d.deviceId;
      // Until permission is granted the label is empty; show a
      // truncated id placeholder so the user at least sees that
      // separate inputs exist.
      opt.textContent = d.label || `Input ${d.deviceId.slice(0, 6)}…`;
      sel.appendChild(opt);
    }
    // Restore the active selection where possible. Precedence:
    //   1. The deviceId the monitor is currently bound to
    //   2. Whatever the <select> showed before we rebuilt it
    const target = state.monitor.deviceId || prevValue;
    if (target && [...sel.options].some(o => o.value === target)) {
      sel.value = target;
    }
    state.monitor.devicesEnumerated = inputs.some(d => d.label);
  }

  async function _startMonitor() {
    if (state.monitor.enabled) return;
    // Refuse if Connect is paired: it already has exclusive access to
    // the input device. Starting getUserMedia in this state crashes
    // the helper's AVAudioEngine (channel-count flap) and produces a
    // flapping WS — observable as "click Enable monitor, hear guitar
    // for a sec, then Connect disconnects".
    if (_isConnectPaired()) {
      _setMonitorStatus('Connect is using your input — quit it to monitor here', 'err');
      _syncMonitorVsConnect();
      return;
    }
    const ctx = await _ensureAudioContextForMonitor();
    if (!ctx) {
      _setMonitorStatus('AudioContext unavailable', 'err');
      return;
    }
    // Some browsers ship the context in 'suspended' state until a
    // user gesture; the toggle click itself is a gesture so this
    // resume() is allowed.
    if (ctx.state === 'suspended') {
      try { await ctx.resume(); } catch {}
    }
    try {
      const deviceId = state.monitor.deviceId || null;
      const isHardware = !!deviceId;
      const stream = await navigator.mediaDevices.getUserMedia(
        _captureConstraints(deviceId, isHardware),
      );
      const sourceNode = ctx.createMediaStreamSource(stream);
      const gainNode = ctx.createGain();
      gainNode.gain.value = state.monitor.muted ? 0.0 : state.monitor.gain;
      const meterAnalyser = ctx.createAnalyser();
      meterAnalyser.fftSize = 1024;
      meterAnalyser.smoothingTimeConstant = 0.0;
      // Two parallel taps off the source:
      //   - through gainNode to destination (the monitoring path)
      //   - through meterAnalyser (analysis only; no further connect)
      // The analyser sits PRE-gain so the clip indicator reflects
      // input level regardless of the monitor gain slider.
      sourceNode.connect(gainNode);
      gainNode.connect(ctx.destination);
      sourceNode.connect(meterAnalyser);
      state.monitor.stream = stream;
      state.monitor.sourceNode = sourceNode;
      state.monitor.gainNode = gainNode;
      state.monitor.meterAnalyser = meterAnalyser;
      state.monitor.meterBuffer = new Float32Array(meterAnalyser.fftSize);
      state.monitor.enabled = true;
      state.monitor.lastDisplayedPeak = 0;
      const track = stream.getAudioTracks()[0];
      state.monitor.deviceLabel = (track && track.label) || '';
      const trackSettings = (track && track.getSettings) ? track.getSettings() : {};
      // If no preferred deviceId was set, remember the one we actually
      // got so a later restart can re-acquire the same input.
      if (!state.monitor.deviceId && trackSettings.deviceId) {
        state.monitor.deviceId = trackSettings.deviceId;
      }
      // Listen for the OS yanking the device out from under us. The
      // HX Stomp's USB re-enumeration during stereo<->mono channel-count
      // flap surfaces here as a 'ended' event on the track; without
      // this handler the meter freezes at the last sample and the
      // status dot stays green, hiding the failure.
      //
      // Handler stored on state.monitor so _stopMonitor can detach it
      // before calling t.stop() -- Safari has been seen to re-fire
      // 'ended' synchronously on explicit stop, which without the
      // detach re-enters _stopMonitor mid-teardown and (combined with
      // an in-flight WBSUserMediaPermissionController query) crashes
      // the tab.
      if (track) {
        const onEnded = () => {
          console.warn('[monitor] MediaStreamTrack ended (capture failure)');
          const lostLabel = state.monitor.deviceLabel || 'input device';
          _stopMonitor();
          _setMonitorStatus(`Disconnected — ${lostLabel} dropped`, 'err');
        };
        state.monitor.trackEndedHandler = onEnded;
        track.addEventListener('ended', onEnded);
      }
      // Now that the page has permission, device labels become
      // visible; re-enumerate so the select shows readable names.
      if (!state.monitor.devicesEnumerated) {
        await _populateMonitorDeviceSelect();
      }
      _setMonitorStatus(`On — ${state.monitor.deviceLabel || 'default input'}`, 'ok');
      _updateMonitorLatencyDisplay();
      _meterTick();
      console.info(
        `[monitor] started — label="${state.monitor.deviceLabel}" `
        + `deviceId=${(trackSettings.deviceId || '').slice(0, 8)}… `
        + `sr=${trackSettings.sampleRate || '?'} channels=${trackSettings.channelCount || '?'}`
      );
    } catch (err) {
      console.warn('[monitor] start failed:', err);
      _setMonitorStatus(_humanReadableMicError(err), 'err');
    }
  }

  // True when the Connect helper has registered as a peer on the
  // connect-bridge channel. While paired, Connect owns the HX Stomp
  // (or whichever input the user picked) at the OS level. The browser
  // monitor MUST NOT also grab the same device: two clients on a USB
  // audio device forces Core Audio to renegotiate the format, which
  // crashes AVAudioEngine inside Connect (the very NSException we
  // trap in 1e16d94 — even with the trap, the supervisor restart
  // cycle still produces audible dropouts).
  function _isConnectPaired() {
    const cb = state.connectBridge;
    return !!cb && cb.status === 'open' && (cb.peers || 0) > 0;
  }

  // Centralised reconciliation between Connect pairing state and the
  // monitor toggle. Called from renderConnectStatus() (which already
  // fires on joined / peer_left) and from initMonitorPanel() once at
  // boot. Two responsibilities:
  //   1. If Connect just paired AND the browser monitor is running,
  //      stop the monitor immediately to release the HX Stomp.
  //   2. Reflect the lockout in the UI: disable the toggle, replace
  //      its label, and update the status line + audio-in row dot so
  //      the user understands WHY they can't enable monitoring.
  function _syncMonitorVsConnect() {
    const toggleBtn = document.getElementById('monitor-toggle');
    if (!toggleBtn) return;
    const fallbackRow = document.getElementById('monitor-fallback-row');
    const paired = _isConnectPaired();
    const cb = state.connectBridge || {};
    // Phase 2: tri-state UX.
    //   A. paired       → toggle disabled, "Connect handles input"
    //   B. installed && !paired && !fallback → toggle disabled,
    //                     "Launch Connect for low-latency monitoring",
    //                     fallback link visible.
    //   C. otherwise (not installed, or user opted into fallback) →
    //                     existing "Enable monitor" behaviour.
    const installedAvailable = cb.installed === true;
    const useDemotedUx =
      !paired && installedAvailable && !state.monitor.fallbackMode;
    if (paired) {
      if (state.monitor.enabled) {
        _stopMonitor();
      }
      toggleBtn.disabled = true;
      toggleBtn.textContent = 'Connect handles input';
      toggleBtn.classList.remove('active');
      toggleBtn.title = 'Connect is online and is using your audio input. '
        + 'Quit Connect to monitor through the browser instead.';
      _setMonitorStatus('Off — Connect is using your input', '');
      if (fallbackRow) fallbackRow.hidden = true;
    } else if (useDemotedUx) {
      // Connect is installed but not paired — the recommended path
      // is to launch it. The toggle is gated; the fallback link
      // beneath gives the user an out.
      if (state.monitor.enabled) {
        // User had the browser monitor running, Connect was just
        // installed (e.g. probe completed late). Don't yank their
        // audio mid-session; treat that as implicit fallback.
        state.monitor.fallbackMode = true;
        if (fallbackRow) fallbackRow.hidden = true;
        toggleBtn.disabled = false;
        toggleBtn.title = '';
        _setMonitorStatus('Fallback (browser monitor active)', '');
        return;
      }
      toggleBtn.disabled = true;
      toggleBtn.textContent = 'Launch Connect for low-latency monitoring';
      toggleBtn.classList.remove('active');
      toggleBtn.title = 'Connect.app is installed — launch it for the '
        + 'best monitoring latency. Or use the browser fallback '
        + 'below if you\'d rather monitor through this tab.';
      _setMonitorStatus('Off', '');
      if (fallbackRow) fallbackRow.hidden = false;
    } else {
      // Connect not installed, OR user opted into fallback mode.
      toggleBtn.disabled = false;
      toggleBtn.title = '';
      if (!state.monitor.enabled) {
        toggleBtn.textContent = 'Enable monitor';
        toggleBtn.classList.remove('active');
        // Fallback users see a muted "Fallback" annotation so they
        // remember they're on the slower path; pre-Phase-6 users
        // see the original "Off" copy.
        if (state.monitor.fallbackMode) {
          _setMonitorStatus('Off — fallback ready', '');
        }
      } else if (state.monitor.fallbackMode) {
        // Monitor is active in fallback mode — annotate the meter
        // pill so the user can't miss it.
        _setMonitorStatus('Fallback (browser monitor active)', '');
      }
      if (fallbackRow) fallbackRow.hidden = true;
    }
  }

  // Phase 2: fallback-link click. Demotes Connect to "optional" for
  // the remainder of the page session: re-enables the monitor toggle,
  // hides the fallback row, and re-syncs the UI so the user can
  // immediately click "Enable monitor". Idempotent.
  (function initMonitorFallbackLink() {
    const link = document.getElementById('monitor-fallback-link');
    if (!link) return;
    link.addEventListener('click', (ev) => {
      ev.preventDefault();
      state.monitor.fallbackMode = true;
      _syncMonitorVsConnect();
    });
  })();

  function _stopMonitor() {
    // Re-entry guard. Safari fires 'ended' on the track during our
    // own t.stop() below; without this, the handler can recursively
    // call _stopMonitor while we're mid-teardown.
    if (state.monitor._stopping) return;
    state.monitor._stopping = true;
    try {
      // Cancel RAF FIRST so _meterTick can't run against
      // partially-nulled state once we disconnect the analyser below.
      if (state.monitor.rafHandle) {
        cancelAnimationFrame(state.monitor.rafHandle);
        state.monitor.rafHandle = null;
      }
      // Detach the 'ended' listener BEFORE stopping tracks so our own
      // teardown doesn't recurse through the handler.
      if (state.monitor.trackEndedHandler && state.monitor.stream) {
        for (const t of state.monitor.stream.getTracks()) {
          try { t.removeEventListener('ended', state.monitor.trackEndedHandler); } catch {}
        }
      }
      state.monitor.trackEndedHandler = null;
      try { state.monitor.sourceNode   && state.monitor.sourceNode.disconnect(); } catch {}
      try { state.monitor.gainNode     && state.monitor.gainNode.disconnect();   } catch {}
      try { state.monitor.meterAnalyser && state.monitor.meterAnalyser.disconnect(); } catch {}
      if (state.monitor.stream) {
        for (const t of state.monitor.stream.getTracks()) {
          try { t.stop(); } catch {}
        }
      }
      state.monitor.stream = null;
      state.monitor.sourceNode = null;
      state.monitor.gainNode = null;
      state.monitor.meterAnalyser = null;
      state.monitor.meterBuffer = null;
      state.monitor.enabled = false;
      state.monitor.lastDisplayedPeak = 0;
      _setMonitorStatus('Off', '');
      _clearMonitorMeter();
      // Sync the toggle UI: _stopMonitor can be called from non-click
      // paths (track.onended, _syncMonitorVsConnect) and previously
      // left the button reading "Disable monitor" / active even though
      // the underlying monitor was off. Click handler still owns the
      // status text update; here we just reset the button surface.
      const toggleBtn = document.getElementById('monitor-toggle');
      if (toggleBtn) {
        toggleBtn.textContent = 'Enable monitor';
        toggleBtn.classList.remove('active');
      }
      const muteBtn = document.getElementById('monitor-mute');
      if (muteBtn) muteBtn.disabled = true;
    } finally {
      state.monitor._stopping = false;
    }
  }

  function _setMonitorGain(g) {
    state.monitor.gain = g;
    if (state.monitor.gainNode && !state.monitor.muted) {
      state.monitor.gainNode.gain.value = g;
    }
    const r = $('monitor-gain-readout');
    if (r) r.textContent = g.toFixed(2);
    // Mirror to the rack's Guitar Input row (Phase 2 mixer) if it
    // exists. Guarded against the redundant assignment so dragging
    // either slider doesn't re-fire `input` on the other.
    document.querySelectorAll('.gain[data-channel="guitar"]').forEach(el => {
      if (parseFloat(el.value) !== g) el.value = String(g);
    });
    try { localStorage.setItem(MONITOR_GAIN_KEY, String(g)); } catch {}
  }

  function _setMonitorMuted(muted) {
    state.monitor.muted = !!muted;
    if (state.monitor.gainNode) {
      state.monitor.gainNode.gain.value = state.monitor.muted ? 0.0 : state.monitor.gain;
    }
    const btn = $('monitor-mute');
    if (btn) {
      btn.textContent = state.monitor.muted ? 'Unmute' : 'Mute';
      btn.classList.toggle('active', state.monitor.muted);
    }
    // Mirror to the rack's Guitar Input mute button (Phase 2 mixer).
    document.querySelectorAll('.mute-btn[data-channel="guitar"]').forEach(b => {
      b.textContent = state.monitor.muted ? 'Muted' : 'Mute';
    });
  }

  function _setMonitorStatus(text, kind) {
    const el = $('monitor-status');
    if (el) {
      el.textContent = text;
      el.classList.remove('monitor-status-ok', 'monitor-status-err');
      if (kind === 'ok') el.classList.add('monitor-status-ok');
      else if (kind === 'err') el.classList.add('monitor-status-err');
    }
    _syncAudioInRowStatus(kind, text);
  }

  // Sync the lightweight in-row indicators (#audio-in-status-dot,
  // #audio-in-mini-meter, #audio-in-latency) with the monitor state.
  // The dot tracks lifecycle (off / on / error); the mini-meter is
  // visible only while the monitor is enabled; the latency text
  // mirrors #monitor-latency. Called whenever monitor status changes;
  // the per-frame meter update happens inline in _meterTick to avoid
  // an extra DOM-query pass per RAF.
  function _syncAudioInRowStatus(kind, text) {
    const dot = $('audio-in-status-dot');
    if (dot) {
      dot.classList.remove('audio-in-status-off', 'audio-in-status-on', 'audio-in-status-err');
      const enabled = !!state.monitor.enabled;
      let cls = 'audio-in-status-off', title = 'Monitor off';
      if (kind === 'err') { cls = 'audio-in-status-err'; title = text ? `Monitor error: ${text}` : 'Monitor error'; }
      else if (enabled || kind === 'ok') { cls = 'audio-in-status-on'; title = 'Monitor on'; }
      dot.classList.add(cls);
      dot.title = title;
    }
    const mini = $('audio-in-mini-meter');
    if (mini) mini.hidden = !state.monitor.enabled;
    if (!state.monitor.enabled) {
      const fill = $('audio-in-mini-meter-fill');
      if (fill) fill.style.width = '0%';
      const clip = $('audio-in-mini-meter-clip');
      if (clip) clip.hidden = true;
    }
    const latencyOut = $('audio-in-latency');
    const panelLatency = $('monitor-latency');
    if (latencyOut) {
      latencyOut.textContent = state.monitor.enabled && panelLatency
        ? panelLatency.textContent
        : '';
    }
  }

  function _updateMonitorLatencyDisplay() {
    const el = $('monitor-latency');
    if (!state.ctx) { return; }
    // baseLatency: render-graph latency (input -> output) within the
    // context. outputLatency: estimated audio-pipeline latency to the
    // physical output. Not all browsers implement outputLatency; fall
    // back to baseLatency only when it's missing.
    const base = (state.ctx.baseLatency || 0) * 1000;
    const out  = (state.ctx.outputLatency || 0) * 1000;
    const total = base + out;
    const text = total > 0 ? `~${Math.round(total)} ms` : '';
    if (el) el.textContent = text;
    const rowEl = $('audio-in-latency');
    if (rowEl) rowEl.textContent = state.monitor.enabled ? text : '';
    // Phase 3: the dual-latency card reads the same browser numbers,
    // so re-render whenever the monitor-side floor changes.
    renderLatencyComparison();
  }

  // Audio-Ownership Pivot, Phase 3 — dual-latency comparison card.
  //
  // Renders two rows: Connect Monitor (from the v2 latency_report
  // frame) and Browser Monitor (from AudioContext.baseLatency +
  // outputLatency). The "Recommended" tag rides off _isConnectPaired();
  // paired-but-pre-running shows the row in unavailable styling
  // because Connect hasn't sent its first latency_report yet.
  //
  // Both numbers are floors (lower bounds), not measurements; the
  // tooltips explain why so a user comparing them to a hardware
  // monitor doesn't feel misled.
  //
  // Idempotent — safe to call on every dispatch event, monitor
  // start/stop, or Connect peer join/leave.
  function renderLatencyComparison() {
    const card = $('audio-latency-card');
    if (!card) return;  // HTML not present (loaded mid-Phase 3, etc.)
    card.hidden = false;

    const cb = state.connectBridge || {};
    const paired = _isConnectPaired();

    // ----- Connect row -----
    const connectRow   = $('audio-latency-row-connect');
    const connectValue = $('audio-latency-connect-value');
    const recommended  = $('audio-latency-recommended');
    const haveConnect  = Number.isFinite(cb.connectLatencyMs);
    if (connectValue) {
      connectValue.textContent = haveConnect
        ? `~${Math.round(cb.connectLatencyMs)} ms`
        : '—';
    }
    if (connectRow) {
      // Unavailable when we don't have a latency report yet (Connect
      // not paired, not yet running, or first report not delivered).
      connectRow.classList.toggle('is-unavailable', !haveConnect);
      const rep = cb.connectLatencyReport;
      if (rep && haveConnect) {
        const fmt = (n) => Number.isFinite(Number(n)) ? Number(n).toFixed(1) : '?';
        connectRow.title =
          'Connect engine floor: input ' + fmt(rep.input_ms) +
          ' ms + output ' + fmt(rep.output_ms) +
          ' ms + 2× buffer ' + fmt(rep.buffer_ms) + ' ms. ' +
          'A lower bound — real round trip can be higher under load.';
      } else {
        connectRow.title = paired
          ? 'Waiting for Connect to report its engine latency…'
          : 'Connect not paired. Launch Connect for low-latency monitoring.';
      }
    }
    if (recommended) {
      // Tag shown only when Connect is actually delivering audio.
      // Pre-pairing the column is "informational"; once paired and a
      // latency report has arrived, it becomes the recommended path.
      recommended.hidden = !(paired && haveConnect);
    }

    // ----- Measured (LatencyProbe) sub-row -----
    // Only meaningful when Connect is paired AND the user has run
    // the impulse probe at least once. We surface measured vs.
    // estimated side-by-side rather than replacing the estimate,
    // because the estimate is a useful "this is the floor" anchor
    // even after a measurement.
    const measuredRow   = $('audio-latency-measured');
    const measuredValue = $('audio-latency-measured-value');
    const measuredBadge = $('audio-latency-measured-badge');
    const measureBtn    = $('audio-latency-measure-btn');
    const haveMeasured  = Number.isFinite(cb.connectMeasuredLatencyMs);
    if (measuredRow) {
      measuredRow.hidden = !paired;
    }
    if (measuredValue) {
      measuredValue.textContent = haveMeasured
        ? `~${Math.round(cb.connectMeasuredLatencyMs)} ms (measured)`
        : 'Not measured';
    }
    if (measuredBadge) {
      // confidence ∈ {"high","low","no_signal"}; we surface low/no-signal
      // as warnings, high is the silent good-case (badge shows '✓').
      const conf = haveMeasured ? cb.connectMeasuredConfidence : null;
      measuredBadge.classList.remove('is-high', 'is-low', 'is-nosignal');
      if (conf === 'high') {
        measuredBadge.textContent = '✓';
        measuredBadge.title = 'Impulse-loopback measurement, high confidence.';
        measuredBadge.classList.add('is-high');
        measuredBadge.hidden = false;
      } else if (conf === 'low') {
        measuredBadge.textContent = 'low confidence';
        measuredBadge.title = 'Probe ran but the impulse was hard to detect. Check input level and try again.';
        measuredBadge.classList.add('is-low');
        measuredBadge.hidden = false;
      } else if (conf === 'no_signal') {
        measuredBadge.textContent = 'no signal';
        measuredBadge.title = 'Probe ran but never detected its own impulse. Check that the input bus actually picks up the speaker output (open-mic loopback).';
        measuredBadge.classList.add('is-nosignal');
        measuredBadge.hidden = false;
      } else {
        measuredBadge.hidden = true;
      }
    }
    if (measureBtn) {
      const canMeasure = paired && !cb.measureInFlight;
      measureBtn.disabled = !canMeasure;
      measureBtn.textContent = cb.measureInFlight ? 'Measuring…' : 'Measure';
      measureBtn.title = paired
        ? (cb.measureInFlight
            ? 'Impulse probe in progress (~1.5 s)…'
            : 'Play a 1 kHz tone through Connect and measure the round-trip time. Audible — turn the volume down first.')
        : 'Connect not paired.';
    }

    // ----- Browser row -----
    const browserRow   = $('audio-latency-row-browser');
    const browserValue = $('audio-latency-browser-value');
    const ctx = state.ctx;
    if (ctx) {
      const baseMs = (ctx.baseLatency  || 0) * 1000;
      const outMs  = (ctx.outputLatency || 0) * 1000;
      const totalMs = baseMs + outMs;
      const haveBrowser = totalMs > 0;
      if (browserValue) {
        browserValue.textContent = haveBrowser
          ? `~${Math.round(totalMs)} ms`
          : '—';
      }
      if (browserRow) {
        browserRow.classList.toggle('is-unavailable', !haveBrowser);
        browserRow.title = haveBrowser
          ? 'Web Audio floor: baseLatency ' + baseMs.toFixed(1) +
            ' ms + outputLatency ' + outMs.toFixed(1) + ' ms. ' +
            'Safari often reports zero outputLatency.'
          : 'Browser audio context not yet started.';
      }
    } else {
      if (browserValue) browserValue.textContent = '—';
      if (browserRow) {
        browserRow.classList.add('is-unavailable');
        browserRow.title = 'Browser audio context not yet started.';
      }
    }
  }

  function _clearMonitorMeter() {
    const fill = $('monitor-meter-fill');
    if (fill) { fill.style.width = '0%'; fill.style.opacity = '1.0'; }
    const clip = $('monitor-meter-clip');
    if (clip) clip.hidden = true;
  }

  function _meterTick() {
    // Bail out if monitor has been (or is being) torn down. The RAF
    // schedule order means a tick can land after _stopMonitor nulled
    // state but before the next RAF cancel takes effect.
    if (state.monitor._stopping || !state.monitor.enabled) return;
    const { meterAnalyser, meterBuffer, muted } = state.monitor;
    if (!meterAnalyser || !meterBuffer) return;
    try {
      meterAnalyser.getFloatTimeDomainData(meterBuffer);
    } catch (e) {
      // Analyser was disconnected mid-frame (Safari can throw on a
      // node whose source has just ended). Drop the frame quietly.
      return;
    }
    // Peak amplitude over the buffer window. Faster than RMS for a
    // visual meter and accurate for clip detection.
    let peak = 0;
    for (let i = 0; i < meterBuffer.length; i++) {
      const a = Math.abs(meterBuffer[i]);
      if (a > peak) peak = a;
    }
    // Slow decay between frames so the bar settles smoothly when
    // input goes quiet, but still snaps up instantly on a transient.
    const displayed = Math.max(peak, state.monitor.lastDisplayedPeak * METER_DECAY);
    state.monitor.lastDisplayedPeak = displayed;
    // Map amplitude to bar width via dBFS with a -40 dB floor. Linear
    // amplitude looks "stuck low" because most signals live below 0.1.
    let pct;
    if (displayed <= 0.00001) {
      pct = 0;
    } else {
      const db = 20 * Math.log10(displayed);
      pct = Math.max(0, Math.min(100, ((db + 40) / 40) * 100));
    }
    const fill = $('monitor-meter-fill');
    const pctStr = `${pct.toFixed(1)}%`;
    if (fill) {
      fill.style.width = pctStr;
      // Dim the bar when muted so the user sees "audio gated" at a
      // glance without checking the mute button.
      fill.style.opacity = muted ? '0.35' : '1.0';
    }
    // Phase 3 mini-meter in the audio-in row (read-only glance).
    const miniFill = $('audio-in-mini-meter-fill');
    if (miniFill) {
      miniFill.style.width = pctStr;
      miniFill.style.opacity = muted ? '0.35' : '1.0';
    }
    // Clip indicator: latches whenever a sample crosses the threshold,
    // held for a short window so a single-frame spike is still visible.
    const now = performance.now();
    if (peak >= METER_CLIP_THRESHOLD) {
      state.monitor.clipUntil = now + METER_CLIP_HOLD_MS;
    }
    const clipped = now <= state.monitor.clipUntil;
    const clip = $('monitor-meter-clip');
    if (clip) clip.hidden = !clipped;
    const miniClip = $('audio-in-mini-meter-clip');
    if (miniClip) miniClip.hidden = !clipped;
    state.monitor.rafHandle = requestAnimationFrame(_meterTick);
  }

  function _humanReadableMicError(err) {
    if (!err || typeof err !== 'object') return 'Mic access failed';
    const n = err.name || '';
    if (n === 'NotAllowedError' || n === 'SecurityError') {
      return 'Permission denied';
    }
    if (n === 'NotFoundError' || n === 'OverconstrainedError') {
      return 'Device unavailable';
    }
    if (n === 'NotReadableError') {
      return 'Device busy';
    }
    return n || 'Mic access failed';
  }

  (function initMonitorPanel() {
    const toggleBtn   = $('monitor-toggle');
    const muteBtn     = $('monitor-mute');
    const gainSlider  = $('monitor-gain');
    const deviceSel   = $('monitor-device-select');
    const refreshBtn  = $('monitor-device-refresh');
    if (!toggleBtn) return; // panel not present (defensive)

    // Restore persisted gain + device. Out-of-range/missing values
    // fall through to the defaults set on the state object.
    try {
      const savedGain = parseFloat(localStorage.getItem(MONITOR_GAIN_KEY) || '');
      if (isFinite(savedGain) && savedGain >= 0 && savedGain <= 4) {
        state.monitor.gain = savedGain;
        if (gainSlider) gainSlider.value = String(savedGain);
        const r = $('monitor-gain-readout');
        if (r) r.textContent = savedGain.toFixed(2);
      }
      const savedDev = localStorage.getItem(MONITOR_DEVICE_KEY) || '';
      if (savedDev) state.monitor.deviceId = savedDev;
    } catch {}

    // First-pass enumeration. Labels will be empty until the user
    // has granted permission at least once; the start path re-runs
    // this after a successful getUserMedia.
    _populateMonitorDeviceSelect().then(() => {
      if (!deviceSel) return;
      if (state.monitor.deviceId) {
        const stillPresent = [...deviceSel.options].some(
          o => o.value === state.monitor.deviceId,
        );
        if (!stillPresent) {
          // Saved device disappeared between sessions (unplugged USB,
          // OS-level rename, etc.). Reset to default so the next
          // enable doesn't fail with OverconstrainedError.
          state.monitor.deviceId = '';
          try { localStorage.removeItem(MONITOR_DEVICE_KEY); } catch {}
        } else {
          deviceSel.value = state.monitor.deviceId;
        }
      }
    });

    toggleBtn.addEventListener('click', async () => {
      // Disabled when Connect is paired (lockout enforced via
      // _syncMonitorVsConnect). Belt-and-braces: still call the guard
      // here so a stale class on the button can't bypass the check.
      if (toggleBtn.disabled || _isConnectPaired()) {
        _syncMonitorVsConnect();
        return;
      }
      if (state.monitor.enabled) {
        _stopMonitor();
        toggleBtn.textContent = 'Enable monitor';
        toggleBtn.classList.remove('active');
        if (muteBtn) muteBtn.disabled = true;
      } else {
        _setMonitorStatus('Requesting input…', '');
        await _startMonitor();
        if (state.monitor.enabled) {
          toggleBtn.textContent = 'Disable monitor';
          toggleBtn.classList.add('active');
          if (muteBtn) muteBtn.disabled = false;
        }
      }
    });

    // Initial sync: page-load state may already have Connect paired
    // (helper running before page loaded). Without this the button
    // would show "Enable monitor" enabled until the next pairing
    // event tick.
    _syncMonitorVsConnect();

    if (muteBtn) {
      muteBtn.addEventListener('click', () => {
        _setMonitorMuted(!state.monitor.muted);
      });
    }

    if (gainSlider) {
      gainSlider.addEventListener('input', () => {
        const v = parseFloat(gainSlider.value);
        if (isFinite(v)) _setMonitorGain(v);
      });
    }

    if (deviceSel) {
      deviceSel.addEventListener('change', async () => {
        const v = deviceSel.value;
        state.monitor.deviceId = v;
        try {
          if (v) localStorage.setItem(MONITOR_DEVICE_KEY, v);
          else localStorage.removeItem(MONITOR_DEVICE_KEY);
        } catch {}
        // If currently running, restart on the new device. Stop +
        // start is the simplest "switch input" implementation;
        // sample-accurate hot-swap is not worth the code in MVP.
        if (state.monitor.enabled) {
          _stopMonitor();
          _setMonitorStatus('Switching device…', '');
          await _startMonitor();
          toggleBtn.textContent = state.monitor.enabled
            ? 'Disable monitor' : 'Enable monitor';
          toggleBtn.classList.toggle('active', state.monitor.enabled);
        }
      });
    }

    if (refreshBtn) {
      refreshBtn.addEventListener('click', () => _populateMonitorDeviceSelect());
    }

    // USB interfaces appear/disappear at runtime; re-enumerate when
    // the browser notifies us so the user doesn't need a reload.
    try {
      navigator.mediaDevices.addEventListener('devicechange', () => {
        _populateMonitorDeviceSelect();
      });
    } catch {}
  })();
})();
