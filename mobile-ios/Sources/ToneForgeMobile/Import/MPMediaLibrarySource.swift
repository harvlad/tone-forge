// MPMediaLibrarySource.swift
//
// MediaPlayer-backed implementation of MediaLibrarySource.
//
// Thin adapter only: maps MPMediaItem → LibraryTrack. All
// analysability policy (DRM filter, not-downloaded labels) lives on
// LibraryTrack in ToneForgeEngine where it's unit-tested.

import Foundation
import ToneForgeEngine
#if os(iOS)
import MediaPlayer
import UIKit

public final class MPMediaLibrarySource: MediaLibrarySource {

    public init() {}

    public var authorization: MediaLibraryAuthorization {
        Self.map(MPMediaLibrary.authorizationStatus())
    }

    public func requestAuthorization() async -> MediaLibraryAuthorization {
        await withCheckedContinuation { continuation in
            MPMediaLibrary.requestAuthorization { status in
                continuation.resume(returning: Self.map(status))
            }
        }
    }

    public func allTracks() -> [LibraryTrack] {
        let items = MPMediaQuery.songs().items ?? []
        return items.map { item in
            LibraryTrack(
                id: String(item.persistentID),
                title: item.title ?? "Untitled",
                artist: item.artist ?? "Unknown artist",
                durationSec: item.playbackDuration,
                isProtected: item.hasProtectedAsset,
                assetURL: item.assetURL
            )
        }
    }

    /// Artwork thumbnail for a track, if any. Adapter-side extra —
    /// not part of the MediaLibrarySource protocol (UIImage isn't
    /// platform-neutral).
    public func artwork(forTrackId id: String, size: CGSize) -> UIImage? {
        guard let persistentID = UInt64(id) else { return nil }
        let query = MPMediaQuery.songs()
        query.addFilterPredicate(
            MPMediaPropertyPredicate(
                value: NSNumber(value: persistentID),
                forProperty: MPMediaItemPropertyPersistentID
            )
        )
        return query.items?.first?.artwork?.image(at: size)
    }

    private static func map(_ status: MPMediaLibraryAuthorizationStatus) -> MediaLibraryAuthorization {
        switch status {
        case .notDetermined: return .notDetermined
        case .denied: return .denied
        case .restricted: return .restricted
        case .authorized: return .authorized
        @unknown default: return .denied
        }
    }
}

#endif
