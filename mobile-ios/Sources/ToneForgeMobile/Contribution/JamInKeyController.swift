// JamInKeyController.swift
//
// UI-facing state + actions for the Jam in Key surface (redesign
// Phase 7). Derives everything from AppState + JamSettingsStore:
// the effective key, the 7 degree pads (note + roman numeral), the
// currently sounding chord and its two suggested follow-ups.
//
// Degree pads bypass the ContributionEventBus in v1 (D-018 — same
// precedent as the sketch metronome preview path): they voice
// directly on the PadSynth. The 8×8 grid presses still go through
// the bus so session capture/replay keeps working.
//
// Trigger timing is factored into the pure ``JamTriggerPlan`` so the
// quantize-delay + strum-stagger math is unit-testable without an
// audio engine.

import Foundation
import Combine
import ToneForgeEngine

/// One of the 7 diatonic degree pads under the jam grid.
public struct JamDegreePad: Identifiable, Equatable, Sendable {
    /// 1…7 scale degree; doubles as the stable row identity.
    public let degree: Int
    /// Key-aware root spelling ("Bb", not "A#", in D minor).
    public let noteName: String
    /// Quality-derived numeral ("i", "ii°", "III", …).
    public let romanNumeral: String
    /// Playable chord symbol ("Dm", "Edim", "Bb", …).
    public let symbol: String

    public var id: Int { degree }
}

/// Pure trigger schedule: which MIDI notes fire, at which sample
/// offsets, with what velocity/pan. Mirrors PadSynth.triggerChord's
/// velocity/pan spread so degree pads sound identical to the synth's
/// own chord path, but adds the quantize delay as a base offset.
public struct JamTriggerPlan: Equatable, Sendable {
    public struct Voice: Equatable, Sendable {
        public let midi: Int
        public let offsetSamples: Int
        public let velocity: Float
        public let pan: Float
    }
    public let voices: [Voice]

    /// Build a plan.
    /// - Parameters:
    ///   - midis: chord notes, low to high.
    ///   - strumMs: stagger between successive voices (0 = block chord).
    ///   - quantizeDelaySec: delay before the first voice (0 = now).
    ///   - sampleRate: engine render rate.
    ///   - velocity: MIDI-style 0…127 chord velocity.
    public static func plan(
        midis: [Int],
        strumMs: Double,
        quantizeDelaySec: Double,
        sampleRate: Double,
        velocity: Float = 100
    ) -> JamTriggerPlan {
        let baseOffset = Int(max(0, quantizeDelaySec) * sampleRate)
        let strumSamples = Int(max(0, strumMs) / 1000.0 * sampleRate)
        let n = midis.count
        let voices = midis.enumerated().map { i, midi in
            Voice(
                midi: midi,
                offsetSamples: baseOffset + i * strumSamples,
                velocity: max(20, velocity * 0.6),
                pan: n > 1 ? (Float(i) / Float(n - 1) - 0.5) * 0.6 : 0
            )
        }
        return JamTriggerPlan(voices: voices)
    }
}

@MainActor
public final class JamInKeyController: ObservableObject {

    /// Degree pad currently held down (visual press state only —
    /// PadSynth voices auto-release).
    @Published public private(set) var heldDegree: Int?

    private unowned let app: AppState

    public init(app: AppState) {
        self.app = app
    }

    // MARK: - Derived state

    private var jamSettings: JamSettingsStore { app.jamSettings }

    /// The key the surface plays in (override ?? detected, variant
    /// applied). nil when no key info exists at all.
    public var effectiveKey: MusicalKey? {
        jamSettings.effectiveKey(
            detectedKey: app.currentBundle?.meta.detectedKey,
            analysisId: app.currentBundle?.analysisId
        )
    }

    /// Display string for the header ("D Minor"). Falls back to the
    /// raw override/detected string when set, else a placeholder.
    public var keyDisplayName: String {
        guard let key = effectiveKey else { return "No key" }
        let root = NoteNames.name(pitchClass: key.root.rawValue, key: key)
        let scale: String
        switch key.scale {
        case .major:         scale = "Major"
        case .minor:         scale = "Minor"
        case .harmonicMinor: scale = "Harmonic Minor"
        case .melodicMinor:  scale = "Melodic Minor"
        case .dorian:        scale = "Dorian"
        case .phrygian:      scale = "Phrygian"
        case .lydian:        scale = "Lydian"
        case .mixolydian:    scale = "Mixolydian"
        case .locrian:       scale = "Locrian"
        }
        return "\(root) \(scale)"
    }

    /// The 7 diatonic degree pads for the effective key. Empty when
    /// there is no key.
    public var degreePads: [JamDegreePad] {
        guard let key = effectiveKey else { return [] }
        return Self.degreePads(for: key)
    }

    /// Pure builder — exposed for tests.
    public static func degreePads(for key: MusicalKey) -> [JamDegreePad] {
        DiatonicChords.triads(key: key).map { triad in
            JamDegreePad(
                degree: triad.degree,
                noteName: NoteNames.name(pitchClass: triad.root.rawValue, key: key),
                romanNumeral: triad.romanNumeral,
                symbol: triad.symbol
            )
        }
    }

    /// Symbol of the chord to display in the Current Chord panel:
    /// the song's sounding chord while one is active, else the tonic
    /// triad so the panel never goes blank.
    public var currentChordSymbol: String? {
        if let symbol = app.currentChord?.symbol { return symbol }
        return degreePads.first?.symbol
    }

    /// Two suggested follow-up chords for the current chord.
    public var suggestedChords: [DiatonicChord] {
        guard let key = effectiveKey, let symbol = currentChordSymbol else {
            return []
        }
        return ChordSuggestions.suggestions(after: symbol, in: key)
    }

    // MARK: - Degree pad actions

    public func padDown(degree: Int) {
        guard let pad = degreePads.first(where: { $0.degree == degree }) else {
            return
        }
        heldDegree = degree
        trigger(symbol: pad.symbol)
    }

    public func padUp(degree: Int) {
        if heldDegree == degree { heldDegree = nil }
    }

    /// Voice a chord symbol on the PadSynth (degree pads + suggested
    /// chips). Applies the octave shift, quantize delay, and strum.
    public func trigger(symbol: String) {
        let midis = ChordVoicing.midiNotes(
            symbol: symbol,
            octaveShift: jamSettings.octaveShift
        )
        guard !midis.isEmpty else { return }
        let plan = JamTriggerPlan.plan(
            midis: midis,
            strumMs: jamSettings.strumEnabled ? app.padSynth.params.strumMs : 0,
            quantizeDelaySec: quantizeDelaySec(),
            sampleRate: AudioEngine.canonicalSampleRate
        )
        for voice in plan.voices {
            app.padSynth.triggerNote(
                midi: voice.midi,
                velocity: voice.velocity,
                pan: voice.pan,
                atOffset: voice.offsetSamples
            )
        }
    }

    /// Seconds until the next quantize boundary. 0 when quantize is
    /// off or the transport is stopped (immediate trigger).
    private func quantizeDelaySec() -> Double {
        let mode = jamSettings.quantizeMode
        guard mode != .off, app.isPlaying else { return 0 }
        let now = app.songSeconds
        let target = Quantizer.nextQuantized(
            songSeconds: now,
            mode: mode,
            beats: app.currentBundle?.timeline.beats ?? [],
            downbeats: app.currentBundle?.timeline.downbeats ?? [],
            sections: app.currentBundle?.timeline.sections ?? [],
            tempoBpm: app.currentBundle?.meta.tempoBpm
                ?? app.sketchSettings.tempoBpm
        )
        return max(0, target - now)
    }

    // MARK: - Settings actions (mutate + refresh the grid layout)

    public func setKeyOverride(_ key: String?) {
        jamSettings.setKeyOverride(key, analysisId: app.currentBundle?.analysisId)
        app.modeCoordinator.refreshLayout()
        objectWillChange.send()
    }

    public func setScaleVariant(_ variant: JamScaleVariant) {
        jamSettings.scaleVariant = variant
        app.modeCoordinator.refreshLayout()
        objectWillChange.send()
    }

    public func setOctaveShift(_ shift: Int) {
        jamSettings.octaveShift = max(-3, min(3, shift))
        app.modeCoordinator.refreshLayout()
        objectWillChange.send()
    }

    public func setHighlightCurrentChord(_ on: Bool) {
        jamSettings.highlightCurrentChord = on
        app.modeCoordinator.refreshLayout()
        objectWillChange.send()
    }

    /// Apply a synth preset by id. Unknown ids are ignored (the
    /// picker only offers catalog entries; persisted ids from newer
    /// builds simply keep the current sound).
    public func applyPreset(id: String) {
        guard let preset = SynthPresetCatalog.preset(id: id) else { return }
        jamSettings.soundPresetId = preset.id
        app.padSynth.update(params: preset.params)
        objectWillChange.send()
    }

    // MARK: - Metronome actions

    public func setMetronomeEnabled(_ on: Bool) {
        jamSettings.metronomeEnabled = on
        app.syncMetronome()
        objectWillChange.send()
    }

    public func setMetronomeAccent(_ accent: MetronomeAccent) {
        jamSettings.metronomeAccent = accent
        app.syncMetronome()
        objectWillChange.send()
    }

    public func setMetronomeSound(_ sound: MetronomeSound) {
        jamSettings.metronomeSound = sound
        app.syncMetronome()
        objectWillChange.send()
    }

    public func setMetronomeSubdivide(_ on: Bool) {
        jamSettings.metronomeSubdivide = on
        app.syncMetronome()
        objectWillChange.send()
    }
}
