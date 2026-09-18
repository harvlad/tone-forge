// ProjectSnapshotTests.swift
//
// Wire-contract tests for the Projects v1 snapshot types
// (Projects/ProjectSnapshot.swift): full round-trip, the LOAD-BEARING
// sectionGates tri-state (nil = allow all must stay ABSENT on the
// wire; [] = deny all must stay PRESENT), and BorrowRef's
// content-addressing (identity is the donor span/assetId — NEVER the
// borrow response's renumbered padIdx).

import XCTest
@testable import ToneForgeEngine

final class ProjectSnapshotTests: XCTestCase {

    // MARK: - Helpers

    private func fullSnapshot() -> ProjectSnapshot {
        let patternId = UUID()
        let slotA = PadSlot(ref: .packPad(packId: "auto-abc", padIdx: 3))
        let slotB = PadSlot(ref: .localSample(id: UUID()))
        let slotC = PadSlot(ref: .sequence(patternId: patternId))
        return ProjectSnapshot(
            padAssignments: [
                "sample": ["11": slotA, "88": slotB],
                "hybrid": ["45": slotC],
            ],
            padFX: [
                "auto-abc#3": SamplePadEffects.neutral,
            ],
            hiddenPads: ["auto-abc#7", "starter#2"],
            sectionGates: ["chorus", "verse"],
            sequencerPatterns: [SequencerPattern(id: patternId, name: "Groove")],
            chopEdits: ["harmonic": {
                var e = ChopEdits(presetKey: "harmonic")
                e.boundaryEdits[2] = ChopBoundaryEdit(
                    chopIndex: 2, originalStart: 1.0, originalEnd: 2.0,
                    editedStart: 1.1, editedEnd: 2.2)
                return e
            }()],
            arrangement: ["0": [11, 12], "3": [45]],
            launchpad: LaunchpadSnapshot(
                padCount: 64, sampleTriggerMode: "latch"),
            borrows: [BorrowRef(
                donorSongId: "donor-1", stemRole: "guitar_center",
                loopStartSec: 12.5, loopEndSec: 20.5, transposeSemis: -5,
                targetPadIdx: 40, assetId: "asset-9",
                donorName: "Donor Song")]
        )
    }

    private func roundTrip(_ s: ProjectSnapshot) throws -> ProjectSnapshot {
        let data = try JSONEncoder().encode(s)
        return try JSONDecoder().decode(ProjectSnapshot.self, from: data)
    }

    private func jsonObject(_ s: ProjectSnapshot) throws -> [String: Any] {
        let data = try JSONEncoder().encode(s)
        return try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    // MARK: - Round-trip

    func testFullRoundTrip() throws {
        let original = fullSnapshot()
        let decoded = try roundTrip(original)
        XCTAssertEqual(decoded, original)
        XCTAssertEqual(decoded.schemaVersion,
                       ProjectSnapshot.currentSchemaVersion)
        // Spot-check the parts most likely to rot through a re-encode.
        XCTAssertEqual(decoded.padAssignments["sample"]?.count, 2)
        XCTAssertEqual(decoded.arrangement?["3"], [45])
        XCTAssertEqual(decoded.launchpad?.sampleTriggerMode, "latch")
        XCTAssertEqual(decoded.chopEdits?["harmonic"]?
            .boundaryEdits[2]?.editedEnd, 2.2)
        XCTAssertEqual(decoded.borrows.first?.assetId, "asset-9")
    }

    func testProjectMetadataRoundTripAndDuplicate() throws {
        let project = Project(
            name: "Friday jam", baseSongId: "song-1",
            baseSongTitle: "My Song", snapshot: fullSnapshot())
        let data = try JSONEncoder().encode(project)
        let decoded = try JSONDecoder().decode(Project.self, from: data)
        XCTAssertEqual(decoded, project)

        let copy = project.duplicated(name: "Friday jam copy")
        XCTAssertNotEqual(copy.id, project.id)
        XCTAssertEqual(copy.baseSongId, project.baseSongId)
        XCTAssertEqual(copy.snapshot, project.snapshot)
    }

    // MARK: - sectionGates tri-state (load-bearing)

    func testSectionGatesNilStaysAbsentOnTheWire() throws {
        var s = fullSnapshot()
        s.sectionGates = nil                       // allow ALL sections
        let obj = try jsonObject(s)
        XCTAssertNil(obj["sectionGates"],
                     "nil gates must be an ABSENT key — [] means deny all")
        let decoded = try roundTrip(s)
        XCTAssertNil(decoded.sectionGates)
    }

    func testSectionGatesEmptyStaysPresentOnTheWire() throws {
        var s = fullSnapshot()
        s.sectionGates = []                        // deny ALL sections
        let obj = try jsonObject(s)
        let gates = try XCTUnwrap(obj["sectionGates"] as? [Any],
                                  "empty gates must be a PRESENT [] key")
        XCTAssertTrue(gates.isEmpty)
        let decoded = try roundTrip(s)
        XCTAssertEqual(decoded.sectionGates, [])
        XCTAssertNotNil(decoded.sectionGates,
                        "deny-all must not decay into allow-all")
    }

    func testMinimalSnapshotDecodes() throws {
        // A future/foreign writer sending only schemaVersion must not
        // fail the decode (additive-field policy).
        let data = Data(#"{"schemaVersion": 1}"#.utf8)
        let decoded = try JSONDecoder().decode(ProjectSnapshot.self, from: data)
        XCTAssertTrue(decoded.padAssignments.isEmpty)
        XCTAssertNil(decoded.sectionGates)
        XCTAssertNil(decoded.arrangement)
        XCTAssertTrue(decoded.borrows.isEmpty)
    }

    // MARK: - BorrowRef content-addressing

    private func donorPad(
        padIdx: Int, start: Double = 12.5, end: Double = 20.5,
        assetId: String? = "asset-9", source: String? = "donor"
    ) -> SamplePad {
        SamplePad(
            padIdx: padIdx, name: "Loop", family: .mixed,
            assetId: assetId, source: source,
            stemRole: "guitar_center",
            sourceLoopStartSec: start, sourceLoopEndSec: end,
            transposeSemis: -5)
    }

    func testBorrowRefIdentityIgnoresResponsePadIdx() throws {
        // The SAME loop arriving at two different response indices
        // (renumbering / feedback drift) must be ONE identity.
        let ref = try XCTUnwrap(BorrowRef(
            pad: donorPad(padIdx: 12), donorSongId: "donor-1"))
        let renumbered = donorPad(padIdx: 47)
        XCTAssertTrue(ref.matches(pad: renumbered))
        let other = try XCTUnwrap(BorrowRef(
            pad: renumbered, donorSongId: "donor-1"))
        XCTAssertEqual(ref.contentKey, other.contentKey,
                       "content identity must not include padIdx")
        XCTAssertNotEqual(ref.targetPadIdx, other.targetPadIdx,
                          "placement may differ; identity may not")
    }

    func testBorrowRefMatchesBySpanWithoutAssetId() throws {
        let ref = try XCTUnwrap(BorrowRef(
            pad: donorPad(padIdx: 1, assetId: nil), donorSongId: "d"))
        XCTAssertTrue(ref.matches(pad: donorPad(padIdx: 9, assetId: nil)))
        // A different span is a different loop.
        XCTAssertFalse(ref.matches(
            pad: donorPad(padIdx: 9, start: 30.0, end: 38.0, assetId: nil)))
    }

    func testBorrowRefMatchesByAssetIdWhenSpanRecut() throws {
        let ref = try XCTUnwrap(BorrowRef(
            pad: donorPad(padIdx: 1), donorSongId: "d"))
        // Same graph asset, re-cut span → still the same loop.
        XCTAssertTrue(ref.matches(
            pad: donorPad(padIdx: 2, start: 11.0, end: 19.0)))
    }

    func testBorrowRefRefusesPadsWithoutContentAddress() {
        // Host pads and span-less pads can NEVER become refs — the
        // padIdx fallback the audit forbids simply does not exist.
        XCTAssertNil(BorrowRef(
            pad: donorPad(padIdx: 1, source: "initial"), donorSongId: "d"))
        let spanless = SamplePad(
            padIdx: 5, name: "Old", family: .mixed, source: "donor")
        XCTAssertNil(BorrowRef(pad: spanless, donorSongId: "d"))
    }

    // MARK: - SamplePad borrow-identity fields decode

    func testSamplePadDecodesBorrowIdentityFields() throws {
        let json = Data("""
        {"padIdx": 4, "name": "Bass Verse", "family": "mixed",
         "source": "donor", "stemRole": "bass",
         "sourceLoopStartSec": 4.0, "sourceLoopEndSec": 12.0,
         "transposeSemis": 3, "assetId": "a1", "loopable": true}
        """.utf8)
        let pad = try JSONDecoder().decode(SamplePad.self, from: json)
        XCTAssertEqual(pad.stemRole, "bass")
        XCTAssertEqual(pad.sourceLoopStartSec, 4.0)
        XCTAssertEqual(pad.sourceLoopEndSec, 12.0)
        XCTAssertEqual(pad.transposeSemis, 3)
        // The content-address span must NOT leak into the playback
        // window/cycle fields — that would skew the shared lock-cycle.
        XCTAssertNil(pad.loopStartSec)
        XCTAssertNil(pad.loopEndSec)
        // relocated() (borrow re-arrange) must carry the identity along.
        let moved = pad.relocated(padIdx: 40, sourceName: "Donor")
        XCTAssertEqual(moved.sourceLoopStartSec, 4.0)
        XCTAssertEqual(moved.stemRole, "bass")
        XCTAssertEqual(moved.transposeSemis, 3)
    }
}
