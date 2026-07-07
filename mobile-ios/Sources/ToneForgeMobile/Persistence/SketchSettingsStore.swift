// SketchSettingsStore.swift
//
// UserDefaults-backed persistence for the Sketch tab's cross-session
// settings. Same single-JSON-blob pattern as SampleSettingsStore:
// `didSet { save() }` on every field, wrong-shape blobs replaced with
// defaults on next write, `decodeIfPresent` for fields added after v1.
//
// Persisted fields (see plan "Data + persistence" section):
//   - tempoBpm:         sketch tempo, 60–200, default 120
//   - timeSigNumerator: 3 (3/4), 4 (4/4) or 6 (6/8), default 4
//   - metronomeEnabled: click on/off, default on
//   - quantizeMode:     Sketch's own quantize policy — deliberately
//                       separate from SampleSettingsStore.quantizeMode
//                       so the Contribute > Samples panel keeps its
//                       own persisted setting (per-mode quantize,
//                       option (a) in the plan's watchpoints)
//   - lastSketchPackId: pack last activated from the Sketch tab

import Foundation
import ToneForgeEngine

@MainActor
public final class SketchSettingsStore: ObservableObject {

    // MARK: - Published (auto-saved on change)

    /// Sketch tempo in BPM. Drives the Quantizer's synthetic grid and
    /// (Phase 2) the metronome click interval. Clamped to `bpmRange`
    /// on save.
    @Published public var tempoBpm: Double {
        didSet { save() }
    }

    /// Time-signature numerator: 3 (3/4), 4 (4/4) or 6 (6/8). Only
    /// the numerator is persisted — the UI maps it to the display
    /// string and the metronome derives its accent cycle from it.
    @Published public var timeSigNumerator: Int {
        didSet { save() }
    }

    /// Whether the metronome clicks during sketch playback/record.
    @Published public var metronomeEnabled: Bool {
        didSet { save() }
    }

    /// Whether arming a sketch recording runs a 1-bar count-in (the
    /// transport plays a negative-time lead bar of clicks before
    /// content starts at song-time 0).
    @Published public var countInEnabled: Bool {
        didSet { save() }
    }

    /// Sketch-mode quantize policy. Applied to the SampleScheduler
    /// only while the Sketch tab is active; the Samples panel's
    /// `SampleSettingsStore.quantizeMode` is untouched.
    @Published public var quantizeMode: QuantizeMode {
        didSet { save() }
    }

    /// Pack last activated while the Sketch tab was active. Written
    /// by `AppState.activateSamplePack`; read for display/metadata
    /// (the active pack itself is shared app-wide via
    /// `SampleSettingsStore.currentPackId`).
    @Published public var lastSketchPackId: String {
        didSet { save() }
    }

    /// Valid BPM range for the TempoStrip stepper.
    nonisolated public static let bpmRange: ClosedRange<Double> = 60...200
    /// Supported time-signature numerators, in display order.
    nonisolated public static let timeSigOptions: [Int] = [3, 4, 6]

    /// Display string for a numerator ("3/4", "4/4", "6/8").
    nonisolated public static func timeSigLabel(_ numerator: Int) -> String {
        numerator == 6 ? "6/8" : "\(numerator)/4"
    }

    // MARK: - Init

    private static let defaultsKey = "toneforge.sketchSettings"

    /// Injectable for tests; production callers use the no-arg init.
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let loaded = Self.load(from: defaults) ?? Persisted.defaults
        self.tempoBpm = loaded.tempoBpm
        self.timeSigNumerator = loaded.timeSigNumerator
        self.metronomeEnabled = loaded.metronomeEnabled
        self.countInEnabled = loaded.countInEnabled
        self.quantizeMode = loaded.quantizeMode
        self.lastSketchPackId = loaded.lastSketchPackId
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var tempoBpm: Double
        var timeSigNumerator: Int
        var metronomeEnabled: Bool
        var countInEnabled: Bool
        var quantizeMode: QuantizeMode
        var lastSketchPackId: String

        static let defaults = Persisted(
            storeVersion: 1,
            tempoBpm: 120,
            timeSigNumerator: 4,
            metronomeEnabled: true,
            countInEnabled: true,
            quantizeMode: .off,
            lastSketchPackId: "starter"
        )

        private enum CodingKeys: String, CodingKey {
            case storeVersion, tempoBpm, timeSigNumerator,
                 metronomeEnabled, countInEnabled, quantizeMode,
                 lastSketchPackId
        }

        init(
            storeVersion: Int,
            tempoBpm: Double,
            timeSigNumerator: Int,
            metronomeEnabled: Bool,
            countInEnabled: Bool,
            quantizeMode: QuantizeMode,
            lastSketchPackId: String
        ) {
            self.storeVersion = storeVersion
            self.tempoBpm = tempoBpm
            self.timeSigNumerator = timeSigNumerator
            self.metronomeEnabled = metronomeEnabled
            self.countInEnabled = countInEnabled
            self.quantizeMode = quantizeMode
            self.lastSketchPackId = lastSketchPackId
        }

        // decodeIfPresent everywhere so fields added in later phases
        // never brick a v1 blob.
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            let d = Persisted.defaults
            self.storeVersion = try c.decodeIfPresent(Int.self, forKey: .storeVersion) ?? d.storeVersion
            self.tempoBpm = try c.decodeIfPresent(Double.self, forKey: .tempoBpm) ?? d.tempoBpm
            self.timeSigNumerator = try c.decodeIfPresent(Int.self, forKey: .timeSigNumerator) ?? d.timeSigNumerator
            self.metronomeEnabled = try c.decodeIfPresent(Bool.self, forKey: .metronomeEnabled) ?? d.metronomeEnabled
            self.countInEnabled = try c.decodeIfPresent(Bool.self, forKey: .countInEnabled) ?? d.countInEnabled
            self.quantizeMode = try c.decodeIfPresent(QuantizeMode.self, forKey: .quantizeMode) ?? d.quantizeMode
            self.lastSketchPackId = try c.decodeIfPresent(String.self, forKey: .lastSketchPackId) ?? d.lastSketchPackId
        }
    }

    private static func load(from defaults: UserDefaults) -> Persisted? {
        guard let data = defaults.data(forKey: defaultsKey) else { return nil }
        return try? JSONDecoder().decode(Persisted.self, from: data)
    }

    private func save() {
        let clampedBpm = min(max(tempoBpm, Self.bpmRange.lowerBound), Self.bpmRange.upperBound)
        let numerator = Self.timeSigOptions.contains(timeSigNumerator) ? timeSigNumerator : 4
        let payload = Persisted(
            storeVersion: 1,
            tempoBpm: clampedBpm,
            timeSigNumerator: numerator,
            metronomeEnabled: metronomeEnabled,
            countInEnabled: countInEnabled,
            quantizeMode: quantizeMode,
            lastSketchPackId: lastSketchPackId
        )
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
