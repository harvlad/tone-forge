// padengine.js — self-contained WebAudio pad engine, the web twin of the
// native sample engine (mobile-ios SeamlessLoop + SampleScheduler, jam-desktop
// ChopPlayer). Same algorithms, same constants:
//
//   * exact-length continuation-crossfade seam bake (SeamlessLoop.exactCrossfaded)
//   * onset-phase snap of grid-cut loop regions (SeamlessLoop.onsetAlignedShift)
//   * edge micro-fades (SeamlessLoop.applyEdgeFades)
//   * -4 dBFS peak normalize with +12 dB boost cap (normalizePeak)
//   * crossfadeMs choice: measured pad.crossfadeMs > loopScore map > 12 ms floor
//   * shared loop-lock grid + per-pad launch-shift compensation
//   * song-transport bar-grid quantize while the song plays (setTransport),
//     free-run lock grid as the stopped-transport fallback
//   * ref-counted stem takeover reporting (ontakeover — ChopPlayer twin;
//     the HOST ducks/restores the song stem, the engine only counts)
//   * practice-rate follow (setRate): launch-grid spacing only, buffers are
//     never resampled — pitch-true rate change is plugin-only
//
// The DSP is pure (Float32Array + sampleRate) so node can test it without
// WebAudio; the PadEngine class wraps it. ES module, no bundler, no deps.

// ---------------------------------------------------------------------------
// Constants (identical to native)
// ---------------------------------------------------------------------------

/// Default seam crossfade (ms) when a pad carries no measured value.
/// (SeamlessLoop.defaultLoopCrossfadeMs)
export const DEFAULT_LOOP_CROSSFADE_MS = 12.0;

/// Peak-normalize target: -4 dBFS linear. Stacking three or four locked
/// loops at -1 dBFS drove the master limiter into audible pumping; -4
/// leaves ~9 dB of stack headroom. (SampleScheduler.normalizeTargetPeak)
export const NORMALIZE_TARGET_PEAK = 0.63;

/// Continuation audio read past a loop-capable region's end for the
/// exact-length seam bake — covers the 8–30 ms crossfade clamp with margin.
/// (SampleScheduler.loopContinuationSec)
export const LOOP_CONTINUATION_SEC = 0.035;

/// Release fade applied on pad release / retrigger self-choke (20 ms, the
/// native voice pool's release fade).
export const RELEASE_FADE_SEC = 0.02;

/// Loop-lock boundary grace: a press landing within this window after a
/// boundary fires immediately instead of waiting a full cycle.
export const LOOP_LOCK_GRACE_SEC = 0.08;

/// Real-time grace past a quantized launch before the armed watchdog
/// declares the voice stuck (context clock never reached its start time)
/// and force-starts it. Armed-forever must be impossible.
export const ARMED_WATCHDOG_GRACE_SEC = 1.0;

/**
 * Real milliseconds the armed watchdog waits before verifying a scheduled
 * voice actually fired: the voice's own scheduled wait plus a grace second.
 * The scheduled wait is at most one lock/bar cycle, so the check always
 * lands within (cycle + grace). Pure — testable without WebAudio.
 * @param {number} startTime scheduled launch (ctx time, seconds)
 * @param {number} now ctx time when the launch was scheduled
 * @param {number} [graceSec]
 * @returns {number} milliseconds, never negative
 */
export function armedWatchdogDelayMs(startTime, now, graceSec = ARMED_WATCHDOG_GRACE_SEC) {
  return Math.max(0, startTime - now + graceSec) * 1000.0;
}

// ---------------------------------------------------------------------------
// Pure DSP (ports of SeamlessLoop / SampleScheduler statics)
// ---------------------------------------------------------------------------

/**
 * Ramp the first `attackMs` and last `releaseMs` of `channels` in place so a
 * one-shot's start and tail don't begin/end on a non-zero sample. Linear
 * ramps; no-op on short buffers. (SeamlessLoop.applyEdgeFades)
 * @param {Float32Array[]} channels
 * @param {number} sampleRate
 */
export function applyEdgeFades(channels, sampleRate, attackMs = 3.0, releaseMs = 5.0) {
  const n = channels.length ? channels[0].length : 0;
  if (!(n > 8) || !(sampleRate > 0)) return;
  let a = Math.trunc((attackMs / 1000.0) * sampleRate);
  let r = Math.trunc((releaseMs / 1000.0) * sampleRate);
  // Never overlap the two ramps, and always leave a body sample.
  a = Math.max(0, Math.min(a, Math.trunc((n - 1) / 2)));
  r = Math.max(0, Math.min(r, Math.trunc((n - 1) / 2)));
  for (const d of channels) {
    if (a > 0) for (let i = 0; i < a; i++) d[i] *= i / a;
    if (r > 0) for (let i = 0; i < r; i++) d[n - 1 - i] *= i / r;
  }
}

/**
 * Normalize `channels` in place so the peak sample sits at
 * NORMALIZE_TARGET_PEAK. Effectively-silent buffers (peak <= 1e-4) are left
 * untouched; boost is capped at +12 dB (4x) so bleed-only quiet slices stay
 * quiet; near-unity gain is skipped. (ChopPlayer.normalizePeak — the desktop
 * twin of SampleScheduler's, with the 1e-4 silence guard.)
 * @param {Float32Array[]} channels
 */
export function normalizePeak(channels) {
  const n = channels.length ? channels[0].length : 0;
  if (!(n > 0) || !(channels.length > 0)) return;
  let peak = 0;
  for (const d of channels) {
    for (let i = 0; i < d.length; i++) {
      const v = Math.abs(d[i]);
      if (v > peak) peak = v;
    }
  }
  if (!(peak > 1e-4)) return;
  const gain = Math.min(NORMALIZE_TARGET_PEAK / peak, 4.0);
  if (!(Math.abs(gain - 1.0) > 0.01)) return;
  for (const d of channels) {
    for (let i = 0; i < d.length; i++) d[i] *= gain;
  }
}

/**
 * Frame shift (within ±searchFrames) that puts `centerFrame` a small preroll
 * BEFORE the strongest energy rise near it; 0 when no clear transient exists
 * (sustained material must not be nudged). The beat grid's downbeat
 * timestamps land tens of ms AFTER the audible attack, so a grid-cut loop
 * region starts just past its own kick — this snaps the cut just ahead of the
 * attack so the hit stays inside the loop. (SeamlessLoop.onsetAlignedShift)
 * @param {Float32Array} ch0 scan buffer (first channel)
 * @param {number} sampleRate
 * @param {number} centerFrame nominal cut position within ch0
 * @param {number} searchFrames
 * @param {number} prerollFrames
 * @returns {number} integer frame shift
 */
export function onsetAlignedShift(ch0, sampleRate, centerFrame, searchFrames, prerollFrames) {
  const n = ch0.length;
  if (!(n > 0) || !(sampleRate > 0) || !(searchFrames > 0)) return 0;
  const lo = Math.max(0, centerFrame - searchFrames);
  const hi = Math.min(n, centerFrame + searchFrames);
  const hop = Math.max(32, Math.trunc(0.002 * sampleRate));
  if (!(hi - lo > hop * 4)) return 0;
  const env = [];
  let i = lo;
  while (i + hop <= hi) {
    let e = 0;
    for (let j = i; j < i + hop; j++) e += ch0[j] * ch0[j];
    env.push(Math.sqrt(e / hop));
    i += hop;
  }
  if (!(env.length > 2)) return 0;
  const rises = [];
  for (let k = 1; k < env.length; k++) rises.push(Math.max(0, env[k] - env[k - 1]));
  const maxRise = Math.max(...rises);
  if (!(maxRise > 1e-4)) return 0;
  const sorted = rises.slice().sort((a, b) => a - b);
  const median = sorted[Math.trunc(sorted.length / 2)];
  if (!(maxRise > 2 * median)) return 0;
  const best = rises.indexOf(maxRise);
  // rises[k] describes the step INTO env window k+1.
  const onsetFrame = lo + (best + 1) * hop;
  const shift = onsetFrame - prerollFrames - centerFrame;
  return Math.max(-searchFrames, Math.min(searchFrames, shift));
}

/**
 * EXACT-LENGTH seamless-loop bake. (SeamlessLoop.exactCrossfaded)
 *
 * `channels` holds the loop region plus optional CONTINUATION audio (extra
 * frames read past the region's end from the same source). The seam is baked
 * into the head at full length: out[0..x) is an equal-power blend of the
 * continuation (fading out) into the head (fading in), so every hard wrap is
 * continuous and the loop period stays EXACTLY `loopFrames`. Without
 * continuation it falls back to equal-power edge ramps at exact length
 * (dip, not click — never a shortened period).
 *
 * Transient-aware fade: when the loop head IS an attack (RMS of the first
 * 10 ms > 2x RMS of 10–60 ms and > 1e-4), the fade is capped at 3 ms so the
 * attack isn't played at reduced gain every pass.
 *
 * @param {Float32Array[]} channels loop region + continuation, per channel
 * @param {number} sampleRate
 * @param {number} loopFrames loop body length; output is exactly this long
 *   (clamped to the input length)
 * @param {number} crossfadeMs clamped to half the body and the continuation
 * @returns {Float32Array[]} new channel arrays of exactly the body length,
 *   or the input unchanged when degenerate (too short / no data)
 */
export function exactCrossfaded(channels, sampleRate, loopFrames, crossfadeMs) {
  const total = channels.length ? channels[0].length : 0;
  const n = Math.min(loopFrames, total);
  if (!(n > 8) || !(sampleRate > 0) || channels.length === 0) return channels;

  let x = Math.trunc((Math.max(0, crossfadeMs) / 1000.0) * sampleRate);
  x = Math.max(1, Math.min(x, Math.trunc(n / 2) - 1));
  const continuation = total - n;

  // Transient-aware fade (measured on channel 0, like the native pointee).
  const ch0 = channels[0];
  const a = Math.trunc(0.010 * sampleRate);
  const b = Math.trunc(0.060 * sampleRate);
  if (b <= n) {
    let e0 = 0;
    let e1 = 0;
    for (let i = 0; i < a; i++) e0 += ch0[i] * ch0[i];
    for (let i = a; i < b; i++) e1 += ch0[i] * ch0[i];
    const rms0 = Math.sqrt(e0 / a);
    const rms1 = Math.sqrt(e1 / (b - a));
    if (rms0 > 2 * rms1 && rms0 > 1e-4) {
      x = Math.max(1, Math.min(x, Math.trunc(0.003 * sampleRate)));
    }
  }

  const out = [];
  for (const s of channels) {
    // Exact-length body first; the seam only rewrites the head.
    const d = new Float32Array(s.subarray(0, n));
    if (continuation > 0) {
      // Baked seam: continuation (what really follows the loop's end)
      // fades out while the head fades back in.
      const xe = Math.min(x, continuation);
      for (let i = 0; i < xe; i++) {
        const t = i / xe;
        const gIn = Math.sin(0.5 * Math.PI * t);
        const gOut = Math.cos(0.5 * Math.PI * t);
        d[i] = Math.fround(s[i] * gIn + s[n + i] * gOut);
      }
    } else {
      // No continuation available: equal-power edge ramps. The wrap meets
      // at ~zero on both sides — dip, not click.
      for (let i = 0; i < x; i++) {
        const t = i / x;
        d[i] *= Math.sin(0.5 * Math.PI * t); // head fade-in
        d[n - x + i] *= Math.cos(0.5 * Math.PI * t); // tail fade-out
      }
    }
    out.push(d);
  }
  return out;
}

/**
 * Seam crossfade length for a kit pad: the analyzer's per-seam measurement
 * (pad.crossfadeMs) when present, else the coarse loopScore→ms map — a worse
 * seam gets a longer fade — else the 12 ms default floor. Clamped musical
 * 8..30 ms. (SampleScheduler trigger path + voice-pool default floor.)
 * @param {{crossfadeMs?: number, loopScore?: number}} pad
 * @returns {number} milliseconds
 */
export function chooseCrossfadeMs(pad) {
  if (pad && pad.crossfadeMs != null) {
    return Math.max(8.0, Math.min(30.0, pad.crossfadeMs));
  }
  if (pad && pad.loopScore != null) {
    return Math.max(8.0, Math.min(30.0, (1.0 - pad.loopScore) * 45.0));
  }
  return DEFAULT_LOOP_CROSSFADE_MS;
}

// ---------------------------------------------------------------------------
// PadEngine — the WebAudio wrapper
// ---------------------------------------------------------------------------

export class PadEngine {
  /**
   * @param {AudioContext} audioContext
   * @param {AudioNode} [destinationNode] defaults to audioContext.destination
   */
  constructor(audioContext, destinationNode) {
    // The context is BORROWED — usually the page's shared AudioContext. The
    // engine never suspends/closes it and never touches ctx.destination when
    // a destination node is provided, so a host can route the pads through
    // its own mixer/master chain. The one exception: trigger() may RESUME a
    // suspended context — a trigger is always user intent to hear audio, and
    // launch math against a suspended context's frozen clock is garbage.
    this.ctx = audioContext;
    this.destination = destinationNode || audioContext.destination;
    /** @type {Object<string, AudioBuffer>} stem role → decoded buffer */
    this._stems = {};
    this._pads = [];
    this._tempoBpm = null;
    /** padIdx → baked entry */
    this._baked = new Map();
    /** padIdx → active voice */
    this._voices = new Map();
    /** padIdx → token for a trigger deferred behind ctx.resume(). */
    this._pendingTriggers = new Map();
    /** Lock-grid anchor: audioContext time of the first loop launch. */
    this._lockAnchor = null;
    /** Host song transport (setTransport), null = free-run only. */
    this._transport = null;
    /** Practice-rate follow (setRate): scales launch-grid spacing only. */
    this._rate = 1.0;
    /** stem role → sounding-voice count (ChopPlayer.takeoverCounts twin). */
    this._takeoverCounts = new Map();
    /** @type {?function(number, {playing: boolean, armedUntil: ?number}): void} */
    this.onstate = null;
    /**
     * Stem takeover (song augmentation): fired when the number of sounding
     * voices for a stem role crosses 0↔active, so the HOST can duck
     * (active=true) / restore (active=false) the song's own stem. The engine
     * only reports; it never touches the song audio. Mirrors
     * ChopPlayer.onStemTakeoverChange: ANY voice with a stem role counts —
     * one-shots included, restored on their natural end — not just loops.
     * @type {?function(string, boolean): void}
     */
    this.ontakeover = null;
  }

  /**
   * Wire the page's song transport so quantized loop launches align to the
   * SONG's bar grid while it plays. Pass null to detach (free-run only).
   * When the transport is stopped — or reports no usable grid — quantized
   * launches fall back to the shared free-run lock grid, exactly as before.
   * (Desktop twin: LaunchpadController.isTransportPlaying + the
   * transport-rolling branch of its fireAt computation.)
   * @param {?{isPlaying: function(): boolean,
   *           getSongTime: function(): number,
   *           tempoBpm: number,
   *           barAnchorSongTime?: number}} transport
   *   getSongTime returns the song position in seconds (song domain);
   *   barAnchorSongTime is the song time of a known bar line (default 0).
   */
  setTransport(transport) {
    this._transport = transport || null;
  }

  /**
   * Practice-rate follow: `rate` scales launch-grid spacing (free-run lock
   * cycles shrink/grow by 1/rate; transport song-time deltas convert to real
   * seconds via /rate, matching the desktop's delay/tempoPct). Loop buffers
   * are NOT resampled — only launch scheduling shifts. Pitch-true playback
   * at a practice rate is plugin-only (the JUCE sampler owns resampling).
   * @param {number} rate > 0; invalid values reset to 1.
   */
  setRate(rate) {
    this._rate = rate > 0 && Number.isFinite(rate) ? rate : 1.0;
  }

  /**
   * Free-run lock-grid state for sibling tools (they should read this, not
   * `_lockAnchor`, which remains as a legacy field). `cycle` is the
   * EFFECTIVE spacing — loopLengthSeconds scaled by the practice rate.
   * @returns {{anchor: ?number, cycle: number}}
   */
  lockInfo() {
    return { anchor: this._lockAnchor, cycle: this.loopLengthSeconds / this._rate };
  }

  /** @param {Object<string, AudioBuffer>} buffersByRole */
  setStems(buffersByRole) {
    this._stems = buffersByRole || {};
    this._baked.clear();
  }

  /**
   * @param {{pads: Array}} kit /api/song/{id}/kit payload
   * @param {{tempoBpm?: number}} [opts]
   */
  setKit(kit, opts = {}) {
    this._pads = (kit && kit.pads) || [];
    this._tempoBpm = opts.tempoBpm != null ? opts.tempoBpm : null;
    this._baked.clear();
  }

  /** One 4/4 bar at the song tempo, or null without tempo. */
  get _barSeconds() {
    const bpm = this._tempoBpm;
    if (!(bpm > 0)) return null;
    return (60.0 / bpm) * 4.0;
  }

  /**
   * Shared lock-cycle length (s): the longest analyzer loop region among
   * loop-capable pads; falls back to the constant-tempo bars-fitting-8s
   * formula, then 8 s. (SampleScheduler.loopLengthSeconds)
   */
  get loopLengthSeconds() {
    let cycle = null;
    for (const p of this._pads) {
      if (!((p.loopable || false) || p.loopStartSec != null)) continue;
      const ls = p.loopStartSec;
      const le = p.loopEndSec;
      if (ls == null || le == null || !(le > ls)) continue;
      const len = le - ls;
      if (cycle == null || len > cycle) cycle = len;
    }
    if (cycle != null && cycle > 0.5) return cycle;
    const barSec = this._barSeconds;
    if (barSec == null) return 8.0;
    const bars = Math.max(1.0, Math.round(8.0 / barSec));
    return bars * barSec;
  }

  /**
   * The stem region to bake for a pad: the analyzer's explicit
   * [loopStartSec, loopEndSec] verbatim when present (real-downbeat bars —
   * never re-snap), else a bar-snapped stemSlice for loopable pads at known
   * tempo, else the raw stemSlice. (SampleScheduler._loopRegion)
   */
  _loopRegion(pad, slice) {
    const ls = pad.loopStartSec;
    const le = pad.loopEndSec;
    if (ls != null && le != null && le > ls) {
      return { startSec: ls, endSec: le };
    }
    const bar = this._barSeconds;
    if (!(pad.loopable || false) || bar == null || !(bar > 0)) {
      return { startSec: slice.startSec, endSec: slice.endSec };
    }
    const len = slice.endSec - slice.startSec;
    const bars = Math.max(1.0, Math.round(len / bar));
    return { startSec: slice.startSec, endSec: slice.startSec + bars * bar };
  }

  /**
   * Bake all pad buffers: slice → onset-phase shift → normalize → edge fades
   * → exact-length seam bake. Idempotent per setStems/setKit.
   * @returns {Object<number, {shiftSec: number, bodySec: number}>} per padIdx
   */
  prepare() {
    const info = {};
    for (const pad of this._pads) {
      const slice = pad.stemSlice;
      if (!slice) continue;
      const stem = this._stems[slice.stemRole];
      if (!stem) continue;
      const entry = this._bakePad(pad, slice, stem);
      if (!entry) continue;
      this._baked.set(pad.padIdx, entry);
      info[pad.padIdx] = { shiftSec: entry.shiftSec, bodySec: entry.bodySec };
    }
    return info;
  }

  _bakePad(pad, slice, stem) {
    const sr = stem.sampleRate;
    const stemLen = stem.length;
    const region = this._loopRegion(pad, slice);
    const mayLoop = (pad.loopable || false) || pad.loopPointSec != null || pad.loopStartSec != null;
    const contSec = mayLoop ? LOOP_CONTINUATION_SEC : 0;

    let startFrame = Math.trunc(Math.max(0, region.startSec) * sr);
    const endFrame = Math.trunc(Math.max(region.startSec, region.endSec) * sr);
    const requested = Math.max(0, endFrame - startFrame);
    const bodyCount = Math.min(requested, Math.max(0, stemLen - startFrame));
    if (!(bodyCount > 0) || startFrame >= stemLen) return null;

    const stemCh0 = stem.getChannelData(0);
    let shiftSec = 0;
    let extra = 0;
    if (contSec > 0 && sr > 0) {
      // Onset-phase snap (loop regions only): shift BOTH edges (period
      // kept) so the cut sits ~5 ms before the strongest nearby onset.
      const search = Math.round(0.060 * sr);
      const lo = Math.max(0, startFrame - search);
      const scanLen = Math.max(0, Math.min(stemLen - lo, startFrame - lo + search));
      if (scanLen > 0) {
        const shift = onsetAlignedShift(
          stemCh0.subarray(lo, lo + scanLen),
          sr,
          startFrame - lo,
          search,
          Math.trunc(0.005 * sr)
        );
        const s2 = startFrame + shift;
        if (s2 >= 0 && s2 + bodyCount <= stemLen) {
          startFrame = s2;
          shiftSec = shift / sr;
        }
      }
      const want = Math.trunc(contSec * sr);
      const avail = Math.max(0, stemLen - startFrame - bodyCount);
      extra = Math.min(want, avail);
    }

    const frameCount = bodyCount + extra;
    const numCh = stem.numberOfChannels;
    const channels = [];
    for (let c = 0; c < numCh; c++) {
      channels.push(new Float32Array(stem.getChannelData(c).subarray(startFrame, startFrame + frameCount)));
    }
    normalizePeak(channels);
    applyEdgeFades(channels, sr);

    // One-shot playback uses the normalized/faded region (loop-capable pads
    // include the 35 ms continuation tail, matching the native preloaded
    // buffer); loop playback uses the exact-body seam bake.
    const oneShotBuffer = this._toAudioBuffer(channels, sr);
    let loopBuffer = null;
    let loopChannels = null;
    if (mayLoop) {
      loopChannels = exactCrossfaded(channels, sr, bodyCount, chooseCrossfadeMs(pad));
      loopBuffer = this._toAudioBuffer(loopChannels, sr);
    }
    return {
      pad,
      sampleRate: sr,
      bodySec: bodyCount / sr,
      shiftSec,
      oneShotBuffer,
      oneShotChannels: channels,
      loopBuffer,
      loopChannels,
    };
  }

  _toAudioBuffer(channels, sampleRate) {
    const buf = this.ctx.createBuffer(channels.length, channels[0].length, sampleRate);
    for (let c = 0; c < channels.length; c++) buf.copyToChannel(channels[c], c);
    return buf;
  }

  /**
   * Next lock-grid launch time for `now`: boundaries at multiples of
   * loopLengthSeconds from the anchor (set at the first loop launch); a
   * press within the 0.08 s grace after a boundary fires immediately.
   */
  _lockLaunchTime(now) {
    if (this._lockAnchor == null) return now;
    // Practice-rate follow: grid spacing scales with rate (a 2x rate halves
    // the wait), same as the desktop dividing launch delays by tempoPct.
    const L = this.loopLengthSeconds / this._rate;
    if (!(L > 0)) return now;
    const elapsed = now - this._lockAnchor;
    const phase = elapsed % L;
    if (phase <= LOOP_LOCK_GRACE_SEC) return now;
    return this._lockAnchor + (Math.floor(elapsed / L) + 1) * L;
  }

  /**
   * Transport-aligned launch time: the next SONG bar line, converted to
   * audioContext time by sampling getSongTime() at call time —
   * launchTime = now + (nextBarSongTime - songTimeNow) / rate. Null when the
   * transport carries no usable grid (caller falls back to the free-run
   * lock grid). A press within the boundary grace fires immediately.
   */
  _transportLaunchTime(now) {
    const t = this._transport;
    if (!t || typeof t.getSongTime !== "function" || !(t.tempoBpm > 0)) return null;
    const bar = (60.0 / t.tempoBpm) * 4.0;
    const songNow = t.getSongTime();
    if (!Number.isFinite(songNow)) return null;
    const anchor = Number.isFinite(t.barAnchorSongTime) ? t.barAnchorSongTime : 0;
    const phase = (((songNow - anchor) % bar) + bar) % bar;
    if (phase <= LOOP_LOCK_GRACE_SEC) return now;
    // Song-domain delta → real seconds at the practice rate.
    return now + (bar - phase) / this._rate;
  }

  /**
   * Trigger a pad.
   * @param {number} padIdx
   * @param {{loop?: boolean, quantized?: boolean}} [opts]
   *   loop+quantized → schedule at the next lock boundary (+ the pad's
   *   launch shift); otherwise immediate. Loop launches are always delayed
   *   by the pad's onset shift so content downbeats line up (never negative).
   * @returns {?{startTime: ?number, loop: boolean, deferred?: boolean}}
   *   `deferred: true` (startTime null) means the context was suspended:
   *   the voice starts asynchronously once ctx.resume() lands. The caller
   *   should show "armed"; padProgress() reports the start as usual.
   */
  trigger(padIdx, opts = {}) {
    const entry = this._baked.get(padIdx);
    if (!entry) return null;
    const ctx = this.ctx;
    // A suspended AudioContext has a FROZEN currentTime. Computing launch
    // times against it schedules at a stale `now`: after resume the clock
    // continues from that stale value, so a "next boundary" landed at an
    // arbitrary real moment, and source.start(past) plays immediately per
    // spec — either way the quantize grid is fiction; if the clock never
    // advances the voice never sounds at all (armed forever). So resume
    // FIRST and compute launch times only once the clock is live. The
    // token guards a release/retrigger racing the resume.
    if (ctx && ctx.state === "suspended" && typeof ctx.resume === "function") {
      const token = {};
      this._pendingTriggers.set(padIdx, token);
      const fire = () => {
        if (this._pendingTriggers.get(padIdx) !== token) return; // superseded
        this._pendingTriggers.delete(padIdx);
        this._startVoice(padIdx, entry, opts);
      };
      try {
        // Fire even when resume rejects: starting on a still-suspended
        // context is the pre-guard legacy behavior, never worse.
        ctx.resume().then(fire, fire);
      } catch (_) {
        fire();
      }
      return { startTime: null, loop: !!opts.loop && entry.loopBuffer != null, deferred: true };
    }
    return this._startVoice(padIdx, entry, opts);
  }

  _startVoice(padIdx, entry, opts) {
    const wantLoop = !!opts.loop;
    const quantized = !!opts.quantized;
    const willLoop = wantLoop && entry.loopBuffer != null;

    // Stem this trigger takes over. Same-stem retrigger keeps the duck (no
    // deactivate/activate blip): the count transfers from the old voice to
    // the new one instead of end+begin — ChopPlayer's
    // `voices[index].takeoverStem != takeoverStem` guard.
    const stemRole = (entry.pad.stemSlice && entry.pad.stemSlice.stemRole) || null;
    const prevVoice = this._voices.get(padIdx);
    const transferTakeover =
      stemRole != null && !!prevVoice && !prevVoice.released && prevVoice.takeoverRole === stemRole;
    if (transferTakeover) prevVoice.takeoverRole = null;

    // Self-choke: retrigger stops the pad's previous voice first.
    this._stopVoice(padIdx, /* notify */ false);

    const now = this.ctx.currentTime;
    let startTime = now;
    if (willLoop) {
      let target = now;
      if (quantized) {
        // Transport rolling → quantize against the SONG's bar grid;
        // stopped/absent transport → shared free-run lock grid (unchanged
        // legacy behavior). Desktop twin: LaunchpadController's
        // transportRolling branch.
        const t = this._transport;
        const rolling = !!(t && typeof t.isPlaying === "function" && t.isPlaying());
        const aligned = rolling ? this._transportLaunchTime(now) : null;
        target = aligned != null ? aligned : this._lockLaunchTime(now);
      }
      // Launch compensation for the onset-phase snap: delay the launch by
      // the amount the region was shifted so the content's downbeat still
      // lands ON the grid; never negative.
      startTime = Math.max(now, target + Math.max(0, entry.shiftSec));
      if (this._lockAnchor == null) this._lockAnchor = startTime;
    }

    const source = this.ctx.createBufferSource();
    const gain = this.ctx.createGain();
    source.connect(gain);
    gain.connect(this.destination);
    if (willLoop) {
      // Baked buffer is exactly the body length; the seam is baked so the
      // browser's hard wrap at loopEnd is clean.
      source.buffer = entry.loopBuffer;
      source.loop = true;
      source.loopStart = 0;
      source.loopEnd = entry.bodySec;
    } else {
      source.buffer = entry.oneShotBuffer;
    }

    const voice = {
      source,
      gain,
      startTime,
      loop: willLoop,
      bodySec: entry.bodySec,
      released: false,
      takeoverRole: stemRole,
      watchdog: null,
    };
    this._voices.set(padIdx, voice);
    if (stemRole != null && !transferTakeover) this._beginTakeover(stemRole);
    source.onended = () => {
      // Natural end (one-shot played through): restore the taken-over stem.
      // A voice killed via _stopVoice was already removed from the map (and
      // its takeover ended), so this guard makes the late callback a no-op —
      // the web analogue of ChopPlayer's gen match.
      if (this._voices.get(padIdx) === voice) {
        if (voice.watchdog) {
          clearTimeout(voice.watchdog);
          voice.watchdog = null;
        }
        this._voices.delete(padIdx);
        this._endVoiceTakeover(voice);
        this._emitState(padIdx, { playing: false, armedUntil: null });
      }
    };
    source.start(startTime);
    // Armed watchdog: a quantized launch must be sounding by its own wait
    // plus a grace second (≤ cycle + 1 s). If the context clock never
    // reached startTime by then — real time passed but audio time didn't
    // (suspended/throttled context) — the pad would pulse "armed" forever;
    // _watchdogCheck force-starts it instead.
    if (willLoop && startTime > now + 1e-3 && typeof setTimeout === "function") {
      voice.watchdog = setTimeout(() => {
        voice.watchdog = null;
        this._watchdogCheck(padIdx, voice);
      }, armedWatchdogDelayMs(startTime, now));
    }
    this._emitState(padIdx, {
      playing: true,
      armedUntil: startTime > now + 1e-3 ? startTime : null,
    });
    return { startTime, loop: willLoop };
  }

  /** Stop a pad with the 20 ms release fade. */
  release(padIdx) {
    this._stopVoice(padIdx, /* notify */ true);
  }

  stopAll() {
    this._pendingTriggers.clear(); // pads waiting on ctx.resume() count too
    for (const padIdx of Array.from(this._voices.keys())) {
      this._stopVoice(padIdx, /* notify */ true);
    }
  }

  /**
   * Armed-forever guard, fired armedWatchdogDelayMs after a scheduled loop
   * launch. If the context clock reached the launch time the voice is
   * sounding and this is a no-op. Otherwise the clock stalled (suspended /
   * heavily throttled context) and the voice would pulse "armed" forever:
   * warn and force an immediate unquantized retrigger — trigger() self-
   * chokes the stuck voice, resumes a still-suspended context via the
   * deferred path, and the same-stem takeover transfer avoids a duck blip.
   */
  _watchdogCheck(padIdx, voice) {
    if (this._voices.get(padIdx) !== voice || voice.released) return;
    const now = this.ctx.currentTime;
    if (now >= voice.startTime - 0.05) return; // launched (or about to) on time
    if (typeof console !== "undefined" && console.warn) {
      console.warn(
        "PadEngine: pad " + padIdx + " stayed armed past its launch window " +
          "(ctx clock at " + now.toFixed(3) + " s, launch was " +
          voice.startTime.toFixed(3) + " s) — force-starting"
      );
    }
    this.trigger(padIdx, { loop: voice.loop, quantized: false });
  }

  _stopVoice(padIdx, notify) {
    // A pad released while its trigger waits on ctx.resume() must not have
    // the pending voice start late over an idle pad.
    this._pendingTriggers.delete(padIdx);
    const voice = this._voices.get(padIdx);
    if (!voice || voice.released) return;
    voice.released = true;
    if (voice.watchdog) {
      clearTimeout(voice.watchdog);
      voice.watchdog = null;
    }
    const now = this.ctx.currentTime;
    try {
      voice.gain.gain.setValueAtTime(voice.gain.gain.value, now);
      voice.gain.gain.linearRampToValueAtTime(0, now + RELEASE_FADE_SEC);
      voice.source.stop(now + RELEASE_FADE_SEC + 0.005);
    } catch (_) {
      /* already stopped */
    }
    this._voices.delete(padIdx);
    this._endVoiceTakeover(voice);
    if (notify) this._emitState(padIdx, { playing: false, armedUntil: null });
  }

  /**
   * Ref-counted stem takeover (ChopPlayer.beginTakeover): the host is
   * notified only when a role goes 0 → active.
   */
  _beginTakeover(role) {
    const c = this._takeoverCounts.get(role) || 0;
    this._takeoverCounts.set(role, c + 1);
    if (c === 0 && this.ontakeover) this.ontakeover(role, true);
  }

  /**
   * End a voice's takeover, if any (ChopPlayer.endTakeover): idempotent —
   * the role is cleared first — and the host is notified only when the
   * role's count returns to 0.
   */
  _endVoiceTakeover(voice) {
    const role = voice.takeoverRole;
    if (role == null) return;
    voice.takeoverRole = null;
    const c = this._takeoverCounts.get(role) || 0;
    if (c <= 1) {
      this._takeoverCounts.delete(role);
      if (this.ontakeover) this.ontakeover(role, false);
    } else {
      this._takeoverCounts.set(role, c - 1);
    }
  }

  /**
   * 0..1 position within the loop cycle, 0 while armed but not yet sounding,
   * null when the pad isn't looping. (ChopPlayer.loopProgress)
   */
  padProgress(padIdx) {
    const voice = this._voices.get(padIdx);
    if (!voice || !voice.loop || !(voice.bodySec > 0)) return null;
    const t = this.ctx.currentTime - voice.startTime;
    if (t < 0) return 0; // scheduled but not yet fired
    return (t % voice.bodySec) / voice.bodySec;
  }

  /**
   * Waveform peak bins of the BAKED buffer — what actually sounds (the seam
   * bake for loop-capable pads). Normalized to 0..1.
   * @returns {?Float32Array}
   */
  peaks(padIdx, bins = 44) {
    const entry = this._baked.get(padIdx);
    if (!entry || !(bins > 0)) return null;
    const channels = entry.loopChannels || entry.oneShotChannels;
    const frames = channels[0].length;
    const framesPerBin = Math.max(1, Math.trunc(frames / bins));
    const peaks = new Float32Array(bins);
    for (let bin = 0; bin < bins; bin++) {
      const start = bin * framesPerBin;
      if (start >= frames) break;
      const end = Math.min(frames, start + framesPerBin);
      let peak = 0;
      for (const d of channels) {
        for (let i = start; i < end; i++) {
          const v = Math.abs(d[i]);
          if (v > peak) peak = v;
        }
      }
      peaks[bin] = peak;
    }
    let maxPeak = 0;
    for (const p of peaks) if (p > maxPeak) maxPeak = p;
    if (maxPeak > 0) for (let i = 0; i < peaks.length; i++) peaks[i] /= maxPeak;
    return peaks;
  }

  _emitState(padIdx, state) {
    if (this.onstate) this.onstate(padIdx, state);
  }
}
