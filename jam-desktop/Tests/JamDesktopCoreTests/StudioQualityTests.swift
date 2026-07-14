// StudioQualityTests.swift
//
// Phase 2: admin credentials round-trip, quality payload decode, and
// StudioModel's quality-analysis flow with a stub client.

import XCTest
@testable import JamDesktopCore

// MARK: - AdminCredentials

final class AdminCredentialsTests: XCTestCase {

    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: "AdminCredentialsTests")!
        defaults.removePersistentDomain(forName: "AdminCredentialsTests")
    }

    func testTokenRoundTripAndTrimming() {
        XCTAssertNil(AdminCredentials.token(defaults: defaults))
        AdminCredentials.setToken("  abc123  ", defaults: defaults)
        XCTAssertEqual(AdminCredentials.token(defaults: defaults), "abc123")
        AdminCredentials.setToken("   ", defaults: defaults)
        XCTAssertNil(AdminCredentials.token(defaults: defaults))
    }

    func testApplySetsHeaderOnlyWhenTokenPresent() {
        var request = URLRequest(url: URL(string: "http://x/api/admin/y")!)
        AdminCredentials.apply(to: &request, defaults: defaults)
        XCTAssertNil(request.value(forHTTPHeaderField: "X-Admin-Token"))

        AdminCredentials.setToken("tok", defaults: defaults)
        AdminCredentials.apply(to: &request, defaults: defaults)
        XCTAssertEqual(request.value(forHTTPHeaderField: "X-Admin-Token"), "tok")
    }
}

// MARK: - decode

final class QualityModelsDecodeTests: XCTestCase {

    func testDecodesFullPayload() throws {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "Fixtures/quality_analysis", withExtension: "json"))
        let quality = try JSONDecoder().decode(
            QualityAnalysis.self, from: Data(contentsOf: url))

        XCTAssertEqual(quality.filename, "riff.wav")
        XCTAssertEqual(quality.reconstructionAvailable, true)
        XCTAssertEqual(quality.stemQuality?.overallQuality ?? 0, 0.82, accuracy: 1e-9)
        XCTAssertNil(quality.stemQuality?.stereoCoherence)
        XCTAssertEqual(quality.contamination?.drumBleed ?? 0, 0.2, accuracy: 1e-9)
        XCTAssertEqual(quality.artifacts?.clippingDetected, true)
        XCTAssertEqual(quality.artifacts?.phaseIssues, false)
        XCTAssertEqual(quality.role?.primaryRole, "lead")
        XCTAssertEqual(quality.confidenceMap?.lowConfidenceRegions, 2)
        XCTAssertEqual(quality.priors?.sourceArchetype, "clean_electric")
        XCTAssertEqual(quality.qualityReport?.qualityLevel, "good")
        XCTAssertEqual(quality.qualityReport?.warnings?.count, 1)
        XCTAssertEqual(
            quality.qualityReport?.warnings?.first?.recommendation,
            "Consider a tighter stem separation model")
        XCTAssertEqual(quality.confidenceScores?.gain ?? 0, 0.64, accuracy: 1e-9)
        XCTAssertEqual(quality.detected?.ampFamily, "fender_clean")
    }

    func testDecodesMinimalPayload() throws {
        // Backend without reconstruction returns only file facts.
        let json = """
        {"filename": "a.wav", "duration_sec": 3.0,
         "reconstruction_available": false}
        """
        let quality = try JSONDecoder().decode(
            QualityAnalysis.self, from: Data(json.utf8))
        XCTAssertEqual(quality.reconstructionAvailable, false)
        XCTAssertNil(quality.stemQuality)
        XCTAssertNil(quality.qualityReport)
    }
}

// MARK: - StudioModel quality flow

private final class StubQualityClient: QualityAnalyzing, @unchecked Sendable {
    var result: QualityAnalysis?
    var errorToThrow: Error?
    var calls: [(fileURL: URL, filename: String)] = []

    func analyzeQuality(
        baseURL: URL, fileURL: URL, filename: String
    ) async throws -> QualityAnalysis {
        calls.append((fileURL, filename))
        if let errorToThrow { throw errorToThrow }
        guard let result else { throw URLError(.badServerResponse) }
        return result
    }
}

@MainActor
final class StudioModelQualityTests: XCTestCase {

    private let base = URL(string: "http://127.0.0.1:8000")!

    private func makeModel(
        quality stub: StubQualityClient
    ) -> StudioModel {
        StudioModel(qualityClient: stub)
    }

    func testRunPassesFilenameAndStoresResult() async {
        let stub = StubQualityClient()
        stub.result = QualityAnalysis(filename: "riff.wav")
        let model = makeModel(quality: stub)
        model.sourceFileURL = URL(fileURLWithPath: "/tmp/riff.wav")

        await model.runQualityAnalysis(baseURL: base)

        XCTAssertEqual(stub.calls.count, 1)
        XCTAssertEqual(stub.calls.first?.filename, "riff.wav")
        XCTAssertEqual(model.quality?.filename, "riff.wav")
        XCTAssertNil(model.qualityError)
        XCTAssertFalse(model.isAnalyzingQuality)
    }

    func testRunWithoutSourceIsNoOp() async {
        let stub = StubQualityClient()
        let model = makeModel(quality: stub)
        await model.runQualityAnalysis(baseURL: base)
        XCTAssertTrue(stub.calls.isEmpty)
    }

    func testAdminTokenErrorSurfacesFriendlyMessage() async {
        let stub = StubQualityClient()
        stub.errorToThrow = StudioAdminError.adminTokenRequired
        let model = makeModel(quality: stub)
        model.sourceFileURL = URL(fileURLWithPath: "/tmp/riff.wav")

        await model.runQualityAnalysis(baseURL: base)

        XCTAssertNil(model.quality)
        XCTAssertEqual(
            model.qualityError?.contains("admin token") ?? false, true)
    }

    func testClearQualityResetsResultAndError() async {
        let stub = StubQualityClient()
        stub.result = QualityAnalysis(filename: "riff.wav")
        let model = makeModel(quality: stub)
        model.sourceFileURL = URL(fileURLWithPath: "/tmp/riff.wav")
        await model.runQualityAnalysis(baseURL: base)

        model.clearQuality()
        XCTAssertNil(model.quality)
        XCTAssertNil(model.qualityError)
    }
}
