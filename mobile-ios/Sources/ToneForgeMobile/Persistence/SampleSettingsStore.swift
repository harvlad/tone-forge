// SampleSettingsStore.swift
//
// UserDefaults-backed persistence for the Samples panel's cross-session
// settings — the values users expect to be remembered when they close
// and reopen the app.
//
// Persisted fields (see plan "Persistence" section):
//   - currentPackId:       last-activated pack (bundled + song-derived + curated)
//   - quantizeMode:        Off | 1/8 | 1/4 | 1/2 | 1 bar | phrase
//   - holdMode:            hold | toggle
//   - beatBarMode:         beat | bar
//   - sectionGatesBySong:  analysisId → allowed section labels
//                          (empty array = deny all; missing key = allow all)
//   - layerFaderDb:        "Your Layer" fader in the Mixer view, in dB
//
// Design notes:
//   * Single top-level JSON blob under a single UserDefaults key. Cheaper
//     than one key per field and lets us evolve the schema with a version
//     tag. Wrong-shape blobs are rejected and replaced with defaults on
//     next write — so a schema break can never brick the app.
//   * `didSet` observers on every `@Published` field autosave, matching
//     the pattern established by `AppState.backendBaseURL`.
//   * Section gate policy mirrors `SectionResolver.isAllowed`:
//       - nil per-song array   → allow all sections
//       - empty per-song array → deny all sections
//       - non-empty            → allow only listed labels
//     Callers convert `nil` ⇄ absent-key on read/write so the JSON stays
//     compact for the common case (nothing gated).

import Foundation
import ToneForgeEngine

@MainActor
public final class SampleSettingsStore: ObservableObject {

    // MARK: - Published (auto-saved on change)

    /// Pack activated on last app run. Defaults to the bundled starter
    /// pack — always available offline so the app is playable on cold
    /// launch with no network.
    @Published public var currentPackId: String {
        didSet { save() }
    }

    /// User-selected quantize policy. Pad-level `defaultQuantize`
    /// overrides this at trigger time.
    @Published public var quantizeMode: QuantizeMode {
        didSet { save() }
    }

    /// Hold vs toggle — controls whether taps latch or fire-and-forget.
    @Published public var holdMode: HoldMode {
        didSet { save() }
    }

    /// Whether the quantize "1" divisor means one beat or one bar.
    @Published public var beatBarMode: BeatBarMode {
        didSet { save() }
    }

    /// Per-song section allowlist. `nil` = allow all sections; empty
    /// set = deny all; non-empty = allow only listed labels. Match is
    /// case-insensitive at gate time (see `SectionResolver.isAllowed`).
    @Published public var sectionGatesBySong: [String: Set<String>] {
        didSet { save() }
    }

    /// "Your Layer" fader position in dB. 0 dB = unity. Clamped to
    /// [-60, +6] on write to match the mixer slider's range.
    @Published public var layerFaderDb: Double {
        didSet { save() }
    }

    /// Per-pad effect overrides. Keyed by "packId#padIdx". When a
    /// user drags sliders in `PadEffectsEditor`, the resulting
    /// `SamplePadEffects` lands here and preempts the pack manifest's
    /// baseline for that pad. Missing key → fall back to
    /// `SamplePad.effects` → fall back to `.neutral`.
    @Published public var padEffectsByKey: [String: SamplePadEffects] {
        didSet { save() }
    }

    /// Pad-synth ("voice") level, 0..1 linear. Mapped onto
    /// `PadSynthParams.masterGain` via a 0.311 factor so the 0.9
    /// default lands on the long-standing 0.28 gain (loudness-neutral
    /// migration; see DECISIONS.md D-010).
    @Published public var voiceGainLinear: Double {
        didSet { save() }
    }

    /// Chop/sample bus level, 0..1 linear → `AudioEngine.setChopGain`.
    /// Default 0.55 (spec) — quieter than the pre-slider 1.0.
    @Published public var chopGainLinear: Double {
        didSet { save() }
    }

    /// Vocoder-preview bus level, 0..1 linear →
    /// `AudioEngine.setVocoderGain`. The bus exists (silently) from
    /// P1; a source lands on it in P5. Default 0.4 per D-013.
    @Published public var vocoderGainLinear: Double {
        didSet { save() }
    }

    /// Persisted AppMode raw value (`AppMode(rawValue:)`), so the app
    /// reopens in the mode the user last played. Unknown values fall
    /// back to `"sample"` at the ModeCoordinator when decoding.
    @Published public var appModeRaw: String {
        didSet { save() }
    }

    /// Persisted AppTab raw value (`AppTab(rawValue:)`), so the app
    /// reopens on the tab the user last used (D-022 five-tab shell).
    /// Unknown values fall back to `"contribute"` at the view layer
    /// when decoding. Migrated once from the legacy D-019
    /// `playSurfaceRaw` blob key (chordPads → jam).
    @Published public var appTabRaw: String {
        didSet { save() }
    }

    /// Last contribute-family AppMode raw value ("sample"/"hybrid").
    /// `appModeRaw` alone is not enough once Jam in Key also writes
    /// it — this remembers which contribute grid to restore when the
    /// user switches back to the Contribute surface.
    @Published public var lastContributeModeRaw: String {
        didSet { save() }
    }

    /// Instrument mode (hybrid) synth preset id. Unknown ids fall back
    /// to the default preset at apply time.
    @Published public var instrumentPresetId: String {
        didSet { save() }
    }

    /// Instrument mode octave shift. Clamped to -3...+3.
    @Published public var instrumentOctaveShift: Int {
        didSet { save() }
    }

    /// Instrument mode brightness override. When != 1.0 this is applied
    /// on top of the preset brightness. Range 0.5...2.0.
    @Published public var instrumentBrightness: Double {
        didSet { save() }
    }

    /// Hidden pack pads. Keyed by "packId#padIdx". When a user deletes
    /// a pack pad via the radial menu, it's added here and excluded from
    /// the sample quadrant until unhidden.
    @Published public var hiddenPadKeys: Set<String> {
        didSet { save() }
    }

    /// When true, an attached generic MIDI pad box (LPD8/MPD) fires the
    /// active sample pack's pads instead of the wavetable synth. Drives
    /// `MIDIKeyboardTransport.noteRouting`.
    @Published public var midiPadsToSamples: Bool {
        didSet { save() }
    }

    /// Built-in defaults, shared by init and the tests.
    nonisolated public static let defaultVoiceGain: Double = 0.9
    nonisolated public static let defaultChopGain: Double = 0.55
    nonisolated public static let defaultVocoderGain: Double = 0.4
    nonisolated public static let defaultAppModeRaw: String = "sample"
    nonisolated public static let defaultAppTabRaw: String = "contribute"
    nonisolated public static let defaultLastContributeModeRaw: String = "sample"
    nonisolated public static let defaultInstrumentPresetId: String = "dreamyLead"
    nonisolated public static let defaultInstrumentOctaveShift: Int = 0
    nonisolated public static let defaultInstrumentBrightness: Double = 1.0
    nonisolated public static let defaultMidiPadsToSamples: Bool = false

    // MARK: - Init

    /// UserDefaults key for the persisted blob. Namespaced under
    /// "toneforge." to match `AppState.backendURLDefaultsKey`.
    private static let defaultsKey = "toneforge.sampleSettings"

    /// Injectable for tests; production callers use the no-arg init.
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let loaded = Self.load(from: defaults) ?? Persisted.defaults
        self.currentPackId = loaded.currentPackId
        self.quantizeMode = loaded.quantizeMode
        self.holdMode = loaded.holdMode
        self.beatBarMode = loaded.beatBarMode
        self.sectionGatesBySong = loaded.sectionGatesBySong.mapValues(Set.init)
        self.layerFaderDb = loaded.layerFaderDb
        self.padEffectsByKey = loaded.padEffectsByKey
        self.voiceGainLinear = loaded.voiceGainLinear
        self.chopGainLinear = loaded.chopGainLinear
        self.vocoderGainLinear = loaded.vocoderGainLinear
        self.appModeRaw = loaded.appModeRaw
        self.appTabRaw = loaded.appTabRaw
        self.lastContributeModeRaw = loaded.lastContributeModeRaw
        self.instrumentPresetId = loaded.instrumentPresetId
        self.instrumentOctaveShift = max(-3, min(3, loaded.instrumentOctaveShift))
        self.instrumentBrightness = max(0.5, min(2.0, loaded.instrumentBrightness))
        self.hiddenPadKeys = Set(loaded.hiddenPadKeys)
        self.midiPadsToSamples = loaded.midiPadsToSamples
    }

    // MARK: - Pad-effect override convenience

    /// Compose the "packId#padIdx" key used inside
    /// `padEffectsByKey`. Kept as a static helper so tests + the
    /// editor UI share one canonical form.
    public static func padEffectsKey(packId: String, padIdx: Int) -> String {
        "\(packId)#\(padIdx)"
    }

    /// User override for `(packId, padIdx)`, or nil if none is set.
    /// Note: an override that happens to equal `.neutral` still
    /// counts as an override — callers who want "override or fall
    /// back" behaviour use `effectivePadEffects`.
    public func padEffectsOverride(packId: String, padIdx: Int) -> SamplePadEffects? {
        padEffectsByKey[Self.padEffectsKey(packId: packId, padIdx: padIdx)]
    }

    /// Persist a new override for `(packId, padIdx)`. Pass nil to
    /// clear the override (revert to manifest baseline).
    public func setPadEffectsOverride(
        _ effects: SamplePadEffects?,
        packId: String,
        padIdx: Int
    ) {
        let key = Self.padEffectsKey(packId: packId, padIdx: padIdx)
        if let effects = effects {
            padEffectsByKey[key] = effects.clamped()
        } else {
            padEffectsByKey.removeValue(forKey: key)
        }
    }

    /// Three-tier resolution: user override > manifest baseline >
    /// `.neutral`. This is what the SampleScheduler consults on every
    /// trigger and what the editor UI's "cleared to default" glyph
    /// mirrors.
    public func effectivePadEffects(
        packId: String,
        padIdx: Int,
        manifestBaseline: SamplePadEffects?
    ) -> SamplePadEffects {
        if let override = padEffectsOverride(packId: packId, padIdx: padIdx) {
            return override
        }
        return manifestBaseline ?? .neutral
    }

    // MARK: - Section-gate convenience

    /// Return the current allow-set for `analysisId`. `nil` means
    /// "allow all sections" — the default when the user hasn't
    /// touched the section chips for this song yet.
    public func sectionGates(for analysisId: String) -> Set<String>? {
        sectionGatesBySong[analysisId]
    }

    /// Persist a new allow-set for the song. Pass `nil` to clear
    /// (revert to "allow all"), matching `SectionResolver.isAllowed`
    /// semantics.
    public func setSectionGates(_ labels: Set<String>?, for analysisId: String) {
        if let labels = labels {
            sectionGatesBySong[analysisId] = labels
        } else {
            sectionGatesBySong.removeValue(forKey: analysisId)
        }
    }

    // MARK: - Hidden pad convenience

    /// Check if a pack pad is hidden.
    public func isPadHidden(packId: String, padIdx: Int) -> Bool {
        hiddenPadKeys.contains(Self.padEffectsKey(packId: packId, padIdx: padIdx))
    }

    /// Hide a pack pad (remove from grid until unhidden).
    public func hidePad(packId: String, padIdx: Int) {
        hiddenPadKeys.insert(Self.padEffectsKey(packId: packId, padIdx: padIdx))
    }

    /// Unhide a pack pad (restore to grid).
    public func unhidePad(packId: String, padIdx: Int) {
        hiddenPadKeys.remove(Self.padEffectsKey(packId: packId, padIdx: padIdx))
    }

    /// Unhide all pads for a given pack.
    public func unhideAllPads(packId: String) {
        hiddenPadKeys = hiddenPadKeys.filter { !$0.hasPrefix("\(packId)#") }
    }

    // MARK: - Persistence

    /// Wire shape of the on-disk blob. Kept as a value type so encode
    /// / decode stays symmetrical and testable without touching the
    /// observable object.
    ///
    /// LOCK-IN: bumping `storeVersion` is the only supported way to
    /// change the field set. A decoder branch on `storeVersion` keeps
    /// older on-disk blobs readable.
    private struct Persisted: Codable {
        var storeVersion: Int
        var currentPackId: String
        var quantizeMode: QuantizeMode
        var holdMode: HoldMode
        var beatBarMode: BeatBarMode
        /// Encoded as `[String]` rather than `Set<String>` for stable
        /// JSON output ordering. The observable stores `Set` for the
        /// O(1) contains checks at gate time.
        var sectionGatesBySong: [String: [String]]
        var layerFaderDb: Double
        /// Per-pad effect overrides, keyed by
        /// `SampleSettingsStore.padEffectsKey(packId:padIdx:)`.
        /// Absent in blobs written before Phase 6d; decoder defaults
        /// to `[:]` so old blobs still round-trip.
        var padEffectsByKey: [String: SamplePadEffects]
        /// Voice/chop levels. Absent in blobs written before the
        /// Settings gain sliders landed; decoder falls back to the
        /// spec defaults (0.9 / 0.55).
        var voiceGainLinear: Double
        var chopGainLinear: Double
        /// Vocoder-preview level + persisted AppMode. Absent in blobs
        /// written before the v2 contribution engine; decoder falls
        /// back to 0.4 / "sample".
        var vocoderGainLinear: Double
        var appModeRaw: String
        /// Top-level tab (D-022). Absent in blobs written before the
        /// five-tab shell; decoder migrates the legacy D-019
        /// `playSurfaceRaw` key (chordPads → jam) and otherwise falls
        /// back to "contribute". Encoder writes only this key.
        var appTabRaw: String
        /// Last contribute-family mode (redesign Phase 7). Absent in
        /// earlier blobs; decoder falls back to "sample".
        var lastContributeModeRaw: String
        /// Instrument mode synth preset id (D-022 Phase 8).
        var instrumentPresetId: String
        /// Instrument mode octave shift -3...+3 (D-022 Phase 8).
        var instrumentOctaveShift: Int
        /// Instrument mode brightness 0.5...2.0 (D-022 Phase 8).
        var instrumentBrightness: Double
        /// Hidden pack pads, keyed by "packId#padIdx".
        var hiddenPadKeys: [String]
        /// Route generic MIDI pads to sample pack pads. Absent in older
        /// blobs; decoder falls back to false (synth routing).
        var midiPadsToSamples: Bool

        static let defaults = Persisted(
            storeVersion: 1,
            currentPackId: "starter",
            quantizeMode: .off,
            holdMode: .hold,
            beatBarMode: .beat,
            sectionGatesBySong: [:],
            layerFaderDb: 0,
            padEffectsByKey: [:],
            voiceGainLinear: SampleSettingsStore.defaultVoiceGain,
            chopGainLinear: SampleSettingsStore.defaultChopGain,
            vocoderGainLinear: SampleSettingsStore.defaultVocoderGain,
            appModeRaw: SampleSettingsStore.defaultAppModeRaw,
            appTabRaw: SampleSettingsStore.defaultAppTabRaw,
            lastContributeModeRaw: SampleSettingsStore.defaultLastContributeModeRaw,
            instrumentPresetId: SampleSettingsStore.defaultInstrumentPresetId,
            instrumentOctaveShift: SampleSettingsStore.defaultInstrumentOctaveShift,
            instrumentBrightness: SampleSettingsStore.defaultInstrumentBrightness,
            hiddenPadKeys: [],
            midiPadsToSamples: SampleSettingsStore.defaultMidiPadsToSamples
        )

        // Custom decoding so pre-6d blobs that lack the newer keys
        // still decode cleanly.
        private enum CodingKeys: String, CodingKey {
            case storeVersion, currentPackId, quantizeMode, holdMode,
                 beatBarMode, sectionGatesBySong, layerFaderDb,
                 padEffectsByKey, voiceGainLinear, chopGainLinear,
                 vocoderGainLinear, appModeRaw, appTabRaw,
                 lastContributeModeRaw, instrumentPresetId,
                 instrumentOctaveShift, instrumentBrightness,
                 hiddenPadKeys, midiPadsToSamples
        }

        /// Decode-only key from the D-019 blob shape. Kept out of
        /// CodingKeys so the synthesized encoder never re-writes it.
        private enum LegacyCodingKeys: String, CodingKey {
            case playSurfaceRaw
        }

        init(
            storeVersion: Int,
            currentPackId: String,
            quantizeMode: QuantizeMode,
            holdMode: HoldMode,
            beatBarMode: BeatBarMode,
            sectionGatesBySong: [String: [String]],
            layerFaderDb: Double,
            padEffectsByKey: [String: SamplePadEffects],
            voiceGainLinear: Double,
            chopGainLinear: Double,
            vocoderGainLinear: Double,
            appModeRaw: String,
            appTabRaw: String,
            lastContributeModeRaw: String,
            instrumentPresetId: String,
            instrumentOctaveShift: Int,
            instrumentBrightness: Double,
            hiddenPadKeys: [String] = [],
            midiPadsToSamples: Bool = SampleSettingsStore.defaultMidiPadsToSamples
        ) {
            self.storeVersion = storeVersion
            self.currentPackId = currentPackId
            self.quantizeMode = quantizeMode
            self.holdMode = holdMode
            self.beatBarMode = beatBarMode
            self.sectionGatesBySong = sectionGatesBySong
            self.layerFaderDb = layerFaderDb
            self.padEffectsByKey = padEffectsByKey
            self.voiceGainLinear = voiceGainLinear
            self.chopGainLinear = chopGainLinear
            self.vocoderGainLinear = vocoderGainLinear
            self.appModeRaw = appModeRaw
            self.appTabRaw = appTabRaw
            self.lastContributeModeRaw = lastContributeModeRaw
            self.instrumentPresetId = instrumentPresetId
            self.instrumentOctaveShift = instrumentOctaveShift
            self.instrumentBrightness = instrumentBrightness
            self.hiddenPadKeys = hiddenPadKeys
            self.midiPadsToSamples = midiPadsToSamples
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            self.storeVersion = try c.decode(Int.self, forKey: .storeVersion)
            self.currentPackId = try c.decode(String.self, forKey: .currentPackId)
            self.quantizeMode = try c.decode(QuantizeMode.self, forKey: .quantizeMode)
            self.holdMode = try c.decode(HoldMode.self, forKey: .holdMode)
            self.beatBarMode = try c.decode(BeatBarMode.self, forKey: .beatBarMode)
            self.sectionGatesBySong = try c.decode([String: [String]].self, forKey: .sectionGatesBySong)
            self.layerFaderDb = try c.decode(Double.self, forKey: .layerFaderDb)
            self.padEffectsByKey = try c.decodeIfPresent(
                [String: SamplePadEffects].self, forKey: .padEffectsByKey
            ) ?? [:]
            self.voiceGainLinear = try c.decodeIfPresent(
                Double.self, forKey: .voiceGainLinear
            ) ?? SampleSettingsStore.defaultVoiceGain
            self.chopGainLinear = try c.decodeIfPresent(
                Double.self, forKey: .chopGainLinear
            ) ?? SampleSettingsStore.defaultChopGain
            self.vocoderGainLinear = try c.decodeIfPresent(
                Double.self, forKey: .vocoderGainLinear
            ) ?? SampleSettingsStore.defaultVocoderGain
            self.appModeRaw = try c.decodeIfPresent(
                String.self, forKey: .appModeRaw
            ) ?? SampleSettingsStore.defaultAppModeRaw
            if let tab = try c.decodeIfPresent(String.self, forKey: .appTabRaw) {
                self.appTabRaw = tab
            } else {
                // One-time D-019 → D-022 migration: read the legacy
                // surface key (chordPads folds into jam); the next
                // save() re-writes the blob with appTabRaw only.
                let legacy = try decoder.container(keyedBy: LegacyCodingKeys.self)
                self.appTabRaw = AppTab.migratedRaw(
                    fromLegacyPlaySurface: try legacy.decodeIfPresent(
                        String.self, forKey: .playSurfaceRaw
                    )
                )
            }
            self.lastContributeModeRaw = try c.decodeIfPresent(
                String.self, forKey: .lastContributeModeRaw
            ) ?? SampleSettingsStore.defaultLastContributeModeRaw
            self.instrumentPresetId = try c.decodeIfPresent(
                String.self, forKey: .instrumentPresetId
            ) ?? SampleSettingsStore.defaultInstrumentPresetId
            self.instrumentOctaveShift = try c.decodeIfPresent(
                Int.self, forKey: .instrumentOctaveShift
            ) ?? SampleSettingsStore.defaultInstrumentOctaveShift
            self.instrumentBrightness = try c.decodeIfPresent(
                Double.self, forKey: .instrumentBrightness
            ) ?? SampleSettingsStore.defaultInstrumentBrightness
            self.hiddenPadKeys = try c.decodeIfPresent(
                [String].self, forKey: .hiddenPadKeys
            ) ?? []
            self.midiPadsToSamples = try c.decodeIfPresent(
                Bool.self, forKey: .midiPadsToSamples
            ) ?? SampleSettingsStore.defaultMidiPadsToSamples
        }
    }

    /// Read + decode the blob. Returns `nil` for absent key or any
    /// decode error — the caller substitutes defaults so a corrupted
    /// blob can never block init.
    private static func load(from defaults: UserDefaults) -> Persisted? {
        guard let data = defaults.data(forKey: defaultsKey) else { return nil }
        return try? JSONDecoder().decode(Persisted.self, from: data)
    }

    /// Encode + write the current field values. Errors are swallowed
    /// silently — this is best-effort UI state; a failed write means
    /// the next launch will fall back to defaults which is acceptable.
    private func save() {
        let clampedDb = max(-60, min(6, layerFaderDb))
        let clampedEffects = padEffectsByKey.mapValues { $0.clamped() }
        let payload = Persisted(
            storeVersion: 1,
            currentPackId: currentPackId,
            quantizeMode: quantizeMode,
            holdMode: holdMode,
            beatBarMode: beatBarMode,
            sectionGatesBySong: sectionGatesBySong.mapValues { Array($0).sorted() },
            layerFaderDb: clampedDb,
            padEffectsByKey: clampedEffects,
            voiceGainLinear: max(0, min(1, voiceGainLinear)),
            chopGainLinear: max(0, min(1, chopGainLinear)),
            vocoderGainLinear: max(0, min(1, vocoderGainLinear)),
            appModeRaw: appModeRaw,
            appTabRaw: appTabRaw,
            lastContributeModeRaw: lastContributeModeRaw,
            instrumentPresetId: instrumentPresetId,
            instrumentOctaveShift: max(-3, min(3, instrumentOctaveShift)),
            instrumentBrightness: max(0.5, min(2.0, instrumentBrightness)),
            hiddenPadKeys: Array(hiddenPadKeys).sorted(),
            midiPadsToSamples: midiPadsToSamples
        )
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
