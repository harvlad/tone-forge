// SessionLoader.swift
//
// Loads a playable session: bundle JSON (network-first with local
// cache fallback, then cached for next launch) plus local stem files
// (BundleStore's cache/download pipeline). Everything downstream —
// transport, mixer, ribbon — consumes the resulting LoadedSession.
//
// Fetching goes through the `BundleFetching` seam so tests can stub
// the network without URLProtocol gymnastics; BundleStore already
// supports `rootOverride` for hermetic disk state.

import Foundation
import ToneForgeEngine

/// Seam over BundleLoader.fetch for testability.
public protocol BundleFetching: Sendable {
    func fetch(from backend: URL, analysisId: String) async throws -> SongBundle
}

// BundleLoader is a value type holding only a TimeInterval; the
// @unchecked spelling is just the retroactive-conformance escape
// hatch (Sendable can't be declared cross-module otherwise).
extension BundleLoader: BundleFetching, @unchecked Sendable {}

/// A fully materialized session: decoded bundle + local stem files
/// keyed by stem role.
public struct LoadedSession: Sendable, Equatable {
    public let bundle: SongBundle
    public let stemURLs: [String: URL]

    public init(bundle: SongBundle, stemURLs: [String: URL]) {
        self.bundle = bundle
        self.stemURLs = stemURLs
    }
}

public struct SessionLoader: Sendable {

    public enum LoaderError: Error, Equatable {
        /// A stem finished the download stream without a local URL.
        case stemMissing(role: String)
    }

    private let store: BundleStore
    private let fetcher: BundleFetching

    // BundleLoader's 5s default suits mobile's cache-fallback UX, but
    // the first fetch after a fresh analysis has no cache and the
    // server may still be assembling the bundle — give it headroom.
    public init(store: BundleStore = BundleStore(),
                fetcher: BundleFetching = BundleLoader(timeout: 30)) {
        self.store = store
        self.fetcher = fetcher
    }

    /// Network-first bundle load: a re-analysis may have refreshed the
    /// bundle server-side, so prefer the wire; fall back to the local
    /// copy when offline. Successful fetches are persisted.
    public func loadBundle(analysisId: String, backend: URL) async throws -> SongBundle {
        do {
            let bundle = try await fetcher.fetch(from: backend, analysisId: analysisId)
            try? store.saveBundle(bundle)
            return bundle
        } catch {
            if let cached = try? store.loadBundle(analysisId: analysisId) {
                return cached
            }
            throw error
        }
    }

    /// Ensure every stem with a URL exists locally; returns role →
    /// local file. Cached stems are reused; the rest stream through
    /// BundleStore.download. `onProgress` relays per-stem progress
    /// for the loading UI.
    public func materializeStems(
        bundle: SongBundle,
        backend: URL,
        onProgress: (@Sendable (BundleStore.StemProgress) -> Void)? = nil
    ) async throws -> [String: URL] {
        var urls: [String: URL] = [:]
        var missing = false
        for stem in bundle.stems where stem.url != nil {
            if let cached = store.cachedStem(for: stem, analysisId: bundle.analysisId) {
                urls[stem.role] = cached
            } else {
                missing = true
            }
        }
        if missing {
            for try await progress in store.download(bundle: bundle, baseURL: backend) {
                onProgress?(progress)
                if progress.isComplete, let local = progress.localURL {
                    urls[progress.role] = local
                }
            }
        }
        for stem in bundle.stems where stem.url != nil {
            guard urls[stem.role] != nil else {
                throw LoaderError.stemMissing(role: stem.role)
            }
        }
        return urls
    }

    /// Convenience: bundle + stems in one call.
    public func load(
        analysisId: String,
        backend: URL,
        onProgress: (@Sendable (BundleStore.StemProgress) -> Void)? = nil
    ) async throws -> LoadedSession {
        let bundle = try await loadBundle(analysisId: analysisId, backend: backend)
        let stems = try await materializeStems(
            bundle: bundle, backend: backend, onProgress: onProgress
        )
        return LoadedSession(bundle: bundle, stemURLs: stems)
    }
}
