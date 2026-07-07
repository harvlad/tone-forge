// TransportClock.swift
//
// Master clock for the perform experience (D-005). Every scheduling
// decision — stem playback offset, chord advancer tick, pad-press
// verify window, chop trigger onset — keys off the timestamps this
// clock produces.
//
// Design:
//   - Backed by `AVAudioTime` sampled from the engine's outputNode.
//     This is the same clock the driver samples the DAC with, so
//     anything scheduled at `AVAudioTime` boundaries is sample-accurate.
//   - Exposes a "song time" (seconds since play() was called) derived
//     by subtracting the anchor host time from `now()`. Song time is
//     what UI + engine logic consume; AVAudioTime is only used at the
//     boundary where audio nodes are scheduled.
//   - Supports pause + seek by mutating the anchor + accumulated
//     offset. Playback stays at Double-precision seconds internally
//     because the LP layer + chord advancer don't need sample-frame
//     accuracy.
//
// The clock does NOT own the AVAudioEngine — it's a value-object-like
// helper the AudioEngine wraps. Under macOS + XCTest we swap the
// `hostTimeProvider` closure for a manual driver so tests are hermetic.

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
    /// from any thread.
    public var nowSongSeconds: Double {
        lock.lock(); defer { lock.unlock() }
        return _nowSongSecondsLocked()
    }

    /// Start (or resume from pause) playback at the current position.
    /// Idempotent when already playing.
    public func play() {
        lock.lock(); defer { lock.unlock() }
        if _state == .playing { return }
        _anchorHostTime = hostTimeProvider()
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
            _anchorHostTime = hostTimeProvider()
        }
    }

    // MARK: - Private

    private func _nowSongSecondsLocked() -> Double {
        switch _state {
        case .stopped:
            return 0
        case .paused:
            return _accumulatedSongSeconds
        case .playing:
            let now = hostTimeProvider()
            let elapsedTicks = Double(now &- _anchorHostTime)
            return _accumulatedSongSeconds + elapsedTicks / ticksPerSecond
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
