/* recordings.js — session Recordings surface (web mirror of the native
 * takes list: jam-desktop RecordingsListView / RecordingModel and the
 * mobile SessionStore). Classic script: defines
 *
 *   window.JamnRecordings = { mount, unmount, attachSource, createTap }
 *
 * The native apps capture an EVENT LOG (SessionCapture — pad events +
 * pad-mapping snapshot, no audio) and re-render it through the engine.
 * The web has no offline bounce path, so this surface captures the
 * rendered AUDIO instead: the host connects its master output into a
 * MediaStreamAudioDestinationNode tap and we run a MediaRecorder over
 * it. There are deliberately NO backend endpoints for this (the only
 * server-side recording seam is the mobile LayerTimeline layer-sync
 * routes) — takes are client-local, persisted in IndexedDB.
 *
 * Host wiring (one line after building the audio graph):
 *   masterGain.connect(JamnRecordings.createTap(audioContext));
 * or, if the host already owns a stream/node:
 *   JamnRecordings.attachSource(mediaStreamOrNode);
 *
 * mount(container, ctx) with ctx = { audioContext } renders the
 * record button + takes list into `container`; unmount() tears it
 * down (stops playback, discards an in-flight recording). */
(function () {
  'use strict';

  // ---------- constants ----------

  var DB_NAME = 'jamn-recordings';
  var DB_VERSION = 1;
  var STORE = 'takes'; // {id, name, date(ms epoch), durationSec, blob, mimeType}

  // Preference order mirrors what browsers actually implement: Chrome/
  // Firefox take webm+opus, Safari only mp4. Empty string = "let the
  // browser pick" (MediaRecorder default), our last resort.
  var MIME_CANDIDATES = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/mp4'
  ];

  // ---------- pure helpers (no DOM / no IndexedDB — testable) ----------

  /** First supported candidate, or '' for the browser default. */
  function pickMimeType(isSupported) {
    for (var i = 0; i < MIME_CANDIDATES.length; i++) {
      if (isSupported(MIME_CANDIDATES[i])) return MIME_CANDIDATES[i];
    }
    return '';
  }

  /** File extension for a recorded mime type ('' → assume webm). */
  function extensionFor(mimeType) {
    return /mp4|aac|m4a/.test(mimeType || '') ? 'm4a' : 'webm';
  }

  /** "m:ss.t" — same %d:%04.1f shape as the desktop takes list. */
  function formatDuration(seconds) {
    var s = Math.max(0, Number(seconds) || 0);
    var m = Math.floor(s / 60);
    var rest = s - m * 60;
    var tenths = Math.round(rest * 10) / 10;
    // 10ths can round up to 60.0 — carry into the minute.
    if (tenths >= 60) { m += 1; tenths = 0; }
    return m + ':' + (tenths < 10 ? '0' : '') + tenths.toFixed(1);
  }

  /** "Sep 8, 3:04 PM" — matches the desktop row's date format. */
  function formatDate(epochMs) {
    var d = new Date(epochMs);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString(undefined, {
      month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });
  }

  /** Default take name at save time; user can rename in the list. */
  function defaultTakeName(epochMs) {
    return 'Take ' + formatDate(epochMs);
  }

  /** Safe download filename: keep word chars/dashes, collapse the rest. */
  function downloadFilename(name, mimeType) {
    var base = String(name || 'take')
      .replace(/[^\w\- ]+/g, '')
      .trim()
      .replace(/\s+/g, '-') || 'take';
    return base + '.' + extensionFor(mimeType);
  }

  // ---------- IndexedDB wrapper ----------
  // Thin promise shims over the three ops we need. The db handle is
  // cached; a failed open leaves takes purely in-memory for the session
  // (list still works until reload) rather than breaking recording.

  var dbPromise = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise(function (resolve, reject) {
      var req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'id' });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
    return dbPromise;
  }

  function dbRequest(mode, fn) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode);
        var req = fn(tx.objectStore(STORE));
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
      });
    });
  }

  function dbPut(take) {
    return dbRequest('readwrite', function (store) { return store.put(take); });
  }

  function dbGetAll() {
    return dbRequest('readonly', function (store) { return store.getAll(); })
      .then(function (rows) {
        // Newest first, like SessionStore.list().
        return (rows || []).sort(function (a, b) { return b.date - a.date; });
      });
  }

  function dbDelete(id) {
    return dbRequest('readwrite', function (store) { return store.delete(id); });
  }

  // ---------- capture source ----------

  var sourceStream = null; // MediaStream we hand to MediaRecorder
  var tapNode = null;      // MediaStreamAudioDestinationNode we created

  /** Create (or reuse) the tap node the host connects master into. */
  function createTap(audioContext) {
    if (tapNode && tapNode.context === audioContext) return tapNode;
    tapNode = audioContext.createMediaStreamDestination();
    sourceStream = tapNode.stream;
    updateRecordButton();
    return tapNode;
  }

  /**
   * Accepts a MediaStream, a MediaStreamAudioDestinationNode, or any
   * plain AudioNode (we grow a destination node in its own context and
   * connect it — the host doesn't have to know about tap plumbing).
   */
  function attachSource(streamOrNode) {
    if (!streamOrNode) return;
    if (typeof MediaStream !== 'undefined' && streamOrNode instanceof MediaStream) {
      sourceStream = streamOrNode;
    } else if (streamOrNode.stream instanceof MediaStream) {
      // MediaStreamAudioDestinationNode (or anything duck-typed like it)
      tapNode = streamOrNode;
      sourceStream = streamOrNode.stream;
    } else if (streamOrNode.context && typeof streamOrNode.connect === 'function') {
      var dest = streamOrNode.context.createMediaStreamDestination();
      streamOrNode.connect(dest);
      tapNode = dest;
      sourceStream = dest.stream;
    }
    updateRecordButton();
  }

  // ---------- recording control ----------

  var mediaRecorder = null;
  var chunks = [];
  var recordStartMs = 0;
  var recordTimer = null;

  function isRecording() {
    return !!(mediaRecorder && mediaRecorder.state === 'recording');
  }

  function startRecording() {
    if (isRecording() || !sourceStream) return;
    var mimeType = (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported)
      ? pickMimeType(function (t) { return MediaRecorder.isTypeSupported(t); })
      : '';
    try {
      mediaRecorder = mimeType
        ? new MediaRecorder(sourceStream, { mimeType: mimeType })
        : new MediaRecorder(sourceStream);
    } catch (err) {
      setStatus('Recording unavailable: ' + err.message);
      return;
    }
    chunks = [];
    mediaRecorder.ondataavailable = function (e) {
      if (e.data && e.data.size) chunks.push(e.data);
    };
    mediaRecorder.onstop = onRecorderStop;
    // 1s timeslice so a crashed tab loses at most a second, not the take.
    mediaRecorder.start(1000);
    recordStartMs = performance.now();
    recordTimer = setInterval(updateRecordButton, 100);
    setStatus('');
    updateRecordButton();
  }

  function stopRecording() {
    if (!isRecording()) return;
    mediaRecorder.stop(); // onRecorderStop persists the take
  }

  function onRecorderStop() {
    clearInterval(recordTimer);
    recordTimer = null;
    // Blob duration metadata is unreliable for streamed webm/opus
    // (audio.duration reports Infinity), so we store the wall-clock
    // measurement instead of trusting the container.
    var durationSec = (performance.now() - recordStartMs) / 1000;
    var mimeType = (mediaRecorder && mediaRecorder.mimeType) || '';
    mediaRecorder = null;
    var blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
    chunks = [];
    updateRecordButton();
    if (!blob.size) {
      setStatus('Empty take discarded.');
      return;
    }
    var now = Date.now();
    var take = {
      id: 'take-' + now + '-' + Math.random().toString(36).slice(2, 8),
      name: defaultTakeName(now),
      date: now,
      durationSec: durationSec,
      mimeType: mimeType,
      blob: blob
    };
    dbPut(take).then(refreshList, function (err) {
      setStatus('Could not save take: ' + (err && err.message ? err.message : err));
    });
  }

  // ---------- playback ----------
  // One shared HTMLAudioElement: starting a take stops the previous
  // one, and the blob URL is revoked as soon as playback ends so long
  // sessions don't leak object URLs.

  var audioEl = null;
  var playingId = null;
  var playingUrl = null;

  function stopPlayback() {
    if (audioEl) {
      audioEl.pause();
      audioEl.src = '';
    }
    if (playingUrl) {
      URL.revokeObjectURL(playingUrl);
      playingUrl = null;
    }
    playingId = null;
    refreshPlayButtons();
  }

  function togglePlay(take) {
    if (playingId === take.id) { stopPlayback(); return; }
    stopPlayback();
    if (!audioEl) {
      audioEl = new Audio();
      audioEl.addEventListener('ended', stopPlayback);
      audioEl.addEventListener('error', stopPlayback);
    }
    playingUrl = URL.createObjectURL(take.blob);
    playingId = take.id;
    audioEl.src = playingUrl;
    audioEl.play().catch(function () { stopPlayback(); });
    refreshPlayButtons();
  }

  // ---------- UI ----------

  var root = null;      // container the host gave mount()
  var listEl = null;
  var recordBtn = null;
  var statusEl = null;
  var mounted = false;

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function setStatus(msg) {
    if (statusEl) statusEl.textContent = msg || '';
  }

  function updateRecordButton() {
    if (!recordBtn) return;
    if (isRecording()) {
      var elapsed = (performance.now() - recordStartMs) / 1000;
      recordBtn.textContent = 'Stop · ' + formatDuration(elapsed);
      recordBtn.classList.add('is-recording');
      recordBtn.disabled = false;
      recordBtn.title = 'Stop and save the take';
    } else {
      recordBtn.textContent = 'Record';
      recordBtn.classList.remove('is-recording');
      recordBtn.disabled = !sourceStream;
      recordBtn.title = sourceStream
        ? 'Record the jam output'
        : 'No audio source attached yet — start a jam first';
    }
  }

  function refreshPlayButtons() {
    if (!listEl) return;
    var btns = listEl.querySelectorAll('.rec-play');
    for (var i = 0; i < btns.length; i++) {
      var on = btns[i].dataset.takeId === playingId;
      btns[i].textContent = on ? '■' : '▶';
      btns[i].title = on ? 'Stop' : 'Play';
      btns[i].classList.toggle('is-on', on);
    }
  }

  function renderEmptyState() {
    var empty = el('div', 'rec-empty');
    empty.appendChild(el('div', 'rec-empty-icon', '●'));
    empty.appendChild(el('div', 'rec-empty-title', 'No takes yet'));
    empty.appendChild(el('div', 'rec-empty-hint',
      'Record your jam — pads and stems you play are captured.'));
    return empty;
  }

  function renderRow(take) {
    var row = el('div', 'rec-row');

    var meta = el('div', 'rec-meta');
    var nameInput = el('input', 'rec-name');
    nameInput.type = 'text';
    nameInput.value = take.name || '';
    nameInput.setAttribute('aria-label', 'Take name');
    // Rename persists on commit (change), not per keystroke — one
    // IndexedDB write per edit, and Escape-style abandons are free.
    nameInput.addEventListener('change', function () {
      var name = nameInput.value.trim();
      if (!name) { nameInput.value = take.name; return; }
      take.name = name;
      dbPut(take).catch(function () { setStatus('Rename failed.'); });
    });
    nameInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') nameInput.blur();
    });
    meta.appendChild(nameInput);
    meta.appendChild(el('div', 'rec-sub',
      formatDate(take.date) + ' · ' + formatDuration(take.durationSec)));
    row.appendChild(meta);

    var actions = el('div', 'rec-actions');

    var playBtn = el('button', 'rec-btn rec-play', '▶');
    playBtn.type = 'button';
    playBtn.dataset.takeId = take.id;
    playBtn.title = 'Play';
    playBtn.addEventListener('click', function () { togglePlay(take); });
    actions.appendChild(playBtn);

    var dlBtn = el('button', 'rec-btn', '⤓');
    dlBtn.type = 'button';
    dlBtn.title = 'Download';
    dlBtn.addEventListener('click', function () {
      var url = URL.createObjectURL(take.blob);
      var a = document.createElement('a');
      a.href = url;
      a.download = downloadFilename(take.name, take.mimeType);
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Revoke on a tick — sync revoke races the download in Safari.
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    });
    actions.appendChild(dlBtn);

    var delBtn = el('button', 'rec-btn rec-delete', '✕');
    delBtn.type = 'button';
    delBtn.title = 'Delete take';
    delBtn.addEventListener('click', function () {
      if (!window.confirm('Delete "' + (take.name || 'this take') + '"?')) return;
      if (playingId === take.id) stopPlayback();
      dbDelete(take.id).then(refreshList, function () {
        setStatus('Delete failed.');
      });
    });
    actions.appendChild(delBtn);

    row.appendChild(actions);
    return row;
  }

  function refreshList() {
    if (!listEl) return Promise.resolve();
    return dbGetAll().then(function (takes) {
      if (!listEl) return; // unmounted while loading
      listEl.textContent = '';
      if (!takes.length) {
        listEl.appendChild(renderEmptyState());
        return;
      }
      for (var i = 0; i < takes.length; i++) {
        listEl.appendChild(renderRow(takes[i]));
      }
      refreshPlayButtons();
    }, function (err) {
      setStatus('Could not load takes: ' + (err && err.message ? err.message : err));
    });
  }

  function mount(container, ctx) {
    if (mounted) unmount();
    root = container;
    // ctx.audioContext lets a host that never calls createTap()/
    // attachSource() explicitly still get a tap to wire up later.
    if (ctx && ctx.audioContext && !tapNode) createTap(ctx.audioContext);

    var surface = el('div', 'rec-surface');

    var head = el('div', 'rec-head');
    head.appendChild(el('div', 'rec-title', 'Recordings'));
    recordBtn = el('button', 'rec-record', 'Record');
    recordBtn.type = 'button';
    recordBtn.addEventListener('click', function () {
      if (isRecording()) stopRecording(); else startRecording();
    });
    head.appendChild(recordBtn);
    surface.appendChild(head);

    statusEl = el('div', 'rec-status');
    surface.appendChild(statusEl);

    listEl = el('div', 'rec-list');
    surface.appendChild(listEl);

    root.appendChild(surface);
    updateRecordButton();
    mounted = true;
    return refreshList();
  }

  function unmount() {
    if (!mounted) return;
    // An in-flight recording is discarded, not saved — unmount is a
    // navigation away, and a surprise half-take in the list is worse
    // than losing it (matches the desktop cancelRecording semantics).
    if (isRecording()) {
      mediaRecorder.onstop = null;
      mediaRecorder.stop();
      mediaRecorder = null;
      chunks = [];
    }
    clearInterval(recordTimer);
    recordTimer = null;
    stopPlayback();
    if (root) {
      var surface = root.querySelector('.rec-surface');
      if (surface) surface.remove();
    }
    root = null;
    listEl = null;
    recordBtn = null;
    statusEl = null;
    mounted = false;
  }

  window.JamnRecordings = {
    mount: mount,
    unmount: unmount,
    attachSource: attachSource,
    createTap: createTap,
    // Pure helpers exposed for tests (node --test style harnesses can
    // import this file in a stub window and exercise these directly).
    _pure: {
      pickMimeType: pickMimeType,
      extensionFor: extensionFor,
      formatDuration: formatDuration,
      defaultTakeName: defaultTakeName,
      downloadFilename: downloadFilename
    }
  };
})();
