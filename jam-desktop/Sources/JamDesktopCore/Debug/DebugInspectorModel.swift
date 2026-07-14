// DebugInspectorModel.swift
//
// Inspector tab state: session picker + loaded bundle + selection +
// tag filter. Mirrors debug.js inspector state; rendering lives in
// the JamDesktop views.

import Foundation
import Observation

@Observable
@MainActor
public final class DebugInspectorModel {
    public private(set) var sessions: [DebugSessionSummary] = []
    public private(set) var currentSessionId: String?
    public private(set) var currentBundle: DebugBundle?
    /// Recomputed once per bundle load so views don't re-run the regex
    /// sweep on every hover/click (debug.js tagSummary cache).
    public private(set) var tagRows: [SectionTagDetector.SectionTagRow] = []
    public var selectedSectionIndex: Int?
    /// nil = show all; else a tag id (barre/colour/jumps/quick).
    public var tagFilter: String?
    public private(set) var isLoading = false
    public private(set) var error: String?

    private let client: DebugFetching

    public init(client: DebugFetching = DebugClient()) {
        self.client = client
    }

    public func loadSessions(baseURL: URL) async {
        isLoading = true
        error = nil
        do {
            sessions = try await client.fetchSessions(baseURL: baseURL)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    public func loadBundle(baseURL: URL, id: String) async {
        guard !id.isEmpty else { return }
        isLoading = true
        error = nil
        do {
            let bundle = try await client.fetchBundle(baseURL: baseURL, id: id)
            currentSessionId = id
            currentBundle = bundle
            selectedSectionIndex = nil
            tagFilter = nil
            tagRows = SectionTagDetector.tagSummary(bundle)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    /// Count of sections carrying each tag, for the filter chips.
    public var tagCounts: [String: Int] {
        var counts: [String: Int] = [:]
        for row in tagRows {
            for tag in row.tags { counts[tag.id, default: 0] += 1 }
        }
        return counts
    }

    /// Whether a section passes the active tag filter.
    public func sectionMatchesFilter(_ index: Int) -> Bool {
        guard let filter = tagFilter else { return true }
        guard index < tagRows.count else { return false }
        return tagRows[index].tags.contains { $0.id == filter }
    }
}
