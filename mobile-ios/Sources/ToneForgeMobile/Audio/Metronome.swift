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

/// Which beats of the bar get the accented click (redesign Phase 6).
public enum MetronomeAccent: String, CaseIterable, Sendable, Codable {
    /// Beat 1 of each bar (the historic default).
    case downbeat
    /// Beats 1 and 3 (even beats of the bar, 0-based).
    case oneAndThree
    case everyBeat
    case none
}

/// Click timbre family (redesign Phase 6). `.sine` reproduces the
/// original synthesized click byte-for-byte.
public enum MetronomeSound: String, CaseIterable, Sendable, Codable {
    case sine
    case woodBlock
    case click
    case rim
}

/// Beat grid for the metronome: BPM + beats-per-bar (+ accent pattern
/// and optional half-beat subdivision). All functions are pure so
/// click timing is unit-testable without audio. Beat indices are
/// `Int` and may be negative (count-in, Phase 3, runs the transport
/// through a negative-time window).
///
/// Two enumerations coexist: beat indices (one per beat, the original
/// API) and click indices (`clicksPerBeat` per beat — 2 when
/// subdividing). The scheduler walks click indices so a look-ahead
/// window that ends between a beat and its half-beat never drops the
/// half-beat on the next refill.
public struct MetronomeGrid: Equatable, Sendable {
    public var bpm: Double
    public var beatsPerBar: Int
    public var accent: MetronomeAccent
    public var subdivide: Bool

    public init(
        bpm: Double,
        beatsPerBar: Int,
        accent: MetronomeAccent = .downbeat,
        subdivide: Bool = false
    ) {
        self.bpm = bpm
        self.beatsPerBar = beatsPerBar
        self.accent = accent
        self.subdivide = subdivide
    }

    /// Seconds between consecutive beats.
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

    /// Whether beat `index` is an accented click under the grid's
    /// accent pattern. Modulo is floored so negative indices accent
    /// correctly: beat -4 in 4/4 is a downbeat.
    public func isAccent(beatIndex index: Int) -> Bool {
        let m = ((index % beatsPerBar) + beatsPerBar) % beatsPerBar
        switch accent {
        case .downbeat: return m == 0
        case .oneAndThree: return m % 2 == 0
        case .everyBeat: return true
        case .none: return false
        }
    }

    /// One scheduled click.
    public struct Click: Equatable, Sendable {
        public let beatIndex: Int
        public let songTime: Double
        public let isAccent: Bool
        /// True for the half-beat click between beats (`subdivide`).
        public let isSubdivision: Bool
    }

    /// Clicks in the half-open window `[fromBeatIndex, …)` whose
    /// song-time is `< before`. Whole beats only — subdivision, when
    /// enabled, is a click-index concern (`clicks(fromClickIndex:)`).
    /// The caller tracks `fromBeatIndex` across calls so consecutive
    /// windows never double-schedule.
    public func clicks(fromBeatIndex: Int, before: Double) -> [Click] {
        var result: [Click] = []
        var idx = fromBeatIndex
        while songTime(ofBeat: idx) < before {
            result.append(Click(
                beatIndex: idx,
                songTime: songTime(ofBeat: idx),
                isAccent: isAccent(beatIndex: idx),
                isSubdivision: false
            ))
            idx += 1
        }
        return result
    }

    // MARK: Click-index enumeration (subdivision-aware)

    /// Clicks per beat: 2 when subdividing (beat + half-beat), else 1
    /// (click index == beat index).
    public var clicksPerBeat: Int { subdivide ? 2 : 1 }

    /// Seconds between consecutive clicks.
    public var secondsPerClick: Double {
        secondsPerBeat / Double(clicksPerBeat)
    }

    /// Song-time of click `index`.
    public func songTime(ofClick index: Int) -> Double {
        Double(index) * secondsPerClick
    }

    /// First click index whose song-time is `>= songTime` (same
    /// epsilon contract as ``beatIndex(onOrAfter:)``).
    public func clickIndex(onOrAfter songTime: Double) -> Int {
        Int(((songTime / secondsPerClick) - 1e-9).rounded(.up))
    }

    /// Clicks in the half-open window `[fromClickIndex, …)` whose
    /// song-time is `< before`. Subdivision clicks are flagged and
    /// never accented. Floored division so negative click indices
    /// (count-in) map to the right beat.
    public func clicks(fromClickIndex: Int, before: Double) -> [Click] {
        var result: [Click] = []
        let cpb = clicksPerBeat
        var idx = fromClickIndex
        while songTime(ofClick: idx) < before {
            let m = ((idx % cpb) + cpb) % cpb
            let beat = (idx - m) / cpb
            result.append(Click(
                beatIndex: beat,
                songTime: songTime(ofClick: idx),
                isAccent: m == 0 && isAccent(beatIndex: beat),
                isSubdivision: m != 0
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
    /// Transport rate source (D-022 practice speed) — at slowed
    /// rates a click's song-time delta spans proportionally more
    /// wall-clock, so its host time sits further out.
    private let rateProvider: () -> Double

    private var grid = MetronomeGrid(bpm: 120, beatsPerBar: 4)
    private var attached = false

    /// Next click index to schedule; nil = needs re-anchoring against
    /// the current song time (fresh start, tempo change, transport
    /// seek/pause). Click indices equal beat indices unless the grid
    /// subdivides.
    private var nextClickIndex: Int?
    private var refillTask: Task<Void, Never>?

    public private(set) var isRunning = false

    /// Click timbre. Rebuilds the synthesized buffers; while running,
    /// flushes queued clicks so the new sound lands on the next beat.
    public var sound: MetronomeSound = .sine {
        didSet {
            guard sound != oldValue else { return }
            rebuildBuffers()
            if isRunning {
                flushQueued()
                nextClickIndex = nil
                refill()
            }
        }
    }

    #if canImport(AVFoundation)
    private let player = AVAudioPlayerNode()
    private var accentBuffer: AVAudioPCMBuffer?
    private var normalBuffer: AVAudioPCMBuffer?
    private var subdivisionBuffer: AVAudioPCMBuffer?
    #endif

    public init(
        engine: AudioEngine,
        nowSongSeconds: (() -> Double)? = nil,
        hostTimeNow: (() -> UInt64)? = nil,
        rateProvider: (() -> Double)? = nil
    ) {
        self.engine = engine
        self.nowSongSeconds = nowSongSeconds ?? { [clock = engine.clock] in
            clock.nowSongSeconds
        }
        self.rateProvider = rateProvider ?? { [clock = engine.clock] in
            clock.rate
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
        rebuildBuffers()
        player.volume = 0.6
        #endif
        attached = true
    }

    /// Synthesize the accent / normal / subdivision buffers for the
    /// current ``sound``. `.sine` calls makeClickBuffer with the
    /// historic G6/C6 parameters, so the default sound is unchanged.
    private func rebuildBuffers() {
        #if canImport(AVFoundation)
        let format = engine.canonicalFormat
        let sr = format.sampleRate
        switch sound {
        case .sine:
            accentBuffer = Self.makeClickBuffer(
                format: format,
                frequency: 1567.98,   // G6 — bright, cuts through pads
                amplitude: 0.85,
                durationSec: 0.05,
                sampleRate: sr
            )
            normalBuffer = Self.makeClickBuffer(
                format: format,
                frequency: 1046.50,   // C6
                amplitude: 0.65,
                durationSec: 0.04,
                sampleRate: sr
            )
            subdivisionBuffer = Self.makeClickBuffer(
                format: format,
                frequency: 1046.50,
                amplitude: 0.3,
                durationSec: 0.025,
                sampleRate: sr
            )
        case .woodBlock:
            accentBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 1050, amplitude: 0.9,
                durationSec: 0.035, sampleRate: sr,
                partialMix: 0.35, noiseMix: 0.05, decayPower: 3
            )
            normalBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 780, amplitude: 0.7,
                durationSec: 0.03, sampleRate: sr,
                partialMix: 0.35, noiseMix: 0.05, decayPower: 3
            )
            subdivisionBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 780, amplitude: 0.32,
                durationSec: 0.02, sampleRate: sr,
                partialMix: 0.35, noiseMix: 0.05, decayPower: 3
            )
        case .click:
            accentBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 2500, amplitude: 0.9,
                durationSec: 0.008, sampleRate: sr,
                partialMix: 0, noiseMix: 1.0, decayPower: 4
            )
            normalBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 2000, amplitude: 0.7,
                durationSec: 0.006, sampleRate: sr,
                partialMix: 0, noiseMix: 1.0, decayPower: 4
            )
            subdivisionBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 2000, amplitude: 0.35,
                durationSec: 0.005, sampleRate: sr,
                partialMix: 0, noiseMix: 1.0, decayPower: 4
            )
        case .rim:
            accentBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 1800, amplitude: 0.85,
                durationSec: 0.025, sampleRate: sr,
                partialMix: 0.6, noiseMix: 0.25, decayPower: 4
            )
            normalBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 1400, amplitude: 0.65,
                durationSec: 0.02, sampleRate: sr,
                partialMix: 0.6, noiseMix: 0.25, decayPower: 4
            )
            subdivisionBuffer = Self.makePercussiveBuffer(
                format: format, frequency: 1400, amplitude: 0.32,
                durationSec: 0.012, sampleRate: sr,
                partialMix: 0.6, noiseMix: 0.25, decayPower: 4
            )
        }
        #endif
    }

    /// Replace the beat grid. While running, flushes queued clicks and
    /// re-anchors on the new grid at the current song time — the next
    /// click lands on the new tempo's next beat boundary.
    public func update(grid: MetronomeGrid) {
        guard grid != self.grid else { return }
        self.grid = grid
        if isRunning {
            flushQueued()
            nextClickIndex = nil
            refill()
        }
    }

    /// Begin clicking, aligned to the transport's song-time beat grid.
    /// No-op when already running or never attached.
    public func start() {
        guard attached, !isRunning else { return }
        isRunning = true
        nextClickIndex = nil
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
        nextClickIndex = nil
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
    /// window). Walks click indices (half-beats when subdividing) so
    /// a window ending between a beat and its half-beat never drops
    /// the half-beat.
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
        var next = nextClickIndex ?? grid.clickIndex(onOrAfter: nowSong)
        let nextTime = grid.songTime(ofClick: next)
        if nextTime < nowSong - 0.05 || nextTime > windowEnd + grid.secondsPerClick {
            player.stop()
            player.play()
            next = grid.clickIndex(onOrAfter: nowSong)
        }

        let hostNow = hostTimeNow()
        let ticksPerSecond = TransportClock.ticksPerSecond()
        let rate = rateProvider()
        for click in grid.clicks(fromClickIndex: next, before: windowEnd) {
            let delaySec = click.songTime - nowSong
            // Same boundary math as SampleScheduler.audioTime: clicks
            // effectively "now" play immediately (at: nil). The delay
            // is scaled by the transport rate (D-022 practice speed);
            // staleness keys off the unscaled song-time delta.
            var at: AVAudioTime? = nil
            if let ticks = TransportTimeMath.hostDelayTicks(
                targetSong: click.songTime, nowSong: nowSong,
                rate: rate, ticksPerSecond: ticksPerSecond
            ) {
                at = AVAudioTime(hostTime: hostNow &+ ticks)
            } else if delaySec < -0.05 {
                next += 1
                continue // stale — don't burst-fire missed clicks
            }
            let buffer: AVAudioPCMBuffer = click.isSubdivision
                ? (subdivisionBuffer ?? normal)
                : (click.isAccent ? accent : normal)
            player.scheduleBuffer(buffer, at: at, options: [])
            next += 1
        }
        nextClickIndex = next
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

    /// Percussive burst for the non-sine sounds: fundamental sine +
    /// optional inharmonic 2.76× partial (wood/rim body) + optional
    /// deterministic noise (LCG, so buffers are reproducible), shaped
    /// by a `decay^decayPower` envelope with a 1 ms attack ramp.
    private static func makePercussiveBuffer(
        format: AVAudioFormat,
        frequency: Double,
        amplitude: Float,
        durationSec: Double,
        sampleRate: Double,
        partialMix: Double,
        noiseMix: Double,
        decayPower: Double
    ) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(durationSec * sampleRate)
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let channels = buffer.floatChannelData
        else { return nil }
        buffer.frameLength = frames

        let attackFrames = max(1, Int(0.001 * sampleRate))
        let total = Int(frames)
        let norm = 1.0 / (1.0 + partialMix)
        var noiseState: UInt32 = 0x1F2E3D4C
        for frame in 0..<total {
            let t = Double(frame) / sampleRate
            var tone = sin(2.0 * .pi * frequency * t)
            if partialMix > 0 {
                tone += partialMix * sin(2.0 * .pi * frequency * 2.76 * t)
            }
            tone *= norm
            if noiseMix > 0 {
                noiseState = noiseState &* 1664525 &+ 1013904223
                let noise = Double(noiseState >> 8) / Double(1 << 24) * 2.0 - 1.0
                tone = tone * (1.0 - noiseMix) + noise * noiseMix
            }
            let decay = pow(1.0 - Double(frame) / Double(total), decayPower)
            let attack = min(1.0, Double(frame) / Double(attackFrames))
            let sample = Float(tone * decay * attack) * amplitude
            for ch in 0..<Int(format.channelCount) {
                channels[ch][frame] = sample
            }
        }
        return buffer
    }
    #endif
}
