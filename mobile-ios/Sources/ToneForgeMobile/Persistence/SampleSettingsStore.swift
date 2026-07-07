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

    /// Persisted PlaySurface raw value (`PlaySurface(rawValue:)`), so
    /// the Play tab reopens on the surface the user last used (D-018
    /// surface switcher). Unknown values fall back to `"contribute"`
    /// at the view layer when decoding.
    @Published public var playSurfaceRaw: String {
        didSet { save() }
    }

    /// Built-in defaults, shared by init and the tests.
    nonisolated public static let defaultVoiceGain: Double = 0.9
    nonisolated public static let defaultChopGain: Double = 0.55
    nonisolated public static let defaultVocoderGain: Double = 0.4
    nonisolated public static let defaultAppModeRaw: String = "sample"
    nonisolated public static let defaultPlaySurfaceRaw: String = "contribute"

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
        self.playSurfaceRaw = loaded.playSurfaceRaw
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
        /// Play-tab surface (D-018). Absent in blobs written before
        /// the redesign; decoder falls back to "contribute".
        var playSurfaceRaw: String

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
            playSurfaceRaw: SampleSettingsStore.defaultPlaySurfaceRaw
        )

        // Custom decoding so pre-6d blobs that lack the newer keys
        // still decode cleanly.
        private enum CodingKeys: String, CodingKey {
            case storeVersion, currentPackId, quantizeMode, holdMode,
                 beatBarMode, sectionGatesBySong, layerFaderDb,
                 padEffectsByKey, voiceGainLinear, chopGainLinear,
                 vocoderGainLinear, appModeRaw, playSurfaceRaw
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
            playSurfaceRaw: String
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
            self.playSurfaceRaw = playSurfaceRaw
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
            self.playSurfaceRaw = try c.decodeIfPresent(
                String.self, forKey: .playSurfaceRaw
            ) ?? SampleSettingsStore.defaultPlaySurfaceRaw
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
            playSurfaceRaw: playSurfaceRaw
        )
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
