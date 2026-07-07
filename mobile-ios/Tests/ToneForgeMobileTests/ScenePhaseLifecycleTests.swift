// ScenePhaseLifecycleTests.swift
//
// P7 scene-phase glue on a headless AppState: `.background` parks
// the transport (stamping the recording's pause gap), force-saves
// an in-flight take under its arm-time identity while leaving the
// recorder armed, and is a clean no-op when nothing is running.
// `.active` / `.inactive` are benign with no Launchpad wired.
//
// Hermetic: session store rooted in a temp dir; no packs and no
// engine start needed — the recorder trips on bare bus events.

import XCTest
import SwiftUI
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class ScenePhaseLifecycleTests: XCTestCase {

    private var app: AppState!
    private var tmpDir: URL!
    private var savedCountIn: Bool = false

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("scene-phase-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState(sessionStoreRoot: tmpDir)
        app.modeCoordinator.setMode(.sample)
        app.modeCoordinator.start()
        savedCountIn = app.sketchSettings.countInEnabled
        app.sketchSettings.countInEnabled = false
    }

    override func tearDown() async throws {
        app.cancelSessionRecording()
        app.pause()
        app.sketchSettings.countInEnabled = savedCountIn
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        try await super.tearDown()
    }

    private func publishPadDown(at t: Double) {
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padDown(row: 8, col: 1),
            timestamp: t,
            hostTime: 0
        ))
    }

    func testBackgroundParksTransportAndAutosavesTake() throws {
        app.armSessionRecording()
        publishPadDown(at: 0.0)
        XCTAssertEqual(app.sessionRecorder.state, .recording)
        XCTAssertTrue(app.isPlaying)
        XCTAssertTrue(app.sessionStore.list().isEmpty,
                      "nothing autosaved before the 10 s tick")

        app.handleScenePhase(.background)

        XCTAssertFalse(app.isPlaying, "background parks the transport")
        XCTAssertEqual(app.sessionRecorder.state, .recording,
                       "recorder stays armed so returning resumes"
                       + " the same take")
        let saved = app.sessionStore.list()
        XCTAssertEqual(saved.count, 1, "take force-saved immediately")
        XCTAssertEqual(saved.first?.sessionId,
                       app.sessionRecorder.snapshot().sessionId,
                       "saved under the arm-time identity")
        XCTAssertTrue(
            saved.first?.events.contains {
                if case .gap = $0.kind { return true }
                return false
            } ?? false,
            "the pause gap is stamped BEFORE the force-save"
        )

        // Coming back and stopping continues the very same take —
        // one session on disk, not two.
        app.handleScenePhase(.active)
        app.stopAndSaveSessionRecording()
        XCTAssertEqual(app.sessionStore.list().count, 1)
        XCTAssertEqual(app.savedSessions.first?.sessionId,
                       saved.first?.sessionId)
    }

    func testBackgroundWithArmedButEmptyTakeSavesNothing() {
        app.armSessionRecording()
        XCTAssertEqual(app.sessionRecorder.state, .armed)

        app.handleScenePhase(.background)

        XCTAssertFalse(app.isPlaying)
        XCTAssertEqual(app.sessionRecorder.state, .armed,
                       "still armed — nothing to save yet")
        XCTAssertTrue(app.sessionStore.list().isEmpty,
                      "no empty session file must appear")
    }

    func testAllPhasesAreBenignWhenIdle() {
        // No recording, no playback, no Launchpad — every phase
        // transition must be a clean no-op.
        app.handleScenePhase(.background)
        app.handleScenePhase(.inactive)
        app.handleScenePhase(.active)
        XCTAssertFalse(app.isPlaying)
        XCTAssertEqual(app.sessionRecorder.state, .idle)
        XCTAssertTrue(app.sessionStore.list().isEmpty)
        XCTAssertNil(app.layerError)
    }

    func testInactiveChangesNothingMidTake() {
        // Notification shade / app switcher peek: deliberately no
        // teardown — the performance keeps running.
        app.armSessionRecording()
        publishPadDown(at: 0.0)

        app.handleScenePhase(.inactive)

        XCTAssertTrue(app.isPlaying, "inactive must not park the transport")
        XCTAssertEqual(app.sessionRecorder.state, .recording)
        XCTAssertTrue(app.sessionStore.list().isEmpty,
                      "no force-save on inactive")
    }
}
