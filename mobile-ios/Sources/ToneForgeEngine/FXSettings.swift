// FXSettings.swift
//
// Master FX parameter sets for the D-022 FX panel: 3-band EQ, dynamics
// compressor, reverb send, and delay send. Applied to the master FX
// chain in AudioEngine.buildMasterFXGraph(), which inserts EQ + comp
// inline (mainMixer → masterEQ → masterComp → output) and runs reverb
// + delay on a parallel send/return.
//
// Value ranges match AVAudioUnitEQ, kAudioUnitSubType_DynamicsProcessor,
// AVAudioUnitReverb, and AVAudioUnitDelay conventions so audio-graph
// mapping stays 1:1 with no scaling maths in AudioEngine:
//   * EQ band gains: -24..+24 dB (AVAudioUnitEQ bandwidth).
//   * Comp threshold: -60..0 dB; headroom (mapped to amount): 0..40 dB;
//     attack 0.1..200 ms; release 10..3000 ms; makeup 0..40 dB.
//   * Reverb mix: 0..100%; size mapped to AVAudioUnitReverbPreset;
//     damp 0..100%.
//   * Delay time: 0..2 s; feedback 0..95%; mix 0..100%.
//   * FX return: -40..+6 dB (linear gain on fxReturnMixer).
//
// LOCK-IN: this schema participates in FXSettingsStore persistence.
// Adding fields is OK (decodeIfPresent); changing semantics bumps
// schemaVersion.

import Foundation

// MARK: - EQ

/// 3-band parametric EQ: low shelf, mid peak, high shelf.
public struct FXEQParams: Codable, Sendable, Equatable, Hashable {
    /// Low shelf center Hz (typically 100–400).
    public var lowFreq: Double
    /// Low shelf gain dB, -24..+24.
    public var lowGainDb: Double
    /// Mid peak center Hz (typically 800–4000).
    public var midFreq: Double
    /// Mid peak gain dB, -24..+24.
    public var midGainDb: Double
    /// High shelf center Hz (typically 4000–12000).
    public var highFreq: Double
    /// High shelf gain dB, -24..+24.
    public var highGainDb: Double

    public init(
        lowFreq: Double = 200,
        lowGainDb: Double = 0,
        midFreq: Double = 1000,
        midGainDb: Double = 0,
        highFreq: Double = 6000,
        highGainDb: Double = 0
    ) {
        self.lowFreq = lowFreq
        self.lowGainDb = lowGainDb
        self.midFreq = midFreq
        self.midGainDb = midGainDb
        self.highFreq = highFreq
        self.highGainDb = highGainDb
    }

    /// Flat EQ — no boost or cut.
    public static let neutral = FXEQParams()

    /// Clamp all fields to their documented ranges.
    public func clamped() -> FXEQParams {
        FXEQParams(
            lowFreq: max(20, min(2000, lowFreq)),
            lowGainDb: max(-24, min(24, lowGainDb)),
            midFreq: max(200, min(8000, midFreq)),
            midGainDb: max(-24, min(24, midGainDb)),
            highFreq: max(1000, min(20000, highFreq)),
            highGainDb: max(-24, min(24, highGainDb))
        )
    }

    public var isNeutral: Bool {
        let eps = 0.01
        return abs(lowGainDb) < eps && abs(midGainDb) < eps && abs(highGainDb) < eps
    }
}

// MARK: - Compressor

/// Dynamics compressor mapped to kAudioUnitSubType_DynamicsProcessor.
/// "amount" is mapped to HeadRoom (0 = no compression, 40 = max).
public struct FXCompParams: Codable, Sendable, Equatable, Hashable {
    /// Threshold in dB, -60..0. Signals above this level are compressed.
    public var thresholdDb: Double
    /// Amount as headroom dB, 0..40. Higher = more compression.
    public var amountDb: Double
    /// Attack time in ms, 0.1..200.
    public var attackMs: Double
    /// Release time in ms, 10..3000.
    public var releaseMs: Double
    /// Makeup gain in dB, 0..40.
    public var makeupDb: Double

    public init(
        thresholdDb: Double = -20,
        amountDb: Double = 0,
        attackMs: Double = 10,
        releaseMs: Double = 100,
        makeupDb: Double = 0
    ) {
        self.thresholdDb = thresholdDb
        self.amountDb = amountDb
        self.attackMs = attackMs
        self.releaseMs = releaseMs
        self.makeupDb = makeupDb
    }

    /// Bypass state — amountDb 0 means infinite headroom (no compression).
    public static let neutral = FXCompParams(
        thresholdDb: -20,
        amountDb: 0,
        attackMs: 10,
        releaseMs: 100,
        makeupDb: 0
    )

    public func clamped() -> FXCompParams {
        FXCompParams(
            thresholdDb: max(-60, min(0, thresholdDb)),
            amountDb: max(0, min(40, amountDb)),
            attackMs: max(0.1, min(200, attackMs)),
            releaseMs: max(10, min(3000, releaseMs)),
            makeupDb: max(0, min(40, makeupDb))
        )
    }

    /// True if compression is effectively bypassed (zero headroom).
    public var isNeutral: Bool { amountDb < 0.01 }
}

// MARK: - Reverb

/// Master reverb send params — distinct from the D-013 contribution
/// reverb (sharedReverb lives on the contribution path only).
public struct FXReverbParams: Codable, Sendable, Equatable, Hashable {
    /// Dry/wet mix percent, 0..100. 0 = fully dry.
    public var mix: Double
    /// Requested tail length in seconds — mapped onto AVAudioUnitReverb
    /// factory preset (same as AudioEngine.presetForSeconds).
    public var sizeSeconds: Double
    /// Damping percent, 0..100. Higher = more high-frequency absorption.
    public var dampPercent: Double

    public init(
        mix: Double = 0,
        sizeSeconds: Double = 2.0,
        dampPercent: Double = 50
    ) {
        self.mix = mix
        self.sizeSeconds = sizeSeconds
        self.dampPercent = dampPercent
    }

    /// No reverb — mix at 0.
    public static let neutral = FXReverbParams(mix: 0, sizeSeconds: 2.0, dampPercent: 50)

    public func clamped() -> FXReverbParams {
        FXReverbParams(
            mix: max(0, min(100, mix)),
            sizeSeconds: max(0.3, min(6, sizeSeconds)),
            dampPercent: max(0, min(100, dampPercent))
        )
    }

    /// True if reverb is effectively off (mix near zero).
    public var isNeutral: Bool { mix < 0.01 }
}

// MARK: - Delay

/// Master delay send params.
public struct FXDelayParams: Codable, Sendable, Equatable, Hashable {
    /// Delay time in seconds, 0..2.
    public var timeSec: Double
    /// Feedback percent, 0..95 (below 100 to avoid runaway).
    public var feedback: Double
    /// Dry/wet mix percent, 0..100.
    public var mix: Double

    public init(
        timeSec: Double = 0.25,
        feedback: Double = 30,
        mix: Double = 0
    ) {
        self.timeSec = timeSec
        self.feedback = feedback
        self.mix = mix
    }

    /// No delay — mix at 0.
    public static let neutral = FXDelayParams(timeSec: 0.25, feedback: 30, mix: 0)

    public func clamped() -> FXDelayParams {
        FXDelayParams(
            timeSec: max(0, min(2, timeSec)),
            feedback: max(0, min(95, feedback)),
            mix: max(0, min(100, mix))
        )
    }

    /// True if delay is effectively off (mix near zero).
    public var isNeutral: Bool { mix < 0.01 }
}

// MARK: - FXSettings

/// Aggregate master FX state. Stored in FXSettingsStore and pushed to
/// AudioEngine via setFXSettings().
public struct FXSettings: Codable, Sendable, Equatable, Hashable {
    /// Schema version for future migrations.
    public var schemaVersion: Int
    /// 3-band EQ params.
    public var eq: FXEQParams
    /// Dynamics compressor params.
    public var comp: FXCompParams
    /// Reverb send params.
    public var reverb: FXReverbParams
    /// Delay send params.
    public var delay: FXDelayParams
    /// FX return level in dB, -40..+6.
    public var fxReturnDb: Double
    /// Preset identifier, nil when user has edited any knob.
    public var presetId: String?

    public init(
        schemaVersion: Int = 1,
        eq: FXEQParams = .neutral,
        comp: FXCompParams = .neutral,
        reverb: FXReverbParams = .neutral,
        delay: FXDelayParams = .neutral,
        fxReturnDb: Double = 0,
        presetId: String? = "clean"
    ) {
        self.schemaVersion = schemaVersion
        self.eq = eq
        self.comp = comp
        self.reverb = reverb
        self.delay = delay
        self.fxReturnDb = fxReturnDb
        self.presetId = presetId
    }

    /// Bit-transparent — all effects bypassed/neutral.
    public static let neutral = FXSettings(
        schemaVersion: 1,
        eq: .neutral,
        comp: .neutral,
        reverb: .neutral,
        delay: .neutral,
        fxReturnDb: 0,
        presetId: "clean"
    )

    public func clamped() -> FXSettings {
        FXSettings(
            schemaVersion: schemaVersion,
            eq: eq.clamped(),
            comp: comp.clamped(),
            reverb: reverb.clamped(),
            delay: delay.clamped(),
            fxReturnDb: max(-40, min(6, fxReturnDb)),
            presetId: presetId
        )
    }

    /// True if all sub-effects are neutral (bit-transparent path).
    public var isNeutral: Bool {
        eq.isNeutral && comp.isNeutral && reverb.isNeutral && delay.isNeutral
    }

    // Custom Codable so absent keys degrade to neutral.
    private enum CodingKeys: String, CodingKey {
        case schemaVersion, eq, comp, reverb, delay, fxReturnDb, presetId
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let n = FXSettings.neutral
        self.schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? n.schemaVersion
        self.eq = try c.decodeIfPresent(FXEQParams.self, forKey: .eq) ?? n.eq
        self.comp = try c.decodeIfPresent(FXCompParams.self, forKey: .comp) ?? n.comp
        self.reverb = try c.decodeIfPresent(FXReverbParams.self, forKey: .reverb) ?? n.reverb
        self.delay = try c.decodeIfPresent(FXDelayParams.self, forKey: .delay) ?? n.delay
        self.fxReturnDb = try c.decodeIfPresent(Double.self, forKey: .fxReturnDb) ?? n.fxReturnDb
        self.presetId = try c.decodeIfPresent(String.self, forKey: .presetId)
    }
}

// MARK: - Preset Catalog

/// Factory FX presets.
public struct FXPreset: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let settings: FXSettings

    public init(id: String, name: String, settings: FXSettings) {
        self.id = id
        self.name = name
        self.settings = FXSettings(
            schemaVersion: settings.schemaVersion,
            eq: settings.eq,
            comp: settings.comp,
            reverb: settings.reverb,
            delay: settings.delay,
            fxReturnDb: settings.fxReturnDb,
            presetId: id
        )
    }
}

public enum FXPresetCatalog {
    /// All built-in presets.
    public static let all: [FXPreset] = [
        clean,
        shoegaze,
        slapback,
        tapeEcho,
        glueComp,
        loFi
    ]

    /// Preset lookup by ID.
    public static func preset(id: String) -> FXPreset? {
        all.first { $0.id == id }
    }

    // MARK: - Individual Presets

    /// Clean — bit-transparent, all effects neutral.
    public static let clean = FXPreset(
        id: "clean",
        name: "Clean",
        settings: .neutral
    )

    /// Shoegaze Hall — lush reverb, subtle compression.
    public static let shoegaze = FXPreset(
        id: "shoegaze",
        name: "Shoegaze Hall",
        settings: FXSettings(
            eq: FXEQParams(lowGainDb: 2, midGainDb: -2, highGainDb: 1),
            comp: FXCompParams(thresholdDb: -18, amountDb: 8, attackMs: 30, releaseMs: 200, makeupDb: 3),
            reverb: FXReverbParams(mix: 45, sizeSeconds: 3.5, dampPercent: 40),
            delay: FXDelayParams(timeSec: 0.3, feedback: 25, mix: 15),
            fxReturnDb: -3
        )
    )

    /// Slapback — classic rockabilly short delay.
    public static let slapback = FXPreset(
        id: "slapback",
        name: "Slapback",
        settings: FXSettings(
            eq: .neutral,
            comp: .neutral,
            reverb: .neutral,
            delay: FXDelayParams(timeSec: 0.12, feedback: 15, mix: 35),
            fxReturnDb: 0
        )
    )

    /// Tape Echo — warm delay with feedback.
    public static let tapeEcho = FXPreset(
        id: "tapeEcho",
        name: "Tape Echo",
        settings: FXSettings(
            eq: FXEQParams(lowGainDb: 1, midGainDb: 0, highGainDb: -3),
            comp: .neutral,
            reverb: FXReverbParams(mix: 10, sizeSeconds: 1.5, dampPercent: 60),
            delay: FXDelayParams(timeSec: 0.375, feedback: 45, mix: 30),
            fxReturnDb: -2
        )
    )

    /// Glue Comp — gentle bus compression for cohesion.
    public static let glueComp = FXPreset(
        id: "glueComp",
        name: "Glue Comp",
        settings: FXSettings(
            eq: .neutral,
            comp: FXCompParams(thresholdDb: -16, amountDb: 12, attackMs: 20, releaseMs: 150, makeupDb: 4),
            reverb: .neutral,
            delay: .neutral,
            fxReturnDb: 0
        )
    )

    /// Lo-Fi — degraded, vintage character.
    public static let loFi = FXPreset(
        id: "loFi",
        name: "Lo-Fi",
        settings: FXSettings(
            eq: FXEQParams(lowGainDb: 3, midGainDb: 2, highGainDb: -6),
            comp: FXCompParams(thresholdDb: -24, amountDb: 15, attackMs: 5, releaseMs: 80, makeupDb: 6),
            reverb: FXReverbParams(mix: 20, sizeSeconds: 1.2, dampPercent: 70),
            delay: .neutral,
            fxReturnDb: -1
        )
    )
}
