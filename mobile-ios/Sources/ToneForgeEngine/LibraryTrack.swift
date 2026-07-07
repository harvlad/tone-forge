// LibraryTrack.swift
//
// Platform-neutral model of a track in the user's local music
// library, plus the source protocol the import UI consumes.
//
// The analysability policy lives HERE (pure Swift, unit-testable),
// not in the MediaPlayer adapter:
//
//   - DRM-protected items (Apple Music streaming catalogue) are never
//     analysable — we only ingest audio the user actually owns.
//   - Items without a local asset URL (undownloaded iCloud items) are
//     not analysable either, but get a distinct label so users aren't
//     told their own purchased music is "DRM".
//
// No streaming-service ingestion of any kind exists in this app.

import Foundation

public struct LibraryTrack: Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let artist: String
    public let durationSec: Double
    /// True for DRM-protected items (`MPMediaItem.hasProtectedAsset`).
    public let isProtected: Bool
    /// Local file URL of the audio asset; nil when the item is not
    /// downloaded to the device.
    public let assetURL: URL?

    public init(
        id: String,
        title: String,
        artist: String,
        durationSec: Double,
        isProtected: Bool,
        assetURL: URL?
    ) {
        self.id = id
        self.title = title
        self.artist = artist
        self.durationSec = durationSec
        self.isProtected = isProtected
        self.assetURL = assetURL
    }

    /// Only unprotected tracks with a local asset can be analysed.
    public var isAnalysable: Bool {
        !isProtected && assetURL != nil
    }

    /// User-facing reason a track can't be analysed; nil when it can.
    public var unavailabilityReason: String? {
        if isProtected { return "streaming (DRM) — not analysable" }
        if assetURL == nil { return "not downloaded — not analysable" }
        return nil
    }
}

public enum MediaLibraryAuthorization: Equatable, Sendable {
    case notDetermined
    case denied
    case restricted
    case authorized
}

/// Abstraction over the device music library so the picker UI and the
/// filtering policy can be tested with a fake source.
public protocol MediaLibrarySource {
    var authorization: MediaLibraryAuthorization { get }
    /// Prompt for access if not yet determined; returns the new state.
    func requestAuthorization() async -> MediaLibraryAuthorization
    /// All songs in the library (any protection state — filtering is
    /// the caller's job via `LibraryTrack.isAnalysable`).
    func allTracks() -> [LibraryTrack]
}
