//
// ChainSpec.swift
//
// Swift mirror of the monitor-chain YAML schema documented in
// backend/tone_forge/monitor/README.md.
//
// This file is intentionally pure value types — no AVFoundation, no
// IO. ChainSpec is what `MonitorChainLoader` (P3c) populates from YAML
// and what `AudioEngine.applyChain(_:)` programs into the DSP graph.
// Splitting parsing from application keeps the test surface narrow:
// ChainSpec semantics are covered by XCTest without spinning up an
// AVAudioEngine, which is hostile to CI sandboxes.
//
// Schema (matches backend/tone_forge/monitor/chains/*.yaml):
//
//   parameters:
//     input:      gain_db, high_pass_hz
//     gain_stage: type, drive, bias
//     eq:         bass_db, mid_db, treble_db, presence_db
//     comp:       enabled, ratio, threshold_db, attack_ms, release_ms
//     reverb:     type, size, mix
//     output:     trim_db
//
// Bounds policy: every numeric field has a documented sane range and
// `clamped()` projects out-of-range values into it. The loader (P3c)
// will clamp on construction so the AVAudioEngine graph never sees a
// value that could damage headphones or stall the audio thread.
//

import Foundation

public struct ChainSpec: Equatable {

    public let id: String
    public let displayName: String
    public let input: Input
    public let gainStage: GainStage
    public let eq: EQ
    public let comp: Comp
    public let reverb: Reverb
    public let output: Output

    public init(
        id: String,
        displayName: String,
        input: Input = .baseline,
        gainStage: GainStage = .baseline,
        eq: EQ = .baseline,
        comp: Comp = .baseline,
        reverb: Reverb = .baseline,
        output: Output = .baseline
    ) {
        self.id = id
        self.displayName = displayName
        self.input = input
        self.gainStage = gainStage
        self.eq = eq
        self.comp = comp
        self.reverb = reverb
        self.output = output
    }

    /// Conservative pre-listening baseline used when no spec is loaded
    /// (fresh engine, or a spec failed to parse). Mirrors the
    /// `tfc.clean_strat` placeholder values so the user always hears
    /// something usable before any chain is applied.
    public static let baseline = ChainSpec(
        id: "tfc.baseline",
        displayName: "Baseline"
    )

    /// Returns a new spec with every numeric field clamped into the
    /// safe DSP range documented on each substruct. Pure: no mutation.
    public func clamped() -> ChainSpec {
        return ChainSpec(
            id: id,
            displayName: displayName,
            input: input.clamped(),
            gainStage: gainStage.clamped(),
            eq: eq.clamped(),
            comp: comp.clamped(),
            reverb: reverb.clamped(),
            output: output.clamped()
        )
    }

    // MARK: - Sections

    public struct Input: Equatable {
        /// Pre-gain in dB. Safe range: [-12, +12]. Outside this band
        /// risks either inaudibility or clipping the saturator.
        public let gainDb: Float
        /// High-pass cutoff in Hz. Safe range: [20, 500]. Below 20 Hz
        /// the filter is a no-op; above 500 Hz it carves out body.
        public let highPassHz: Float

        public init(gainDb: Float, highPassHz: Float) {
            self.gainDb = gainDb
            self.highPassHz = highPassHz
        }

        public static let baseline = Input(gainDb: 0, highPassHz: 80)

        public func clamped() -> Input {
            return Input(
                gainDb: max(-12, min(12, gainDb)),
                highPassHz: max(20, min(500, highPassHz))
            )
        }
    }

    /// Saturation stage. ``type`` is a human label that selects an
    /// AVAudioUnitDistortion factory preset on the audio side; the
    /// mapping is owned by AudioEngine so ChainSpec stays pure.
    public struct GainStage: Equatable {
        public enum StageType: String, Equatable {
            case tubeClean = "tube_clean"
            case tubeBreak = "tube_break"
            case tubeOverdrive = "tube_overdrive"
            case tubeHighGain = "tube_high_gain"

            /// Permissive parser: unknown labels fall back to a safe
            /// clean-channel so a typo in a YAML never silently
            /// produces a high-gain wall of noise.
            public static func parse(_ raw: String) -> StageType {
                return StageType(rawValue: raw) ?? .tubeClean
            }
        }

        public let type: StageType
        /// Drive 0.0 (clean) – 1.0 (maximum saturation).
        public let drive: Float
        /// Bias 0.0 – 1.0. Currently advisory — the AVAudioEngine
        /// distortion unit doesn't expose bias directly; reserved for
        /// when the gain stage moves to a custom AU.
        public let bias: Float

        public init(type: StageType, drive: Float, bias: Float) {
            self.type = type
            self.drive = drive
            self.bias = bias
        }

        public static let baseline = GainStage(
            type: .tubeClean,
            drive: 0.1,
            bias: 0.5
        )

        public func clamped() -> GainStage {
            return GainStage(
                type: type,
                drive: max(0, min(1, drive)),
                bias: max(0, min(1, bias))
            )
        }
    }

    /// 4-band parametric EQ. Frequencies are fixed (guitar-shaped)
    /// and only the gains come from YAML — keeps the DSP graph stable
    /// across chains and makes the listening engagement focus on
    /// tonal balance, not sweepable bands.
    public struct EQ: Equatable {
        public let bassDb: Float
        public let midDb: Float
        public let trebleDb: Float
        public let presenceDb: Float

        public init(
            bassDb: Float,
            midDb: Float,
            trebleDb: Float,
            presenceDb: Float
        ) {
            self.bassDb = bassDb
            self.midDb = midDb
            self.trebleDb = trebleDb
            self.presenceDb = presenceDb
        }

        public static let baseline = EQ(
            bassDb: 0,
            midDb: 0,
            trebleDb: 0,
            presenceDb: 0
        )

        public func clamped() -> EQ {
            // ±12 dB per band — anything larger is more likely a YAML
            // typo than a tonal choice.
            return EQ(
                bassDb: max(-12, min(12, bassDb)),
                midDb: max(-12, min(12, midDb)),
                trebleDb: max(-12, min(12, trebleDb)),
                presenceDb: max(-12, min(12, presenceDb))
            )
        }
    }

    public struct Comp: Equatable {
        public let enabled: Bool
        /// Compression ratio. Safe range [1.0, 20.0]. 1.0 is bypass-equivalent.
        public let ratio: Float
        /// Threshold in dB. Safe range [-40, 0].
        public let thresholdDb: Float
        /// Attack in ms. Safe range [0.1, 200].
        public let attackMs: Float
        /// Release in ms. Safe range [1, 2000].
        public let releaseMs: Float

        public init(
            enabled: Bool,
            ratio: Float,
            thresholdDb: Float,
            attackMs: Float,
            releaseMs: Float
        ) {
            self.enabled = enabled
            self.ratio = ratio
            self.thresholdDb = thresholdDb
            self.attackMs = attackMs
            self.releaseMs = releaseMs
        }

        public static let baseline = Comp(
            enabled: false,
            ratio: 2.0,
            thresholdDb: -18,
            attackMs: 5,
            releaseMs: 80
        )

        public func clamped() -> Comp {
            return Comp(
                enabled: enabled,
                ratio: max(1.0, min(20.0, ratio)),
                thresholdDb: max(-40, min(0, thresholdDb)),
                attackMs: max(0.1, min(200, attackMs)),
                releaseMs: max(1, min(2000, releaseMs))
            )
        }
    }

    public struct Reverb: Equatable {
        public enum ReverbType: String, Equatable {
            case room
            case plate
            case spring
            case hall
            case smallHall = "small_hall"

            /// Permissive parser: unknown labels fall back to room.
            public static func parse(_ raw: String) -> ReverbType {
                return ReverbType(rawValue: raw) ?? .room
            }
        }

        public let type: ReverbType
        /// Reverb size 0.0 – 1.0. The AU exposes size as a factory
        /// preset choice; AudioEngine collapses size+type into the
        /// nearest preset.
        public let size: Float
        /// Wet mix 0.0 – 1.0. The AU's wetDryMix is 0–100%; the
        /// engine scales on the way through.
        public let mix: Float

        public init(type: ReverbType, size: Float, mix: Float) {
            self.type = type
            self.size = size
            self.mix = mix
        }

        public static let baseline = Reverb(type: .room, size: 0.3, mix: 0.1)

        public func clamped() -> Reverb {
            return Reverb(
                type: type,
                size: max(0, min(1, size)),
                mix: max(0, min(1, mix))
            )
        }
    }

    public struct Output: Equatable {
        /// Output trim in dB. Safe range [-12, +6]. We allow less
        /// boost than cut so a misbehaving chain can't blow out
        /// headphones at unity input.
        public let trimDb: Float

        public init(trimDb: Float) {
            self.trimDb = trimDb
        }

        public static let baseline = Output(trimDb: 0)

        public func clamped() -> Output {
            return Output(trimDb: max(-12, min(6, trimDb)))
        }
    }
}

// MARK: - Wire-format decoding
//
// The backend ships a fully-resolved chain spec over the connect_bridge
// WS in the shape produced by ``_monitor_chain_to_wire``:
//
//   {
//     "id": "tfc.clean_strat",
//     "family": "clean",
//     "display_name": "...",
//     "description": "...",
//     "parameters": {
//       "input": {"gain_db": ..., "high_pass_hz": ...},
//       "gain_stage": {"type": "...", "drive": ..., "bias": ...},
//       "eq": {"bass_db": ..., "mid_db": ..., ...},
//       "comp": {"enabled": ..., "ratio": ..., ...},
//       "reverb": {"type": "...", "size": ..., "mix": ...},
//       "output": {"trim_db": ...}
//     }
//   }
//
// `decode(fromWireDict:)` is permissive on numeric typing (JSON numbers
// arrive as NSNumber from JSONSerialization; Swift demotes them through
// Float / Double / Int) and on unknown enum labels (fall back to safe
// defaults — see GainStage.StageType.parse and ReverbType.parse). The
// decoder never throws — a malformed frame returns nil and the caller
// (PresetBridge dispatch) logs and ignores the frame. This keeps the
// audio path resilient to a backend-side schema typo.

extension ChainSpec {

    /// Decode a wire-format dictionary into a ChainSpec. Returns nil if
    /// the dictionary is missing any required top-level key or the
    /// parameters subtree is malformed. Field defaults inside each
    /// section come from the corresponding ``baseline`` so a partially-
    /// populated frame still produces a usable spec.
    public static func decode(fromWireDict dict: [String: Any]) -> ChainSpec? {
        guard let id = dict["id"] as? String, !id.isEmpty,
              let parameters = dict["parameters"] as? [String: Any]
        else { return nil }

        let displayName = (dict["display_name"] as? String) ?? id

        let input = decodeInput(parameters["input"] as? [String: Any])
        let gainStage = decodeGainStage(parameters["gain_stage"] as? [String: Any])
        let eq = decodeEQ(parameters["eq"] as? [String: Any])
        let comp = decodeComp(parameters["comp"] as? [String: Any])
        let reverb = decodeReverb(parameters["reverb"] as? [String: Any])
        let output = decodeOutput(parameters["output"] as? [String: Any])

        return ChainSpec(
            id: id,
            displayName: displayName,
            input: input,
            gainStage: gainStage,
            eq: eq,
            comp: comp,
            reverb: reverb,
            output: output
        )
    }

    // MARK: - Section decoders (private helpers exposed for tests)

    /// Permissive numeric coercion. JSONSerialization returns NSNumber
    /// for every JSON number; cast paths via Float / Double / Int catch
    /// the cases we care about. Returns nil only if the value is
    /// genuinely non-numeric.
    static func coerceFloat(_ any: Any?) -> Float? {
        if let f = any as? Float { return f }
        if let d = any as? Double { return Float(d) }
        if let i = any as? Int { return Float(i) }
        if let n = any as? NSNumber { return n.floatValue }
        return nil
    }

    static func decodeInput(_ dict: [String: Any]?) -> Input {
        guard let dict = dict else { return .baseline }
        return Input(
            gainDb: coerceFloat(dict["gain_db"]) ?? Input.baseline.gainDb,
            highPassHz: coerceFloat(dict["high_pass_hz"]) ?? Input.baseline.highPassHz
        )
    }

    static func decodeGainStage(_ dict: [String: Any]?) -> GainStage {
        guard let dict = dict else { return .baseline }
        let typeLabel = (dict["type"] as? String) ?? ""
        return GainStage(
            type: GainStage.StageType.parse(typeLabel),
            drive: coerceFloat(dict["drive"]) ?? GainStage.baseline.drive,
            bias: coerceFloat(dict["bias"]) ?? GainStage.baseline.bias
        )
    }

    static func decodeEQ(_ dict: [String: Any]?) -> EQ {
        guard let dict = dict else { return .baseline }
        return EQ(
            bassDb: coerceFloat(dict["bass_db"]) ?? EQ.baseline.bassDb,
            midDb: coerceFloat(dict["mid_db"]) ?? EQ.baseline.midDb,
            trebleDb: coerceFloat(dict["treble_db"]) ?? EQ.baseline.trebleDb,
            presenceDb: coerceFloat(dict["presence_db"]) ?? EQ.baseline.presenceDb
        )
    }

    static func decodeComp(_ dict: [String: Any]?) -> Comp {
        guard let dict = dict else { return .baseline }
        // bool can arrive as NSNumber(1/0); accept both.
        let enabled: Bool
        if let b = dict["enabled"] as? Bool {
            enabled = b
        } else if let n = dict["enabled"] as? NSNumber {
            enabled = n.boolValue
        } else {
            enabled = Comp.baseline.enabled
        }
        return Comp(
            enabled: enabled,
            ratio: coerceFloat(dict["ratio"]) ?? Comp.baseline.ratio,
            thresholdDb: coerceFloat(dict["threshold_db"]) ?? Comp.baseline.thresholdDb,
            attackMs: coerceFloat(dict["attack_ms"]) ?? Comp.baseline.attackMs,
            releaseMs: coerceFloat(dict["release_ms"]) ?? Comp.baseline.releaseMs
        )
    }

    static func decodeReverb(_ dict: [String: Any]?) -> Reverb {
        guard let dict = dict else { return .baseline }
        let typeLabel = (dict["type"] as? String) ?? ""
        return Reverb(
            type: Reverb.ReverbType.parse(typeLabel),
            size: coerceFloat(dict["size"]) ?? Reverb.baseline.size,
            mix: coerceFloat(dict["mix"]) ?? Reverb.baseline.mix
        )
    }

    static func decodeOutput(_ dict: [String: Any]?) -> Output {
        guard let dict = dict else { return .baseline }
        return Output(
            trimDb: coerceFloat(dict["trim_db"]) ?? Output.baseline.trimDb
        )
    }
}
