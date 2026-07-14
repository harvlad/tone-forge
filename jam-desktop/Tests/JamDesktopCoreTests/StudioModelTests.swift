// StudioModelTests.swift
//
// Fixture is a trimmed real jamn.app history entry (2026-07), so
// decode failures mean the wire drifted. Model tests cover the
// descriptor fallback chain and derived rows.

import XCTest
@testable import JamDesktopCore

private final class StubStudioClient: StudioFetching, @unchecked Sendable {
    var detail: StudioHistoryDetail?
    var errorToThrow: Error?
    var fetchedIds: [String] = []

    func fetchHistoryDetail(
        baseURL: URL, id: String
    ) async throws -> StudioHistoryDetail {
        fetchedIds.append(id)
        if let errorToThrow { throw errorToThrow }
        guard let detail else { throw URLError(.badServerResponse) }
        return detail
    }
}

@MainActor
final class StudioModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func fixtureDetail() throws -> StudioHistoryDetail {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "Fixtures/studio_history_detail", withExtension: "json"))
        return try JSONDecoder().decode(
            StudioHistoryDetail.self, from: Data(contentsOf: url))
    }

    // MARK: - decode (live-wire fixture)

    func testDecodesLiveEntry() throws {
        let detail = try fixtureDetail()
        XCTAssertEqual(detail.id, "69441871fed548d7b7c2ee379020b565")
        XCTAssertEqual(detail.detectedType, "guitar")
        XCTAssertEqual(detail.deepAnalysis, true)
        XCTAssertEqual(detail.artist, "Kevin MacLeod")

        let result = try XCTUnwrap(detail.result)
        XCTAssertEqual(result.sampleRate, 22050)
        XCTAssertEqual(result.detectedKey, "C major")
        XCTAssertEqual(result.tempoBpm ?? 0, 103.448, accuracy: 0.01)

        let descriptor = try XCTUnwrap(result.descriptor)
        XCTAssertEqual(descriptor.amp?.family, "bogner")
        XCTAssertEqual(descriptor.amp?.voicing?.midScoop, 1.0)
        XCTAssertEqual(descriptor.amp?.alternates?.first?.family, "soldano")
        XCTAssertEqual(descriptor.cab?.configuration, "4x12")
        XCTAssertEqual(descriptor.cab?.speakerCharacter, "alnico_blue_like")
        XCTAssertEqual(descriptor.guitar?.playingStyle, "chord_riff")

        // overdrive_pedal null, delay/reverb/compressor present.
        let effects = try XCTUnwrap(descriptor.effects)
        XCTAssertNil(effects.overdrivePedal)
        XCTAssertEqual(effects.delay?.type, "tape")
        XCTAssertEqual(effects.reverb?.type, "plate")
        XCTAssertNotNil(effects.compressor?.amount)

        XCTAssertEqual(result.stemsPaths?.count, 4)
        XCTAssertEqual(result.midiStems?["bass"]?.noteCount, 403)
        XCTAssertNotNil(result.profiling?.totalMs)
        XCTAssertEqual(result.profiling?.stages?["stem_separation"]?.gpuUsed, true)
    }

    // MARK: - descriptor fallback chain

    func testDescriptorPrefersTypeNested() async throws {
        let stub = StubStudioClient()
        let nested = ToneDescriptor(amp: AmpDescriptor(family: "nested"))
        let topLevel = ToneDescriptor(amp: AmpDescriptor(family: "top"))
        stub.detail = StudioHistoryDetail(
            id: "x",
            result: StudioResult(
                detectedType: "guitar",
                descriptor: topLevel,
                guitar: InstrumentWrapper(descriptor: nested)))
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "x")
        XCTAssertEqual(model.descriptor?.amp?.family, "nested")
    }

    func testDescriptorFallsBackToTopLevel() async throws {
        let stub = StubStudioClient()
        stub.detail = StudioHistoryDetail(
            id: "x",
            result: StudioResult(
                detectedType: "synth",
                descriptor: ToneDescriptor(amp: AmpDescriptor(family: "top"))))
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "x")
        XCTAssertEqual(model.descriptor?.amp?.family, "top")
    }

    // MARK: - derived rows

    func testMidiStemRowsOrderedAndTotalled() async throws {
        let stub = StubStudioClient()
        stub.detail = StudioHistoryDetail(
            id: "x",
            result: StudioResult(midiStems: [
                "vocals": MidiStemInfo(noteCount: 10),
                "drums": MidiStemInfo(noteCount: 100),
                "guitar": MidiStemInfo(noteCount: 50),
            ]))
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "x")
        XCTAssertEqual(model.midiStemRows.map { $0.stem }, ["drums", "guitar", "vocals"])
        XCTAssertEqual(model.totalMidiNotes, 160)
    }

    func testStageRowsSortedByStartWithFractions() async throws {
        let stub = StubStudioClient()
        stub.detail = StudioHistoryDetail(
            id: "x",
            result: StudioResult(profiling: StudioProfiling(
                totalMs: 1000,
                stages: [
                    "later": ProfilingStage(startedMs: 500, durationMs: 500),
                    "first": ProfilingStage(startedMs: 0, durationMs: 250),
                ])))
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "x")
        XCTAssertEqual(model.stageRows.map { $0.name }, ["first", "later"])
        XCTAssertEqual(model.stageRows[0].fraction, 0.25)
        XCTAssertEqual(model.stageRows[1].fraction, 0.5)
    }

    // MARK: - load state

    func testLoadErrorSurfaces() async {
        let stub = StubStudioClient()
        stub.errorToThrow = URLError(.timedOut)
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "x")
        XCTAssertNotNil(model.error)
        XCTAssertNil(model.detail)
    }

    func testLoadIgnoresEmptyId() async {
        let stub = StubStudioClient()
        let model = StudioModel(client: stub)
        await model.load(baseURL: base, id: "")
        XCTAssertTrue(stub.fetchedIds.isEmpty)
    }
}
