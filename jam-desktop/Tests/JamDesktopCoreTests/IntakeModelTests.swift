// IntakeModelTests.swift
//
// Form-state tests with stubbed clients: engine status banner and the
// demo CC-track catalog. Flow tests live in AnalysisQueueModelTests.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

// MARK: - Stubs

private struct StubEngineStatus: EngineStatusFetching {
    let result: EngineStatus
    func status(baseURL: URL) async throws -> EngineStatus { result }
}

private struct StubCC: CCTrackProviding {
    let tracks: [CCTrack]

    func fetchCatalog(baseURL: URL) async throws -> [CCTrack] { tracks }
    func startImport(baseURL: URL, trackId: String) async throws -> String {
        XCTFail("startImport should not be called by IntakeModel")
        return "unused"
    }
}

// MARK: - Tests

@MainActor
final class IntakeModelTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func makeModel(
        cc: CCTrackProviding = StubCC(tracks: [])
    ) -> IntakeModel {
        IntakeModel(
            engineClient: StubEngineStatus(
                result: EngineStatus(online: true, device: "mps")),
            ccClient: cc
        )
    }

    func testRefreshEngineStatus() async {
        let model = makeModel()
        await model.refreshEngineStatus(baseURL: base)
        XCTAssertEqual(model.engineStatus, EngineStatus(online: true, device: "mps"))
    }

    func testLoadDemoTracks() async {
        let track = CCTrack(id: "t1", title: "Demo Song", artist: "A", license: "CC BY 4.0")
        let model = makeModel(cc: StubCC(tracks: [track]))
        await model.loadDemoTracks(baseURL: base)
        XCTAssertEqual(model.demoTracks, [track])
        XCTAssertNil(model.demoTracksError)
    }
}
