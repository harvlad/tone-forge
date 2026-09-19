// TransportClock.swift
//
// Master clock for the perform experience (D-005). Every scheduling
// decision — stem playback offset, chord advancer tick, pad-press
// verify window, chop trigger onset — keys off the timestamps this
// clock produces.
//
// Design:
//   - Song time is slaved to the AUDIO RENDER CLOCK. When the engine is
//     rendering, `nowSongSeconds` derives elapsed seconds from the output
//     node's SAMPLE time (`outputNode.lastRenderTime.sampleTime`), which
//     advances at the true DAC rate. The AudioEngine wires that source in
//     via `attachRenderClock`. This is the whole point: the audio crystal
//     and the CPU's wall clock drift apart, so a wall clock (what this
//     used to be — a bare `mach_absolute_time`) makes the chord highlight
//     slide against the audio over a multi-minute song. Sample time can't.
//   - Before the engine has rendered (cold boot, headless XCTest with no
//     engine, or the pre-roll window before the first render callback),
//     the clock falls back to the injected `hostTimeProvider` wall clock,
//     then LAZILY adopts the sample anchor the first time a valid render
//     sample appears — folding the wall-clock elapsed so far into the
//     accumulated offset so song time is continuous across the handoff.
//   - `audibleSongSeconds` additionally subtracts `AVAudioSession
//     .outputLatency`: `nowSongSeconds` reports the sample being RENDERED,
//     but the DAC won't sound it until `outputLatency` later, so the chord
//     ribbon keys off the audible value to avoid LEADING the audio (badly
//     on Bluetooth). Scheduling (stems / metronome / quantize) keeps using
//     `nowSongSeconds` — those emit their own audio through the same output
//     path, so latency-shifting them would double-compensate.
//   - Supports pause + seek by mutating the anchor + accumulated offset.
//     Playback stays at Double-precision seconds internally because the LP
//     layer + chord advancer don't need sample-frame accuracy.
//
// The clock does NOT own the AVAudioEngine — it's a value-object-like
// helper the AudioEngine wraps. Under macOS + XCTest we swap the
// `hostTimeProvider` closure for a manual driver (and attach no render
// clock) so tests are hermetic.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Song-time clock backed by AVAudioTime. Thread-safety: all mutators
/// are `@MainActor`. Read-only accessors (`nowSongSeconds`) are
/// `nonisolated` so audio nodes can call them from render threads
/// without hopping.
public final class TransportClock: @unchecked Sendable {

    /// Playback state.
    public enum State: Sendable, Equatable {
        case stopped        // no song loaded, or after stop()
        case playing        // running; songSeconds advances
        case paused         // holds current position; songSeconds frozen
    }

    /// Absolute host-time provider. Injectable for tests.
    private let hostTimeProvider: @Sendable () -> UInt64

    /// Ticks-per-second for host time. Grabbed once on init because
    /// `mach_timebase_info` is stable for the process lifetime.
    private let ticksPerSecond: Double

    /// Lock protecting the mutable fields below. `os_unfair_lock` would
    /// be lighter but `NSLock` is cross-platform and this clock isn't
    /// on the hot path (called once per audio buffer at most).
    private let lock = NSLock()

    private var _state: State = .stopped
    /// Host-time at which the current playing/paused segment began.
    private var _anchorHostTime: UInt64 = 0
    /// Song seconds accumulated in previous segments (pauses + seeks
    /// snapshot into this before resetting the anchor).
    private var _accumulatedSongSeconds: Double = 0
    /// Playback rate (D-022 practice speed): song-seconds advance at
    /// `_rate` × wall-clock. 1.0 = normal.
    private var _rate: Double = 1.0

    /// Audio render-sample anchor for the current playing segment, or nil
    /// when the engine wasn't rendering at segment start (still on the
    /// wall-clock fallback until a valid sample is adopted).
    private var _anchorRenderSample: RenderSample?

    /// Output-node render-time source. Returns the current sample time +
    /// its sample rate while the engine is rendering, else nil. Not
    /// `@Sendable`-typed because the enclosing class is `@unchecked
    /// Sendable` and this closure captures the (non-Sendable)
    /// AVAudioEngine; `AVAudioNode.lastRenderTime` is documented safe to
    /// read from any thread, and every call sits under `lock`.
    private var _renderSampleProvider: (() -> RenderSample?)?

    /// Hardware output latency (seconds) source — AVAudioSession
    /// .outputLatency, injected by the AudioEngine. Subtracted by
    /// `audibleSongSeconds`.
    private var _outputLatencyProvider: (() -> Double)?

    /// A reading of the output node's render position: the monotonic
    /// output SAMPLE index and the rate it advances at.
    public struct RenderSample: Sendable {
        public let sampleTime: Int64
        public let sampleRate: Double
        public init(sampleTime: Int64, sampleRate: Double) {
            self.sampleTime = sampleTime
            self.sampleRate = sampleRate
        }
    }

    public init(
        hostTimeProvider: (@Sendable () -> UInt64)? = nil
    ) {
        if let provider = hostTimeProvider {
            self.hostTimeProvider = provider
        } else {
            #if canImport(AVFoundation)
            self.hostTimeProvider = { mach_absolute_time() }
            #else
            self.hostTimeProvider = { UInt64(Date().timeIntervalSince1970 * 1_000_000_000) }
            #endif
        }
        self.ticksPerSecond = TransportClock.ticksPerSecond()
    }

    // MARK: - Public API

    public var state: State {
        lock.lock(); defer { lock.unlock() }
        return _state
    }

    /// Current song-time in seconds. Advances monotonically while
    /// playing; frozen when paused; zero when stopped. Safe to call
    /// from any thread. This is the SCHEDULING authority — the sample
    /// the engine is rendering. UI that must match what the listener
    /// HEARS should use `audibleSongSeconds`.
    public var nowSongSeconds: Double {
        lock.lock(); defer { lock.unlock() }
        return _nowSongSecondsLocked()
    }

    /// Song-time as the LISTENER hears it: the rendered position minus
    /// the hardware output latency (DAC + buffer + Bluetooth codec). The
    /// chord ribbon keys off this so the highlight tracks the audible
    /// chord instead of leading it by the output latency. Equal to
    /// `nowSongSeconds` when not playing or when no latency source is
    /// attached (headless tests).
    public var audibleSongSeconds: Double {
        lock.lock(); defer { lock.unlock() }
        let now = _nowSongSecondsLocked()
        guard _state == .playing, let latency = _outputLatencyProvider?() else {
            return now
        }
        return now - latency
    }

    /// Wire the audio render clock + output-latency source. Called once by
    /// the AudioEngine after the engine exists. Passing these makes
    /// `nowSongSeconds` track the audio hardware's sample clock (drift-
    /// free) and `audibleSongSeconds` latency-compensate. Both closures
    /// may be invoked from audio render threads under `lock`.
    public func attachRenderClock(
        sampleProvider: @escaping () -> RenderSample?,
        outputLatencyProvider: @escaping () -> Double
    ) {
        lock.lock(); defer { lock.unlock() }
        _renderSampleProvider = sampleProvider
        _outputLatencyProvider = outputLatencyProvider
    }

    /// Playback rate: song-seconds advance at `rate` × wall-clock
    /// time. 1.0 = normal speed (D-022 practice speed).
    public var rate: Double {
        lock.lock(); defer { lock.unlock() }
        return _rate
    }

    /// Change the playback rate without a position glitch: the
    /// current position is snapshotted under the OLD rate, the anchor
    /// resets, then the new rate applies — song time is continuous
    /// across the change. Non-positive rates are ignored.
    public func setRate(_ newRate: Double) {
        lock.lock(); defer { lock.unlock() }
        guard newRate > 0, newRate != _rate else { return }
        _accumulatedSongSeconds = _nowSongSecondsLocked()
        if _state == .playing {
            _reanchorLocked()
        }
        _rate = newRate
    }

    /// Start (or resume from pause) playback at the current position.
    /// Idempotent when already playing.
    public func play() {
        lock.lock(); defer { lock.unlock() }
        if _state == .playing { return }
        _reanchorLocked()
        _state = .playing
    }

    /// Freeze the clock at its current position. Idempotent.
    public func pause() {
        lock.lock(); defer { lock.unlock() }
        if _state != .playing { return }
        _accumulatedSongSeconds = _nowSongSecondsLocked()
        _state = .paused
    }

    /// Reset to zero and stop.
    public func stop() {
        lock.lock(); defer { lock.unlock() }
        _accumulatedSongSeconds = 0
        _anchorHostTime = hostTimeProvider()
        _anchorRenderSample = nil
        _state = .stopped
    }

    /// Move to `seconds`. Preserves play/pause state. Negative values
    /// are allowed: sketch-record count-in runs the transport through
    /// a `[-barDuration, 0)` window so the metronome can click a lead
    /// bar while recorded content still starts at song-time 0.
    public func seek(to seconds: Double) {
        lock.lock(); defer { lock.unlock() }
        _accumulatedSongSeconds = seconds
        if _state == .playing {
            _reanchorLocked()
        }
    }

    // MARK: - Private

    /// Reset both anchors for a fresh playing segment (play / seek-while-
    /// playing / rate change). Sampling the render clock here means the
    /// wall-clock fallback is only ever used before the engine's first
    /// render, after which `_nowSongSecondsLocked` adopts the sample
    /// anchor. Must be called under `lock`.
    private func _reanchorLocked() {
        _anchorHostTime = hostTimeProvider()
        _anchorRenderSample = _renderSampleProvider?()
    }

    private func _nowSongSecondsLocked() -> Double {
        switch _state {
        case .stopped:
            return 0
        case .paused:
            return _accumulatedSongSeconds
        case .playing:
            // Preferred path: derive elapsed from the audio SAMPLE clock,
            // which advances at the true DAC rate (drift-free vs audio).
            // The `>= anchor` guard rejects a BACKWARD jump — AVAudioEngine
            // resets the output node's sample time when it restarts under
            // us (config change / media-services reset), which would
            // otherwise yield a negative elapsed and lurch the clock
            // backward. That case drops through to the wall clock, which
            // re-anchors onto the fresh sample clock below.
            if let anchor = _anchorRenderSample,
               let now = _renderSampleProvider?(), now.sampleRate > 0,
               now.sampleTime >= anchor.sampleTime {
                let elapsed = Double(now.sampleTime - anchor.sampleTime)
                    / now.sampleRate
                return _accumulatedSongSeconds + elapsed * _rate
            }
            // No usable render sample: headless tests, the pre-first-render
            // window, an engine stopped mid-load, or a just-restarted
            // engine whose sample clock reset. Advance by the wall clock
            // from the current anchor…
            let wallNow = hostTimeProvider()
            let wallElapsed = Double(wallNow &- _anchorHostTime) / ticksPerSecond
            let songNow = _accumulatedSongSeconds + wallElapsed * _rate
            // …and if a (fresh) render sample now exists, adopt it as the
            // anchor at THIS position so subsequent reads are drift-free
            // again — the one-time handoff (first render after play/seek,
            // or the first render after an engine restart) that moves the
            // clock off the wall fallback and onto the sample clock.
            if let now = _renderSampleProvider?(), now.sampleRate > 0 {
                _accumulatedSongSeconds = songNow
                _anchorHostTime = wallNow
                _anchorRenderSample = now
            }
            return songNow
        }
    }

    // MARK: - Host time <-> seconds

    /// Convert host-time ticks to seconds. Only needed at the audio
    /// scheduling boundary.
    ///
    /// mach_timebase_info gives (numer, denom) such that
    ///   nanoseconds = ticks * numer / denom
    /// therefore
    ///   ticks_per_second = 1e9 * denom / numer
    ///
    /// On Intel Macs numer == denom == 1 (so ticks == ns), which
    /// masked the earlier inverted formula. On Apple-Silicon iPhones
    /// the timebase is 125/3 and the inversion made songSeconds
    /// advance at ~1/1736 real time — the clock effectively froze.
    public static func ticksPerSecond() -> Double {
        #if canImport(AVFoundation)
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return 1_000_000_000.0 * Double(info.denom) / Double(info.numer)
        #else
        return 1_000_000_000
        #endif
    }
}
