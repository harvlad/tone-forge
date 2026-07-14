// StudioArrangementTests.swift
//
// Phase 3: trim-selection math, arrangement/region payload decode,
// analyze-stream form fields, and StudioModel's Phase 3 flows with
// stub clients.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

// MARK: - TrimSelection

final class TrimSelectionTests: XCTestCase {

    func testInitCoversFullRange() {
        let trim = TrimSelection(duration: 60)
        XCTAssertTrue(trim.isFullRange)
        XCTAssertEqual(trim.startSeconds, 0)
        XCTAssertEqual(trim.endSeconds, 60)
        XCTAssertEqual(trim.selectedSeconds, 60)
    }

    func testDragClampsToBounds() {
        var trim = TrimSelection(duration: 60)
        trim.dragStart(to: -0.5)
        XCTAssertEqual(trim.startFraction, 0)
        trim.dragEnd(to: 1.5)
        XCTAssertEqual(trim.endFraction, 1)
    }

    func testMinWidthEnforced() {
        // 0.5s of a 10s file = 0.05 fraction.
        var trim = TrimSelection(duration: 10)
        trim.dragEnd(to: 0.5)
        trim.dragStart(to: 0.49)
        XCTAssertEqual(trim.startFraction, 0.45, accuracy: 1e-9)
        trim.dragEnd(to: 0.46)
        XCTAssertEqual(trim.endFraction, 0.5, accuracy: 1e-9)
    }

    func testMovePreservesWidthAndClamps() {
        var trim = TrimSelection(duration: 100)
        trim.dragStart(to: 0.2)
        trim.dragEnd(to: 0.4)
        trim.move(by: 0.3)
        XCTAssertEqual(trim.startFraction, 0.5, accuracy: 1e-9)
        XCTAssertEqual(trim.endFraction, 0.7, accuracy: 1e-9)
        trim.move(by: 5)  // clamp at right edge
        XCTAssertEqual(trim.endFraction, 1, accuracy: 1e-9)
        XCTAssertEqual(
            trim.endFraction - trim.startFraction, 0.2, accuracy: 1e-9)
    }

    func testResetRestoresFullRange() {
        var trim = TrimSelection(duration: 60)
        trim.dragStart(to: 0.3)
        XCTAssertFalse(trim.isFullRange)
        trim.reset()
        XCTAssertTrue(trim.isFullRange)
    }

    func testZeroDurationStaysSane() {
        var trim = TrimSelection(duration: 0)
        trim.dragStart(to: 0.5)
        XCTAssertLessThanOrEqual(trim.startFraction, 0)
        XCTAssertTrue(trim.isFullRange)
    }
}

// MARK: - decode

final class ArrangementDecodeTests: XCTestCase {

    private func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "Fixtures/\(name)", withExtension: "json"))
        return try Data(contentsOf: url)
    }

    func testDecodesArrangement() throws {
        let dto = try JSONDecoder().decode(
            ArrangementAnalysisDTO.self,
            from: fixture("arrangement_analysis"))
        XCTAssertEqual(dto.sections.count, 2)
        XCTAssertEqual(dto.tempoBpm ?? 0, 120, accuracy: 1e-9)
        XCTAssertEqual(dto.key, "A minor")
        XCTAssertEqual(dto.energyCurve?.count, 5)

        let intro = dto.sections[0]
        XCTAssertEqual(intro.type, "intro")
        XCTAssertEqual(intro.duration, 12.5, accuracy: 1e-9)
        XCTAssertEqual(intro.guidanceMode, "chord")
        XCTAssertEqual(intro.structuralRole, "opening")

        // Second section omits most optionals — must still decode.
        let chorus = dto.sections[1]
        XCTAssertEqual(chorus.type, "chorus")
        XCTAssertNil(chorus.energyMean)
        XCTAssertEqual(chorus.duration, 27.5, accuracy: 1e-9)
    }

    func testDecodesRegion() throws {
        let dto = try JSONDecoder().decode(
            RegionAnalysisDTO.self, from: fixture("region_analysis"))
        XCTAssertEqual(dto.sectionType, "chorus")
        XCTAssertEqual(dto.noteCount, 2)
        XCTAssertEqual(dto.notes?.count, 2)
        XCTAssertEqual(dto.bounds?.duration ?? 0, 27.5, accuracy: 1e-9)
        XCTAssertEqual(dto.confidence?.needsCleanup, true)
        XCTAssertEqual(
            dto.confidence?.suggestedPasses, ["merge_short_notes"])
        XCTAssertEqual(
            dto.provenance?.detectorContributions?["basic_pitch"] ?? 0,
            0.7, accuracy: 1e-9)
        XCTAssertEqual(
            dto.audioFeatures?.tempoLocal ?? 0, 121.3, accuracy: 1e-9)
    }

    func testDecodesWaveformPreview() throws {
        let json = """
        {"peaks_positive": [0.1, 0.9], "peaks_negative": [-0.1, -0.8],
         "rms": [0.05, 0.4], "sample_rate": 22050,
         "duration_sec": 3.5, "filename": "riff.wav"}
        """
        let preview = try JSONDecoder().decode(
            WaveformPreview.self, from: Data(json.utf8))
        XCTAssertEqual(preview.peaksPositive, [0.1, 0.9])
        XCTAssertEqual(preview.durationSec ?? 0, 3.5, accuracy: 1e-9)
    }
}

// MARK: - stream form fields

final class StudioStreamFieldsTests: XCTestCase {

    func testTrimFieldsOnlyWhenSet() {
        let base = StudioStreamClient.formFields(
            startTime: nil, endTime: nil)
        XCTAssertFalse(base.contains { $0.name == "start_time" })
        XCTAssertFalse(base.contains { $0.name == "end_time" })
        XCTAssertTrue(base.contains {
            $0.name == "analysis_mode" && $0.value == "studio"
        })
        XCTAssertTrue(base.contains {
            $0.name == "fast_mode" && $0.value == "true"
        })
        XCTAssertTrue(base.contains {
            $0.name == "attested" && $0.value == "true"
        })

        let trimmed = StudioStreamClient.formFields(
            startTime: 12.5, endTime: 40.0)
        XCTAssertEqual(
            trimmed.first { $0.name == "start_time" }?.value, "12.5")
        XCTAssertEqual(
            trimmed.first { $0.name == "end_time" }?.value, "40.0")
    }
}

// MARK: - StudioModel Phase 3 flows

private final class StubWaveformClient: WaveformPreviewing, @unchecked Sendable {
    var result: WaveformPreview?
    func previewWaveform(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> WaveformPreview {
        guard let result else { throw URLError(.badServerResponse) }
        return result
    }
}

private final class StubSectionClient: SectionDetecting, @unchecked Sendable {
    var result: ArrangementAnalysisDTO?
    var tempos: [Double?] = []
    func detectSections(
        baseURL: URL, fileURL: URL, filename: String, tempo: Double?
    ) async throws -> ArrangementAnalysisDTO {
        tempos.append(tempo)
        guard let result else { throw URLError(.badServerResponse) }
        return result
    }
}

private final class StubRegionClient: RegionAnalyzing, @unchecked Sendable {
    var result: RegionAnalysisDTO?
    var calls: [(start: Double, end: Double, stem: String)] = []
    func analyzeRegion(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double, endTime: Double, stemType: String
    ) async throws -> RegionAnalysisDTO {
        calls.append((startTime, endTime, stemType))
        guard let result else { throw URLError(.badServerResponse) }
        return result
    }
}

private final class StubStreamClient: StudioStreamAnalyzing, @unchecked Sendable {
    var events: [AnalyzeEvent] = []
    var calls: [(startTime: Double?, endTime: Double?)] = []
    func analyze(
        baseURL: URL, fileURL: URL, filename: String,
        startTime: Double?, endTime: Double?
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        calls.append((startTime, endTime))
        let events = events
        return AsyncThrowingStream { continuation in
            for event in events { continuation.yield(event) }
            continuation.finish()
        }
    }
}

private final class StubDetailClient: StudioFetching, @unchecked Sendable {
    var detail: StudioHistoryDetail?
    var requestedIDs: [String] = []
    func fetchHistoryDetail(
        baseURL: URL, id: String
    ) async throws -> StudioHistoryDetail {
        requestedIDs.append(id)
        guard let detail else { throw URLError(.badServerResponse) }
        return detail
    }
}

@MainActor
final class StudioModelPhase3Tests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!
    private let file = URL(fileURLWithPath: "/tmp/riff.wav")

    func testLoadWaveformSeedsTrimFromDuration() async {
        let waveformStub = StubWaveformClient()
        waveformStub.result = WaveformPreview(
            peaksPositive: [0.1], peaksNegative: [-0.1], rms: [0.05],
            durationSec: 42)
        let model = StudioModel(waveformClient: waveformStub)
        model.sourceFileURL = file

        await model.loadWaveform(baseURL: base)

        XCTAssertNotNil(model.waveform)
        XCTAssertEqual(model.trim?.duration ?? 0, 42, accuracy: 1e-9)
        XCTAssertEqual(model.trim?.isFullRange, true)
        XCTAssertNil(model.waveformError)
    }

    func testTrimmedRunOmitsFieldsForFullRangeAndLoadsHistory() async {
        let streamStub = StubStreamClient()
        streamStub.events = [
            .progress(message: "working", percent: 50),
            .completed(historyId: "hist-1"),
        ]
        let detailStub = StubDetailClient()
        detailStub.detail = StudioHistoryDetail(id: "hist-1")
        let model = StudioModel(
            client: detailStub, streamClient: streamStub)
        model.sourceFileURL = file
        model.trim = TrimSelection(duration: 60)  // full range

        await model.runTrimmedAnalysis(baseURL: base)

        XCTAssertEqual(streamStub.calls.count, 1)
        XCTAssertNil(streamStub.calls.first?.startTime)
        XCTAssertNil(streamStub.calls.first?.endTime)
        XCTAssertEqual(detailStub.requestedIDs, ["hist-1"])
        XCTAssertEqual(model.detail?.id, "hist-1")
        XCTAssertNil(model.trimmedRunError)
        XCTAssertFalse(model.isRunningTrimmedAnalysis)
    }

    func testTrimmedRunSendsSecondsForPartialRange() async {
        let streamStub = StubStreamClient()
        streamStub.events = [.completed(historyId: "hist-2")]
        let detailStub = StubDetailClient()
        detailStub.detail = StudioHistoryDetail(id: "hist-2")
        let model = StudioModel(
            client: detailStub, streamClient: streamStub)
        model.sourceFileURL = file
        var trim = TrimSelection(duration: 100)
        trim.dragStart(to: 0.25)
        trim.dragEnd(to: 0.75)
        model.trim = trim

        await model.runTrimmedAnalysis(baseURL: base)

        XCTAssertEqual(
            streamStub.calls.first?.startTime ?? 0, 25, accuracy: 1e-9)
        XCTAssertEqual(
            streamStub.calls.first?.endTime ?? 0, 75, accuracy: 1e-9)
    }

    func testDetectSectionsPassesTempoHintFromLoadedDetail() async {
        let sectionStub = StubSectionClient()
        sectionStub.result = ArrangementAnalysisDTO(sections: [])
        let model = StudioModel(sectionClient: sectionStub)
        model.sourceFileURL = file

        await model.detectSections(baseURL: base)
        XCTAssertEqual(sectionStub.tempos, [nil])
        XCTAssertNotNil(model.arrangement)
    }

    func testAnalyzeRegionStoresResult() async {
        let regionStub = StubRegionClient()
        regionStub.result = RegionAnalysisDTO(sectionType: "chorus")
        let model = StudioModel(regionClient: regionStub)
        model.sourceFileURL = file

        await model.analyzeRegion(
            baseURL: base, startTime: 12.5, endTime: 40)

        XCTAssertEqual(regionStub.calls.first?.start ?? 0, 12.5)
        XCTAssertEqual(regionStub.calls.first?.stem, "other")
        XCTAssertEqual(model.region?.sectionType, "chorus")
    }

    func testClearWaveformResetsPhase3State() async {
        let waveformStub = StubWaveformClient()
        waveformStub.result = WaveformPreview(
            peaksPositive: [], peaksNegative: [], rms: [],
            durationSec: 10)
        let model = StudioModel(waveformClient: waveformStub)
        model.sourceFileURL = file
        await model.loadWaveform(baseURL: base)
        XCTAssertNotNil(model.waveform)

        model.clearWaveform()
        XCTAssertNil(model.waveform)
        XCTAssertNil(model.trim)
        XCTAssertNil(model.arrangement)
        XCTAssertNil(model.region)
    }

    func testFlowsAreNoOpsWithoutSourceFile() async {
        let streamStub = StubStreamClient()
        let model = StudioModel(streamClient: streamStub)
        await model.loadWaveform(baseURL: base)
        await model.runTrimmedAnalysis(baseURL: base)
        await model.detectSections(baseURL: base)
        await model.analyzeRegion(baseURL: base, startTime: 0, endTime: 1)
        XCTAssertTrue(streamStub.calls.isEmpty)
        XCTAssertNil(model.waveformError)
        XCTAssertNil(model.arrangement)
    }
}
