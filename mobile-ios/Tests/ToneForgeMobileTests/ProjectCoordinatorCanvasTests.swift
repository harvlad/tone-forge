// ProjectCoordinatorCanvasTests.swift
//
// Projects v2 — blank creative canvas. Pins the coordinator-level
// lifecycle against a real AppState (no audio boot, no network):
//
//   * createBlankProject mounts the EMPTY canvas pack, clears the pad
//     surface, and saves a durable baseSongId-nil project that becomes
//     the auto-save target (activeBlankProjectId).
//   * saveBlankNow captures the live canvas (pins + launchpad
//     settings) into that project's own file — there is no per-song
//     working sidecar for a canvas.
//   * load(blank) restores the snapshot's store state over a freshly
//     mounted canvas (borrow re-derivation is network and covered by
//     the donor-only backend route test + BorrowRef matching tests).
//   * resetCanvas = clear: stores emptied, stored snapshot emptied.
//   * The donor-only request rule (no host song → host==donor) and the
//     cross-song picker mapping are pure and pinned directly.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class ProjectCoordinatorCanvasTests: XCTestCase {

    private var app: AppState!
    private var root: URL!
    private var coordinator: ProjectCoordinator!

    override func setUpWithError() throws {
        try super.setUpWithError()
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("canvas-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true)
        app = AppState()
        coordinator = ProjectCoordinator(
            app: app, store: ProjectStore(root: root))
        // Shared-store hygiene: these tests assert on the pad surface,
        // so start from a known-empty one.
        app.padAssignmentStore.replaceAll([:])
        app.sampleSettings.padEffectsByKey = [:]
        app.sampleSettings.hiddenPadKeys = []
    }

    override func tearDownWithError() throws {
        app.padAssignmentStore.replaceAll([:])
        app.sampleSettings.padEffectsByKey = [:]
        app.sampleSettings.hiddenPadKeys = []
        try? FileManager.default.removeItem(at: root)
        coordinator = nil
        app = nil
        try super.tearDownWithError()
    }

    // MARK: - Canvas pack mount (no bundle)

    func testCreateBlankProjectMountsEmptyCanvasWithoutBundle() {
        // Pre-existing sketch-surface state must not leak into a NEW
        // canvas.
        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: "starter", padIdx: 1)),
            mode: .sample, padIdx: 12)

        let project = coordinator.createBlankProject(named: "My canvas")

        XCTAssertNil(app.currentBundle)
        XCTAssertTrue(app.canvasModeOn)
        XCTAssertEqual(app.activeSamplePack?.pack.packId,
                       SampleBank.canvasPackId)
        XCTAssertTrue(app.activeSamplePack?.pack.pads.isEmpty ?? false,
                      "the canvas pack is 64 EMPTY slots — no pads")
        // Surface cleared: the canvas starts from scratch.
        XCTAssertTrue(app.padAssignmentStore.assignmentsByMode.isEmpty)
        // The project exists as a durable Library row immediately.
        XCTAssertTrue(project.isBlankCanvas)
        XCTAssertEqual(coordinator.activeBlankProjectId, project.id)
        XCTAssertEqual(coordinator.savedProjects.map(\.id), [project.id])
        // Canvas lands on the Build surface in Samples mode.
        XCTAssertEqual(app.selectedTab, .jam)
        XCTAssertEqual(app.jamSettings.padMode, .samples)
    }

    // MARK: - Auto-save target (blank project's own file)

    func testSaveBlankNowCapturesCanvasIntoProjectFile() throws {
        let project = coordinator.createBlankProject(named: "Canvas")

        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: "starter", padIdx: 3)),
            mode: .sample, padIdx: 11)
        app.jamSettings.sampleTriggerMode = .latch
        coordinator.saveBlankNow(projectId: project.id)

        let saved = try ProjectStore(root: root)
            .load(projectId: project.id)
        XCTAssertTrue(saved.isBlankCanvas)
        XCTAssertEqual(
            saved.snapshot.padAssignments["sample"]?["11"]?.ref,
            .packPad(packId: "starter", padIdx: 3))
        XCTAssertEqual(saved.snapshot.launchpad?.sampleTriggerMode,
                       "latch")
        // A canvas has no sections: gates stay the nil tri-state and
        // no arrangement is captured.
        XCTAssertNil(saved.snapshot.sectionGates)
        XCTAssertNil(saved.snapshot.arrangement)
    }

    func testSaveBlankNowRefusesStaleTargets() throws {
        let project = coordinator.createBlankProject(named: "Canvas")
        let second = coordinator.createBlankProject(named: "Canvas 2")
        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: "starter", padIdx: 3)),
            mode: .sample, padIdx: 11)
        // The FIRST project is no longer live — a debounce that fires
        // late must not clobber it with the second canvas's state.
        coordinator.saveBlankNow(projectId: project.id)
        let saved = try ProjectStore(root: root).load(projectId: project.id)
        XCTAssertTrue(saved.snapshot.padAssignments.isEmpty)
        XCTAssertEqual(coordinator.activeBlankProjectId, second.id)
    }

    // MARK: - Load (snapshot round-trip with baseSongId nil)

    func testLoadBlankProjectRestoresStoresOverFreshCanvas() throws {
        let patternId = UUID()
        var snapshot = ProjectSnapshot(
            padAssignments: ["sample": [
                "11": PadSlot(ref: .packPad(packId: "starter", padIdx: 5)),
                "88": PadSlot(ref: .sequence(patternId: patternId)),
            ]],
            hiddenPads: ["starter#2"],
            sequencerPatterns: [
                SequencerPattern(id: patternId, name: "Canvas groove"),
            ]
        )
        snapshot.launchpad = LaunchpadSnapshot(
            padCount: 64, sampleTriggerMode: "oneShot")
        let project = Project(
            name: "Saved canvas", baseSongId: nil, snapshot: snapshot)
        try ProjectStore(root: root).save(project)
        coordinator = ProjectCoordinator(
            app: app, store: ProjectStore(root: root))

        // Dirty the live surface, then load the blank project over it.
        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: "other", padIdx: 9)),
            mode: .sample, padIdx: 22)
        coordinator.load(project)

        XCTAssertNil(app.currentBundle)
        XCTAssertEqual(app.activeSamplePack?.pack.packId,
                       SampleBank.canvasPackId)
        XCTAssertEqual(coordinator.activeBlankProjectId, project.id)
        XCTAssertEqual(
            app.padAssignmentStore.slot(mode: .sample, padIdx: 11)?.ref,
            .packPad(packId: "starter", padIdx: 5))
        XCTAssertEqual(
            app.padAssignmentStore.slot(mode: .sample, padIdx: 88)?.ref,
            .sequence(patternId: patternId))
        // Restore REPLACES: the pre-load stray pin is gone.
        XCTAssertNil(app.padAssignmentStore.slot(mode: .sample, padIdx: 22))
        XCTAssertTrue(app.sampleSettings.isPadHidden(
            packId: "starter", padIdx: 2))
        XCTAssertEqual(
            app.sequencerPatternStore.pattern(id: patternId)?.name,
            "Canvas groove")
        XCTAssertEqual(app.jamSettings.sampleTriggerMode, .oneShot)
    }

    // MARK: - Reset = clear canvas

    func testResetCanvasClearsSurfaceAndStoredSnapshot() throws {
        let project = coordinator.createBlankProject(named: "Canvas")
        app.padAssignmentStore.assign(
            PadSlot(ref: .packPad(packId: "starter", padIdx: 3)),
            mode: .sample, padIdx: 11)
        coordinator.saveBlankNow(projectId: project.id)

        coordinator.resetCanvas()

        XCTAssertTrue(app.padAssignmentStore.assignmentsByMode.isEmpty)
        XCTAssertEqual(app.activeSamplePack?.pack.packId,
                       SampleBank.canvasPackId)
        let stored = try ProjectStore(root: root).load(projectId: project.id)
        XCTAssertEqual(stored.snapshot, ProjectSnapshot())
        // Still the live canvas project — clearing is not closing.
        XCTAssertEqual(coordinator.activeBlankProjectId, project.id)
    }

    func testDeleteActiveBlankProjectEndsAutoSaveSession() {
        let project = coordinator.createBlankProject(named: "Canvas")
        coordinator.delete(project)
        XCTAssertNil(coordinator.activeBlankProjectId)
        XCTAssertTrue(coordinator.savedProjects.isEmpty)
    }

    // MARK: - Donor-only borrow request rule

    func testBorrowRequestHostIsDonorWhenNoSongLoaded() {
        // Blank canvas: the donor IS the only song — host==donor, which
        // the backend serves as a donor-only kit.
        XCTAssertEqual(
            AppState.borrowRequestHost(currentAnalysisId: nil,
                                       donorId: "donor-7"),
            "donor-7")
        // With a song loaded the host stays the host.
        XCTAssertEqual(
            AppState.borrowRequestHost(currentAnalysisId: "song-1",
                                       donorId: "donor-7"),
            "song-1")
    }

    // MARK: - Cross-song picker sources

    func testPickerSongsMapsHistoryAndExcludesLoadedSong() {
        let entries = [
            HistoryEntry(id: "a", timestamp: "t", name: "Song A",
                         summary: "120 bpm · C major"),
            HistoryEntry(id: "b", timestamp: "t", name: nil),
            HistoryEntry(id: "current", timestamp: "t", name: "Me"),
        ]
        let songs = AppState.pickerSongs(from: entries,
                                         excludingId: "current")
        XCTAssertEqual(songs.map(\.id), ["a", "b"])
        XCTAssertEqual(songs[0].name, "Song A")
        XCTAssertEqual(songs[0].detail, "120 bpm · C major")
        // Nameless entries fall back to their id, never an empty row.
        XCTAssertEqual(songs[1].name, "b")
        // No loaded song (blank canvas): the WHOLE library is offered.
        XCTAssertEqual(
            AppState.pickerSongs(from: entries, excludingId: nil).count, 3)
    }
}
