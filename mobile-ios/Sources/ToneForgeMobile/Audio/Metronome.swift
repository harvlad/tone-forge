// Metronome.swift
//
// Song-less click track (Sketch plan Phase 2, on the D-016 surface:
// "sketch" is the Play tab with no song loaded). One AVAudioPlayerNode
// scheduling short synthesized click buffers (accent on beat 0 of each
// bar, normal elsewhere) at explicit host times, refilled by a rolling
// ~100 ms look-ahead loop.
//
// Timing contract: the TransportClock is authoritative. Click N sits at
// song-time `N * (60 / bpm)`, converted to a host time with the exact
// same math SampleScheduler uses for quantized pad hits
// (SampleScheduler.audioTime(forSongSeconds:)) — so a 1/4-quantized pad
// lands sample-adjacent to the click it snapped to.
//
// The node connects straight to the engine's main mixer, NOT the
// shared contribution bus: the click is a monitoring aid, so it must
// not ride the shared reverb (D-013) and must not follow the layer
// fader. It keeps its own volume knob.
//
// The class is deliberately dumb: `start()` / `stop()` / `update(grid:)`
// — AppState decides *when* (no bundle + toggle on + transport playing)
// and owns the persisted settings (SketchSettingsStore).
//
// Pure beat math lives in `MetronomeGrid` so tests can walk click
// sequences at multiple BPMs without an audio engine.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

// MARK: - Pure grid math

/// Beat grid for the metronome: BPM + beats-per-bar. All functions are
/// pure so click timing is unit-testable without audio. Beat indices
/// are `Int` and may be negative (count-in, Phase 3, runs the transport
/// through a negative-time window).
public struct MetronomeGrid: Equatable, Sendable {
    public var bpm: Double
    public var beatsPerBar: Int

    public init(bpm: Double, beatsPerBar: Int) {
        self.bpm = bpm
        self.beatsPerBar = beatsPerBar
    }

    /// Seconds between consecutive clicks.
    public var secondsPerBeat: Double { 60.0 / bpm }

    /// Song-time of beat `index`.
    public func songTime(ofBeat index: Int) -> Double {
        Double(index) * secondsPerBeat
    }

    /// First beat index whose song-time is `>= songTime` (within a
    /// small epsilon so a beat we're exactly on isn't skipped by
    /// floating-point noise).
    public func beatIndex(onOrAfter songTime: Double) -> Int {
        Int(((songTime / secondsPerBeat) - 1e-9).rounded(.up))
    }

    /// Whether beat `index` is a bar downbeat (accented click).
    /// Modulo is floored so negative indices accent correctly:
    /// beat -4 in 4/4 is a downbeat.
    public func isAccent(beatIndex index: Int) -> Bool {
        let m = index % beatsPerBar
        return (m + beatsPerBar) % beatsPerBar == 0
    }

    /// One scheduled click.
    public struct Click: Equatable, Sendable {
        public let beatIndex: Int
        public let songTime: Double
        public let isAccent: Bool
    }

    /// Clicks in the half-open window `[fromBeatIndex, …)` whose
    /// song-time is `< before`. The caller tracks `fromBeatIndex`
    /// across calls so consecutive windows never double-schedule.
    public func clicks(fromBeatIndex: Int, before: Double) -> [Click] {
        var result: [Click] = []
        var idx = fromBeatIndex
        while songTime(ofBeat: idx) < before {
            result.append(Click(
                beatIndex: idx,
                songTime: songTime(ofBeat: idx),
                isAccent: isAccent(beatIndex: idx)
            ))
            idx += 1
        }
        return result
    }
}

// MARK: - Audio node

/// Rolling-window click scheduler over an AVAudioPlayerNode. Main-actor
/// bound like the rest of the audio control layer (scheduling happens
/// ahead of time, so no render-thread work).
@MainActor
public final class Metronome {

    /// How far ahead of "now" clicks are kept scheduled. Long enough
    /// that a stalled main thread doesn't starve the click; short
    /// enough that tempo changes re-grid quickly (stale clicks past
    /// the change are flushed anyway).
    private static let lookAheadSec: Double = 1.2
    /// Refill cadence for the scheduling loop.
    private static let refillIntervalSec: Double = 0.1

    private let engine: AudioEngine

    /// Injectable time sources for tests. Production: the transport
    /// clock + mach_absolute_time (same pair SampleScheduler uses).
    private let nowSongSeconds: () -> Double
    private let hostTimeNow: () -> UInt64

    private var grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
    private var attached = false

    /// Next beat index to schedule; nil = needs re-anchoring against
    /// the current song time (fresh start, tempo change, transport
    /// seek/pause).
    private var nextBeatIndex: Int?
    private var refillTask: Task<Void, Never>?

    public private(set) var isRunning = false

    #if canImport(AVFoundation)
    private let player = AVAudioPlayerNode()
    private var accentBuffer: AVAudioPCMBuffer?
    private var normalBuffer: AVAudioPCMBuffer?
    #endif

    public init(
        engine: AudioEngine,
        nowSongSeconds: (() -> Double)? = nil,
        hostTimeNow: (() -> UInt64)? = nil
    ) {
        self.engine = engine
        self.nowSongSeconds = nowSongSeconds ?? { [clock = engine.clock] in
            clock.nowSongSeconds
        }
        if let hostTimeNow {
            self.hostTimeNow = hostTimeNow
        } else {
            #if canImport(AVFoundation)
            self.hostTimeNow = { mach_absolute_time() }
            #else
            self.hostTimeNow = { UInt64(Date().timeIntervalSince1970 * 1_000_000_000) }
            #endif
        }
    }

    /// Click level, 0…1. Default sits under the pads so the click
    /// guides without dominating.
    public var volume: Float {
        get {
            #if canImport(AVFoundation)
            return player.volume
            #else
            return 0
            #endif
        }
        set {
            #if canImport(AVFoundation)
            player.volume = max(0, min(1, newValue))
            #endif
        }
    }

    /// Attach the player to the engine graph (idempotent). Call from
    /// bootAudio before `engine.start()` alongside the other attaches.
    public func attach() {
        guard !attached else { return }
        #if canImport(AVFoundation)
        engine.engine.attach(player)
        engine.engine.connect(
            player,
            to: engine.engine.mainMixerNode,
            format: engine.canonicalFormat
        )
        let sampleRate = engine.canonicalFormat.sampleRate
        accentBuffer = Self.makeClickBuffer(
            format: engine.canonicalFormat,
            frequency: 1567.98,   // G6 — bright, cuts through pads
            amplitude: 0.85,
            durationSec: 0.05,
            sampleRate: sampleRate
        )
        normalBuffer = Self.makeClickBuffer(
            format: engine.canonicalFormat,
            frequency: 1046.50,   // C6
            amplitude: 0.65,
            durationSec: 0.04,
            sampleRate: sampleRate
        )
        player.volume = 0.6
        #endif
        attached = true
    }

    /// Replace the beat grid. While running, flushes queued clicks and
    /// re-anchors on the new grid at the current song time — the next
    /// click lands on the new tempo's next beat boundary.
    public func update(grid: MetronomeGrid) {
        guard grid != self.grid else { return }
        self.grid = grid
        if isRunning {
            flushQueued()
            nextBeatIndex = nil
            refill()
        }
    }

    /// Begin clicking, aligned to the transport's song-time beat grid.
    /// No-op when already running or never attached.
    public func start() {
        guard attached, !isRunning else { return }
        isRunning = true
        nextBeatIndex = nil
        refill()
        refillTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(
                    nanoseconds: UInt64(Self.refillIntervalSec * 1_000_000_000)
                )
                guard let self, self.isRunning else { return }
                self.refill()
            }
        }
    }

    /// Stop clicking and flush anything queued.
    public func stop() {
        guard isRunning else { return }
        isRunning = false
        refillTask?.cancel()
        refillTask = nil
        flushQueued()
        nextBeatIndex = nil
    }

    // MARK: - Scheduling

    private func flushQueued() {
        #if canImport(AVFoundation)
        player.stop()
        #endif
    }

    /// Schedule every click inside the look-ahead window that isn't
    /// scheduled yet. Re-anchors when the transport was seeked/paused
    /// out from under us (song time regressed or jumped past the
    /// window).
    private func refill() {
        #if canImport(AVFoundation)
        guard isRunning, let accent = accentBuffer, let normal = normalBuffer else { return }
        // The player dies with the engine on interruption; revive it
        // (cheap no-op otherwise).
        guard engine.engine.isRunning else { return }
        if !player.isPlaying { player.play() }

        let nowSong = nowSongSeconds()
        let windowEnd = nowSong + Self.lookAheadSec

        // Re-anchor when fresh, or when the tracked position drifted
        // outside [now, window] (seek backwards/forwards, long stall).
        var next = nextBeatIndex ?? grid.beatIndex(onOrAfter: nowSong)
        let nextTime = grid.songTime(ofBeat: next)
        if nextTime < nowSong - 0.05 || nextTime > windowEnd + grid.secondsPerBeat {
            player.stop()
            player.play()
            next = grid.beatIndex(onOrAfter: nowSong)
        }

        let hostNow = hostTimeNow()
        let ticksPerSecond = TransportClock.ticksPerSecond()
        for click in grid.clicks(fromBeatIndex: next, before: windowEnd) {
            let delaySec = click.songTime - nowSong
            // Same boundary math as SampleScheduler.audioTime: clicks
            // effectively "now" play immediately (at: nil).
            var at: AVAudioTime? = nil
            if delaySec > 0.001 {
                let hostTime = hostNow &+ UInt64(delaySec * ticksPerSecond)
                at = AVAudioTime(hostTime: hostTime)
            } else if delaySec < -0.05 {
                next = click.beatIndex + 1
                continue // stale — don't burst-fire missed clicks
            }
            player.scheduleBuffer(
                click.isAccent ? accent : normal,
                at: at,
                options: []
            )
            next = click.beatIndex + 1
        }
        nextBeatIndex = next
        #endif
    }

    // MARK: - Click synthesis

    #if canImport(AVFoundation)
    /// Short sine burst with a squared-decay envelope + 2 ms attack
    /// ramp (kills the onset discontinuity click-on-the-click).
    private static func makeClickBuffer(
        format: AVAudioFormat,
        frequency: Double,
        amplitude: Float,
        durationSec: Double,
        sampleRate: Double
    ) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(durationSec * sampleRate)
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let channels = buffer.floatChannelData
        else { return nil }
        buffer.frameLength = frames

        let attackFrames = max(1, Int(0.002 * sampleRate))
        let total = Int(frames)
        for frame in 0..<total {
            let t = Double(frame) / sampleRate
            let sine = sin(2.0 * .pi * frequency * t)
            let decay = 1.0 - Double(frame) / Double(total)
            let attack = min(1.0, Double(frame) / Double(attackFrames))
            let sample = Float(sine * decay * decay * attack) * amplitude
            for ch in 0..<Int(format.channelCount) {
                channels[ch][frame] = sample
            }
        }
        return buffer
    }
    #endif
}
