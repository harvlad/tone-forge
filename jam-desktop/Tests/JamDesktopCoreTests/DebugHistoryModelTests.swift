// DebugHistoryModelTests.swift
//
// binNumeric/countBy edge cases (debug.js:905-928 parity) plus the
// lazy bundle enrichment for section/mode histograms.

import XCTest
@testable import JamDesktopCore

private final class StubHistoryDebugClient: DebugFetching, @unchecked Sendable {
    var history: [DebugHistoryRow] = []
    var bundles: [String: DebugBundle] = [:]
    var bundleFetchIds: [String] = []
    var errorToThrow: Error?

    func fetchSessions(baseURL: URL) async throws -> [DebugSessionSummary] { [] }

    func fetchBundle(baseURL: URL, id: String) async throws -> DebugBundle {
        bundleFetchIds.append(id)
        guard let bundle = bundles[id] else { throw URLError(.badServerResponse) }
        return bundle
    }

    func fetchCorpus(baseURL: URL) async throws -> DebugCorpus {
        DebugCorpus(songs: [])
    }

    func fetchHistory(baseURL: URL, limit: Int) async throws -> [DebugHistoryRow] {
        if let errorToThrow { throw errorToThrow }
        return history
    }
}

@MainActor
final class DebugHistoryModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    // MARK: - binNumeric

    func testBinNumericEmpty() {
        XCTAssertEqual(DebugHistoryModel.binNumeric([], bins: 8), [])
    }

    func testBinNumericSingleValueCollapses() {
        let bins = DebugHistoryModel.binNumeric([120.4, 120.4, 120.4], bins: 8)
        XCTAssertEqual(bins, [HistogramBin(label: "120", value: 3)])
    }

    func testBinNumericSpreadsAndClampsMax() {
        let bins = DebugHistoryModel.binNumeric([0, 1, 2, 3, 4], bins: 4)
        XCTAssertEqual(bins.count, 4)
        // width 1: 0→bin0, 1→bin1, 2→bin2, 3→clamp? floor(3/1)=3 → bin3,
        // 4 clamps to bin3.
        XCTAssertEqual(bins.map { $0.value }, [1, 1, 1, 2])
        XCTAssertEqual(bins.map { $0.label }, ["0", "1", "2", "3"])
    }

    // MARK: - countBy

    func testCountBySortsDescAndCaps() {
        let values = ["a", "b", "b", "c", "c", "c"]
        let bins = DebugHistoryModel.countBy(values)
        XCTAssertEqual(bins.map { $0.label }, ["c", "b", "a"])
        XCTAssertEqual(bins.map { $0.value }, [3, 2, 1])

        let many = (0..<20).flatMap { i in
            Array(repeating: "k\(i)", count: 20 - i)
        }
        XCTAssertEqual(DebugHistoryModel.countBy(many).count, 12)
    }

    func testCountByEmpty() {
        XCTAssertEqual(DebugHistoryModel.countBy([]), [])
    }

    // MARK: - load + derived bins

    func testLoadPopulatesRowsAndBundleBins() async {
        let stub = StubHistoryDebugClient()
        stub.history = [
            DebugHistoryRow(id: "h1", tempoBpm: 120, detectedKey: "C major"),
            DebugHistoryRow(id: "h2", tempoBpm: 120, detectedKey: "C major"),
            DebugHistoryRow(id: "h3"),  // bundle fetch fails — swallowed
        ]
        stub.bundles["h1"] = DebugBundle(understanding: DebugUnderstanding(
            sections: [
                DebugSection(label: "verse", guidanceMode: "chord"),
                DebugSection(label: "chorus", guidanceMode: "chord"),
            ]))
        stub.bundles["h2"] = DebugBundle(understanding: DebugUnderstanding(
            sections: [DebugSection(label: "verse", guidanceMode: "riff")]))

        let model = DebugHistoryModel(client: stub)
        await model.load(baseURL: base)
        XCTAssertEqual(model.rows.count, 3)
        XCTAssertEqual(model.bundles.count, 2)
        XCTAssertFalse(model.isFetchingBundles)

        XCTAssertEqual(model.tempoBins, [HistogramBin(label: "120", value: 2)])
        XCTAssertEqual(model.keyBins, [HistogramBin(label: "C major", value: 2)])
        XCTAssertEqual(
            model.sectionTypeBins,
            [HistogramBin(label: "verse", value: 2),
             HistogramBin(label: "chorus", value: 1)])
        XCTAssertEqual(model.guidanceModeBins.map { $0.value }, [2, 1])
    }

    func testLoadOnlyFetchesFirst20Bundles() async {
        let stub = StubHistoryDebugClient()
        stub.history = (0..<25).map { DebugHistoryRow(id: "h\($0)") }
        let model = DebugHistoryModel(client: stub)
        await model.load(baseURL: base)
        XCTAssertEqual(stub.bundleFetchIds.count, 20)
    }

    func testLoadErrorSurfacesAndSkipsBundles() async {
        let stub = StubHistoryDebugClient()
        stub.errorToThrow = URLError(.timedOut)
        let model = DebugHistoryModel(client: stub)
        await model.load(baseURL: base)
        XCTAssertNotNil(model.error)
        XCTAssertTrue(stub.bundleFetchIds.isEmpty)
    }
}
