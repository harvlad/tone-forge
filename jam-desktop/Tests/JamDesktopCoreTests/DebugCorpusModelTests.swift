// DebugCorpusModelTests.swift
//
// ConfusionMatrix math verified against hand-computed values; matching
// mirrors debug.js slugify/matchSong.

import XCTest
@testable import JamDesktopCore

private final class StubCorpusClient: DebugFetching, @unchecked Sendable {
    var sessions: [DebugSessionSummary] = []
    var bundles: [String: DebugBundle] = [:]
    var corpus = DebugCorpus(songs: [])
    var bundleFetchIds: [String] = []

    func fetchSessions(baseURL: URL) async throws -> [DebugSessionSummary] { sessions }

    func fetchBundle(baseURL: URL, id: String) async throws -> DebugBundle {
        bundleFetchIds.append(id)
        guard let bundle = bundles[id] else { throw URLError(.badServerResponse) }
        return bundle
    }

    func fetchCorpus(baseURL: URL) async throws -> DebugCorpus { corpus }

    func fetchHistory(baseURL: URL, limit: Int) async throws -> [DebugHistoryRow] { [] }
}

@MainActor
final class DebugCorpusModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    // MARK: - ConfusionMatrix

    func testMatrixAccuracyAndCounts() {
        var m = ConfusionMatrix()
        m.add(
            predicted: ["chord", "riff", "lead", "chord"],
            actual: ["chord", "chord", "lead", "riff"])
        XCTAssertEqual(m.total, 4)
        XCTAssertEqual(m.correct, 2)
        XCTAssertEqual(m.accuracy, 0.5)
        XCTAssertEqual(m.count(predicted: "chord", actual: "chord"), 1)
        XCTAssertEqual(m.count(predicted: "riff", actual: "chord"), 1)
        XCTAssertEqual(m.count(predicted: "chord", actual: "riff"), 1)
        XCTAssertEqual(m.count(predicted: "lead", actual: "lead"), 1)
    }

    func testMatrixSkipsNonModesAndNilsAndPairsByMinCount() {
        var m = ConfusionMatrix()
        m.add(
            predicted: ["chord", nil, "solo", "riff", "chord"],
            actual: ["chord", "riff", "chord", "outro"])  // shorter list
        // Pairs considered: (chord,chord) ok; (nil,riff) skip;
        // (solo,chord) skip; (riff,outro) skip; index 4 beyond min.
        XCTAssertEqual(m.total, 1)
        XCTAssertEqual(m.correct, 1)
    }

    func testMacroF1HandComputed() {
        var m = ConfusionMatrix()
        // chord: tp=2. riff: predicted riff actual chord ×1 (fp for
        // riff, fn for chord). lead: tp=1.
        m.add(
            predicted: ["chord", "chord", "riff", "lead"],
            actual: ["chord", "chord", "chord", "lead"])
        // chord: prec 2/2=1, rec 2/3, f1 = 2*(1*2/3)/(1+2/3) = 0.8
        // riff: prec 0/1=0, rec 0 (no actual riff) → f1 0
        // lead: prec 1, rec 1 → f1 1
        XCTAssertEqual(m.macroF1, (0.8 + 0.0 + 1.0) / 3.0, accuracy: 1e-9)
    }

    func testEmptyMatrix() {
        let m = ConfusionMatrix()
        XCTAssertEqual(m.accuracy, 0)
        XCTAssertEqual(m.macroF1, 0)
    }

    // MARK: - slugify / matchSong

    func testSlugify() {
        XCTAssertEqual(DebugCorpusModel.slugify("Wonderwall"), "wonderwall")
        XCTAssertEqual(DebugCorpusModel.slugify("Hey Jude!"), "hey_jude")
        XCTAssertEqual(DebugCorpusModel.slugify("  A -- B  "), "a_b")
        XCTAssertEqual(DebugCorpusModel.slugify(nil), "")
        XCTAssertEqual(DebugCorpusModel.slugify("Café 24/7"), "caf_24_7")
    }

    func testMatchSongSlugExact() {
        let song = CorpusSong(slug: "hey_jude", title: "Hey Jude")
        let sessions = [
            DebugSessionSummary(id: "a", name: "Other"),
            DebugSessionSummary(id: "b", name: "Hey Jude"),
        ]
        let match = DebugCorpusModel.matchSong(song, sessions: sessions)
        XCTAssertEqual(match?.session.id, "b")
        XCTAssertEqual(match?.fuzzy, false)
    }

    func testMatchSongFuzzyTitleSubstring() {
        let song = CorpusSong(slug: "hey_jude", title: "Hey Jude")
        let sessions = [
            DebugSessionSummary(id: "c", name: "The Beatles - Hey Jude (Remastered)"),
        ]
        let match = DebugCorpusModel.matchSong(song, sessions: sessions)
        XCTAssertEqual(match?.session.id, "c")
        XCTAssertEqual(match?.fuzzy, true)
    }

    func testMatchSongNoMatch() {
        let song = CorpusSong(slug: "hey_jude", title: "Hey Jude")
        XCTAssertNil(DebugCorpusModel.matchSong(
            song, sessions: [DebugSessionSummary(id: "d", name: "Wonderwall")]))
    }

    // MARK: - load

    func testLoadBuildsRowsAndMatrix() async {
        let stub = StubCorpusClient()
        stub.corpus = DebugCorpus(songs: [
            CorpusSong(slug: "song_one", title: "Song One", groundTruthSections: [
                GroundTruthSection(label: "intro", guidanceMode: "chord"),
                GroundTruthSection(label: "verse", guidanceMode: "riff"),
            ]),
            CorpusSong(slug: "missing", title: "Missing Song"),
        ])
        stub.sessions = [
            DebugSessionSummary(id: "s1", name: "Song One", hasDebugFeatures: true),
        ]
        stub.bundles["s1"] = DebugBundle(understanding: DebugUnderstanding(
            sections: [
                DebugSection(guidanceMode: "chord"),
                DebugSection(guidanceMode: "lead"),
            ]))

        let model = DebugCorpusModel(client: stub)
        await model.load(baseURL: base, sessions: [])
        XCTAssertEqual(model.rows.count, 2)
        XCTAssertEqual(model.analyzedCount, 1)
        XCTAssertNotNil(model.rows[0].bundle)
        XCTAssertNil(model.rows[1].session)
        XCTAssertEqual(model.matrix.total, 2)
        XCTAssertEqual(model.matrix.correct, 1)
        XCTAssertEqual(model.matrix.count(predicted: "lead", actual: "riff"), 1)
    }

    func testLoadSkipsBundleWhenNoDebugFeatures() async {
        let stub = StubCorpusClient()
        stub.corpus = DebugCorpus(songs: [CorpusSong(slug: "legacy", title: "Legacy")])
        stub.sessions = [
            DebugSessionSummary(id: "s2", name: "Legacy", hasDebugFeatures: false),
        ]
        let model = DebugCorpusModel(client: stub)
        await model.load(baseURL: base, sessions: [])
        XCTAssertTrue(stub.bundleFetchIds.isEmpty)
        XCTAssertEqual(model.rows[0].session?.id, "s2")
        XCTAssertNil(model.rows[0].bundle)
    }
}
