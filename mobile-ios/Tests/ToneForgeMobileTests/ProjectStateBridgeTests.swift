// ProjectStateBridgeTests.swift
//
// The capture→mutate→restore round-trip at the STORE level: seed the
// real stores (isolated UserDefaults suite), capture a snapshot,
// wreck the stores, restore, and assert the workspace came back —
// including the tri-state sectionGates (nil vs empty are different
// states and both must survive). Plus reset-to-song semantics (the
// sequencer pattern library survives a reset; the pad surface does
// not).

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class ProjectStateBridgeTests: XCTestCase {

    private var suiteName: String!
    private var defaults: UserDefaults!
    private var padAssignments: PadAssignmentStore!
    private var sampleSettings: SampleSettingsStore!
    private var patternStore: SequencerPatternStore!
    private var jamSettings: JamSettingsStore!
    private var arrangementStore: ArrangementStore!

    private let analysisId = "song-under-test"

    override func setUp() {
        super.setUp()
        suiteName = "ProjectStateBridgeTests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        padAssignments = PadAssignmentStore(defaults: defaults)
        sampleSettings = SampleSettingsStore(defaults: defaults)
        patternStore = SequencerPatternStore(defaults: defaults)
        jamSettings = JamSettingsStore(defaults: defaults)
        arrangementStore = ArrangementStore(defaults: defaults)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        super.tearDown()
    }

    private func capture(borrows: [BorrowRef] = []) -> ProjectSnapshot {
        ProjectStateBridge.capture(
            analysisId: analysisId,
            padAssignments: padAssignments,
            sampleSettings: sampleSettings,
            patternStore: patternStore,
            jamSettings: jamSettings,
            arrangementStore: arrangementStore,
            borrows: borrows)
    }

    private func restore(_ snapshot: ProjectSnapshot) {
        ProjectStateBridge.restore(
            snapshot,
            analysisId: analysisId,
            padAssignments: padAssignments,
            sampleSettings: sampleSettings,
            patternStore: patternStore,
            jamSettings: jamSettings,
            arrangementStore: arrangementStore)
    }

    /// Seed every store with recognizable state; returns the pattern a
    /// pad references (so tests can assert it snapshots along).
    private func seedStores() -> SequencerPattern {
        let pattern = SequencerPattern(name: "Seeded groove")
        patternStore.save(pattern)
        // An unreferenced pattern must NOT be swept into the snapshot.
        patternStore.save(SequencerPattern(name: "Unreferenced"))

        padAssignments.assign(
            PadSlot(ref: .packPad(packId: "auto-x", padIdx: 3)),
            mode: .sample, padIdx: 11)
        padAssignments.assign(
            PadSlot(ref: .sequence(patternId: pattern.id)),
            mode: .sample, padIdx: 88)
        padAssignments.assign(
            PadSlot(ref: .localSample(id: UUID())),
            mode: .hybrid, padIdx: 45)

        sampleSettings.setPadEffectsOverride(
            .neutral, packId: "auto-x", padIdx: 3)
        sampleSettings.hidePad(packId: "auto-x", padIdx: 7)
        sampleSettings.setSectionGates(["chorus"], for: analysisId)

        arrangementStore.save([0: [11, 12], 3: [88]],
                              analysisId: analysisId)

        jamSettings.launchpadPadCount = 64
        jamSettings.sampleTriggerMode = .latch
        return pattern
    }

    // MARK: - Capture

    func testCaptureReadsSeededState() {
        let pattern = seedStores()
        let snapshot = capture()

        XCTAssertEqual(snapshot.padAssignments["sample"]?.count, 2)
        XCTAssertEqual(
            snapshot.padAssignments["sample"]?["11"]?.ref,
            .packPad(packId: "auto-x", padIdx: 3))
        XCTAssertEqual(snapshot.padFX.count, 1)
        XCTAssertEqual(snapshot.hiddenPads, ["auto-x#7"])
        XCTAssertEqual(snapshot.sectionGates, ["chorus"])
        // Only the REFERENCED pattern snapshots (the library is global).
        XCTAssertEqual(snapshot.sequencerPatterns.map(\.id), [pattern.id])
        XCTAssertEqual(snapshot.arrangement?["0"], [11, 12])
        XCTAssertEqual(snapshot.launchpad?.padCount, 64)
        XCTAssertEqual(snapshot.launchpad?.sampleTriggerMode, "latch")
        XCTAssertNil(snapshot.chopEdits)   // no iOS chop-edit store yet
    }

    func testCaptureTriStateGates() {
        _ = seedStores()
        // nil = allow all → snapshot nil.
        sampleSettings.setSectionGates(nil, for: analysisId)
        XCTAssertNil(capture().sectionGates)
        // empty = deny all → snapshot [] (NOT nil).
        sampleSettings.setSectionGates([], for: analysisId)
        XCTAssertEqual(capture().sectionGates, [])
    }

    // MARK: - Capture → mutate → restore round-trip

    func testRestoreBringsBackCapturedWorkspace() {
        let pattern = seedStores()
        let snapshot = capture()

        // Wreck everything the snapshot covers.
        padAssignments.replaceAll([:])
        sampleSettings.padEffectsByKey = [:]
        sampleSettings.hiddenPadKeys = []
        sampleSettings.setSectionGates([], for: analysisId)
        patternStore.delete(id: pattern.id)
        arrangementStore.save([:], analysisId: analysisId)
        jamSettings.launchpadPadCount = 16
        jamSettings.sampleTriggerMode = .follow

        restore(snapshot)

        XCTAssertEqual(
            padAssignments.slot(mode: .sample, padIdx: 11)?.ref,
            .packPad(packId: "auto-x", padIdx: 3))
        XCTAssertEqual(
            padAssignments.slot(mode: .sample, padIdx: 88)?.ref,
            .sequence(patternId: pattern.id))
        XCTAssertNotNil(
            padAssignments.slot(mode: .hybrid, padIdx: 45))
        XCTAssertNotNil(sampleSettings.padEffectsOverride(
            packId: "auto-x", padIdx: 3))
        XCTAssertTrue(sampleSettings.isPadHidden(
            packId: "auto-x", padIdx: 7))
        XCTAssertEqual(sampleSettings.sectionGates(for: analysisId),
                       ["chorus"])
        // The referenced pattern is back (upserted from the snapshot),
        // so the .sequence pad resolves.
        XCTAssertEqual(patternStore.pattern(id: pattern.id)?.name,
                       "Seeded groove")
        XCTAssertEqual(
            arrangementStore.captured(analysisId: analysisId),
            [0: [11, 12], 3: [88]])
        XCTAssertEqual(jamSettings.launchpadPadCount, 64)
        XCTAssertEqual(jamSettings.sampleTriggerMode, .latch)
    }

    func testRestoreTriStateGates() {
        _ = seedStores()

        // Snapshot with nil gates restores to "allow all" even over a
        // deny-all live state.
        sampleSettings.setSectionGates(nil, for: analysisId)
        let allowAll = capture()
        sampleSettings.setSectionGates([], for: analysisId)
        restore(allowAll)
        XCTAssertNil(sampleSettings.sectionGates(for: analysisId))

        // Snapshot with [] restores deny-all over an allow-all state.
        sampleSettings.setSectionGates([], for: analysisId)
        let denyAll = capture()
        sampleSettings.setSectionGates(nil, for: analysisId)
        restore(denyAll)
        XCTAssertEqual(sampleSettings.sectionGates(for: analysisId), [])
    }

    func testRestoreReplacesRatherThanMerges() {
        _ = seedStores()
        let snapshot = capture()

        // State added AFTER capture must be gone after restore — a
        // workspace is the complete surface, not a patch.
        padAssignments.assign(
            PadSlot(ref: .packPad(packId: "other", padIdx: 1)),
            mode: .sample, padIdx: 22)
        sampleSettings.hidePad(packId: "other", padIdx: 9)

        restore(snapshot)

        XCTAssertNil(padAssignments.slot(mode: .sample, padIdx: 22))
        XCTAssertFalse(sampleSettings.isPadHidden(
            packId: "other", padIdx: 9))
    }

    // MARK: - Reset

    func testResetClearsSurfaceButKeepsPatternLibrary() {
        let pattern = seedStores()

        ProjectStateBridge.reset(
            analysisId: analysisId,
            padAssignments: padAssignments,
            sampleSettings: sampleSettings,
            arrangementStore: arrangementStore)

        XCTAssertTrue(padAssignments.assignmentsByMode.isEmpty)
        XCTAssertTrue(sampleSettings.padEffectsByKey.isEmpty)
        XCTAssertTrue(sampleSettings.hiddenPadKeys.isEmpty)
        XCTAssertNil(sampleSettings.sectionGates(for: analysisId))
        XCTAssertTrue(
            arrangementStore.captured(analysisId: analysisId).isEmpty)
        // The pattern LIBRARY is global user data — reset must not
        // touch it.
        XCTAssertNotNil(patternStore.pattern(id: pattern.id))
    }

    // MARK: - activateFresh (no-workspace song activation)

    func testActivateFreshClearsGlobalStoresOnly() {
        let pattern = seedStores()

        ProjectStateBridge.activateFresh(
            padAssignments: padAssignments,
            sampleSettings: sampleSettings)

        // The GLOBAL stores are swapped to defaults — the previous
        // song's workspace must not leak onto a fresh song's grid
        // (the phantom-pad-tiles bug, desktop D-034 / 8e56570c).
        XCTAssertTrue(padAssignments.assignmentsByMode.isEmpty)
        XCTAssertTrue(sampleSettings.padEffectsByKey.isEmpty)
        XCTAssertTrue(sampleSettings.hiddenPadKeys.isEmpty)
        // analysisId-keyed state and the pattern library are NOT
        // workspace leaks — they stay (unlike the explicit reset).
        XCTAssertEqual(sampleSettings.sectionGates(for: analysisId), ["chorus"])
        XCTAssertEqual(
            arrangementStore.captured(analysisId: analysisId),
            [0: [11, 12], 3: [88]])
        XCTAssertNotNil(patternStore.pattern(id: pattern.id))
    }
}
