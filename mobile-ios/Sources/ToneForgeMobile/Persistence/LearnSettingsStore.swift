// LearnSettingsStore.swift
//
// UserDefaults-backed persistence for the Learn tab's cross-session
// settings (D-022). Same single-JSON-blob pattern as
// SketchSettingsStore: `didSet { save() }`, wrong-shape blobs
// replaced with defaults on next write, `decodeIfPresent` for fields
// added after v1.
//
// Persisted fields:
//   - practiceRateX: playback speed multiplier, 0.5–1.0, default 1.0.
//     Applied while the Learn tab is active; every other tab runs at
//     1.0 (AppState.applySelectedTab restores it).

import Foundation

@MainActor
public final class LearnSettingsStore: ObservableObject {

    // MARK: - Published (auto-saved on change)

    /// Practice playback rate. Clamped to `rateRange` on save.
    @Published public var practiceRateX: Double {
        didSet { save() }
    }

    /// Valid practice-rate range (0.5x–1.0x). Above 1.0 is out of
    /// scope: speeding UP a song is not a practice aid and the
    /// schedulers' look-ahead windows assume rate ≤ 1.
    nonisolated public static let rateRange: ClosedRange<Double> = 0.5...1.0

    // MARK: - Init

    private static let defaultsKey = "toneforge.learnSettings"

    /// Injectable for tests; production callers use the no-arg init.
    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        let loaded = Self.load(from: defaults) ?? Persisted.defaults
        self.practiceRateX = loaded.practiceRateX
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var practiceRateX: Double

        static let defaults = Persisted(storeVersion: 1, practiceRateX: 1.0)

        private enum CodingKeys: String, CodingKey {
            case storeVersion, practiceRateX
        }

        init(storeVersion: Int, practiceRateX: Double) {
            self.storeVersion = storeVersion
            self.practiceRateX = practiceRateX
        }

        // decodeIfPresent everywhere so fields added in later phases
        // never brick a v1 blob.
        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            let d = Persisted.defaults
            self.storeVersion = try c.decodeIfPresent(Int.self, forKey: .storeVersion) ?? d.storeVersion
            self.practiceRateX = try c.decodeIfPresent(Double.self, forKey: .practiceRateX) ?? d.practiceRateX
        }
    }

    private static func load(from defaults: UserDefaults) -> Persisted? {
        guard let data = defaults.data(forKey: defaultsKey) else { return nil }
        return try? JSONDecoder().decode(Persisted.self, from: data)
    }

    private func save() {
        let clamped = min(
            max(practiceRateX, Self.rateRange.lowerBound),
            Self.rateRange.upperBound
        )
        let payload = Persisted(storeVersion: 1, practiceRateX: clamped)
        if let data = try? JSONEncoder().encode(payload) {
            defaults.set(data, forKey: Self.defaultsKey)
        }
    }
}
