// TransportController.swift
//
// Single owner of transport intent: play/pause/seek/tempo/loop.
// Pure logic — the audio side (stem player + clock, JamDesktopAudio)
// plugs in via `TransportAudioSink` and `SongClock`, so every
// behavior here is testable headless with a fake clock.
//
// Semantics mirror the web jam client + backend reducer:
//   - tempo clamps to 0.5…1.0 (practice slow-down only, like
//     session/transport.py and the mobile app, D-022);
//   - loop wraps by seeking back to loop-in when the playhead crosses
//     loop-out (checked from `tick()`, driven by the UI's display
//     timer — same cadence the web app uses);
//   - hitting end-of-song pauses at the end rather than wrapping.

import Foundation
import Observation

/// A song-position loop region in song-seconds.
public struct LoopRegion: Equatable, Sendable {
    public var inSeconds: Double
    public var outSeconds: Double

    public init(inSeconds: Double, outSeconds: Double) {
        self.inSeconds = inSeconds
        self.outSeconds = outSeconds
    }

    /// A region is usable only if it spans forward.
    public var isValid: Bool { outSeconds > inSeconds }
}

/// Read side: where are we in the song right now. Implemented by the
/// audio layer's TransportClock; tests inject a fake.
public protocol SongClock: AnyObject {
    var nowSongSeconds: Double { get }
}

/// Write side: transport commands the audio layer executes.
/// Implemented by the stem-player + clock pair in JamDesktopAudio.
@MainActor
public protocol TransportAudioSink: AnyObject {
    func play(atSongSeconds seconds: Double)
    func pause()
    func seek(toSongSeconds seconds: Double)
    func setPlaybackRate(_ rate: Double)
}

@Observable
@MainActor
public final class TransportController {

    public static let tempoRange: ClosedRange<Double> = 0.5...1.0

    public private(set) var isPlaying = false

    /// Last position pushed by `tick()` — what the UI renders.
    public private(set) var positionSeconds: Double = 0

    /// Playback rate, clamped to `tempoRange`.
    public private(set) var tempoPct: Double = 1.0

    public private(set) var loop: LoopRegion?

    /// Song length; seeks clamp into 0…duration.
    public var durationSeconds: Double = 0

    /// Fired after any discrete transport change (play/pause/seek/
    /// tempo/loop) — the bridge mirrors these immediately, position
    /// ticks are throttled separately by the caller.
    public var onDiscreteChange: (() -> Void)?

    /// Fired when playback pauses (user pause, peer pause, or end of
    /// song). The session recorder inserts a gap marker (P4).
    public var onPause: (() -> Void)?

    /// Fired when the playhead jumps discontinuously: explicit seeks
    /// and loop wrap-arounds. `from` is the pre-jump position. The
    /// session recorder inserts a signed gap marker (P4).
    public var onSeek: ((_ from: Double, _ to: Double) -> Void)?

    @ObservationIgnored public weak var clock: SongClock?
    @ObservationIgnored public weak var audio: TransportAudioSink?

    public init() {}

    // MARK: - Intents

    public func play() {
        guard !isPlaying else { return }
        isPlaying = true
        audio?.play(atSongSeconds: positionSeconds)
        onDiscreteChange?()
    }

    public func pause() {
        guard isPlaying else { return }
        isPlaying = false
        positionSeconds = clock?.nowSongSeconds ?? positionSeconds
        audio?.pause()
        onPause?()
        onDiscreteChange?()
    }

    public func togglePlay() {
        isPlaying ? pause() : play()
    }

    public func seek(to seconds: Double) {
        let clamped = min(max(0, seconds), max(0, durationSeconds))
        let from = positionSeconds
        positionSeconds = clamped
        audio?.seek(toSongSeconds: clamped)
        onSeek?(from, clamped)
        onDiscreteChange?()
    }

    public func setTempo(_ pct: Double) {
        let clamped = min(max(Self.tempoRange.lowerBound, pct),
                          Self.tempoRange.upperBound)
        guard clamped != tempoPct else { return }
        tempoPct = clamped
        audio?.setPlaybackRate(clamped)
        onDiscreteChange?()
    }

    /// Set (or replace) the loop region. Invalid regions are ignored.
    public func setLoop(_ region: LoopRegion) {
        guard region.isValid else { return }
        loop = region
        onDiscreteChange?()
    }

    public func clearLoop() {
        guard loop != nil else { return }
        loop = nil
        onDiscreteChange?()
    }

    // MARK: - Clock pump

    /// Called from the UI display timer while visible. Reads the
    /// audio clock, applies loop-wrap / end-of-song, and publishes
    /// `positionSeconds`. Safe to call when paused (no-ops position).
    public func tick() {
        guard isPlaying, let clock else { return }
        var now = clock.nowSongSeconds

        if let loop, loop.isValid, now >= loop.outSeconds {
            // Wrap: land exactly on loop-in. Seeking through the
            // sink restarts stem playback at the right frame.
            audio?.seek(toSongSeconds: loop.inSeconds)
            onSeek?(now, loop.inSeconds)
            now = loop.inSeconds
        } else if durationSeconds > 0, now >= durationSeconds {
            // End of song: pause parked at the end.
            isPlaying = false
            positionSeconds = durationSeconds
            audio?.pause()
            onPause?()
            onDiscreteChange?()
            return
        }

        positionSeconds = now
    }
}
