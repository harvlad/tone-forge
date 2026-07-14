// VocoderProgram.swift
//
// Everything the capture needs to preview and process one take,
// pre-built by the coordinator before capture starts (carrier
// synthesis is deterministic and cheap).

import Foundation
import ToneForgeEngine

/// Everything the capture needs to preview and process one take.
public struct VocoderProgram: Sendable {
    public let mode: VocoderMode
    /// 48 kHz mono carrier covering the full 8 s cap. Ignored for
    /// `.harmony`.
    public let carrier: [Float]
    /// Chord grid for `.harmony`'s voice leading.
    public let chordSpans: [VocoderCarriers.ChordSpan]
    public let harmonySettings: HarmonySettings
    public let config: VocoderConfig

    public init(
        mode: VocoderMode,
        carrier: [Float],
        chordSpans: [VocoderCarriers.ChordSpan] = [],
        harmonySettings: HarmonySettings = HarmonySettings(),
        config: VocoderConfig = VocoderConfig()
    ) {
        self.mode = mode
        self.carrier = carrier
        self.chordSpans = chordSpans
        self.harmonySettings = harmonySettings
        self.config = config
    }
}
