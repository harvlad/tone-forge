// PerfFXSettings.swift
//
// Performance ("DJ") FX model (PERFORM_PARITY spec 1) — the momentary,
// beat-synced gesture effects distinct from the studio-bus FXSettings
// (EQ/comp/reverb/delay). These process the whole mix as a master
// insert BEFORE the studio chain, and are driven live by touch +
// BeatClock rather than by static knobs.
//
// Split in two, mirroring FXSettings' static/neutral idiom:
//   * PerfFXConfig  — the tunable shape of each effect (persisted).
//   * PerfFXState   — the live momentary engagement (NOT persisted):
//                     which pads are held right now + the filter XY.
//
// v1 ships Filter, Gater, Stopper, Flanger, Delay-throw. Stutter /
// beat-repeat is deferred (needs ring-buffer capture) — see the doc.
//
// The pure mapping helpers here (cutoff curve, gater duty, flanger LFO,
// stopper rate ramp) are unit-tested so the AVFoundation chain in the
// app target stays a thin translation layer.

import Foundation

// MARK: - Filter

public enum PerfFilterType: String, Codable, Sendable, CaseIterable {
    case lowPass, highPass
}

/// Resonant sweep filter. Cutoff is driven live by the filter pad's X
/// axis; resonance by its Y axis. `config` only pins the type + the
/// cutoff range the sweep spans.
public struct PerfFilterConfig: Codable, Sendable, Equatable {
    public var type: PerfFilterType
    /// Lowest cutoff the X sweep reaches, Hz.
    public var minHz: Double
    /// Highest cutoff the X sweep reaches, Hz.
    public var maxHz: Double
    /// Max resonance (dB of band gain / Q emphasis) at Y = 1.
    public var maxResonanceDb: Double

    public init(
        type: PerfFilterType = .lowPass,
        minHz: Double = 200,
        maxHz: Double = 18_000,
        maxResonanceDb: Double = 18
    ) {
        self.type = type
        self.minHz = minHz
        self.maxHz = maxHz
        self.maxResonanceDb = maxResonanceDb
    }

    public func clamped() -> PerfFilterConfig {
        PerfFilterConfig(
            type: type,
            minHz: max(20, min(minHz, maxHz - 1)),
            maxHz: min(20_000, max(maxHz, minHz + 1)),
            maxResonanceDb: max(0, min(24, maxResonanceDb))
        )
    }

    /// Map the X pad position (0..1) onto a cutoff frequency,
    /// logarithmically so the sweep feels even to the ear.
    public func cutoffHz(x: Double) -> Double {
        let c = clamped()
        let t = max(0, min(1, x))
        return c.minHz * pow(c.maxHz / c.minHz, t)
    }

    /// Map the Y pad position (0..1) onto a resonance in dB.
    public func resonanceDb(y: Double) -> Double {
        clamped().maxResonanceDb * max(0, min(1, y))
    }
}

// MARK: - Gater

/// Beat-synced amplitude gate. Chops the mix into pulses at
/// `subdivisionBeats` (1.0 = quarter, 0.5 = eighth, 0.25 = sixteenth in
/// 4/4). `duty` is the fraction of each pulse that stays open; `depth`
/// how far the closed part drops (1 = full silence).
public struct PerfGaterConfig: Codable, Sendable, Equatable {
    public var subdivisionBeats: Double
    public var duty: Double
    public var depth: Double

    public init(subdivisionBeats: Double = 0.5, duty: Double = 0.5, depth: Double = 1.0) {
        self.subdivisionBeats = subdivisionBeats
        self.duty = duty
        self.depth = depth
    }

    public func clamped() -> PerfGaterConfig {
        PerfGaterConfig(
            subdivisionBeats: max(0.0625, min(4, subdivisionBeats)),
            duty: max(0.05, min(0.95, duty)),
            depth: max(0, min(1, depth))
        )
    }

    /// Linear gain (0..1) for a phase (0..1) within one gate cell.
    /// Open (1.0) for the first `duty` of the cell, else `1 - depth`.
    public func gain(cellPhase: Double) -> Double {
        let c = clamped()
        let p = cellPhase - floor(cellPhase)   // wrap to 0..1
        return p < c.duty ? 1.0 : (1.0 - c.depth)
    }
}

// MARK: - Flanger

/// Short modulated-delay flanger. `rateBeats` is the LFO period in
/// beats (tempo-synced); `depthMs` the delay sweep amplitude; the base
/// delay is fixed small so the sweep stays in flange territory.
public struct PerfFlangerConfig: Codable, Sendable, Equatable {
    public var rateBeats: Double
    public var depthMs: Double
    public var feedback: Double
    public var baseMs: Double

    public init(rateBeats: Double = 2, depthMs: Double = 4, feedback: Double = 0.6, baseMs: Double = 3) {
        self.rateBeats = rateBeats
        self.depthMs = depthMs
        self.feedback = feedback
        self.baseMs = baseMs
    }

    public func clamped() -> PerfFlangerConfig {
        PerfFlangerConfig(
            rateBeats: max(0.25, min(16, rateBeats)),
            depthMs: max(0.5, min(9, depthMs)),
            feedback: max(0, min(0.95, feedback)),
            baseMs: max(1, min(10, baseMs))
        )
    }

    /// Delay time in milliseconds for an LFO phase (0..1).
    public func delayMs(lfoPhase: Double) -> Double {
        let c = clamped()
        let s = sin(2 * Double.pi * lfoPhase)      // -1..1
        return c.baseMs + c.depthMs * (s * 0.5 + 0.5)  // base .. base+depth
    }
}

// MARK: - Delay-throw

/// Tempo-synced feedback delay ramped up while the throw pad is held.
public struct PerfDelayThrowConfig: Codable, Sendable, Equatable {
    public var timeBeats: Double
    public var feedback: Double

    public init(timeBeats: Double = 0.75, feedback: Double = 0.6) {
        self.timeBeats = timeBeats
        self.feedback = feedback
    }

    public func clamped() -> PerfDelayThrowConfig {
        PerfDelayThrowConfig(
            timeBeats: max(0.0625, min(4, timeBeats)),
            feedback: max(0, min(0.95, feedback))
        )
    }

    /// Delay time in seconds at the given beat duration (seconds).
    public func timeSec(beatDuration: Double) -> Double {
        clamped().timeBeats * max(0, beatDuration)
    }
}

// MARK: - Stopper

/// Tape-stop brake: over `brakeBeats` the playback rate ramps to 0 with
/// a matching gain fade while the pad is held; release snaps back.
public struct PerfStopperConfig: Codable, Sendable, Equatable {
    public var brakeBeats: Double

    public init(brakeBeats: Double = 1.0) { self.brakeBeats = brakeBeats }

    public func clamped() -> PerfStopperConfig {
        PerfStopperConfig(brakeBeats: max(0.125, min(8, brakeBeats)))
    }

    /// Playback-rate multiplier for `elapsed` seconds into the brake,
    /// given the beat duration. Eases 1 → 0 with a quadratic so the
    /// pitch drop feels like a real motor spinning down.
    public func rate(elapsedSec: Double, beatDuration: Double) -> Double {
        let total = clamped().brakeBeats * max(1e-6, beatDuration)
        let t = max(0, min(1, elapsedSec / total))
        let e = 1 - t
        return e * e            // quadratic ease-out to zero
    }
}

// MARK: - Config aggregate

public struct PerfFXConfig: Codable, Sendable, Equatable {
    public var schemaVersion: Int
    public var filter: PerfFilterConfig
    public var gater: PerfGaterConfig
    public var flanger: PerfFlangerConfig
    public var delayThrow: PerfDelayThrowConfig
    public var stopper: PerfStopperConfig

    public init(
        schemaVersion: Int = 1,
        filter: PerfFilterConfig = .init(),
        gater: PerfGaterConfig = .init(),
        flanger: PerfFlangerConfig = .init(),
        delayThrow: PerfDelayThrowConfig = .init(),
        stopper: PerfStopperConfig = .init()
    ) {
        self.schemaVersion = schemaVersion
        self.filter = filter
        self.gater = gater
        self.flanger = flanger
        self.delayThrow = delayThrow
        self.stopper = stopper
    }

    public static let `default` = PerfFXConfig()
}

// MARK: - Live state

/// Which performance FX are engaged right now (momentary — held pads)
/// plus the filter pad's XY. NOT persisted: a fresh session starts with
/// every effect released.
public struct PerfFXState: Sendable, Equatable {
    public var filter: Bool
    public var gater: Bool
    public var flanger: Bool
    public var delayThrow: Bool
    public var stopper: Bool
    /// Filter pad position, 0..1 each axis. X = cutoff, Y = resonance.
    public var filterX: Double
    public var filterY: Double

    public init(
        filter: Bool = false,
        gater: Bool = false,
        flanger: Bool = false,
        delayThrow: Bool = false,
        stopper: Bool = false,
        filterX: Double = 0.5,
        filterY: Double = 0.0
    ) {
        self.filter = filter
        self.gater = gater
        self.flanger = flanger
        self.delayThrow = delayThrow
        self.stopper = stopper
        self.filterX = filterX
        self.filterY = filterY
    }

    public static let idle = PerfFXState()

    /// True when nothing is engaged — the chain can stay in bypass and
    /// the modulation driver can idle.
    public var isIdle: Bool {
        !filter && !gater && !flanger && !delayThrow && !stopper
    }
}
