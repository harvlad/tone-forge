// JamInKeyModel.swift
//
// State for the Jam in Key pad surface (iOS parity P5): the 12
// performance pads from JamPadGrid12Mapping over the engine's
// JamInKeyLayout, plus the settings the iOS JamSettingsStore
// persists (key override per song, minor scale variant, octave
// shift, chord highlight).
//
// Pure logic — no audio. The audio layer subscribes to onNoteOn /
// onNoteOff (SessionController routes them into DesktopSynthNode).
// The pressed map remembers the MIDI note each surface pad fired so
// a key/octave change mid-hold still releases the right note.

import Foundation
import Observation
import ToneForgeEngine

/// Minor-family scale variant (port of the iOS JamScaleVariant —
/// ToneForgeMobile isn't linkable). Natural leaves the key untouched;
/// harmonic/melodic apply only when the effective key is minor.
public enum JamScaleVariant: String, CaseIterable, Codable, Sendable {
    case natural
    case harmonic
    case melodic

    public var displayName: String {
        switch self {
        case .natural:  return "Natural"
        case .harmonic: return "Harmonic"
        case .melodic:  return "Melodic"
        }
    }

    /// Map a base scale through the variant. Only minor-family scales
    /// respond; everything else passes through unchanged.
    public func apply(to scale: ScaleType) -> ScaleType {
        let isMinorFamily = scale == .minor
            || scale == .harmonicMinor
            || scale == .melodicMinor
        guard isMinorFamily else { return scale }
        switch self {
        case .natural:  return .minor
        case .harmonic: return .harmonicMinor
        case .melodic:  return .melodicMinor
        }
    }
}

/// One surface pad, ready to draw: `id` is the surface index 0..11
/// ascending by pitch (0 = lowest note, bottom-left).
public struct JamPadInfo: Identifiable, Equatable, Sendable {
    public let id: Int
    public let midi: Int
    public let noteName: String
    public let degreeLabel: String?
    /// 0xRRGGBB; 0 = unlit (out-of-key pad with no key info).
    public let colorHint: UInt32
    public let isBright: Bool
}

@Observable
@MainActor
public final class JamInKeyModel {

    // MARK: - Persisted settings

    public var scaleVariant: JamScaleVariant {
        didSet {
            defaults.set(scaleVariant.rawValue, forKey: Keys.scaleVariant)
        }
    }

    /// Whole-octave shift, clamped to −3…+3 (JamInKeyLayout's clamp).
    public var octaveShift: Int {
        didSet {
            let clamped = max(-3, min(3, octaveShift))
            if clamped != octaveShift { octaveShift = clamped; return }
            defaults.set(octaveShift, forKey: Keys.octaveShift)
        }
    }

    /// Brighten the currently sounding chord's pitch classes.
    public var highlightCurrentChord: Bool {
        didSet {
            defaults.set(
                highlightCurrentChord, forKey: Keys.highlightChord)
        }
    }

    /// Per-song key override: analysisId (or "sketch") → a
    /// MusicalKey.parse-able string like "D minor".
    public private(set) var keyOverrideBySong: [String: String] {
        didSet {
            if let data = try? JSONEncoder().encode(keyOverrideBySong) {
                defaults.set(data, forKey: Keys.keyOverrides)
            }
        }
    }

    // MARK: - Song context

    public private(set) var detectedKey: String?
    public private(set) var analysisId: String?

    /// The chord currently sounding in the song (set by the UI pump
    /// from the chord ribbon); drives the chord-tone highlight.
    public var currentChordSymbol: String?

    // MARK: - Playing state

    /// Surface index → the MIDI note it fired, so pad-up releases the
    /// note that actually sounded even if the layout changed while
    /// the pad was held.
    public private(set) var pressed: [Int: Int] = [:]

    @ObservationIgnored public var onNoteOn: ((Int, Float) -> Void)?
    @ObservationIgnored public var onNoteOff: ((Int) -> Void)?

    // MARK: - Init

    private enum Keys {
        static let scaleVariant = "jamdesktop.jam.scaleVariant"
        static let octaveShift = "jamdesktop.jam.octaveShift"
        static let highlightChord = "jamdesktop.jam.highlightChord"
        static let keyOverrides = "jamdesktop.jam.keyOverrides"
    }

    /// keyOverrideBySong key used when no song is loaded.
    public static let sketchSongKey = "sketch"

    @ObservationIgnored private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.scaleVariant = defaults.string(forKey: Keys.scaleVariant)
            .flatMap(JamScaleVariant.init(rawValue:)) ?? .natural
        let shift = defaults.object(forKey: Keys.octaveShift) as? Int ?? 0
        self.octaveShift = max(-3, min(3, shift))
        self.highlightCurrentChord = defaults
            .object(forKey: Keys.highlightChord) as? Bool ?? true
        self.keyOverrideBySong = defaults.data(forKey: Keys.keyOverrides)
            .flatMap { try? JSONDecoder().decode([String: String].self, from: $0) }
            ?? [:]
    }

    // MARK: - Song adoption

    /// Adopt a song's detected key (attach) or clear it (detach —
    /// pass nils for the sketch surface).
    public func configure(detectedKey: String?, analysisId: String?) {
        releaseAll()
        self.detectedKey = detectedKey
        self.analysisId = analysisId
        currentChordSymbol = nil
    }

    // MARK: - Key resolution

    private var songKey: String { analysisId ?? Self.sketchSongKey }

    /// The user's key override for the current song, or nil.
    public var keyOverride: String? { keyOverrideBySong[songKey] }

    /// Set (or clear, with nil) the key override for the current song.
    public func setKeyOverride(_ raw: String?) {
        if let raw {
            keyOverrideBySong[songKey] = raw
        } else {
            keyOverrideBySong.removeValue(forKey: songKey)
        }
    }

    /// Override if set, else the detected key, with the scale variant
    /// applied. nil when neither parses (grid falls back chromatic).
    public var effectiveKey: MusicalKey? {
        let raw = keyOverride ?? detectedKey
        guard let base = MusicalKey.parse(raw) else { return nil }
        return MusicalKey(
            root: base.root, scale: scaleVariant.apply(to: base.scale))
    }

    /// Display string for the key control ("D minor", "No key").
    public var keyDisplayName: String {
        if let override = keyOverride { return override }
        if let detected = detectedKey, MusicalKey.parse(detected) != nil {
            return detected
        }
        return "No key"
    }

    // MARK: - Pads

    /// The 12 surface pads, ascending by pitch.
    public var pads: [JamPadInfo] {
        let key = effectiveKey
        let layout = JamInKeyLayout(
            key: key,
            chordPitchClasses: highlightPitchClasses,
            octaveShift: octaveShift
        )
        return JamPadGrid12Mapping.pads(key: key).enumerated().map {
            index, gridPad in
            let visual = layout.visual(at: gridPad)
            let midi: Int
            let pitchClass: Int
            if case .note(let m, let pc, _) = layout.meaning(at: gridPad) {
                midi = m
                pitchClass = pc
            } else {
                midi = 0
                pitchClass = 0
            }
            return JamPadInfo(
                id: index,
                midi: midi,
                noteName: NoteNames.name(pitchClass: pitchClass, key: key),
                degreeLabel: visual.label,
                colorHint: visual.colorHint,
                isBright: visual.isBright
            )
        }
    }

    private var highlightPitchClasses: Set<Int> {
        guard highlightCurrentChord, let symbol = currentChordSymbol
        else { return [] }
        return ChordVoicing.pitchClassSet(symbol: symbol)
    }

    // MARK: - Touch

    public func padDown(_ index: Int, velocity: Float = 1) {
        guard pressed[index] == nil else { return }
        let infos = pads
        guard infos.indices.contains(index) else { return }
        let midi = infos[index].midi
        pressed[index] = midi
        onNoteOn?(midi, max(0, min(1, velocity)))
    }

    public func padUp(_ index: Int) {
        guard let midi = pressed.removeValue(forKey: index) else { return }
        onNoteOff?(midi)
    }

    public func releaseAll() {
        for midi in pressed.values { onNoteOff?(midi) }
        pressed.removeAll()
    }
}
