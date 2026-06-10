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
    // Detected key from analysis result, parsed once per jam.
    //   { root: int 0-11, scale: 'Major'|'Minor', pitchClasses: Set<int> }
    songKey: null,
    // Learning-assistance settings, persisted to localStorage.
    settings: {
      listenEnabled: false,
      feedbackView: 'cents', // 'cents' | 'rolling' | 'full'
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
    },
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
        protocol_version: 1,
      }));
      cb.status = 'open';
      cb.reconnectMs = 1000;
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
      if (data.type === 'hello_ack') {
        // Server confirms it speaks our protocol version. `joined`
        // follows; nothing to do here beyond logging.
        console.log(`[connect] hello_ack (server protocol v${data.protocol_version})`);
      } else if (data.type === 'version_mismatch') {
        // Server is older than us, or we're past what it supports.
        // Stop reconnecting — looping would just rehit the rejection.
        // In practice this happens when an older backend is paired with
        // a newer jam.js (e.g. cached static asset after deploy).
        console.warn(`[connect] version_mismatch: server requires v${data.required}, we sent v1`);
        cb.lastPreset = null;  // suppress auto-reconnect in onclose
        try { ws.close(); } catch {}
      } else if (data.type === 'joined') {
        cb.peers = data.peers || 0;
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
        // Push confirmed.
      } else if (data.type === 'error') {
        console.warn('[connect] server error:', data.message);
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
    if (paired) {
      label = `Connect: paired (${cb.peers})`;
      btn.classList.add('connected');
    } else if (cb.status === 'open') {
      label = 'Connect: waiting for helper';
      btn.classList.remove('connected');
    } else if (cb.status === 'connecting') {
      label = 'Connect: connecting…';
      btn.classList.remove('connected');
    } else if (cb.status === 'closed') {
      label = 'Connect: offline — click for help';
      btn.classList.remove('connected');
    } else {
      label = 'Connect to play';
      btn.classList.remove('connected');
    }
    btn.textContent = label;

    // Reveal the monitor-gain slider only once the helper is actually
    // paired. Showing it before then would imply the slider does
    // something — it doesn't, the server has no peer to broadcast to.
    const wrap = document.getElementById('connect-gain-wrap');
    if (wrap) wrap.hidden = !paired;

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
      } else if (paired) {
        text = 'Paired — no matching preset for this song.';
      } else if (cb.status === 'connecting') {
        text = 'Connecting to the desktop helper…';
      } else if (cb.status === 'open') {
        text = 'Waiting for the desktop helper to join.';
        showLauncherLink = !!cb.sessionId;
      } else if (cb.status === 'closed') {
        text = 'Desktop helper offline. Restart it from the tray menu.';
      }
      statusEl.textContent = text;
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
        // Space the link onto its own line so it's distinguishable
        // from the status copy above it.
        statusEl.appendChild(document.createElement('br'));
        statusEl.appendChild(launcher);
      }
      statusEl.hidden = !text;
      statusEl.classList.toggle('ok', ok);
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
    state.duration = 0;
    state.playOffset = 0;
    $('t-play').textContent = 'Play';
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
    } else {
      $('user-tone-name').textContent = 'No tone match yet';
      $('user-tone-meta').textContent = 'Default monitoring preset will be used';
    }

    // Build the stem rack rows
    buildStemRack();
    buildSectionBar(result.sections || []);

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
    rack.querySelectorAll('.gain').forEach(el =>
      el.addEventListener('input', () => setStemGain(el.dataset.stem, parseFloat(el.value)))
    );
    rack.querySelectorAll('.mute-btn').forEach(btn =>
      btn.addEventListener('click', () => toggleMute(btn.dataset.stem, btn))
    );
    rack.querySelectorAll('.solo-btn').forEach(btn =>
      btn.addEventListener('click', () => toggleSolo(btn.dataset.stem, btn))
    );
  }

  function buildSectionBar(sections) {
    const bar = $('section-bar');
    bar.innerHTML = '';
    state.sections = sections.map((s) => {
      const pill = document.createElement('button');
      pill.className = 'section-pill';
      // Sections come back as {type, start_time, end_time} from the backend
      // (see ArrangementSection.to_dict in reconstruction/section_detector.py).
      // Tolerate the older {name, start, end} shape as a fallback.
      const label = s.type || s.name || s.label || 'Section';
      const start = secondsOf(s.start_time ?? s.start ?? s.start_sec ?? s.startSec);
      const end = secondsOf(s.end_time ?? s.end ?? s.end_sec ?? s.endSec);
      pill.textContent = `${label} ${formatTime(start)}`;
      pill.addEventListener('click', () => toggleSectionLoop({ name: label, startSec: start, endSec: end, el: pill }));
      bar.appendChild(pill);
      return { name: label, startSec: start, endSec: end, el: pill };
    });
    if (!state.sections.length) {
      bar.innerHTML = '<div style="color: var(--text-dim); font-size:13px;">Section detection unavailable for this song</div>';
    }
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
        state.ctx = new (window.AudioContext || window.webkitAudioContext)();
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
    console.log('[jam] audio prepared:', {
      stems: state.stems.size,
      ctxState: state.ctx.state,
      sampleRate: state.ctx.sampleRate,
      durationSec: maxDur,
    });
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
  function flashConnectStatus(text, ok = false) {
    const statusEl = document.getElementById('connect-status');
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.hidden = false;
    statusEl.classList.toggle('ok', !!ok);
    clearTimeout(_connectStatusFlashTimer);
    _connectStatusFlashTimer = setTimeout(renderConnectStatus, 2200);
  }

  $('connect-btn').addEventListener('click', () => {
    const cb = state.connectBridge;
    const paired = cb.status === 'open' && cb.peers > 0;
    if (paired && cb.lastPreset) {
      // Re-push so the helper re-applies the matched tone.
      pushPresetToConnect(cb.lastPreset);
      flashConnectStatus('Re-synced tone preset to Connect.', true);
      return;
    }
    if (paired) {
      // Paired but no match exists for this song. Acknowledge the click
      // so it doesn't feel dead; re-rendering after the flash will
      // restore the persistent "no matching preset" message.
      flashConnectStatus('Already paired — no preset to push for this song.', true);
      return;
    }
    if (cb.status === 'connecting') {
      flashConnectStatus('Still connecting — give it a sec.');
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
    if (cbAfterKick.sessionId) {
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
    flashConnectStatus('Reaching out to the desktop helper…');
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
    }
  }

  function tickClock() {
    if (!state.isPlaying) return;
    const t = currentPlayTime();
    $('t-time').textContent = `${formatTime(t)} / ${formatTime(state.duration)}`;
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
    // Conservative level — the click is a guide, not a drum.
    state.clickGain.gain.value = 0.25;
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
    $('t-click').textContent = state.clickEnabled ? 'Click: on' : 'Click: off';
    if (state.clickEnabled && state.isPlaying) {
      startClickScheduler();
    } else {
      stopClickScheduler();
    }
  });

  // ---------------------------------------------- stem mute/solo/gain
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
  function toggleMute(name, btn) {
    const m = setStemMuted(name, !isMuted(name));
    btn.textContent = m ? 'Muted' : 'Mute';
  }
  function toggleSolo(name, btn) {
    const soloing = btn.classList.toggle('active');
    if (soloing) {
      // Mute everything except the soloed stem (and other solo'd ones).
      for (const [n, _] of state.stems.entries()) {
        const isSolo = document.querySelector(`.solo-btn[data-stem="${n}"]`).classList.contains('active');
        setStemMuted(n, !isSolo);
      }
    } else {
      // If no solos remain, unmute everything; else re-mute non-solo'd.
      const anySolo = !!document.querySelector('.solo-btn.active');
      for (const [n, _] of state.stems.entries()) {
        const isSolo = document.querySelector(`.solo-btn[data-stem="${n}"]`).classList.contains('active');
        if (anySolo) setStemMuted(n, !isSolo);
        else setStemMuted(n, false);
      }
    }
    // Reflect mute button text
    document.querySelectorAll('.mute-btn').forEach(b => {
      b.textContent = isMuted(b.dataset.stem) ? 'Muted' : 'Mute';
    });
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

    const stemsPaths = {};
    for (const role of ['drums', 'bass', 'vocals', 'other', 'guitar_left', 'guitar_right']) {
      const stem = stems[role];
      if (stem && stem.audio_url) stemsPaths[role] = stem.audio_url;
    }

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
      tempo_bpm: understanding.tempo_bpm || null,
      detected_key: understanding.key || null,
      sections: understanding.sections || [],
      beat_times: understanding.beats_s || [],
      // Stems
      stems_paths: stemsPaths,
      // MIDI
      midi: userMidi ? {
        notes: userMidi.notes || [],
        overall_confidence: userMidi.overall_confidence ?? 0,
      } : null,
      // Tone — bundle carries an UNKNOWN-tier ToneMatch in MVP; emit an
      // empty preset_matches so the "no tone match yet" branch fires.
      preset_matches: {},
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
  async function startListening() {
    if (state.listen.stream) return; // already running
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          // Browser AEC subtracts speaker output from the mic so the laptop's
          // own playback doesn't get scored as the player's input. Headphones
          // give cleaner results, but most users won't be wearing them.
          echoCancellation: true,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
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
      $('intonation-panel').hidden = false;
      $('into-status').textContent = 'Listening…';
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
    // Panel is visible only while listening is enabled.
    panel.hidden = !state.settings.listenEnabled;
    rolling.hidden = state.settings.feedbackView === 'cents';
    full.hidden = state.settings.feedbackView !== 'full';
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
  }

  initSettingsUI();
})();
