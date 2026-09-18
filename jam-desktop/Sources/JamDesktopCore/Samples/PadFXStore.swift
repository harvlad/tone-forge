// PadFXStore.swift
//
// Per-pad effects overrides — the desktop twin of the FX half of iOS
// SampleSettingsStore. Keys are the SAME cross-surface form iOS
// persists into Project snapshots ("packId#padIdx", the pack pad's
// own index, never a grid slot), so a workspace saved on iPhone
// resolves its FX here byte-for-byte and vice versa.
//
// Persisted as one JSON file under Application Support/Jamn/ (not
// UserDefaults) because Projects swap the WHOLE map on load/reset —
// a file the ProjectStateBridge replaces wholesale keeps the store's
// on-disk state and the snapshot's padFX field trivially in sync.
//
// Desktop ships no FX editor yet: entries arrive via restored Project
// snapshots (typically authored on iOS) and are consulted at trigger
// time by SessionController (ChopPlayer applies the actual chain —
// its applyEffects is already the full SamplePadEffects DSP).

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class PadFXStore {

    /// "packId#padIdx" → effects override. Values are stored clamped.
    public private(set) var effectsByKey: [String: SamplePadEffects] = [:]

    /// Fired after any mutation persists (Project auto-save hook).
    @ObservationIgnored public var onChanged: (() -> Void)?

    @ObservationIgnored private let root: URL?
    @ObservationIgnored private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   Application Support/Jamn.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
        self.effectsByKey = (try? load()) ?? [:]
    }

    // MARK: - Keys

    /// The canonical cross-surface key form (iOS
    /// SampleSettingsStore.padEffectsKey twin).
    public static func key(packId: String, padIdx: Int) -> String {
        "\(packId)#\(padIdx)"
    }

    // MARK: - Queries

    /// User override for `(packId, padIdx)`, or nil (play dry).
    public func effects(packId: String, padIdx: Int) -> SamplePadEffects? {
        effectsByKey[Self.key(packId: packId, padIdx: padIdx)]
    }

    // MARK: - Mutations

    /// Set (or clear, with nil) one pad's override. Persists.
    public func setEffects(
        _ effects: SamplePadEffects?, packId: String, padIdx: Int
    ) {
        let key = Self.key(packId: packId, padIdx: padIdx)
        if let effects {
            effectsByKey[key] = effects.clamped()
        } else {
            effectsByKey.removeValue(forKey: key)
        }
        persist()
    }

    /// Replace the whole map (Project restore / reset). Whole-value by
    /// design: a workspace is the complete FX state, not a patch.
    public func replaceAll(_ next: [String: SamplePadEffects]) {
        effectsByKey = next.mapValues { $0.clamped() }
        persist()
    }

    // MARK: - Persistence

    private struct Persisted: Codable {
        var storeVersion: Int
        var effectsByKey: [String: SamplePadEffects]
    }

    private func fileURL() throws -> URL {
        let base: URL
        if let root {
            base = root
        } else {
            base = try fileManager.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            ).appendingPathComponent("Jamn", isDirectory: true)
        }
        try fileManager.createDirectory(
            at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("padFX.json")
    }

    private func load() throws -> [String: SamplePadEffects] {
        let data = try Data(contentsOf: fileURL())
        return try JSONDecoder()
            .decode(Persisted.self, from: data).effectsByKey
    }

    private func persist() {
        do {
            let payload = Persisted(storeVersion: 1, effectsByKey: effectsByKey)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(payload)
            try data.write(to: fileURL(), options: .atomic)
        } catch {
            NSLog("[PadFXStore] persist failed: %@", error.localizedDescription)
        }
        onChanged?()
    }
}
