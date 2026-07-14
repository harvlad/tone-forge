// HistoryModel.swift
//
// Recent-analyses list under the intake card (parity with jam.js's
// history strip). Thin @Observable wrapper over ToneForgeEngine's
// HistoryClient; protocol seam so tests inject canned entries.

import Combine
import Foundation
import ToneForgeEngine

public protocol HistoryFetching: Sendable {
    func fetch(baseURL: URL, query: String?, limit: Int) async throws -> [HistoryEntry]
    func delete(baseURL: URL, entryId: String) async throws
}

/// Production adapter over the engine client.
public struct HistoryClientAdapter: HistoryFetching {
    private let client: HistoryClient

    // HistoryClient's 5s default suits the iOS Library (fail fast when
    // the debug mDNS host is off-LAN) but is too tight for desktop:
    // the backend can take >5s to answer while an analysis worker is
    // uploading results. 30s matches SessionLoader.
    public init(client: HistoryClient = HistoryClient(timeout: 30)) {
        self.client = client
    }

    public func fetch(baseURL: URL, query: String?, limit: Int) async throws -> [HistoryEntry] {
        try await client.fetch(baseURL: baseURL, query: query, limit: limit)
    }

    public func delete(baseURL: URL, entryId: String) async throws {
        try await client.delete(baseURL: baseURL, entryId: entryId)
    }
}

@MainActor
public final class HistoryModel: ObservableObject {
    @Published public private(set) var entries: [HistoryEntry] = []
    @Published public private(set) var isLoading = false
    @Published public private(set) var error: String?
    @Published public var query = ""

    private let client: HistoryFetching

    public init(client: HistoryFetching = HistoryClientAdapter()) {
        self.client = client
    }

    public func refresh(baseURL: URL, limit: Int = 50) async {
        isLoading = true
        error = nil
        do {
            let q = query.trimmingCharacters(in: .whitespaces)
            entries = try await client.fetch(
                baseURL: baseURL, query: q.isEmpty ? nil : q, limit: limit)
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    public func delete(baseURL: URL, entryId: String) async {
        do {
            try await client.delete(baseURL: baseURL, entryId: entryId)
            entries.removeAll { $0.id == entryId }
        } catch {
            self.error = error.localizedDescription
        }
    }
}
