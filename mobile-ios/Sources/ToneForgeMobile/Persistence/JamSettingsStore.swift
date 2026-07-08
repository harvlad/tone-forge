// JamSettingsStore.swift
//
// UserDefaults-backed persistence for Jam in Key mode (redesign
// Phase 7). Same single-blob pattern as SampleSettingsStore: one
// versioned JSON payload, `didSet` autosave on every @Published
// field, decodeIfPresent for fields added after v1, corrupt blobs
// silently replaced with defaults.
//
// Persisted fields:
//   - scaleVariant:          natural | harmonic | melodic (minor only)
//   - highlightCurrentChord: brighten the sounding chord's tones
//   - soundPresetId:         SynthPresetCatalog id for the pad voice
//   - octaveShift:           whole octaves applied to the grid + pads
//   - strumEnabled:          stagger chord voices (degree pads)
//   - quantizeMode:          snap degree-pad triggers to the song grid
//   - keyOverrideBySong:     analysisId (or "sketch") → key string
//                            ("D minor" — MusicalKey.parse format)
//   - metronome…:            jam-surface metronome (independent of
//                            the sketch metronome settings)
//   - padMode:               pads (12-pad note grid) | chords (4×4
//                            chord grid) — the Phase 5 toggle that
//                            folds the Chord Pads surface into Jam
//   - holdEnabled:           pads mode: suppress touch pad-up so
//                            pads stay lit (visual/LED hold; jam
//                            pad-up routes no audio anyway)

import Foundation
import ToneForgeEngine

/// Which minor-family scale the jam surface uses. Natural leaves the
/// key untouched; harmonic/melodic only apply when the effective key
/// is in the minor family (a major/modal key ignores the variant).
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

/// Which pad surface the Jam tab shows (D-022 Phase 5): the 12-pad
/// in-key note grid or the 4×4 diatonic chord grid (the former
/// standalone Chord Pads surface).
public enum JamPadMode: String, CaseIterable, Codable, Sendable {
    case pads
    case chords

    public var displayName: String {
        switch self {
        case .pads:   return "Pads"
        case .chords: return "Chords"
        }
    }
}

@MainActor
public final class JamSettingsStore: ObservableObject {

    // MARK: - Published (auto-saved on change)

    /// Minor-family scale variant applied on top of the effective key.
    @Published public var scaleVariant: JamScaleVariant {
        didSet { save() }
    }

    /// Brighten the currently sounding chord's pitch classes on the
    /// grid (teal, same as hybrid mode).
    @Published public var highlightCurrentChord: Bool {
        didSet { save() }
    }

    /// SynthPresetCatalog id for the jam pad voice. Unknown ids fall
    /// back to the default preset at apply time.
    @Published public var soundPresetId: String {
        didSet { save() }
    }

    /// Whole-octave shift for the grid and the degree pads. Clamped
    /// to −3…+3 on write (matches JamInKeyLayout's clamp).
    @Published public var octaveShift: Int {
        didSet { save() }
    }

    /// Stagger degree-pad chord voices by the preset's strum time.
    @Published public var strumEnabled: Bool {
        didSet { save() }
    }

    /// Snap degree-pad triggers to the song's beat grid while the
    /// transport runs. `.off` = immediate.
    @Published public var quantizeMode: QuantizeMode {
        didSet { save() }
    }

    /// Per-song key override, keyed by analysisId (or "sketch" when
    /// no song is loaded). Values are MusicalKey.parse-able strings
    /// like "D minor". Missing key = use the song's detected key.
    @Published public var keyOverrideBySong: [String: String] {
        didSet { save() }
    }

    /// Jam-surface metronome. Independent of the sketch metronome so
    /// jamming over a song doesn't disturb sketch settings.
    @Published public var metronomeEnabled: Bool {
        didSet { save() }
    }

    @Published public var metronomeAccent: MetronomeAccent {
        didSet { save() }
    }

    @Published public var metronomeSound: MetronomeSound {
        didSet { save() }
    }

    @Published public var metronomeSubdivide: Bool {
        didSet { save() }
    }

    /// Which pad surface the Jam tab shows (pads | chords).
    @Published public var padMode: JamPadMode {
        didSet { save() }
    }

    /// Pads mode hold: keep touched pads down (suppress pad-up) until
    /// the chip is toggled off.
    @Published public var holdEnabled: Bool {
        didSet { save() }
    }

    /// Chord follow mode: highlight the current chord pad, pulse the
    /// next chord, show a countdown strip, and animate Launchpad row 1.
    @Published public var followEnabled: Bool {
        didSet { save() }
    }

    /// Built-in defaults, shared by init and the tests.
    nonisolated public static let defaultSoundPresetId = "dreamyLead"
    /// keyOverrideBySong key used when no song is loaded.
    nonisolated public static let sketchSongKey = "sketch"

    // MARK: - Init

    private static let defaultsKey = "toneforge.jamSettings"

    /// Injectable for tests; production callers use the no-arg init.
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let loaded = Self.load(from: defaults) ?? Persisted.defaults
        self.scaleVariant = loaded.scaleVariant
        self.highlightCurrentChord = loaded.highlightCurrentChord
        self.soundPresetId = loaded.soundPresetId
        self.octaveShift = max(-3, min(3, loaded.octaveShift))
        self.strumEnabled = loaded.strumEnabled
        self.quantizeMode = loaded.quantizeMode
        self.keyOverrideBySong = loaded.keyOverrideBySong
        self.metronomeEnabled = loaded.metronomeEnabled
        self.metronomeAccent = loaded.metronomeAccent
        self.metronomeSound = loaded.metronomeSound
        self.metronomeSubdivide = loaded.metronomeSubdivide
        self.padMode = loaded.padMode
        self.holdEnabled = loaded.holdEnabled
        self.followEnabled = loaded.followEnabled
    }

    // MARK: - Key resolution

    /// Key override for a song (or the sketch surface), or nil when
    /// the user hasn't overridden it.
    public func keyOverride(analysisId: String?) -> String? {
        keyOverrideBySong[analysisId ?? Self.sketchSongKey]
    }

    /// Persist a key override. Pass nil to clear (revert to the
    /// detected key).
    public func setKeyOverride(_ key: String?, analysisId: String?) {
        let songKey = analysisId ?? Self.sketchSongKey
        if let key = key {
            keyOverrideBySong[songKey] = key
        } else {
            keyOverrideBySong.removeValue(forKey: songKey)
        }
    }

    /// The key the jam surface plays in: user override if set, else
    /// the song's detected key, with the scale variant applied on
    /// top. nil when neither parses (no key info at all — the grid
    /// falls back to its chromatic rendering).
    public func effectiveKey(detectedKey: String?, analysisId: String?) -> MusicalKey? {
        let raw = keyOverride(analysisId: analysisId) ?? detectedKey
        guard let base = MusicalKey.parse(raw) else { return nil }
        return MusicalKey(root: base.root, scale: scaleVariant.apply(to: base.scale))
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var scaleVariant: JamScaleVariant
        var highlightCurrentChord: Bool
        var soundPresetId: String
        var octaveShift: Int
        var strumEnabled: Bool
        var quantizeMode: QuantizeMode
        var keyOverrideBySong: [String: String]
        var metronomeEnabled: Bool
        var metronomeAccent: MetronomeAccent
        var metronomeSound: MetronomeSound
        var metronomeSubdivide: Bool
        var padMode: JamPadMode
        var holdEnabled: Bool
        var followEnabled: Bool

        static let defaults = Persisted(
            storeVersion: 1,
            scaleVariant: .natural,
            highlightCurrentChord: true,
            soundPresetId: JamSettingsStore.defaultSoundPresetId,
            octaveShift: 0,
            strumEnabled: true,
            quantizeMode: .off,
            keyOverrideBySong: [:],
            metronomeEnabled: false,
            metronomeAccent: .downbeat,
            metronomeSound: .sine,
            metronomeSubdivide: false,
            padMode: .pads,
            holdEnabled: false,
            followEnabled: false
        )

        private enum CodingKeys: String, CodingKey {
            case storeVersion, scaleVariant, highlightCurrentChord,
                 soundPresetId, octaveShift, strumEnabled, quantizeMode,
                 keyOverrideBySong, metronomeEnabled, metronomeAccent,
                 metronomeSound, metronomeSubdivide, padMode, holdEnabled,
                 followEnabled
        }

        init(
            storeVersion: Int,
            scaleVariant: JamScaleVariant,
            highlightCurrentChord: Bool,
            soundPresetId: String,
            octaveShift: Int,
            strumEnabled: Bool,
            quantizeMode: QuantizeMode,
            keyOverrideBySong: [String: String],
            metronomeEnabled: Bool,
            metronomeAccent: MetronomeAccent,
            metronomeSound: MetronomeSound,
            metronomeSubdivide: Bool,
            padMode: JamPadMode,
            holdEnabled: Bool,
            followEnabled: Bool
        ) {
            self.storeVersion = storeVersion
            self.scaleVariant = scaleVariant
            self.highlightCurrentChord = highlightCurrentChord
            self.soundPresetId = soundPresetId
            self.octaveShift = octaveShift
            self.strumEnabled = strumEnabled
            self.quantizeMode = quantizeMode
            self.keyOverrideBySong = keyOverrideBySong
            self.metronomeEnabled = metronomeEnabled
            self.metronomeAccent = metronomeAccent
            self.metronomeSound = metronomeSound
            self.metronomeSubdivide = metronomeSubdivide
            self.padMode = padMode
            self.holdEnabled = holdEnabled
            self.followEnabled = followEnabled
        }

        // decodeIfPresent everywhere except storeVersion so fields
        // added after v1 (and enum raw values from newer builds that
        // fail to decode) degrade to defaults rather than nuking the
        // whole blob.
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            let d = Persisted.defaults
            self.storeVersion = try c.decode(Int.self, forKey: .storeVersion)
            self.scaleVariant = ((try? c.decodeIfPresent(
                JamScaleVariant.self, forKey: .scaleVariant
            )) ?? nil) ?? d.scaleVariant
            self.highlightCurrentChord = try c.decodeIfPresent(
                Bool.self, forKey: .highlightCurrentChord
            ) ?? d.highlightCurrentChord
            self.soundPresetId = try c.decodeIfPresent(
                String.self, forKey: .soundPresetId
            ) ?? d.soundPresetId
            self.octaveShift = try c.decodeIfPresent(
                Int.self, forKey: .octaveShift
            ) ?? d.octaveShift
            self.strumEnabled = try c.decodeIfPresent(
                Bool.self, forKey: .strumEnabled
            ) ?? d.strumEnabled
            self.quantizeMode = ((try? c.decodeIfPresent(
                QuantizeMode.self, forKey: .quantizeMode
            )) ?? nil) ?? d.quantizeMode
            self.keyOverrideBySong = try c.decodeIfPresent(
                [String: String].self, forKey: .keyOverrideBySong
            ) ?? d.keyOverrideBySong
            self.metronomeEnabled = try c.decodeIfPresent(
                Bool.self, forKey: .metronomeEnabled
            ) ?? d.metronomeEnabled
            self.metronomeAccent = ((try? c.decodeIfPresent(
                MetronomeAccent.self, forKey: .metronomeAccent
            )) ?? nil) ?? d.metronomeAccent
            self.metronomeSound = ((try? c.decodeIfPresent(
                MetronomeSound.self, forKey: .metronomeSound
            )) ?? nil) ?? d.metronomeSound
            self.metronomeSubdivide = try c.decodeIfPresent(
                Bool.self, forKey: .metronomeSubdivide
            ) ?? d.metronomeSubdivide
            self.padMode = ((try? c.decodeIfPresent(
                JamPadMode.self, forKey: .padMode
            )) ?? nil) ?? d.padMode
            self.holdEnabled = try c.decodeIfPresent(
                Bool.self, forKey: .holdEnabled
            ) ?? d.holdEnabled
            self.followEnabled = try c.decodeIfPresent(
                Bool.self, forKey: .followEnabled
            ) ?? d.followEnabled
        }
    }

    private static func load(from defaults: UserDefaults) -> Persisted? {
        guard let data = defaults.data(forKey: defaultsKey) else { return nil }
        return try? JSONDecoder().decode(Persisted.self, from: data)
    }

    private func save() {
        let payload = Persisted(
            storeVersion: 1,
            scaleVariant: scaleVariant,
            highlightCurrentChord: highlightCurrentChord,
            soundPresetId: soundPresetId,
            octaveShift: max(-3, min(3, octaveShift)),
            strumEnabled: strumEnabled,
            quantizeMode: quantizeMode,
            keyOverrideBySong: keyOverrideBySong,
            metronomeEnabled: metronomeEnabled,
            metronomeAccent: metronomeAccent,
            metronomeSound: metronomeSound,
            metronomeSubdivide: metronomeSubdivide,
            padMode: padMode,
            holdEnabled: holdEnabled,
            followEnabled: followEnabled
        )
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
