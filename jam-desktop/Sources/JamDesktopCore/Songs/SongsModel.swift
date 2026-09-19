// SongsModel.swift
//
// Backing model for the full-screen Songs page — the ONE library
// destination that replaces the cramped Recent-Songs sidebar list AND
// the Band Room card stack. Its rows are the UNION of finished analyses
// and in-flight jobs; a completing job COLLAPSES into its history row.
//
// The server (LibrarySource behind /api/library/search) already returns
// that union, sorted-before-paged with an opaque cursor. On top of it we
// overlay the desktop's own live analysis queue (AnalysisQueueModel): a
// just-submitted upload appears INSTANTLY as a processing row before the
// server union has caught up, and a running row's percent updates from
// the fresher SSE stream rather than the last poll. The overlay is a
// PURE function (``merged``) so the collapse/dedupe rules are unit-tested
// without a view.

import Combine
import Foundation

@MainActor
public final class SongsModel: ObservableObject {
    // MARK: - Query state (drives the next search)

    /// Which pluggable catalog is showing. MVP: only `.library` is wired;
    /// the source tabs offer the rest as coming-soon.
    @Published public var source: SourceId = .library
    @Published public var query: String = ""
    @Published public var sort: SongsSort = .recent
    @Published public var filters = SongsFilters()

    // MARK: - Results

    /// Rows from the last first-page/next-page fetch (server union).
    @Published public private(set) var serverTracks: [SourceTrack] = []
    @Published public private(set) var facets: [String: [FacetBucket]] = [:]
    @Published public private(set) var nextCursor: String?
    @Published public private(set) var total: Int?

    @Published public private(set) var isLoading = false
    @Published public private(set) var isLoadingMore = false
    @Published public private(set) var error: String?

    /// Live rows from the desktop's analysis queue, pushed in by the view.
    /// Overlaid onto `serverTracks` for instant/fresh processing status.
    @Published public var liveItems: [AnalysisQueueItem] = []

    private let client: LibrarySearching
    private let pageLimit: Int

    public init(client: LibrarySearching = BackendLibrarySearchClient(), pageLimit: Int = 50) {
        self.client = client
        self.pageLimit = pageLimit
    }

    // MARK: - Derived rows

    /// The rows the table actually renders: server union with the live
    /// analysis queue overlaid (fresh progress on known rows, brand-new
    /// processing rows prepended). Pure — see ``SongsModel/merged``.
    public var displayTracks: [SourceTrack] {
        Self.merged(server: serverTracks, live: liveItems, filters: filters)
    }

    /// How many analyses are still cooking, across BOTH the server union
    /// and the local queue (deduped by merge key). Drives the
    /// "Processing (N)" chip — independent of the current filter so the
    /// count doesn't vanish when you filter to something else.
    public var processingCount: Int {
        var keys = Set<String>()
        for t in serverTracks where t.status.isActive { keys.insert(t.mergeKey) }
        for item in liveItems where item.status.isActive {
            keys.insert(Self.liveKey(item))
        }
        return keys.count
    }

    public var hasMore: Bool { nextCursor != nil }

    // MARK: - Fetching

    /// Load the first page for the current query/filters/sort. Resets the
    /// cursor so a filter change never appends onto stale rows.
    public func reload(baseURL: URL) async {
        isLoading = true
        error = nil
        do {
            let page = try await fetch(baseURL: baseURL, cursor: nil)
            serverTracks = page.tracks
            facets = page.facets
            nextCursor = page.nextCursor
            total = page.total
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    /// Append the next page via the opaque cursor. No-op when there's no
    /// cursor or a load is already in flight.
    public func loadMore(baseURL: URL) async {
        guard let cursor = nextCursor, !isLoading, !isLoadingMore else { return }
        isLoadingMore = true
        do {
            let page = try await fetch(baseURL: baseURL, cursor: cursor)
            // Dedupe by merge key: a background ingest could surface a row
            // that's already on-screen. Opaque cursor keeps the window
            // stable, but belt-and-suspenders against overlap.
            let known = Set(serverTracks.map(\.mergeKey))
            serverTracks.append(contentsOf: page.tracks.filter { !known.contains($0.mergeKey) })
            nextCursor = page.nextCursor
            total = page.total
        } catch {
            self.error = error.localizedDescription
        }
        isLoadingMore = false
    }

    private func fetch(baseURL: URL, cursor: String?) async throws -> SearchPage {
        try await client.search(
            baseURL: baseURL,
            source: source,
            query: query.trimmingCharacters(in: .whitespaces),
            filters: filters,
            sort: sort,
            cursor: cursor,
            limit: pageLimit
        )
    }

    /// Re-add a track through the one ingest door (dedupe reuses an
    /// already-analyzed track). Returns the result so the caller can open
    /// a dedupe-hit history id or watch a new job id.
    @discardableResult
    public func ingest(baseURL: URL, track: SourceTrack) async -> IngestResult? {
        do {
            return try await client.ingest(
                baseURL: baseURL, source: track.source, sourceRef: track.sourceRef)
        } catch {
            self.error = error.localizedDescription
            return nil
        }
    }

    // MARK: - Filter mutations (each is a fresh first page)

    public func setStatusFilter(_ status: String?, baseURL: URL) {
        filters.status = status
        Task { await reload(baseURL: baseURL) }
    }

    /// Toggle a facet bucket: selecting the already-selected value clears
    /// it (a second tap = "show all" for that facet).
    public func toggleFacet(field: String, value: String, baseURL: URL) {
        switch field {
        case "genre": filters.genre = (filters.genre == value) ? nil : value
        case "key": filters.key = (filters.key == value) ? nil : value
        case "mood": filters.mood = (filters.mood == value) ? nil : value
        case "status": filters.status = (filters.status == value) ? nil : value
        case "tags":
            if let idx = filters.tags.firstIndex(of: value) {
                filters.tags.remove(at: idx)
            } else {
                filters.tags.append(value)
            }
        default:
            break
        }
        Task { await reload(baseURL: baseURL) }
    }

    public func setTempoRange(min: Double?, max: Double?, baseURL: URL) {
        filters.tempoMin = min
        filters.tempoMax = max
        Task { await reload(baseURL: baseURL) }
    }

    public func setSort(_ sort: SongsSort, baseURL: URL) {
        self.sort = sort
        Task { await reload(baseURL: baseURL) }
    }

    public func clearFilters(baseURL: URL) {
        filters = SongsFilters()
        query = ""
        Task { await reload(baseURL: baseURL) }
    }

    public func isFacetSelected(field: String, value: String) -> Bool {
        switch field {
        case "genre": return filters.genre == value
        case "key": return filters.key == value
        case "mood": return filters.mood == value
        case "status": return filters.status == value
        case "tags": return filters.tags.contains(value)
        default: return false
        }
    }

    // MARK: - Pure merge (unit-tested; no view, no network)

    /// Merge-key for a live queue item: a completed item keys on its
    /// history id (converging with the server's history row); an active
    /// item keys on its job id (else its local id). Public so the view can
    /// map a table row back to its queue item (Dismiss).
    public static func liveKey(_ item: AnalysisQueueItem) -> String {
        if case let .done(historyId) = item.status { return historyId }
        return item.jobId ?? item.id
    }

    /// Overlay the live analysis queue onto the server union:
    ///  * an ACTIVE live item whose key matches a server row replaces that
    ///    row's status/progress (fresher SSE) — the collapse point;
    ///  * an ACTIVE live item with NO server row is prepended as a fresh
    ///    processing row (instant upload feedback) — unless a metadata
    ///    facet is active (a metadata-less processing row would be filtered
    ///    out server-side too), or the status filter excludes processing;
    ///  * DONE/errored live items defer to the server row (full metadata);
    ///    a refresh (onJobCompleted) pulls a not-yet-surfaced finish in.
    static func merged(
        server: [SourceTrack],
        live: [AnalysisQueueItem],
        filters: SongsFilters
    ) -> [SourceTrack] {
        // Index active live items by merge key.
        var liveByKey: [String: AnalysisQueueItem] = [:]
        for item in live where item.status.isActive {
            liveByKey[liveKey(item)] = item
        }

        let serverKeys = Set(server.map(\.mergeKey))

        // Overlay onto existing server rows.
        let overlaid: [SourceTrack] = server.map { track in
            guard let item = liveByKey[track.mergeKey] else { return track }
            var copy = track
            let (status, progress) = liveStatus(item.status)
            copy.status = status
            copy.progress = progress
            return copy
        }

        // A status filter that excludes processing (e.g. "done"/"error")
        // must not resurrect live processing rows the server omitted.
        let statusAllowsProcessing =
            filters.status == nil || filters.status == "" || filters.status == "processing"

        guard !filters.hasMetadataFilter, statusAllowsProcessing else {
            return overlaid
        }

        // Prepend brand-new processing rows the server hasn't surfaced yet,
        // newest-first (live is maintained newest-first).
        let synthetic: [SourceTrack] = live.compactMap { item -> SourceTrack? in
            guard item.status.isActive else { return nil }
            let key = liveKey(item)
            guard !serverKeys.contains(key) else { return nil }
            let (status, progress) = liveStatus(item.status)
            return SourceTrack(
                source: .library,
                sourceRef: key,
                title: item.title,
                status: status,
                progress: progress
            )
        }
        return synthetic + overlaid
    }

    /// QueueItemStatus → (TrackStatus, [0,1] progress).
    static func liveStatus(_ status: QueueItemStatus) -> (TrackStatus, Double?) {
        switch status {
        case .queued:
            return (.queued, nil)
        case let .running(_, percent):
            return (.running, percent.map { min(1, max(0, $0 / 100)) })
        case .done:
            return (.done, 1)
        case .error:
            return (.error, nil)
        }
    }
}
