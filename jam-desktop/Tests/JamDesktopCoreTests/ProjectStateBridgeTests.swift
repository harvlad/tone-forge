// ProjectStateBridgeTests.swift
//
// Pins the desktop store⇄snapshot translation for Projects v1:
//   * grid-index translation (0..63 row-major top-left ⇄ PadIndex
//     11..88, row 1 = bottom) — the exact formula iOS lays pack pads
//     with, so positions agree across surfaces.
//   * capture/restore round-trip over real (suite-scoped) stores.
//   * preserve-on-round-trip: other modes' pad axes, hiddenPads, and
//     the transform chain of unchanged sample-axis slots survive a
//     desktop save.
//   * tri-state section gates, chop-edit inclusion, launchpad
//     surface settings.
//   * borrow-ref capture/match — content-addressed, never padIdx.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class ProjectStateBridgeTests: XCTestCase {

    private var defaults: UserDefaults!
    private var tempDir: URL!
    private var padAssignments: PadAssignmentStore!
    private var padFX: PadFXStore!
    private var patterns: SequencerPatternStore!
    private var chopEdits: ChopEditStore!
    private var arrangements: ArrangementStore!
    private var launchpad: LaunchpadController!

    private let suite = "ProjectStateBridgeTests"

    override func setUp() async throws {
        defaults = UserDefaults(suiteName: suite)!
        defaults.removePersistentDomain(forName: suite)
        tempDir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        padAssignments = PadAssignmentStore(defaults: defaults)
        padFX = PadFXStore(root: tempDir)
        patterns = SequencerPatternStore(defaults: defaults)
        chopEdits = ChopEditStore(defaults: defaults)
        arrangements = ArrangementStore(defaults: defaults)
        launchpad = LaunchpadController(nowProvider: { 0 })
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suite)
        try? FileManager.default.removeItem(at: tempDir)
    }

    // MARK: - Index translation

    func testTranslationAnchors() {
        // Desktop top-left (0) = engine 81 (row 8 = top, col 1).
        XCTAssertEqual(ProjectStateBridge.padIndexRaw(fromGridSlot: 0), 81)
        // Desktop top-right (7) = engine 88.
        XCTAssertEqual(ProjectStateBridge.padIndexRaw(fromGridSlot: 7), 88)
        // Desktop bottom-left (56) = engine 11.
        XCTAssertEqual(ProjectStateBridge.padIndexRaw(fromGridSlot: 56), 11)
        // Desktop bottom-right (63) = engine 18.
        XCTAssertEqual(ProjectStateBridge.padIndexRaw(fromGridSlot: 63), 18)
    }

    func testTranslationIsBijectiveOverTheGrid() {
        var seen = Set<Int>()
        for slot in 0..<64 {
            let raw = ProjectStateBridge.padIndexRaw(fromGridSlot: slot)
            XCTAssertNotNil(raw)
            XCTAssertTrue(PadIndex(raw!).isValid)
            seen.insert(raw!)
            XCTAssertEqual(
                ProjectStateBridge.gridSlot(fromPadIndexRaw: raw!), slot)
        }
        XCTAssertEqual(seen.count, 64)
    }

    func testInvalidIndicesRejected() {
        XCTAssertNil(ProjectStateBridge.padIndexRaw(fromGridSlot: -1))
        XCTAssertNil(ProjectStateBridge.padIndexRaw(fromGridSlot: 64))
        for raw in [0, 9, 10, 19, 80, 89, 90, 99, -5, 111] {
            XCTAssertNil(
                ProjectStateBridge.gridSlot(fromPadIndexRaw: raw),
                "raw \(raw) should be invalid")
        }
    }

    func testMatchesIOSPackLayoutFormula() {
        // iOS places pack padIdx d at
        // PadIndex.at(row: 8 - d/8, col: d%8 + 1)
        // (ModeCoordinator+Layout). The desktop translation must be
        // the same function of the row-major slot.
        for d in 0..<64 {
            let ios = PadIndex.at(row: 8 - d / 8, col: d % 8 + 1).rawValue
            XCTAssertEqual(ProjectStateBridge.padIndexRaw(fromGridSlot: d), ios)
        }
    }

    // MARK: - Ref conversion

    func testRefConversionRoundTrips() {
        let refs: [PadSlotReference] = [
            .sequence(patternId: UUID()),
            .localSample(id: UUID()),
            .packPad(packId: "kit:abc", padIdx: 5),
        ]
        for ref in refs {
            XCTAssertEqual(
                ProjectStateBridge.desktopRef(
                    ProjectStateBridge.engineRef(ref)),
                ref)
        }
    }

    // MARK: - Capture / restore round-trip

    private func capture(
        analysisId: String = "song1",
        preserved: ProjectSnapshot? = nil,
        borrows: [BorrowRef] = []
    ) -> ProjectSnapshot {
        ProjectStateBridge.capture(
            analysisId: analysisId,
            padAssignments: padAssignments,
            padFX: padFX,
            patternStore: patterns,
            chopEditStore: chopEdits,
            arrangementStore: arrangements,
            launchpad: launchpad,
            preserved: preserved,
            borrows: borrows
        )
    }

    private func restore(
        _ snapshot: ProjectSnapshot, analysisId: String = "song1"
    ) {
        ProjectStateBridge.restore(
            snapshot,
            analysisId: analysisId,
            padAssignments: padAssignments,
            padFX: padFX,
            patternStore: patterns,
            chopEditStore: chopEdits,
            arrangementStore: arrangements,
            launchpad: launchpad
        )
    }

    func testCaptureRestoreRoundTrip() {
        // Build a workspace across every store.
        let patternId = UUID()
        var pattern = SequencerPattern(id: patternId, name: "Groove")
        pattern.addTrack(
            for: .packPad(packId: "kit:abc", padIdx: 0), name: "Kick")
        patterns.save(pattern)
        padAssignments.assign(.sequence(patternId: patternId), padIdx: 0)
        padAssignments.assign(
            .packPad(packId: "kit:abc", padIdx: 3), padIdx: 63)
        let sampleId = UUID()
        padAssignments.assign(.localSample(id: sampleId), padIdx: 12)

        let fx = SamplePadEffects(
            delayTimeSec: 0.5, delayFeedback: 40, delayMix: 30,
            filterCutoffHz: 2_000, filterResonanceDb: 6)
        padFX.setEffects(fx, packId: "kit:abc", padIdx: 3)

        var edits = ChopEdits(presetKey: "harmonic")
        edits.boundaryEdits[2] = ChopBoundaryEdit(
            chopIndex: 2, originalStart: 1.0, originalEnd: 2.0,
            editedStart: 1.25, editedEnd: 2.0)
        chopEdits.save(edits, analysisId: "song1")

        arrangements.save([0: [1, 2], 3: [5]], analysisId: "song1")

        launchpad.padCount = 16
        launchpad.playbackMode = .latch
        launchpad.sectionGate = ["Chorus", "Verse"]

        let snapshot = capture()

        // Wire shape spot-checks.
        let sample = snapshot.padAssignments["sample"]
        XCTAssertEqual(sample?.count, 3)
        // Desktop slot 0 (top-left) travels as engine "81".
        XCTAssertEqual(sample?["81"]?.ref, .sequence(patternId: patternId))
        // Desktop slot 63 (bottom-right) travels as engine "18".
        XCTAssertEqual(sample?["18"]?.ref,
                       .packPad(packId: "kit:abc", padIdx: 3))
        // Desktop slot 12 (2nd row from top, 5th col) = engine 75.
        XCTAssertEqual(sample?["75"]?.ref, .localSample(id: sampleId))
        XCTAssertEqual(snapshot.padFX["kit:abc#3"], fx)
        XCTAssertEqual(snapshot.sectionGates, ["Chorus", "Verse"])
        XCTAssertEqual(snapshot.sequencerPatterns.map(\.id), [patternId])
        XCTAssertEqual(snapshot.chopEdits?["harmonic"]?.boundaryEdits.count, 1)
        XCTAssertEqual(snapshot.arrangement?["0"], [1, 2])
        XCTAssertEqual(snapshot.launchpad?.padCount, 16)
        XCTAssertEqual(snapshot.launchpad?.sampleTriggerMode, "latch")

        // Wipe every store, then restore.
        padAssignments.clearAll()
        padFX.replaceAll([:])
        patterns.delete(id: patternId)
        chopEdits.replaceAll([:], analysisId: "song1")
        arrangements.save([:], analysisId: "song1")
        launchpad.padCount = 64
        launchpad.playbackMode = .follow
        launchpad.sectionGate = nil

        restore(snapshot)

        XCTAssertEqual(padAssignments.slot(padIdx: 0),
                       .sequence(patternId: patternId))
        XCTAssertEqual(padAssignments.slot(padIdx: 63),
                       .packPad(packId: "kit:abc", padIdx: 3))
        XCTAssertEqual(padAssignments.slot(padIdx: 12),
                       .localSample(id: sampleId))
        XCTAssertEqual(padFX.effects(packId: "kit:abc", padIdx: 3), fx)
        XCTAssertEqual(patterns.pattern(id: patternId)?.name, "Groove")
        XCTAssertEqual(
            chopEdits.edits(analysisId: "song1", presetKey: "harmonic")
                .boundaryEdits[2]?.editedStart ?? 0, 1.25, accuracy: 1e-9)
        XCTAssertEqual(arrangements.captured(analysisId: "song1"),
                       [0: [1, 2], 3: [5]])
        XCTAssertEqual(launchpad.padCount, 16)
        XCTAssertEqual(launchpad.playbackMode, .latch)
        XCTAssertEqual(launchpad.sectionGate, ["Chorus", "Verse"])

        // Re-capture equals the original snapshot (stable round-trip).
        let again = capture(preserved: snapshot)
        XCTAssertEqual(again, snapshot)
    }

    func testOtherModesAndHiddenPadsPreservedOnRoundTrip() {
        // An iOS-authored snapshot with a hybrid-mode axis, hidden
        // pads, and a sample slot carrying a transform chain.
        let seqId = UUID()
        let transformedSlot = PadSlot(
            ref: .packPad(packId: "kit:abc", padIdx: 1),
            transforms: [.reverse],
            timing: TransformTiming()
        )
        let hybridSlot = PadSlot(ref: .sequence(patternId: seqId))
        let incoming = ProjectSnapshot(
            padAssignments: [
                "sample": ["81": transformedSlot],
                "hybrid": ["55": hybridSlot],
            ],
            hiddenPads: ["kit:abc#7"]
        )

        restore(incoming)
        // Desktop's store now holds the ref (slot 0 = raw 81).
        XCTAssertEqual(padAssignments.slot(padIdx: 0),
                       .packPad(packId: "kit:abc", padIdx: 1))

        let out = capture(preserved: incoming)
        // Hybrid axis passes through byte-identical.
        XCTAssertEqual(out.padAssignments["hybrid"], ["55": hybridSlot])
        // hiddenPads pass through.
        XCTAssertEqual(out.hiddenPads, ["kit:abc#7"])
        // The unchanged sample slot keeps its TRANSFORM CHAIN verbatim.
        XCTAssertEqual(out.padAssignments["sample"]?["81"], transformedSlot)

        // Reassigning the pad on desktop drops the stale chain (the
        // ref changed — the preserved PadSlot no longer describes it).
        padAssignments.assign(
            .packPad(packId: "kit:abc", padIdx: 2), padIdx: 0)
        let changed = capture(preserved: incoming)
        XCTAssertEqual(changed.padAssignments["sample"]?["81"],
                       PadSlot(ref: .packPad(packId: "kit:abc", padIdx: 2)))
    }

    func testSectionGateTriState() {
        // nil = allow all → key absent.
        launchpad.sectionGate = nil
        XCTAssertNil(capture().sectionGates)
        // empty = deny all → PRESENT empty array.
        launchpad.sectionGate = []
        XCTAssertEqual(capture().sectionGates, [])
        // allowlist survives restore.
        launchpad.sectionGate = ["Bridge"]
        let snap = capture()
        launchpad.sectionGate = nil
        restore(snap)
        XCTAssertEqual(launchpad.sectionGate, ["Bridge"])
        // And nil clears on restore.
        launchpad.sectionGate = ["Chorus"]
        restore(ProjectSnapshot())
        XCTAssertNil(launchpad.sectionGate)
    }

    func testRestoreClampsUnknownLaunchpadValues() {
        let snap = ProjectSnapshot(
            launchpad: LaunchpadSnapshot(
                padCount: 999, sampleTriggerMode: "tap"))
        restore(snap)
        // Contract: anything but 64 clamps to 16; legacy "tap" → follow.
        XCTAssertEqual(launchpad.padCount, 16)
        XCTAssertEqual(launchpad.playbackMode, .follow)
    }

    func testReset() {
        padAssignments.assign(
            .packPad(packId: "p", padIdx: 0), padIdx: 4)
        padFX.setEffects(.neutral, packId: "p", padIdx: 0)
        launchpad.sectionGate = ["Verse"]
        var edits = ChopEdits(presetKey: "harmonic")
        edits.splits.append(ChopSplit(parentIndex: 0, splitPoint: 0.5))
        chopEdits.save(edits, analysisId: "song1")
        arrangements.save([1: [2]], analysisId: "song1")

        ProjectStateBridge.reset(
            analysisId: "song1",
            padAssignments: padAssignments,
            padFX: padFX,
            chopEditStore: chopEdits,
            arrangementStore: arrangements,
            launchpad: launchpad
        )

        XCTAssertTrue(padAssignments.assignments.isEmpty)
        XCTAssertTrue(padFX.effectsByKey.isEmpty)
        XCTAssertNil(launchpad.sectionGate)
        XCTAssertTrue(chopEdits.edits(analysisId: "song1").isEmpty)
        XCTAssertTrue(arrangements.captured(analysisId: "song1").isEmpty)
    }

    // MARK: - Borrow refs (content-addressed)

    private func borrowPad(
        idx: Int, source: String?, stemRole: String? = "drums",
        span: (Double, Double)? = (12.0, 19.5),
        transpose: Int? = 0, assetId: String? = nil
    ) -> SamplePad {
        SamplePad(
            padIdx: idx, name: "Pad \(idx)", family: .mixed,
            assetId: assetId, source: source, stemRole: stemRole,
            sourceLoopStartSec: span?.0, sourceLoopEndSec: span?.1,
            transposeSemis: transpose
        )
    }

    func testBorrowRefsCaptureOnlyContentAddressedDonorPads() {
        let pads = [
            borrowPad(idx: 0, source: "initial"),          // host — dropped
            borrowPad(idx: 5, source: "donor"),            // captured
            borrowPad(idx: 6, source: "donor", span: nil), // span-less — dropped
        ]
        let refs = ProjectStateBridge.borrowRefs(
            pads: pads, donorId: "donor123", donorName: "Donor Song")
        XCTAssertEqual(refs.count, 1)
        XCTAssertEqual(refs[0].donorSongId, "donor123")
        XCTAssertEqual(refs[0].stemRole, "drums")
        XCTAssertEqual(refs[0].loopStartSec, 12.0, accuracy: 1e-9)
        XCTAssertEqual(refs[0].loopEndSec, 19.5, accuracy: 1e-9)
        XCTAssertEqual(refs[0].donorName, "Donor Song")
    }

    func testBorrowRefMatchIsContentNotIndex() {
        let saved = ProjectStateBridge.borrowRefs(
            pads: [borrowPad(idx: 5, source: "donor")],
            donorId: "d", donorName: nil)
        // Fresh response renumbered the pad (idx 9) but the SPAN is the
        // same (within the backend's 1 ms rounding) → still resolves.
        let renumbered = borrowPad(
            idx: 9, source: "donor", span: (12.0005, 19.4995))
        XCTAssertTrue(ProjectStateBridge.missingRefs(
            saved, in: [renumbered]).isEmpty)
        // Same index, DIFFERENT span → missing (index is never identity).
        let drifted = borrowPad(idx: 5, source: "donor", span: (30.0, 37.5))
        XCTAssertEqual(ProjectStateBridge.missingRefs(
            saved, in: [drifted]).count, 1)
        // assetId is an alternative match key for re-cut spans.
        let savedWithAsset = ProjectStateBridge.borrowRefs(
            pads: [borrowPad(idx: 5, source: "donor", assetId: "asset-1")],
            donorId: "d", donorName: nil)
        let recut = borrowPad(
            idx: 2, source: "donor", span: (40.0, 47.5), assetId: "asset-1")
        XCTAssertTrue(ProjectStateBridge.missingRefs(
            savedWithAsset, in: [recut]).isEmpty)
    }

    func testLogicalStem() {
        XCTAssertEqual(ProjectStateBridge.logicalStem("drums"), "drums")
        XCTAssertEqual(ProjectStateBridge.logicalStem("drums_kick"), "drums")
        XCTAssertEqual(ProjectStateBridge.logicalStem("bass"), "bass")
        XCTAssertEqual(ProjectStateBridge.logicalStem("vocals"), "vocals")
        XCTAssertEqual(ProjectStateBridge.logicalStem("guitar_center"), "other")
        XCTAssertEqual(ProjectStateBridge.logicalStem("other"), "other")
    }

    // MARK: - Phantom pack-pad fix (no-workspace song ⇒ empty workspace)

    /// The phantom-pad regression: `.packPad` slots persisted in the
    /// MACHINE-GLOBAL UserDefaults store by an earlier song/app launch
    /// rendered as purple "speaker" tiles on EVERY song's grid. A song
    /// activating with NO workspace must swap in the empty workspace over
    /// the two global stores (same whole-value semantics as a restore).
    func testClearGlobalPadStateEmptiesBothGlobalStores() {
        padAssignments.assign(
            .packPad(packId: "auto-oldsong-intermediate", padIdx: 3),
            padIdx: 24)
        padAssignments.assign(
            .packPad(packId: "auto-oldsong-intermediate", padIdx: 7),
            padIdx: 33)
        padFX.setEffects(
            SamplePadEffects(
                delayTimeSec: 0.4, delayFeedback: 30, delayMix: 25,
                filterCutoffHz: 4_000, filterResonanceDb: 3),
            packId: "somepack", padIdx: 3)

        ProjectStateBridge.clearGlobalPadState(
            padAssignments: padAssignments, padFX: padFX)

        XCTAssertTrue(padAssignments.assignments.isEmpty)
        XCTAssertTrue(padFX.effectsByKey.isEmpty)
    }

    /// The clear must PERSIST — the phantoms lived in UserDefaults, so an
    /// in-memory clear alone would resurrect them on the next launch.
    func testClearGlobalPadStatePersistsAcrossReload() {
        padAssignments.assign(
            .packPad(packId: "stale-pack", padIdx: 0), padIdx: 24)
        ProjectStateBridge.clearGlobalPadState(
            padAssignments: padAssignments, padFX: padFX)

        // Fresh store instances over the same backing suite/dir = relaunch.
        let reloadedAssignments = PadAssignmentStore(defaults: defaults)
        let reloadedFX = PadFXStore(root: tempDir)
        XCTAssertTrue(reloadedAssignments.assignments.isEmpty)
        XCTAssertTrue(reloadedFX.effectsByKey.isEmpty)
        XCTAssertNil(reloadedAssignments.slot(padIdx: 24))
    }
}
