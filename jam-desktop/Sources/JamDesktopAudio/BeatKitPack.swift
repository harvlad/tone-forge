// BeatKitPack.swift
//
// Resolves the bundled 7-piece `beatkit` percussion pack shipped as a
// SwiftPM resource (Resources/Samples/beatkit) of the JamDesktopAudio
// module. Beat Capture (D-024) registers it with the PackPadPlayer so
// captured drum patterns play back immediately, independent of any
// fronted curated pack.

import Foundation
import ToneForgeEngine

public enum BeatKitPack {

    public static let packId = "beatkit"

    public enum ResolveError: Error, LocalizedError {
        case resourcesMissing

        public var errorDescription: String? {
            switch self {
            case .resourcesMissing:
                return "Bundled beatkit samples are missing from the app resources."
            }
        }
    }

    /// Resolve `beatkit` from the module's `Samples` resource dir into a
    /// `ResolvedSamplePack` ready for `PackPadPlayer.register`.
    public static func resolve() throws -> ResolvedSamplePack {
        guard let samplesRoot = Bundle.module.url(
            forResource: "Samples", withExtension: nil
        ) else {
            throw ResolveError.resourcesMissing
        }
        let bank = SampleBank(
            bundleResourcesRoot: samplesRoot,
            cachedPacksRoot: FileManager.default.temporaryDirectory
                .appendingPathComponent("jamdesktop-beatkit-cache", isDirectory: true)
        )
        return try bank.loadBundled(packId: packId)
    }
}
