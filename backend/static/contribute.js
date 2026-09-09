/* contribute.js — web Contribute surface (Voice | Beat | Sample), the
 * browser mirror of mobile's Contribute capture flows:
 *
 *   Voice  — record a short phrase from the mic (MicRecorder.swift):
 *            8 s hard cap, live level meter, keep/discard, take list.
 *   Beat   — tap/clap a rhythm over a metronome (BeatCaptureSheet.swift):
 *            16 s analysis-only cap, onset markers drawn on the
 *            captured waveform, tempo follows the loaded song.
 *   Sample — record or import a sample destined for a pad
 *            (PadSourceSheet.swift): 8 s cap, waveform trim handles,
 *            "send to pad" hand-off + WAV download.
 *
 * Classic script: defines window.JamnContribute = { mount, unmount }.
 * Contract: JamnContribute.mount(container, ctx) where ctx =
 *   { audioContext, entry, onSampleReady? } — entry is the full
 *   /api/history/{id} object (tempo read from entry.result.tempo_bpm,
 *   same field kit.js feeds PadEngine), onSampleReady({buffer, name})
 *   lets the host hand a trimmed AudioBuffer to the kit.
 *
 * COMPLIANCE (mirrors PadSampleMetadata.neverUpload): mic captures
 * never leave the browser. There is deliberately no upload call in
 * this file — takes persist to IndexedDB db "jamn-contribute" only.
 * The backend exposes no contribute/user-sample upload endpoint
 * (verified against the live route table); if one is ever added it
 * must be opt-in per take, never automatic.
 */
(function () {
  "use strict";

  // ---- caps (mirror mobile constants) ------------------------------
  // StemSlice.maxChopDurationSec — the 8 s compliance cap MicRecorder
  // auto-stops at and PadSampleStore rejects beyond.
  var MAX_SAMPLE_SEC = 8;
  // BeatCaptureSheet.captureDurationSec — longer than the pad cap is
  // allowed because the beat take is analysis-only (only the derived
  // hits are kept alongside it here).
  var MAX_BEAT_SEC = 16;
  // MicRecorder.maxLevels — rolling meter window length.
  var METER_BARS = 64;

  var DB_NAME = "jamn-contribute";
  var DB_STORE = "takes";

  // ------------------------------------------------------------------
  // Small DOM helpers
  // ------------------------------------------------------------------

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function button(cls, label, onClick) {
    var b = el("button", cls, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function fmtSec(s) {
    return (Math.round(s * 10) / 10).toFixed(1) + "s";
  }

  // ------------------------------------------------------------------
  // IndexedDB take store (device-local; the web analogue of
  // PadSampleStore's Documents/samples sidecars)
  // ------------------------------------------------------------------

  function openDb() {
    return new Promise(function (resolve, reject) {
      if (!window.indexedDB) { reject(new Error("IndexedDB unavailable")); return; }
      var req = indexedDB.open(DB_NAME, 1);
      req.onupgradeneeded = function () {
        var db = req.result;
        if (!db.objectStoreNames.contains(DB_STORE)) {
          var store = db.createObjectStore(DB_STORE, { keyPath: "id" });
          store.createIndex("kind", "kind", { unique: false });
        }
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error || new Error("IndexedDB open failed")); };
    });
  }

  function dbPut(record) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).put(record);
        tx.oncomplete = function () { db.close(); resolve(); };
        tx.onerror = function () { db.close(); reject(tx.error); };
      });
    });
  }

  function dbDelete(id) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(DB_STORE, "readwrite");
        tx.objectStore(DB_STORE).delete(id);
        tx.oncomplete = function () { db.close(); resolve(); };
        tx.onerror = function () { db.close(); reject(tx.error); };
      });
    });
  }

  function dbListByKind(kind) {
    return openDb().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(DB_STORE, "readonly");
        var req = tx.objectStore(DB_STORE).index("kind").getAll(kind);
        req.onsuccess = function () {
          db.close();
          var rows = req.result || [];
          rows.sort(function (a, b) { return b.createdAt - a.createdAt; });
          resolve(rows);
        };
        req.onerror = function () { db.close(); reject(req.error); };
      });
    });
  }

  /** Frozen v1 take record. Additive evolution only (mirrors the
   * PadSampleMetadata schemaVersion discipline). `samples` is a plain
   * ArrayBuffer of Float32 mono PCM so IndexedDB clones it cheaply. */
  function makeTakeRecord(kind, name, mono, sampleRate, extras) {
    var rec = {
      schemaVersion: 1,
      id: "take-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8),
      kind: kind,                 // 'voice' | 'beat' | 'sample'
      name: name,
      source: (extras && extras.source) || "mic",
      createdAt: Date.now(),
      durationSec: mono.length / sampleRate,
      sampleRate: sampleRate,
      channels: 1,
      neverUpload: true,          // compliance tripwire, mirrors mobile
      samples: mono.buffer.slice(mono.byteOffset, mono.byteOffset + mono.byteLength),
    };
    if (extras) {
      if (extras.bpm != null) rec.bpm = extras.bpm;
      if (extras.onsets) rec.onsets = extras.onsets;
    }
    return rec;
  }

  // ------------------------------------------------------------------
  // PCM helpers
  // ------------------------------------------------------------------

  /** Average channels to mono (CaptureBox does the same on mobile) and
   * hard-truncate at capSec — the cap is exact regardless of how late
   * the MediaRecorder stop landed. */
  function monoize(audioBuffer, capSec) {
    var frames = Math.min(
      audioBuffer.length,
      Math.floor(capSec * audioBuffer.sampleRate)
    );
    var out = new Float32Array(frames);
    var chans = audioBuffer.numberOfChannels;
    for (var c = 0; c < chans; c++) {
      var data = audioBuffer.getChannelData(c);
      for (var i = 0; i < frames; i++) out[i] += data[i];
    }
    if (chans > 1) {
      for (var j = 0; j < frames; j++) out[j] /= chans;
    }
    return out;
  }

  function monoToAudioBuffer(actx, mono, sampleRate) {
    var buf = actx.createBuffer(1, Math.max(1, mono.length), sampleRate);
    buf.copyToChannel(mono, 0);
    return buf;
  }

  /** 16-bit PCM mono WAV encode for the download buttons. */
  function encodeWav(mono, sampleRate) {
    var n = mono.length;
    var buf = new ArrayBuffer(44 + n * 2);
    var v = new DataView(buf);
    function str(off, s) { for (var i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)); }
    str(0, "RIFF"); v.setUint32(4, 36 + n * 2, true); str(8, "WAVE");
    str(12, "fmt "); v.setUint32(16, 16, true); v.setUint16(20, 1, true);
    v.setUint16(22, 1, true); v.setUint32(24, sampleRate, true);
    v.setUint32(28, sampleRate * 2, true); v.setUint16(32, 2, true);
    v.setUint16(34, 16, true); str(36, "data"); v.setUint32(40, n * 2, true);
    for (var i = 0; i < n; i++) {
      var s = Math.max(-1, Math.min(1, mono[i]));
      v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: "audio/wav" });
  }

  function downloadWav(mono, sampleRate, name) {
    var url = URL.createObjectURL(encodeWav(mono, sampleRate));
    var a = document.createElement("a");
    a.href = url;
    a.download = (name || "take").replace(/[^\w\- ]+/g, "_") + ".wav";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 5000);
  }

  // ------------------------------------------------------------------
  // Onset detection (Beat tab) — short-window energy rise with a
  // refractory gap. Deliberately simple: the mobile flow's failure
  // message ("tap a little louder and leave space between hits") is
  // the contract, not classifier parity.
  // ------------------------------------------------------------------

  function detectOnsets(mono, sampleRate) {
    var hop = Math.max(64, Math.round(sampleRate * 0.010)); // 10 ms hops
    var win = hop * 2;
    var nHops = Math.max(0, Math.floor((mono.length - win) / hop));
    if (nHops < 3) return [];
    var env = new Float32Array(nHops);
    var i, j;
    for (i = 0; i < nHops; i++) {
      var sum = 0, off = i * hop;
      for (j = 0; j < win; j++) { var s = mono[off + j]; sum += s * s; }
      env[i] = Math.sqrt(sum / win);
    }
    // Adaptive floor: median energy of the take. Guards a silent room
    // (tiny median) with an absolute floor so hiss never "hits".
    var sorted = Array.prototype.slice.call(env).sort(function (a, b) { return a - b; });
    var median = sorted[Math.floor(sorted.length / 2)];
    var thresh = Math.max(0.02, median * 2.5);
    var refractory = Math.round(0.09 * sampleRate / hop); // 90 ms between hits
    var onsets = [];
    var last = -refractory;
    for (i = 1; i < nHops; i++) {
      if (env[i] > thresh && env[i] > env[i - 1] * 1.3 && i - last >= refractory) {
        onsets.push({
          timeSec: (i * hop + win / 2) / sampleRate,
          strength: Math.min(1, env[i] / (thresh * 4)),
        });
        last = i;
      }
    }
    return onsets;
  }

  // ------------------------------------------------------------------
  // Waveform drawing
  // ------------------------------------------------------------------

  function cssVar(name, fallback) {
    try {
      var v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return v || fallback;
    } catch (_) { return fallback; }
  }

  function fitCanvas(canvas) {
    var dpr = window.devicePixelRatio || 1;
    var rect = canvas.getBoundingClientRect();
    var w = Math.max(1, Math.round(rect.width)), h = Math.max(1, Math.round(rect.height));
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr;
      canvas.height = h * dpr;
    }
    var g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { g: g, w: w, h: h };
  }

  function drawWaveform(canvas, mono, opts) {
    opts = opts || {};
    var c = fitCanvas(canvas), g = c.g, w = c.w, h = c.h;
    g.clearRect(0, 0, w, h);
    g.fillStyle = cssVar("--jamn-surface2", "#1C1C24");
    g.fillRect(0, 0, w, h);
    if (!mono || !mono.length) return;
    var accent = cssVar("--jamn-accent", "#8B5CF6");
    var per = mono.length / w;
    g.fillStyle = accent;
    for (var x = 0; x < w; x++) {
      var start = Math.floor(x * per), end = Math.floor((x + 1) * per);
      var mn = 0, mx = 0;
      for (var i = start; i < end && i < mono.length; i++) {
        var s = mono[i];
        if (s < mn) mn = s;
        if (s > mx) mx = s;
      }
      var y0 = (1 - mx) * 0.5 * h, y1 = (1 - mn) * 0.5 * h;
      g.fillRect(x, y0, 1, Math.max(1, y1 - y0));
    }
    // Onset markers (Beat review — the "tapped-onset markers on the
    // waveform" the mobile hit list shows as rows).
    if (opts.onsets && opts.durationSec) {
      g.fillStyle = cssVar("--jamn-danger", "#EF4444");
      for (var k = 0; k < opts.onsets.length; k++) {
        var ox = Math.round(opts.onsets[k].timeSec / opts.durationSec * w);
        g.fillRect(ox, 0, 2, h);
      }
    }
    // Trim shading (Sample tab): dim outside [trimStart, trimEnd].
    if (opts.trim && opts.durationSec) {
      var sx = Math.round(opts.trim.startSec / opts.durationSec * w);
      var ex = Math.round(opts.trim.endSec / opts.durationSec * w);
      g.fillStyle = "rgba(0,0,0,0.55)";
      g.fillRect(0, 0, sx, h);
      g.fillRect(ex, 0, w - ex, h);
      g.fillStyle = cssVar("--jamn-success", "#22C55E");
      g.fillRect(sx, 0, 2, h);
      g.fillRect(ex - 2, 0, 2, h);
    }
  }

  /** Scrolling bar meter of recent input peaks — same visual contract
   * as mobile's WaveformMeter: newest bar right, empty slots pad left. */
  function drawMeter(canvas, levels) {
    var c = fitCanvas(canvas), g = c.g, w = c.w, h = c.h;
    g.clearRect(0, 0, w, h);
    var accent = cssVar("--jamn-accent", "#8B5CF6");
    var spacing = 2;
    var barW = Math.max(1, (w - spacing * (METER_BARS - 1)) / METER_BARS);
    var pad = METER_BARS - levels.length;
    for (var i = 0; i < METER_BARS; i++) {
      var level = i >= pad ? levels[i - pad] : 0;
      g.fillStyle = accent;
      g.globalAlpha = level > 0.001 ? 0.9 : 0.15;
      var bh = Math.max(2, level * h);
      g.fillRect(i * (barW + spacing), (h - bh) / 2, barW, bh);
    }
    g.globalAlpha = 1;
  }

  // ------------------------------------------------------------------
  // Recorder — getUserMedia + MediaRecorder + AnalyserNode meter with
  // a hard auto-stop at the cap (MicRecorder's tap-thread cap becomes
  // a timer here; the exact cap is re-enforced at decode by monoize).
  // ------------------------------------------------------------------

  function Recorder(actx) {
    this.actx = actx;
    this.stream = null;
    this.media = null;
    this.analyser = null;
    this.srcNode = null;
    this.levels = [];
    this.startedAt = 0;
    this.capSec = MAX_SAMPLE_SEC;
    this.raf = 0;
    this.stopTimer = 0;
    this.onLevels = null;   // (levels[], elapsedSec) at ~30 fps
    this.onDone = null;     // (mono Float32Array, sampleRate) after decode
    this.onError = null;    // (message)
    this._active = false;
  }

  Recorder.prototype.isActive = function () { return this._active; };

  Recorder.prototype.start = function (capSec) {
    var self = this;
    if (self._active) return Promise.reject(new Error("already recording"));
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      return Promise.reject(new Error("mic-unsupported"));
    }
    self.capSec = capSec;
    // Raw capture: music/percussion through echoCancellation comes out
    // pumped and gated, so ask for the unprocessed path like mobile's
    // private AVAudioEngine tap.
    return navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
    }).then(function (stream) {
      self.stream = stream;
      var chunks = [];
      var mime = "";
      if (window.MediaRecorder) {
        var candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", ""];
        for (var i = 0; i < candidates.length; i++) {
          if (!candidates[i] || MediaRecorder.isTypeSupported(candidates[i])) {
            mime = candidates[i];
            break;
          }
        }
      } else {
        stream.getTracks().forEach(function (t) { t.stop(); });
        throw new Error("mic-unsupported");
      }
      var rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
      self.media = rec;
      rec.ondataavailable = function (e) { if (e.data && e.data.size) chunks.push(e.data); };
      rec.onstop = function () { self._decode(new Blob(chunks, { type: mime || "audio/webm" })); };

      // Level meter from an analyser tap on the raw stream.
      self.srcNode = self.actx.createMediaStreamSource(stream);
      self.analyser = self.actx.createAnalyser();
      self.analyser.fftSize = 1024;
      self.srcNode.connect(self.analyser);
      var timeBuf = new Float32Array(self.analyser.fftSize);
      var lastPush = 0;
      function tick(now) {
        if (!self._active) return;
        if (now - lastPush > 1000 / 30) {
          lastPush = now;
          var peak = 0;
          if (self.analyser.getFloatTimeDomainData) {
            self.analyser.getFloatTimeDomainData(timeBuf);
            for (var i = 0; i < timeBuf.length; i++) {
              var a = Math.abs(timeBuf[i]);
              if (a > peak) peak = a;
            }
          }
          self.levels.push(Math.min(peak, 1));
          if (self.levels.length > METER_BARS) {
            self.levels.splice(0, self.levels.length - METER_BARS);
          }
          var elapsed = Math.min((performance.now() - self.startedAt) / 1000, self.capSec);
          if (self.onLevels) self.onLevels(self.levels, elapsed);
        }
        self.raf = requestAnimationFrame(tick);
      }

      self.levels = [];
      self.startedAt = performance.now();
      self._active = true;
      rec.start(250); // chunked so a tab crash loses ≤250 ms, not the take
      self.raf = requestAnimationFrame(tick);
      self.stopTimer = setTimeout(function () { self.stop(); }, capSec * 1000);
    });
  };

  Recorder.prototype.stop = function () {
    if (!this._active) return;
    this._active = false;
    clearTimeout(this.stopTimer);
    cancelAnimationFrame(this.raf);
    try { if (this.media && this.media.state !== "inactive") this.media.stop(); } catch (_) {}
    this._teardownStream();
  };

  /** Abandon without decoding (tab switch / unmount). */
  Recorder.prototype.cancel = function () {
    if (!this._active) { this._teardownStream(); return; }
    this._active = false;
    clearTimeout(this.stopTimer);
    cancelAnimationFrame(this.raf);
    if (this.media) this.media.onstop = null;
    try { if (this.media && this.media.state !== "inactive") this.media.stop(); } catch (_) {}
    this._teardownStream();
  };

  Recorder.prototype._teardownStream = function () {
    try { if (this.srcNode) this.srcNode.disconnect(); } catch (_) {}
    this.srcNode = null;
    this.analyser = null;
    if (this.stream) {
      this.stream.getTracks().forEach(function (t) { t.stop(); });
      this.stream = null;
    }
  };

  Recorder.prototype._decode = function (blob) {
    var self = this;
    blob.arrayBuffer().then(function (ab) {
      // decodeAudioData with both callback and promise forms for Safari.
      return new Promise(function (resolve, reject) {
        var p = self.actx.decodeAudioData(ab, resolve, reject);
        if (p && p.then) p.then(resolve, reject);
      });
    }).then(function (audioBuf) {
      var mono = monoize(audioBuf, self.capSec);
      if (self.onDone) self.onDone(mono, audioBuf.sampleRate);
    }).catch(function (e) {
      if (self.onError) self.onError("Could not decode the recording: " + (e && e.message ? e.message : e));
    });
  };

  // ------------------------------------------------------------------
  // Metronome (Beat tab) — lookahead-scheduled WebAudio clicks,
  // accent on beat 1 of 4.
  // ------------------------------------------------------------------

  function Metronome(actx) {
    this.actx = actx;
    this.bpm = 120;
    this.timer = 0;
    this.nextTime = 0;
    this.beat = 0;
    this.running = false;
    this.onBeat = null; // (beatIndex) for the UI pulse
  }

  Metronome.prototype._click = function (when, accent) {
    var osc = this.actx.createOscillator();
    var gain = this.actx.createGain();
    osc.frequency.value = accent ? 1200 : 800;
    gain.gain.setValueAtTime(accent ? 0.5 : 0.3, when);
    gain.gain.exponentialRampToValueAtTime(0.001, when + 0.05);
    osc.connect(gain).connect(this.actx.destination);
    osc.start(when);
    osc.stop(when + 0.06);
  };

  Metronome.prototype.start = function () {
    if (this.running) return;
    this.running = true;
    this.beat = 0;
    this.nextTime = this.actx.currentTime + 0.1;
    var self = this;
    this.timer = setInterval(function () {
      var horizon = self.actx.currentTime + 0.15;
      while (self.nextTime < horizon) {
        var accent = self.beat % 4 === 0;
        self._click(self.nextTime, accent);
        var b = self.beat;
        var dt = Math.max(0, (self.nextTime - self.actx.currentTime) * 1000);
        if (self.onBeat) setTimeout(function () { if (self.running && self.onBeat) self.onBeat(b); }, dt);
        self.nextTime += 60 / self.bpm;
        self.beat++;
      }
    }, 50);
  };

  Metronome.prototype.stop = function () {
    this.running = false;
    clearInterval(this.timer);
  };

  // ------------------------------------------------------------------
  // Playback of a mono take through the shared context
  // ------------------------------------------------------------------

  function Player(actx) {
    this.actx = actx;
    this.node = null;
    this.onEnded = null;
  }

  Player.prototype.play = function (mono, sampleRate, startSec, endSec) {
    this.stop();
    var self = this;
    var buf = monoToAudioBuffer(this.actx, mono, sampleRate);
    var src = this.actx.createBufferSource();
    src.buffer = buf;
    src.connect(this.actx.destination);
    src.onended = function () {
      if (self.node === src) self.node = null;
      if (self.onEnded) self.onEnded();
    };
    var offset = Math.max(0, startSec || 0);
    var dur = endSec != null ? Math.max(0.01, endSec - offset) : undefined;
    if (dur != null) src.start(0, offset, dur);
    else src.start(0, offset);
    this.node = src;
  };

  Player.prototype.stop = function () {
    if (this.node) {
      try { this.node.onended = null; this.node.stop(); } catch (_) {}
      this.node = null;
    }
  };

  Player.prototype.isPlaying = function () { return !!this.node; };

  // ------------------------------------------------------------------
  // Take list rendering (shared by all three tabs)
  // ------------------------------------------------------------------

  function renderTakeList(host, kind, player, refresh) {
    host.textContent = "";
    var heading = el("div", "jc-takes-heading", "Saved takes");
    host.appendChild(heading);
    var listEl = el("div", "jc-takes-list");
    host.appendChild(listEl);
    var empty = el("div", "jc-empty", "No saved takes yet — they stay on this device.");
    listEl.appendChild(empty);
    dbListByKind(kind).then(function (rows) {
      if (!rows.length) return;
      empty.remove();
      rows.forEach(function (row) {
        var item = el("div", "jc-take");
        var main = el("div", "jc-take-main");
        main.appendChild(el("div", "jc-take-name", row.name || "Untitled"));
        var meta = fmtSec(row.durationSec) + " · " +
          new Date(row.createdAt).toLocaleString();
        if (row.bpm) meta += " · " + Math.round(row.bpm) + " BPM";
        if (row.onsets) meta += " · " + row.onsets.length + " hits";
        main.appendChild(el("div", "jc-take-meta", meta));
        item.appendChild(main);

        var mono = new Float32Array(row.samples);
        var playBtn = button("jc-btn jc-btn--ghost", "Play", function () {
          if (player.isPlaying()) { player.stop(); playBtn.textContent = "Play"; return; }
          playBtn.textContent = "Stop";
          player.onEnded = function () { playBtn.textContent = "Play"; };
          player.play(mono, row.sampleRate);
        });
        item.appendChild(playBtn);
        item.appendChild(button("jc-btn jc-btn--ghost", "WAV", function () {
          downloadWav(mono, row.sampleRate, row.name);
        }));
        item.appendChild(button("jc-btn jc-btn--danger", "Delete", function () {
          dbDelete(row.id).then(refresh, refresh);
        }));
        listEl.appendChild(item);
      });
    }).catch(function () {
      empty.textContent = "Saved takes are unavailable (IndexedDB blocked in this browser mode).";
    });
  }

  // ------------------------------------------------------------------
  // Mount / tabs
  // ------------------------------------------------------------------

  var current = null; // { container, ctx, actx, recorder, metronome, player, cleanup[] }

  function mount(container, ctx) {
    unmount();
    if (!container) return;
    ctx = ctx || {};
    var actx = ctx.audioContext;
    if (!actx) {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (AC) actx = new AC();
    }
    if (!actx) {
      container.textContent = "";
      container.appendChild(el("div", "jc-banner jc-banner--error",
        "This browser has no Web Audio support — Contribute needs it to record and play takes."));
      return;
    }

    var state = {
      container: container,
      ctx: ctx,
      actx: actx,
      ownActx: !ctx.audioContext,
      recorder: new Recorder(actx),
      metronome: new Metronome(actx),
      player: new Player(actx),
      cleanup: [],
      activeTab: "voice",
    };
    current = state;

    container.textContent = "";
    container.classList.add("jc-root");

    // Single-section mode: each sidebar item (Voice / Beat / Sample) opens
    // its OWN popup showing only that section — no shared tab strip. The
    // inline #view-contribute pane (no `only`) still gets all three tabs.
    var ALL = ["voice", "beat", "sample"];
    var only = ctx.only && ALL.indexOf(ctx.only) !== -1 ? ctx.only : null;
    var visible = only ? [only] : ALL;

    var panes = {};
    var tabBtns = {};

    // Tab strip only when showing more than one section.
    var tabs = null;
    if (!only) {
      tabs = el("div", "jc-tabs");
      var LABEL = { voice: "Voice", beat: "Beat", sample: "Sample" };
      visible.forEach(function (id) {
        tabBtns[id] = button("jc-tab", LABEL[id], function () { selectTab(id); });
        tabs.appendChild(tabBtns[id]);
      });
      container.appendChild(tabs);
    }

    visible.forEach(function (id) {
      panes[id] = el("div", "jc-pane");
      panes[id].hidden = true;
      container.appendChild(panes[id]);
    });

    function selectTab(id) {
      // Leaving a tab mid-recording abandons the take, mirroring the
      // mobile sheets' cancel-on-dismiss.
      state.recorder.cancel();
      state.metronome.stop();
      state.player.stop();
      state.activeTab = id;
      visible.forEach(function (k) {
        // style.display (not the `hidden` attr) so a .jc-pane{display:block}
        // rule can't override it and stack every section (the old bug).
        panes[k].style.display = k === id ? "" : "none";
        panes[k].hidden = k !== id;
        if (tabBtns[k]) tabBtns[k].classList.toggle("jc-tab--active", k === id);
      });
    }

    if (visible.indexOf("voice") !== -1) buildVoicePane(state, panes.voice);
    if (visible.indexOf("beat") !== -1) buildBeatPane(state, panes.beat);
    if (visible.indexOf("sample") !== -1) buildSamplePane(state, panes.sample);
    selectTab(only || "voice");

    // Autoplay policy: the context may arrive suspended; resume on the
    // first gesture inside the surface so play/click buttons work.
    var resumeOnce = function () {
      if (actx.state === "suspended") actx.resume().catch(function () {});
    };
    container.addEventListener("pointerdown", resumeOnce);
    state.cleanup.push(function () {
      container.removeEventListener("pointerdown", resumeOnce);
    });
  }

  function unmount() {
    if (!current) return;
    var s = current;
    current = null;
    try { s.recorder.cancel(); } catch (_) {}
    try { s.metronome.stop(); } catch (_) {}
    try { s.player.stop(); } catch (_) {}
    s.cleanup.forEach(function (fn) { try { fn(); } catch (_) {} });
    if (s.ownActx) { try { s.actx.close(); } catch (_) {} }
    try {
      s.container.textContent = "";
      s.container.classList.remove("jc-root");
    } catch (_) {}
  }

  // ------------------------------------------------------------------
  // Shared capture panel builder: idle → recording → review, with the
  // permission-denied and unsupported-browser paths always visible as
  // banners rather than dead buttons.
  //
  // opts: { capSec, hint, kind, onReview(mono, sampleRate, reviewHost,
  //         saveExtras()) — returns optional teardown fn }
  // ------------------------------------------------------------------

  function buildCapturePanel(state, pane, opts) {
    var status = el("div", "jc-status", opts.hint);
    var banner = el("div", "jc-banner");
    banner.hidden = true;
    var meterWrap = el("div", "jc-meter-wrap");
    var meter = el("canvas", "jc-meter");
    meterWrap.appendChild(meter);
    var clock = el("div", "jc-clock", "0.0 / " + opts.capSec + " s");
    meterWrap.appendChild(clock);
    meterWrap.hidden = true;

    var controls = el("div", "jc-controls");
    var recordBtn = button("jc-btn jc-btn--primary", "Record", onRecord);
    var stopBtn = button("jc-btn jc-btn--danger", "Stop", onStop);
    stopBtn.hidden = true;
    controls.appendChild(recordBtn);
    controls.appendChild(stopBtn);

    var reviewHost = el("div", "jc-review");
    reviewHost.hidden = true;

    pane.appendChild(status);
    pane.appendChild(banner);
    pane.appendChild(controls);
    pane.appendChild(meterWrap);
    pane.appendChild(reviewHost);

    var reviewTeardown = null;

    function showBanner(msg, isError) {
      banner.textContent = msg;
      banner.className = "jc-banner" + (isError ? " jc-banner--error" : "");
      banner.hidden = false;
    }

    function toIdle() {
      recordBtn.hidden = false;
      recordBtn.disabled = false;
      stopBtn.hidden = true;
      meterWrap.hidden = true;
      status.textContent = opts.hint;
    }

    function clearReview() {
      if (reviewTeardown) { try { reviewTeardown(); } catch (_) {} reviewTeardown = null; }
      // Import-path reviews aren't registered as reviewTeardown, so
      // stop playback unconditionally — a cleared review must go silent.
      state.player.stop();
      reviewHost.textContent = "";
      reviewHost.hidden = true;
    }

    function onRecord() {
      if (state.recorder.isActive()) return;
      banner.hidden = true;
      clearReview();
      state.player.stop();
      recordBtn.disabled = true;
      status.textContent = "Starting the microphone…";
      if (state.actx.state === "suspended") state.actx.resume().catch(function () {});

      state.recorder.onLevels = function (levels, elapsed) {
        drawMeter(meter, levels);
        clock.textContent = fmtSec(elapsed) + " / " + opts.capSec + " s";
      };
      state.recorder.onDone = function (mono, sampleRate) {
        toIdle();
        if (!mono.length) {
          showBanner("Nothing was captured — the take was empty.", false);
          return;
        }
        reviewHost.hidden = false;
        reviewTeardown = opts.onReview(mono, sampleRate, reviewHost, clearReview) || null;
      };
      state.recorder.onError = function (msg) {
        toIdle();
        showBanner(msg, true);
      };
      if (opts.beforeStart) opts.beforeStart();

      state.recorder.start(opts.capSec).then(function () {
        recordBtn.hidden = true;
        recordBtn.disabled = false;
        stopBtn.hidden = false;
        meterWrap.hidden = false;
        status.textContent = "Recording — auto-stops at " + opts.capSec + " s.";
        if (opts.onStarted) opts.onStarted();
      }).catch(function (e) {
        toIdle();
        if (opts.onStopped) opts.onStopped();
        if (e && (e.name === "NotAllowedError" || e.name === "SecurityError")) {
          // Mirrors MicRecorder.RecorderError.permissionDenied wording.
          showBanner("Microphone access is not allowed. Enable it for this site in your browser settings, then try again.", true);
        } else if (e && e.name === "NotFoundError") {
          showBanner("No microphone was found on this device.", true);
        } else if (e && e.message === "mic-unsupported") {
          showBanner("This browser can't record audio (no MediaRecorder support).", true);
        } else {
          showBanner("Could not start the microphone: " + (e && e.message ? e.message : e), true);
        }
      });
    }

    function onStop() {
      state.recorder.stop();
      if (opts.onStopped) opts.onStopped();
      status.textContent = "Processing the take…";
      stopBtn.hidden = true;
    }

    return { status: status, banner: showBanner, clearReview: clearReview, toIdle: toIdle };
  }

  // ------------------------------------------------------------------
  // Voice tab — phrase recorder + take list (MicRecorder semantics:
  // 8 s cap, level meter, keep/discard)
  // ------------------------------------------------------------------

  function buildVoicePane(state, pane) {
    pane.appendChild(el("h3", "jc-title", "Voice"));
    pane.appendChild(el("p", "jc-sub",
      "Record a short vocal phrase — a hook, a word, a hum. Takes cap at " +
      MAX_SAMPLE_SEC + " seconds and never leave this device."));

    var takesHost = el("div", "jc-takes");

    function refreshTakes() {
      renderTakeList(takesHost, "voice", state.player, refreshTakes);
    }

    buildCapturePanel(state, pane, {
      capSec: MAX_SAMPLE_SEC,
      kind: "voice",
      hint: "Ready when you are.",
      onReview: function (mono, sampleRate, host, clearReview) {
        var wave = el("canvas", "jc-wave");
        host.appendChild(wave);
        requestAnimationFrame(function () { drawWaveform(wave, mono); });

        var nameRow = el("div", "jc-name-row");
        var nameInput = el("input", "jc-input");
        nameInput.type = "text";
        nameInput.placeholder = "Name this take";
        nameInput.value = "Voice " + new Date().toLocaleTimeString();
        nameRow.appendChild(nameInput);
        host.appendChild(nameRow);

        var row = el("div", "jc-controls");
        var playBtn = button("jc-btn jc-btn--ghost", "Play", function () {
          if (state.player.isPlaying()) { state.player.stop(); playBtn.textContent = "Play"; return; }
          playBtn.textContent = "Stop";
          state.player.onEnded = function () { playBtn.textContent = "Play"; };
          state.player.play(mono, sampleRate);
        });
        row.appendChild(playBtn);
        row.appendChild(button("jc-btn jc-btn--primary", "Keep", function () {
          dbPut(makeTakeRecord("voice", nameInput.value.trim() || "Voice take", mono, sampleRate))
            .then(function () { clearReview(); refreshTakes(); },
                  function () { clearReview(); refreshTakes(); });
        }));
        row.appendChild(button("jc-btn jc-btn--ghost", "Discard", function () {
          state.player.stop();
          clearReview();
        }));
        host.appendChild(row);
        return function () { state.player.stop(); };
      },
    });

    pane.appendChild(takesHost);
    refreshTakes();
  }

  // ------------------------------------------------------------------
  // Beat tab — metronome + record + onset markers (BeatCaptureSheet
  // semantics: 16 s cap, tempo follows the song, 60–200 BPM manual
  // range, no-hits failure message verbatim)
  // ------------------------------------------------------------------

  function buildBeatPane(state, pane) {
    pane.appendChild(el("h3", "jc-title", "Beat"));
    pane.appendChild(el("p", "jc-sub",
      "Tap, clap, or beatbox a rhythm over the click. We detect the hits and mark them on the waveform. Takes cap at " +
      MAX_BEAT_SEC + " seconds."));

    // Tempo: follow the loaded song when it has one (same field kit.js
    // reads), else default 120; editable 60–200 like the mobile stepper.
    var songBpm = null;
    try {
      var t = state.ctx.entry && state.ctx.entry.result && state.ctx.entry.result.tempo_bpm;
      if (typeof t === "number" && t >= 40 && t <= 240) songBpm = Math.round(t);
    } catch (_) {}
    state.metronome.bpm = songBpm || 120;

    var tempoRow = el("div", "jc-tempo-row");
    tempoRow.appendChild(el("span", "jc-label", "Tempo"));
    var bpmInput = el("input", "jc-input jc-input--bpm");
    bpmInput.type = "number";
    bpmInput.min = "60";
    bpmInput.max = "200";
    bpmInput.step = "1";
    bpmInput.value = String(state.metronome.bpm);
    bpmInput.addEventListener("change", function () {
      var v = Math.max(60, Math.min(200, Math.round(Number(bpmInput.value) || 120)));
      bpmInput.value = String(v);
      state.metronome.bpm = v;
    });
    tempoRow.appendChild(bpmInput);
    tempoRow.appendChild(el("span", "jc-label", "BPM" + (songBpm ? " · following the song" : "")));
    var beatDot = el("span", "jc-beat-dot");
    tempoRow.appendChild(beatDot);
    var clickToggle = el("input");
    clickToggle.type = "checkbox";
    clickToggle.checked = true;
    clickToggle.id = "jc-click-toggle";
    var clickLabel = el("label", "jc-label");
    clickLabel.htmlFor = clickToggle.id;
    clickLabel.textContent = "Click";
    tempoRow.appendChild(clickToggle);
    tempoRow.appendChild(clickLabel);
    pane.appendChild(tempoRow);

    // Speaker-feedback note: the web analogue of MicRecorder's
    // speakerFeedbackRisk route warning — we can't read the route, so
    // it's a standing note while the click is armed.
    pane.appendChild(el("div", "jc-note",
      "Use headphones — on speakers the mic records the click too."));

    state.metronome.onBeat = function (b) {
      beatDot.classList.toggle("jc-beat-dot--accent", b % 4 === 0);
      beatDot.classList.remove("jc-beat-dot--on");
      // restart the pulse animation
      void beatDot.offsetWidth;
      beatDot.classList.add("jc-beat-dot--on");
    };

    var takesHost = el("div", "jc-takes");
    function refreshTakes() {
      renderTakeList(takesHost, "beat", state.player, refreshTakes);
    }

    buildCapturePanel(state, pane, {
      capSec: MAX_BEAT_SEC,
      kind: "beat",
      hint: "Hit Record and play along with the click.",
      beforeStart: function () {
        if (clickToggle.checked) state.metronome.start();
      },
      onStopped: function () { state.metronome.stop(); },
      onReview: function (mono, sampleRate, host, clearReview) {
        state.metronome.stop();
        var durationSec = mono.length / sampleRate;
        var onsets = detectOnsets(mono, sampleRate);

        if (!onsets.length) {
          // Verbatim mobile failure copy (BeatCaptureSheet).
          host.appendChild(el("div", "jc-banner",
            "No beats detected. Try tapping a little louder and leave space between hits."));
          host.appendChild(button("jc-btn jc-btn--ghost", "Try Again", clearReview));
          return null;
        }

        var wave = el("canvas", "jc-wave");
        host.appendChild(wave);
        requestAnimationFrame(function () {
          drawWaveform(wave, mono, { onsets: onsets, durationSec: durationSec });
        });
        host.appendChild(el("div", "jc-take-meta",
          onsets.length + " hits detected · " + state.metronome.bpm + " BPM"));

        var nameRow = el("div", "jc-name-row");
        var nameInput = el("input", "jc-input");
        nameInput.type = "text";
        nameInput.placeholder = "Name this beat";
        nameInput.value = "Beat " + state.metronome.bpm + " BPM";
        nameRow.appendChild(nameInput);
        host.appendChild(nameRow);

        var row = el("div", "jc-controls");
        var playBtn = button("jc-btn jc-btn--ghost", "Play", function () {
          if (state.player.isPlaying()) { state.player.stop(); playBtn.textContent = "Play"; return; }
          playBtn.textContent = "Stop";
          state.player.onEnded = function () { playBtn.textContent = "Play"; };
          state.player.play(mono, sampleRate);
        });
        row.appendChild(playBtn);
        row.appendChild(button("jc-btn jc-btn--primary", "Keep", function () {
          dbPut(makeTakeRecord("beat", nameInput.value.trim() || "Beat take", mono, sampleRate, {
            bpm: state.metronome.bpm,
            onsets: onsets.map(function (o) { return { timeSec: o.timeSec, strength: o.strength }; }),
          })).then(function () { clearReview(); refreshTakes(); },
                   function () { clearReview(); refreshTakes(); });
        }));
        row.appendChild(button("jc-btn jc-btn--ghost", "Discard", function () {
          state.player.stop();
          clearReview();
        }));
        host.appendChild(row);
        return function () { state.player.stop(); };
      },
    });

    pane.appendChild(takesHost);
    refreshTakes();
  }

  // ------------------------------------------------------------------
  // Sample tab — capture/import → trim → send to pad / download
  // (PadSourceSheet semantics: 8 s cap enforced on import too, the
  // web analogue of StemSlice.clamped())
  // ------------------------------------------------------------------

  function buildSamplePane(state, pane) {
    pane.appendChild(el("h3", "jc-title", "Sample"));
    pane.appendChild(el("p", "jc-sub",
      "Record or import a sound, trim it, and send it to a pad. Samples cap at " +
      MAX_SAMPLE_SEC + " seconds — longer imports are clipped to the first " +
      MAX_SAMPLE_SEC + "."));

    var takesHost = el("div", "jc-takes");
    function refreshTakes() {
      renderTakeList(takesHost, "sample", state.player, refreshTakes);
    }

    var panel = buildCapturePanel(state, pane, {
      capSec: MAX_SAMPLE_SEC,
      kind: "sample",
      hint: "Record from the mic, or import a file below.",
      onReview: function (mono, sampleRate, host, clearReview) {
        return buildTrimReview(state, mono, sampleRate, host, clearReview, refreshTakes, "mic");
      },
    });

    // Import path — also the graceful degrade when the mic is blocked.
    var importRow = el("div", "jc-controls");
    var fileInput = el("input");
    fileInput.type = "file";
    fileInput.accept = "audio/*";
    fileInput.hidden = true;
    importRow.appendChild(button("jc-btn jc-btn--ghost", "Import audio file", function () {
      fileInput.click();
    }));
    importRow.appendChild(fileInput);
    pane.appendChild(importRow);

    fileInput.addEventListener("change", function () {
      var f = fileInput.files && fileInput.files[0];
      fileInput.value = "";
      if (!f) return;
      panel.clearReview();
      panel.status.textContent = "Decoding " + f.name + "…";
      f.arrayBuffer().then(function (ab) {
        return new Promise(function (resolve, reject) {
          var p = state.actx.decodeAudioData(ab, resolve, reject);
          if (p && p.then) p.then(resolve, reject);
        });
      }).then(function (audioBuf) {
        panel.status.textContent = "Record from the mic, or import a file below.";
        var mono = monoize(audioBuf, MAX_SAMPLE_SEC);
        var reviewHost = pane.querySelector(".jc-review");
        reviewHost.hidden = false;
        buildTrimReview(state, mono, audioBuf.sampleRate, reviewHost,
          panel.clearReview, refreshTakes, "import",
          f.name.replace(/\.[^.]+$/, ""));
      }).catch(function (e) {
        panel.status.textContent = "Record from the mic, or import a file below.";
        panel.banner("Could not decode that file: " + (e && e.message ? e.message : e), true);
      });
    });

    pane.appendChild(takesHost);
    refreshTakes();
  }

  /** Trim review UI: waveform with draggable start/end handles, play
   * (trimmed), send-to-pad, download, keep, discard. */
  function buildTrimReview(state, mono, sampleRate, host, clearReview, refreshTakes, source, suggestedName) {
    host.textContent = "";
    var durationSec = mono.length / sampleRate;
    var trim = { startSec: 0, endSec: durationSec };

    var wave = el("canvas", "jc-wave jc-wave--trim");
    host.appendChild(wave);
    var readout = el("div", "jc-take-meta");
    host.appendChild(readout);

    function redraw() {
      drawWaveform(wave, mono, { trim: trim, durationSec: durationSec });
      readout.textContent =
        "Trim " + fmtSec(trim.startSec) + " – " + fmtSec(trim.endSec) +
        " (" + fmtSec(trim.endSec - trim.startSec) + ")";
    }
    requestAnimationFrame(redraw);

    // Drag the nearer handle; a plain click also snaps the nearer one.
    var dragging = null;
    function posToSec(evt) {
      var rect = wave.getBoundingClientRect();
      var frac = Math.max(0, Math.min(1, (evt.clientX - rect.left) / rect.width));
      return frac * durationSec;
    }
    wave.addEventListener("pointerdown", function (evt) {
      wave.setPointerCapture(evt.pointerId);
      var sec = posToSec(evt);
      dragging = Math.abs(sec - trim.startSec) <= Math.abs(sec - trim.endSec) ? "start" : "end";
      onDrag(evt);
    });
    function onDrag(evt) {
      if (!dragging) return;
      var sec = posToSec(evt);
      // ≥50 ms floor keeps the trimmed slice audible and the pad
      // hand-off buffer non-degenerate.
      if (dragging === "start") trim.startSec = Math.min(sec, trim.endSec - 0.05);
      else trim.endSec = Math.max(sec, trim.startSec + 0.05);
      trim.startSec = Math.max(0, trim.startSec);
      trim.endSec = Math.min(durationSec, trim.endSec);
      redraw();
    }
    wave.addEventListener("pointermove", onDrag);
    wave.addEventListener("pointerup", function () { dragging = null; });
    wave.addEventListener("pointercancel", function () { dragging = null; });

    function trimmedMono() {
      var a = Math.floor(trim.startSec * sampleRate);
      var b = Math.min(mono.length, Math.ceil(trim.endSec * sampleRate));
      return mono.slice(a, Math.max(a + 1, b));
    }

    var nameRow = el("div", "jc-name-row");
    var nameInput = el("input", "jc-input");
    nameInput.type = "text";
    nameInput.placeholder = "Name this sample";
    nameInput.value = suggestedName || ("Sample " + new Date().toLocaleTimeString());
    nameRow.appendChild(nameInput);
    host.appendChild(nameRow);

    var row = el("div", "jc-controls");
    var playBtn = button("jc-btn jc-btn--ghost", "Play", function () {
      if (state.player.isPlaying()) { state.player.stop(); playBtn.textContent = "Play"; return; }
      playBtn.textContent = "Stop";
      state.player.onEnded = function () { playBtn.textContent = "Play"; };
      state.player.play(mono, sampleRate, trim.startSec, trim.endSec);
    });
    row.appendChild(playBtn);

    var sendBtn = button("jc-btn jc-btn--primary", "Send to pad", function () {
      var m = trimmedMono();
      var buffer = monoToAudioBuffer(state.actx, m, sampleRate);
      try {
        state.ctx.onSampleReady({
          buffer: buffer,
          name: nameInput.value.trim() || "Sample",
        });
        sendBtn.textContent = "Sent ✓";
        setTimeout(function () { sendBtn.textContent = "Send to pad"; }, 1500);
      } catch (e) {
        sendBtn.textContent = "Send failed";
        setTimeout(function () { sendBtn.textContent = "Send to pad"; }, 1500);
      }
    });
    if (typeof state.ctx.onSampleReady !== "function") {
      // No dead buttons: explain instead of disabling silently.
      sendBtn.disabled = true;
      sendBtn.title = "Open a song's Jam Pads first — the pad grid receives the sample.";
    }
    row.appendChild(sendBtn);

    row.appendChild(button("jc-btn jc-btn--ghost", "Download WAV", function () {
      downloadWav(trimmedMono(), sampleRate, nameInput.value.trim() || "sample");
    }));
    row.appendChild(button("jc-btn jc-btn--ghost", "Keep", function () {
      dbPut(makeTakeRecord("sample", nameInput.value.trim() || "Sample", trimmedMono(), sampleRate, { source: source }))
        .then(function () { clearReview(); refreshTakes(); },
              function () { clearReview(); refreshTakes(); });
    }));
    row.appendChild(button("jc-btn jc-btn--ghost", "Discard", function () {
      state.player.stop();
      clearReview();
    }));
    host.appendChild(row);
    if (sendBtn.disabled) {
      host.appendChild(el("div", "jc-note",
        "“Send to pad” needs the host to pass onSampleReady — load a song and open Jam Pads."));
    }
    return function () { state.player.stop(); };
  }

  // ------------------------------------------------------------------

  window.JamnContribute = {
    mount: mount,
    unmount: unmount,
    // Pure pieces exposed for smoke tests, same pattern as kit.js.
    _internals: {
      detectOnsets: detectOnsets,
      monoize: monoize,
      encodeWav: encodeWav,
      makeTakeRecord: makeTakeRecord,
      MAX_SAMPLE_SEC: MAX_SAMPLE_SEC,
      MAX_BEAT_SEC: MAX_BEAT_SEC,
    },
  };
})();
