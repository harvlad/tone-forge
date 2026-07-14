// PacksModel.swift
//
// Curated sample-pack browser state (iOS parity P5): catalog fetch,
// pack download with progress, cache inventory via SampleBank, and
// pack activation. Pure logic — playback is the audio layer's job;
// SessionController subscribes to onPackActivated and registers the
// resolved pack with PackPadPlayer.
//
// The provider seam wraps ToneForgeEngine.PackClient so tests drive
// the model with scripted catalogs and instantly-complete downloads.

import Foundation
import Observation
import ToneForgeEngine

/// The slice of PackClient the model needs.
public protocol PackCatalogProviding: Sendable {
    func fetchCatalog(baseURL: URL) async throws -> [SamplePackCatalogEntry]
    func download(
        baseURL: URL, packId: String, cacheRoot: URL
    ) -> AsyncThrowingStream<PackDownloadProgress, Error>
}

/// Production provider — ToneForgeEngine's PackClient verbatim.
public struct BackendPackProvider: PackCatalogProviding {
    private let client = PackClient()

    public init() {}

    public func fetchCatalog(
        baseURL: URL
    ) async throws -> [SamplePackCatalogEntry] {
        try await client.fetchCatalog(baseURL: baseURL)
    }

    public func download(
        baseURL: URL, packId: String, cacheRoot: URL
    ) -> AsyncThrowingStream<PackDownloadProgress, Error> {
        client.download(baseURL: baseURL, packId: packId, cacheRoot: cacheRoot)
    }
}

@Observable
@MainActor
public final class PacksModel {

    // MARK: - State

    public private(set) var entries: [SamplePackCatalogEntry] = []
    public private(set) var isLoading = false
    public private(set) var errorMessage: String?

    /// Pack ids fully cached on disk (loadable without network).
    public private(set) var cachedPackIds: Set<String> = []

    /// In-flight download progress keyed by packId.
    public private(set) var downloading: [String: PackDownloadProgress] = [:]

    /// The pack whose pads the trigger grid currently shows.
    public private(set) var activePack: ResolvedSamplePack?

    /// Fired after a pack resolves from cache (activation). The audio
    /// layer registers the pad file URLs with its player here.
    @ObservationIgnored public var onPackActivated: ((ResolvedSamplePack) -> Void)?

    // MARK: - Deps

    @ObservationIgnored private let provider: any PackCatalogProviding
    @ObservationIgnored private let bank: SampleBank
    @ObservationIgnored private var downloadTasks: [String: Task<Void, Never>] = [:]

    /// - Parameter cacheRoot: override for tests; production uses the
    ///   standard caches/toneforge/packs tree so PackClient downloads
    ///   land where SampleBank.loadCached reads.
    public init(
        provider: any PackCatalogProviding = BackendPackProvider(),
        cacheRoot: URL? = nil
    ) {
        self.provider = provider
        if let cacheRoot {
            self.bank = SampleBank(
                bundleResourcesRoot: nil, cachedPacksRoot: cacheRoot)
        } else {
            self.bank = (try? SampleBank.defaultBank()) ?? SampleBank(
                bundleResourcesRoot: nil,
                cachedPacksRoot: FileManager.default.temporaryDirectory
                    .appendingPathComponent("toneforge-packs", isDirectory: true)
            )
        }
        refreshCached()
    }

    // MARK: - Catalog

    public func loadCatalog(baseURL: URL) async {
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            entries = try await provider.fetchCatalog(baseURL: baseURL)
        } catch {
            errorMessage = error.localizedDescription
        }
        refreshCached()
    }

    /// Re-scan the on-disk cache inventory.
    public func refreshCached() {
        cachedPackIds = Set(bank.listCachedPackIds())
    }

    public func isCached(_ packId: String) -> Bool {
        cachedPackIds.contains(packId)
    }

    public func isDownloading(_ packId: String) -> Bool {
        downloadTasks[packId] != nil
    }

    // MARK: - Download

    /// Start (or ignore, if already running) a pack download. Progress
    /// lands in `downloading[packId]`; on the terminal event the pack
    /// joins `cachedPackIds` and auto-activates.
    public func download(baseURL: URL, packId: String) {
        guard downloadTasks[packId] == nil else { return }
        errorMessage = nil
        let stream = provider.download(
            baseURL: baseURL, packId: packId,
            cacheRoot: bank.cachedPacksRoot
        )
        downloadTasks[packId] = Task { [weak self] in
            do {
                for try await progress in stream {
                    guard let self else { return }
                    self.downloading[packId] = progress
                    if progress.isComplete {
                        self.refreshCached()
                        self.activate(packId: packId)
                    }
                }
            } catch {
                self?.errorMessage = error.localizedDescription
            }
            guard let self else { return }
            self.downloading.removeValue(forKey: packId)
            self.downloadTasks.removeValue(forKey: packId)
        }
    }

    // MARK: - Activation

    /// Resolve a cached pack and make it the live trigger grid.
    public func activate(packId: String) {
        do {
            let resolved = try bank.loadCached(packId: packId)
            activePack = resolved
            onPackActivated?(resolved)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    public func deactivate() {
        activePack = nil
    }
}
