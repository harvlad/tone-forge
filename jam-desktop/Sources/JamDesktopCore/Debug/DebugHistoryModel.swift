// DebugHistoryModel.swift
//
// History tab: table rows plus four histograms (tempo, key, section
// types, guidance modes). Section/mode data needs bundle fetches for
// the first 20 rows — same lazy batch as debug.js fetchHistoryBundles,
// but sequential to keep backend load polite. binNumeric/countBy are
// pure static functions (debug.js:905-928) so they test headless.

import Foundation
import Observation

public struct HistogramBin: Equatable, Sendable, Identifiable {
    public let label: String
    public let value: Int

    public var id: String { label }

    public init(label: String, value: Int) {
        self.label = label
        self.value = value
    }
}

@Observable
@MainActor
public final class DebugHistoryModel {
    public private(set) var rows: [DebugHistoryRow] = []
    /// Bundles for the first 20 rows, keyed by session id. Populated
    /// after `rows` so the table renders before histogram enrichment.
    public private(set) var bundles: [String: DebugBundle] = [:]
    public private(set) var isLoading = false
    /// Bundle batch still in flight (web shows "fetching bundles…").
    public private(set) var isFetchingBundles = false
    public private(set) var error: String?

    private let client: DebugFetching

    public init(client: DebugFetching = DebugClient()) {
        self.client = client
    }

    public func load(baseURL: URL, limit: Int = 100) async {
        isLoading = true
        error = nil
        do {
            rows = try await client.fetchHistory(baseURL: baseURL, limit: limit)
        } catch {
            self.error = error.localizedDescription
            isLoading = false
            return
        }
        isLoading = false

        isFetchingBundles = true
        var fetched: [String: DebugBundle] = [:]
        for row in rows.prefix(20) where !row.id.isEmpty {
            // Best effort like the web — a failed bundle just leaves
            // that session out of the section/mode histograms.
            if let bundle = try? await client.fetchBundle(baseURL: baseURL, id: row.id) {
                fetched[row.id] = bundle
            }
        }
        bundles = fetched
        isFetchingBundles = false
    }

    // MARK: derived histogram data

    public var tempoBins: [HistogramBin] {
        let tempos = rows.compactMap { $0.tempoBpm }.filter { $0.isFinite }
        return Self.binNumeric(tempos, bins: 8)
    }

    public var keyBins: [HistogramBin] {
        Self.countBy(rows.compactMap { $0.detectedKey }.filter { !$0.isEmpty })
    }

    public var sectionTypeBins: [HistogramBin] {
        var labels: [String] = []
        for bundle in bundles.values {
            for section in bundle.sections {
                if let label = section.label, !label.isEmpty { labels.append(label) }
            }
        }
        return Self.countBy(labels)
    }

    public var guidanceModeBins: [HistogramBin] {
        var modes: [String] = []
        for bundle in bundles.values {
            for section in bundle.sections {
                if let mode = section.guidanceMode, !mode.isEmpty { modes.append(mode) }
            }
        }
        return Self.countBy(modes)
    }

    // MARK: pure helpers (debug.js binNumeric/countBy)

    public static func binNumeric(_ values: [Double], bins n: Int = 8) -> [HistogramBin] {
        guard let min = values.min(), let max = values.max() else { return [] }
        if min == max {
            return [HistogramBin(label: String(Int(min.rounded())), value: values.count)]
        }
        let width = (max - min) / Double(n)
        var counts = [Int](repeating: 0, count: n)
        for v in values {
            let i = Swift.min(n - 1, Int((v - min) / width))
            counts[i] += 1
        }
        return (0..<n).map { i in
            HistogramBin(
                label: String(Int((min + Double(i) * width).rounded())),
                value: counts[i])
        }
    }

    /// Counts by value, sorted descending, capped at 12. Ties keep
    /// first-seen order (JS stable sort parity).
    public static func countBy(_ values: [String]) -> [HistogramBin] {
        var counts: [String: Int] = [:]
        var order: [String] = []
        for v in values {
            if counts[v] == nil { order.append(v) }
            counts[v, default: 0] += 1
        }
        return order
            .map { HistogramBin(label: $0, value: counts[$0]!) }
            .sorted { $0.value > $1.value }
            .prefix(12)
            .map { $0 }
    }
}
