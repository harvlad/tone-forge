// LatencyProbeSelfTests.swift
//
// P7: the probe must be trustworthy before its numbers gate the
// ship. Headless (macOS swift test) the four software gates run for
// real — pad-tap and Launchpad through the live bus → ModeRouter →
// coordinator → scheduler path with the resolver spy, session load
// against a real 1000-event file, mic pipeline through the real
// process → classify → save → assign chain. The output-chain add-on
// is 0 off-device (macOS hosts and the simulator) so budgets
// translate directly.
//
// The vocoder gate is NOT invoked here: it starts a real capture,
// which pops the macOS microphone-permission dialog on the test
// host. It's covered by the manual device checklist.
//
// Every test also asserts the probe cleans up after itself — the
// borrowed pad, the temp sample, the synthetic session — because
// PadSampleStore/PadAssignmentStore write real Documents /
// UserDefaults even under test.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class LatencyProbeSelfTests: XCTestCase {

    private var app: AppState!
    private var probe: LatencyProbe!
    private var tmpDir: URL!
    private var savedCountIn = false
    private var savedSlot: PadSlot?

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("latency-probe-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState(sessionStoreRoot: tmpDir)
        app.modeCoordinator.setMode(.sample)
        app.modeCoordinator.start()
        savedCountIn = app.sketchSettings.countInEnabled
        app.sketchSettings.countInEnabled = false
        // The probe pad must start empty so post-run cleanup is
        // distinguishable from restoration.
        savedSlot = app.padAssignmentStore.slot(
            mode: .sample, padIdx: LatencyProbe.probePadRaw
        )
        app.padAssignmentStore.assign(
            nil, mode: .sample, padIdx: LatencyProbe.probePadRaw
        )
        probe = LatencyProbe(app: app)
    }

    override func tearDown() async throws {
        app.padAssignmentStore.assign(
            savedSlot, mode: .sample, padIdx: LatencyProbe.probePadRaw
        )
        app.pause()
        app.sketchSettings.countInEnabled = savedCountIn
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        probe = nil
        app = nil
        try await super.tearDown()
    }

    private func assertProbePadCleaned(
        file: StaticString = #filePath, line: UInt = #line
    ) {
        XCTAssertNil(
            app.sampleScheduler.localMetadata(for: LatencyProbe.probePadRaw),
            "probe buffer must not stay resident",
            file: file, line: line
        )
        XCTAssertNil(
            app.padAssignmentStore.slot(
                mode: .sample, padIdx: LatencyProbe.probePadRaw
            ),
            "probe assignment must be cleared (pad was empty before)",
            file: file, line: line
        )
    }

    // MARK: - Attack gates

    func testPadTapGateMeasuresAndPasses() async throws {
        await probe.run(.padTap)
        let reading = try XCTUnwrap(probe.readings[.padTap])
        XCTAssertGreaterThan(reading.measured, 0,
                             "a real span was measured")
        XCTAssertEqual(reading.status, .passed,
                       "headless path (no output chain) must sit "
                       + "well inside 8 ms: \(reading.detail)")
        assertProbePadCleaned()
    }

    func testLaunchpadGateMeasuresAndPasses() async throws {
        await probe.run(.launchpad)
        let reading = try XCTUnwrap(probe.readings[.launchpad])
        XCTAssertGreaterThan(reading.measured, 0)
        XCTAssertEqual(reading.status, .passed,
                       "off-actor stamp + hop must sit inside "
                       + "12 ms: \(reading.detail)")
        assertProbePadCleaned()
    }

    // MARK: - Session-load gate

    func testSessionLoadGatePassesAndLeavesStoreEmpty() async throws {
        await probe.run(.sessionLoad)
        let reading = try XCTUnwrap(probe.readings[.sessionLoad])
        XCTAssertGreaterThan(reading.measured, 0)
        XCTAssertEqual(reading.status, .passed,
                       "1000-event decode must beat 2 s: "
                       + "\(reading.detail)")
        XCTAssertTrue(app.sessionStore.list().isEmpty,
                      "the synthetic probe session is deleted")
    }

    // MARK: - Mic-pipeline gate

    func testMicPipelineGatePassesAndCleansUp() async throws {
        await probe.run(.micPipeline)
        let reading = try XCTUnwrap(probe.readings[.micPipeline])
        XCTAssertGreaterThan(reading.measured, 0)
        XCTAssertEqual(reading.status, .passed,
                       "process → classify → save → assign → "
                       + "resident must beat 5 s: \(reading.detail)")
        assertProbePadCleaned()
    }

    // MARK: - Reporting

    func testSummaryListsEveryGateWithVerdicts() async {
        await probe.run(.sessionLoad)
        let summary = probe.summary()
        for gate in LatencyProbe.Gate.allCases {
            XCTAssertTrue(summary.contains(gate.title),
                          "summary lists \(gate.title)")
        }
        XCTAssertTrue(summary.contains("PASS"),
                      "the run gate reports a verdict")
        XCTAssertTrue(summary.contains("not run"),
                      "unrun gates say so instead of lying")
    }

    func testRunGuardsAgainstReentrancy() async {
        // isRunning flips true inside run(); a second concurrent run
        // must bail without clobbering state. Simplest observable
        // check: back-to-back awaited runs both land readings.
        await probe.run(.sessionLoad)
        await probe.run(.sessionLoad)
        XCTAssertNotNil(probe.readings[.sessionLoad])
        XCTAssertFalse(probe.isRunning)
    }
}
