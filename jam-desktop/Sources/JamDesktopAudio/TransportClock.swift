// TransportClock.swift
//
// Song-position clock, ported from the mobile app's TransportClock:
// mach_absolute_time anchored, rate-aware (D-022 practice speed),
// lock-protected so the display timer, scheduler and UI can read it
// from anywhere. The audio clock is ground truth for transport
// position — never a SwiftUI timer.
//
// Rate semantics: at rate r the clock advances r song-seconds per
// wall-clock second. setRate() snapshots the position under the old
// rate before re-anchoring, so a mid-play tempo change never jumps
// the playhead.

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
    private var _anchorHostTime: UInt64 = 0
    private var _accumulatedSongSeconds: Double = 0
    private var _rate: Double = 1.0

    /// Injectable for tests; production uses mach_absolute_time.
    private let hostTimeProvider: @Sendable () -> UInt64

    public init(hostTimeProvider: @escaping @Sendable () -> UInt64 = { mach_absolute_time() }) {
        self.hostTimeProvider = hostTimeProvider
    }

    public var state: State {
        lock.lock(); defer { lock.unlock() }
        return _state
    }

    public var rate: Double {
        lock.lock(); defer { lock.unlock() }
        return _rate
    }

    public var nowSongSeconds: Double {
        lock.lock(); defer { lock.unlock() }
        return unlockedNowSongSeconds()
    }

    private func unlockedNowSongSeconds() -> Double {
        switch _state {
        case .stopped:
            return 0
        case .paused:
            return _accumulatedSongSeconds
        case .playing:
            let elapsedTicks = Double(hostTimeProvider() &- _anchorHostTime)
            return _accumulatedSongSeconds
                + (elapsedTicks / Self.ticksPerSecond()) * _rate
        }
    }

    public func play() {
        lock.lock(); defer { lock.unlock() }
        guard _state != .playing else { return }
        _anchorHostTime = hostTimeProvider()
        _state = .playing
    }

    public func pause() {
        lock.lock(); defer { lock.unlock() }
        guard _state == .playing else { return }
        _accumulatedSongSeconds = unlockedNowSongSeconds()
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
            _anchorHostTime = hostTimeProvider()
        }
    }

    /// Change the advance rate glitch-free: the current position is
    /// captured under the OLD rate, then the anchor resets so future
    /// elapsed time scales by the new rate.
    public func setRate(_ rate: Double) {
        lock.lock(); defer { lock.unlock() }
        let position = unlockedNowSongSeconds()
        _accumulatedSongSeconds = position
        _anchorHostTime = hostTimeProvider()
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
