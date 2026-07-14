// BeatModel.swift
//
// Beat Capture (D-024): locates the bundled Core ML drum classifier
// shipped as a compiled `.mlmodelc` resource of this target. Kept in
// ToneForgeEngine (no Core ML import) so the pure engine owns the
// resource; the actual MLModel load lives in ToneForgeML.

import Foundation

/// Locator for the baseline drum-classifier model bundled with the
/// engine. A fresher downloaded model (see `BeatModelStore`) takes
/// precedence at runtime; this is the always-present fallback.
public enum BeatModel {

    /// Resource base name and extension of the compiled model.
    public static let resourceName = "BeatClassifier"
    public static let resourceExtension = "mlmodelc"

    /// URL of the bundled compiled model, or nil if missing.
    public static func bundledModelURL() -> URL? {
        Bundle.module.url(
            forResource: resourceName, withExtension: resourceExtension
        )
    }
}
