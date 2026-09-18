// TransportClock.swift
//
// Song-position clock. Ground truth for transport position is the
// AUDIO RENDER CLOCK — the output node's `lastRenderTime` sample count
// (an AVAudioTime backed by the DAC's own oscillator), NOT a SwiftUI
// timer and NOT the CPU wall clock. Slaving the song position to the
// same sample clock that clocks playback is what keeps the chord
// highlight from drifting against the audio over a long song: a free-
// running `mach_absolute_time` wall clock is a DIFFERENT oscillator
// than the audio device, so the two accumulate skew over minutes.
//
// Rate semantics: at rate r the clock advances r song-seconds per
// real second of rendered audio. setRate() snapshots the position
// under the old rate before re-anchoring, so a mid-play tempo change
// never jumps the playhead.
//
// Latency: the render clock counts samples the engine has HANDED to
// the output, but the listener hears them `outputLatency` seconds
// later (render buffer + DAC + Bluetooth). `nowSongSeconds` subtracts
// that latency so the highlight matches what's HEARD instead of
// leading it — the lead was worst on Bluetooth, where the latency is
// tens of ms. Set the value with `setOutputLatency`.
//
// Thread-safety: lock-protected so the display timer, scheduler and UI
// can read the position from anywhere. The monotonic time source is an
// injectable closure — production reads the render clock, tests inject
// a fake host-time provider so clock semantics are exercised with no
// real time and no audio engine.

import Foundation
import JamDesktopCore

public final class TransportClock: @unchecked Sendable {

    public enum State: Equatable, Sendable {
        case stopped
        case playing
        case paused
    }

    private let lock = NSLock()
    private var _state: State = .stopped
    /// Anchor captured in the monotonic source's own timeline (seconds).
    private var _anchorMonotonicSeconds: Double = 0
    /// RAW (render-aligned) song position frozen at the last re-anchor
    /// point — never carries the output-latency offset, so resuming /
    /// rate changes don't compound the compensation.
    private var _accumulatedSongSeconds: Double = 0
    private var _rate: Double = 1.0
    /// Output latency in seconds, subtracted from the reported HEARD
    /// position while playing (see file header).
    private var _outputLatencySeconds: Double = 0

    /// Monotonic seconds source. Production injects the audio render
    /// clock; the default reads `mach_absolute_time` (fallback + the
    /// path the unit tests drive through the host-time provider).
    private let monotonicSeconds: () -> Double

    /// Test / fallback initializer: a raw mach-tick provider. Kept so
    /// the headless clock tests can advance "fake ticks" and assert
    /// rate/pause/seek semantics without an audio engine.
    public init(hostTimeProvider: @escaping @Sendable () -> UInt64 = { mach_absolute_time() }) {
        self.monotonicSeconds = {
            Double(hostTimeProvider()) / Self.ticksPerSecond()
        }
    }

    /// Production initializer: `provider` returns a monotonic time in
    /// SECONDS from the audio render clock (outputNode.lastRenderTime),
    /// so song position tracks the sample clock that drives playback.
    public init(monotonicSecondsProvider provider: @escaping () -> Double) {
        self.monotonicSeconds = provider
    }

    public var state: State {
        lock.lock(); defer { lock.unlock() }
        return _state
    }

    public var rate: Double {
        lock.lock(); defer { lock.unlock() }
        return _rate
    }

    /// The HEARD song position: render-aligned position minus the
    /// output latency (in song-seconds) while playing.
    public var nowSongSeconds: Double {
        lock.lock(); defer { lock.unlock() }
        return unlockedNowSongSeconds()
    }

    /// Set the output latency (seconds) the reported position is
    /// retarded by so the highlight matches the audio the listener
    /// hears. Clamped non-negative; safe to update on a device flap.
    public func setOutputLatency(_ seconds: Double) {
        lock.lock(); defer { lock.unlock() }
        _outputLatencySeconds = max(0, seconds)
    }

    /// Render-aligned position (what the engine has clocked out), no
    /// latency compensation. This is the value the anchor/accumulate
    /// math and pause snapshots operate on.
    private func unlockedRawSongSeconds() -> Double {
        switch _state {
        case .stopped:
            return 0
        case .paused:
            return _accumulatedSongSeconds
        case .playing:
            let elapsed = monotonicSeconds() - _anchorMonotonicSeconds
            return _accumulatedSongSeconds + elapsed * _rate
        }
    }

    private func unlockedNowSongSeconds() -> Double {
        let raw = unlockedRawSongSeconds()
        // Only the live, playing position leads the audio; a paused /
        // stopped playhead sits where it was parked.
        guard _state == .playing else { return raw }
        return raw - _outputLatencySeconds * _rate
    }

    public func play() {
        lock.lock(); defer { lock.unlock() }
        guard _state != .playing else { return }
        _anchorMonotonicSeconds = monotonicSeconds()
        _state = .playing
    }

    public func pause() {
        lock.lock(); defer { lock.unlock() }
        guard _state == .playing else { return }
        // Freeze the RAW position (no latency term) so resume doesn't
        // double-count the compensation.
        _accumulatedSongSeconds = unlockedRawSongSeconds()
        _state = .paused
    }

    public func stop() {
        lock.lock(); defer { lock.unlock() }
        _accumulatedSongSeconds = 0
        _state = .stopped
    }

    /// Jump the song position. Negative values are legal (count-in
    /// windows run the transport through negative time).
    public func seek(to songSeconds: Double) {
        lock.lock(); defer { lock.unlock() }
        _accumulatedSongSeconds = songSeconds
        if _state == .playing {
            _anchorMonotonicSeconds = monotonicSeconds()
        }
    }

    /// Change the advance rate glitch-free: the current RAW position is
    /// captured under the OLD rate, then the anchor resets so future
    /// elapsed time scales by the new rate.
    public func setRate(_ rate: Double) {
        lock.lock(); defer { lock.unlock() }
        let position = unlockedRawSongSeconds()
        _accumulatedSongSeconds = position
        _anchorMonotonicSeconds = monotonicSeconds()
        _rate = rate
    }

    /// mach ticks per second. Note the timebase fraction converts
    /// ticks → nanoseconds via numer/denom, so ticks-per-second is
    /// 1e9 * denom / numer (the inversion matters on Apple Silicon,
    /// where the timebase is not 1:1).
    public static func ticksPerSecond() -> Double {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return 1_000_000_000.0 * Double(info.denom) / Double(info.numer)
    }
}

extension TransportClock: SongClock {}
