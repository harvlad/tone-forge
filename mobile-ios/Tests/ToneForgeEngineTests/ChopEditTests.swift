// ChopEditTests.swift
//
// Unit tests for ChopEdit, ChopReference, and resolvedChops (D-023).

import XCTest
@testable import ToneForgeEngine

final class ChopEditTests: XCTestCase {

    // MARK: - Test Helpers

    private func makeChop(
        idx: Int,
        start: Double,
        end: Double,
        kind: String? = nil
    ) -> Chop {
        Chop(
            idx: idx,
            startSec: start,
            endSec: end,
            durationSec: end - start,
            kind: kind
        )
    }

    // MARK: - ChopBoundaryEdit

    func testBoundaryEditFromChop() {
        let chop = makeChop(idx: 3, start: 1.0, end: 2.0)
        let edit = ChopBoundaryEdit(from: chop)

        XCTAssertEqual(edit.chopIndex, 3)
        XCTAssertEqual(edit.originalStart, 1.0)
        XCTAssertEqual(edit.originalEnd, 2.0)
        XCTAssertEqual(edit.editedStart, 1.0)
        XCTAssertEqual(edit.editedEnd, 2.0)
        XCTAssertFalse(edit.isModified)
    }

    func testBoundaryEditIsModified() {
        var edit = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: 0.0,
            editedEnd: 1.0
        )
        XCTAssertFalse(edit.isModified)

        edit.editedStart = 0.1
        XCTAssertTrue(edit.isModified)

        edit.editedStart = 0.0
        edit.editedEnd = 0.9
        XCTAssertTrue(edit.isModified)
    }

    func testBoundaryEditClamp() {
        var edit = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: -0.5,
            editedEnd: 2.0
        )
        edit.clamp(minStart: 0.0, maxEnd: 1.0)

        XCTAssertEqual(edit.editedStart, 0.0)
        XCTAssertEqual(edit.editedEnd, 1.0)
    }

    func testBoundaryEditClampMinimumDuration() {
        var edit = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: 0.5,
            editedEnd: 0.5
        )
        edit.clamp(minStart: 0.0, maxEnd: 1.0)

        // Start should be pushed back to maintain 0.01 minimum duration.
        XCTAssertLessThan(edit.editedStart, edit.editedEnd)
        XCTAssertGreaterThanOrEqual(edit.editedEnd - edit.editedStart, 0.01)
    }

    // MARK: - ChopEdits

    func testChopEditsInitialization() {
        let edits = ChopEdits(presetKey: "harmonic")

        XCTAssertEqual(edits.presetKey, "harmonic")
        XCTAssertTrue(edits.boundaryEdits.isEmpty)
        XCTAssertTrue(edits.splits.isEmpty)
        XCTAssertTrue(edits.merges.isEmpty)
        XCTAssertFalse(edits.hasEdits)
    }

    func testChopEditsHasEdits() {
        var edits = ChopEdits(presetKey: "sections")
        XCTAssertFalse(edits.hasEdits)

        edits.boundaryEdits[0] = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: 0.1,
            editedEnd: 0.9
        )
        XCTAssertTrue(edits.hasEdits)
    }

    func testChopEditsReset() {
        var edits = ChopEdits(presetKey: "harmonic")
        edits.boundaryEdits[0] = ChopBoundaryEdit(from: makeChop(idx: 0, start: 0, end: 1))
        edits.splits.append(ChopSplit(parentIndex: 1, splitPoint: 0.5))
        edits.merges.append(ChopMerge(firstIndex: 2, secondIndex: 3))

        XCTAssertTrue(edits.hasEdits)
        edits.reset()
        XCTAssertFalse(edits.hasEdits)
        XCTAssertTrue(edits.boundaryEdits.isEmpty)
        XCTAssertTrue(edits.splits.isEmpty)
        XCTAssertTrue(edits.merges.isEmpty)
    }

    // MARK: - resolvedChops (no edits)

    func testResolvedChopsNoEdits() {
        let chops = [
            makeChop(idx: 0, start: 0.0, end: 1.0, kind: "chord"),
            makeChop(idx: 1, start: 1.0, end: 2.0, kind: "chord"),
            makeChop(idx: 2, start: 2.0, end: 3.0, kind: "chord")
        ]
        let edits = ChopEdits(presetKey: "harmonic")

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved.count, 3)
        XCTAssertEqual(resolved[0].id, 0)
        XCTAssertEqual(resolved[0].startSec, 0.0)
        XCTAssertEqual(resolved[0].endSec, 1.0)
        XCTAssertFalse(resolved[0].isSynthetic)
        XCTAssertFalse(resolved[0].isEdited)
        XCTAssertEqual(resolved[0].metadata?.kind, "chord")
    }

    // MARK: - resolvedChops (boundary edits)

    func testResolvedChopsWithBoundaryEdit() {
        let chops = [
            makeChop(idx: 0, start: 0.0, end: 1.0),
            makeChop(idx: 1, start: 1.0, end: 2.0)
        ]
        var edits = ChopEdits(presetKey: "harmonic")
        edits.boundaryEdits[0] = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: 0.1,
            editedEnd: 0.9
        )

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved.count, 2)
        XCTAssertEqual(resolved[0].id, 0)
        XCTAssertEqual(resolved[0].startSec, 0.1)
        XCTAssertEqual(resolved[0].endSec, 0.9)
        XCTAssertTrue(resolved[0].isEdited)

        // Second chop unchanged.
        XCTAssertEqual(resolved[1].id, 1)
        XCTAssertEqual(resolved[1].startSec, 1.0)
        XCTAssertEqual(resolved[1].endSec, 2.0)
        XCTAssertFalse(resolved[1].isEdited)
    }

    // MARK: - resolvedChops (splits)

    func testResolvedChopsWithSplit() {
        let chops = [
            makeChop(idx: 0, start: 0.0, end: 2.0, kind: "section")
        ]
        var edits = ChopEdits(presetKey: "sections")
        edits.splits.append(ChopSplit(parentIndex: 0, splitPoint: 1.0))

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved.count, 2)
        // First half.
        XCTAssertEqual(resolved[0].startSec, 0.0)
        XCTAssertEqual(resolved[0].endSec, 1.0)
        XCTAssertTrue(resolved[0].isSynthetic)
        XCTAssertEqual(resolved[0].parentIndex, 0)
        XCTAssertEqual(resolved[0].metadata?.kind, "section")

        // Second half.
        XCTAssertEqual(resolved[1].startSec, 1.0)
        XCTAssertEqual(resolved[1].endSec, 2.0)
        XCTAssertTrue(resolved[1].isSynthetic)
        XCTAssertEqual(resolved[1].parentIndex, 0)
    }

    func testResolvedChopsSplitOutOfBounds() {
        let chops = [makeChop(idx: 0, start: 0.0, end: 1.0)]
        var edits = ChopEdits(presetKey: "sections")
        // Split point outside chop bounds should be ignored.
        edits.splits.append(ChopSplit(parentIndex: 0, splitPoint: 1.5))

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved.count, 1)
        XCTAssertEqual(resolved[0].id, 0)
        XCTAssertFalse(resolved[0].isSynthetic)
    }

    // MARK: - resolvedChops (merges)

    func testResolvedChopsWithMerge() {
        let chops = [
            makeChop(idx: 0, start: 0.0, end: 1.0, kind: "chord"),
            makeChop(idx: 1, start: 1.0, end: 2.0, kind: "chord"),
            makeChop(idx: 2, start: 2.0, end: 3.0, kind: "chord")
        ]
        var edits = ChopEdits(presetKey: "harmonic")
        edits.merges.append(ChopMerge(firstIndex: 0, secondIndex: 1))

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved.count, 2)

        // Third chop unchanged.
        let thirdChop = resolved.first { $0.id == 2 }
        XCTAssertNotNil(thirdChop)
        XCTAssertEqual(thirdChop?.startSec, 2.0)
        XCTAssertEqual(thirdChop?.endSec, 3.0)

        // Merged chop.
        let merged = resolved.first { $0.isSynthetic }
        XCTAssertNotNil(merged)
        XCTAssertEqual(merged?.startSec, 0.0)
        XCTAssertEqual(merged?.endSec, 2.0)
        XCTAssertTrue(merged?.isEdited ?? false)
    }

    // MARK: - resolvedChops (combined)

    func testResolvedChopsCombinedEdits() {
        let chops = [
            makeChop(idx: 0, start: 0.0, end: 2.0),
            makeChop(idx: 1, start: 2.0, end: 4.0),
            makeChop(idx: 2, start: 4.0, end: 6.0)
        ]
        var edits = ChopEdits(presetKey: "harmonic")
        // Boundary edit on first.
        edits.boundaryEdits[0] = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 2.0,
            editedStart: 0.5,
            editedEnd: 1.5
        )
        // Split the second.
        edits.splits.append(ChopSplit(parentIndex: 1, splitPoint: 3.0))

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        // Original 3 chops → 1 edited + 2 from split + 1 unchanged = 4.
        XCTAssertEqual(resolved.count, 4)

        // Verify sorted by startSec.
        for i in 0..<(resolved.count - 1) {
            XCTAssertLessThanOrEqual(resolved[i].startSec, resolved[i + 1].startSec)
        }
    }

    // MARK: - resolvedChops (sorting)

    func testResolvedChopsSortedByStart() {
        let chops = [
            makeChop(idx: 0, start: 2.0, end: 3.0),
            makeChop(idx: 1, start: 0.0, end: 1.0),
            makeChop(idx: 2, start: 1.0, end: 2.0)
        ]
        let edits = ChopEdits(presetKey: "sections")

        let resolved = resolvedChops(bundleChops: chops, edits: edits)

        XCTAssertEqual(resolved[0].startSec, 0.0)
        XCTAssertEqual(resolved[1].startSec, 1.0)
        XCTAssertEqual(resolved[2].startSec, 2.0)
    }

    // MARK: - ResolvedChop.toChop

    func testResolvedChopToChop() {
        let resolved = ResolvedChop(
            id: 5,
            parentIndex: 3,
            startSec: 1.0,
            endSec: 2.5,
            metadata: ChopMetadata(
                kind: "chord",
                root: 2,
                sectionLabel: "verse",
                chordSymbol: "Dm",
                colorHint: "blue"
            ),
            isSynthetic: false,
            isEdited: true
        )

        let chop = resolved.toChop()

        XCTAssertEqual(chop.idx, 5)
        XCTAssertEqual(chop.startSec, 1.0)
        XCTAssertEqual(chop.endSec, 2.5)
        XCTAssertEqual(chop.durationSec, 1.5)
        XCTAssertEqual(chop.kind, "chord")
        XCTAssertEqual(chop.root, 2)
        XCTAssertEqual(chop.sectionLabel, "verse")
        XCTAssertEqual(chop.chordSymbol, "Dm")
        XCTAssertEqual(chop.colorHint, "blue")
    }

    // MARK: - ChopEdits Codable

    func testChopEditsCodableRoundTrip() throws {
        var edits = ChopEdits(presetKey: "harmonic")
        edits.boundaryEdits[0] = ChopBoundaryEdit(
            chopIndex: 0,
            originalStart: 0.0,
            originalEnd: 1.0,
            editedStart: 0.1,
            editedEnd: 0.9
        )
        edits.splits.append(ChopSplit(parentIndex: 1, splitPoint: 0.5))
        edits.merges.append(ChopMerge(firstIndex: 2, secondIndex: 3))

        let data = try JSONEncoder().encode(edits)
        let decoded = try JSONDecoder().decode(ChopEdits.self, from: data)

        XCTAssertEqual(decoded.presetKey, edits.presetKey)
        XCTAssertEqual(decoded.boundaryEdits.count, 1)
        XCTAssertEqual(decoded.splits.count, 1)
        XCTAssertEqual(decoded.merges.count, 1)
        XCTAssertEqual(decoded.splits[0].splitPoint, 0.5)
    }
}

// MARK: - ChopReferenceTests

final class ChopReferenceTests: XCTestCase {

    func testBundleChopReference() {
        let ref = ChopReference.chop(preset: "harmonic", index: 5)

        XCTAssertTrue(ref.isBundleChop)
        XCTAssertFalse(ref.isPackPad)
        XCTAssertFalse(ref.isLocalSample)

        if case .bundleChop(let preset, let index, let resolved) = ref {
            XCTAssertEqual(preset, "harmonic")
            XCTAssertEqual(index, 5)
            XCTAssertNil(resolved)
        } else {
            XCTFail("Expected bundleChop case")
        }
    }

    func testPackPadReference() {
        let ref = ChopReference.pad(packId: "starter", padIdx: 34)

        XCTAssertTrue(ref.isPackPad)
        XCTAssertFalse(ref.isBundleChop)

        if case .packPad(let packId, let padIdx) = ref {
            XCTAssertEqual(packId, "starter")
            XCTAssertEqual(padIdx, 34)
        } else {
            XCTFail("Expected packPad case")
        }
    }

    func testLocalSampleReference() {
        let id = UUID()
        let ref = ChopReference.local(id: id)

        XCTAssertTrue(ref.isLocalSample)

        if case .localSample(let refId) = ref {
            XCTAssertEqual(refId, id)
        } else {
            XCTFail("Expected localSample case")
        }
    }

    func testCustomURLReference() {
        let url = URL(fileURLWithPath: "/samples/kick.wav")
        let ref = ChopReference.file(url: url, start: 0.1, end: 0.5)

        if case .customURL(let refUrl, let start, let end) = ref {
            XCTAssertEqual(refUrl, url)
            XCTAssertEqual(start, 0.1)
            XCTAssertEqual(end, 0.5)
        } else {
            XCTFail("Expected customURL case")
        }
    }

    func testSequenceReference() {
        let id = UUID()
        let ref = ChopReference.sequence(patternId: id)

        if case .sequence(let refId) = ref {
            XCTAssertEqual(refId, id)
        } else {
            XCTFail("Expected sequence case")
        }
    }

    func testDisplayLabel() {
        let chopRef = ChopReference.chop(preset: "harmonic", index: 2)
        XCTAssertEqual(chopRef.displayLabel, "Harmonic #3")

        let padRef = ChopReference.pad(packId: "starter", padIdx: 34)
        XCTAssertEqual(padRef.displayLabel, "starter R3C4")

        let localRef = ChopReference.local(id: UUID())
        XCTAssertEqual(localRef.displayLabel, "Local")

        let fileRef = ChopReference.file(url: URL(fileURLWithPath: "/samples/kick.wav"))
        XCTAssertEqual(fileRef.displayLabel, "kick.wav")

        let seqRef = ChopReference.sequence(patternId: UUID())
        XCTAssertEqual(seqRef.displayLabel, "Sequence")
    }

    func testMatchesIgnoreResolved() {
        let ref1 = ChopReference.bundleChop(presetKey: "harmonic", chopIndex: 5, resolvedId: nil)
        let ref2 = ChopReference.bundleChop(presetKey: "harmonic", chopIndex: 5, resolvedId: -1)
        let ref3 = ChopReference.bundleChop(presetKey: "sections", chopIndex: 5, resolvedId: nil)

        XCTAssertTrue(ref1.matches(ignoreResolved: ref2))
        XCTAssertFalse(ref1.matches(ignoreResolved: ref3))
    }

    // MARK: - Codable

    func testChopReferenceCodableRoundTrip() throws {
        let refs: [ChopReference] = [
            .chop(preset: "harmonic", index: 3, resolvedId: -1),
            .pad(packId: "starter", padIdx: 45),
            .local(id: UUID()),
            .file(url: URL(fileURLWithPath: "/audio/sample.wav"), start: 0.5, end: 1.5),
            .sequence(patternId: UUID())
        ]

        for ref in refs {
            let data = try JSONEncoder().encode(ref)
            let decoded = try JSONDecoder().decode(ChopReference.self, from: data)
            XCTAssertEqual(decoded, ref)
        }
    }

    func testChopReferenceHashable() {
        let ref1 = ChopReference.chop(preset: "harmonic", index: 5)
        let ref2 = ChopReference.chop(preset: "harmonic", index: 5)
        let ref3 = ChopReference.chop(preset: "harmonic", index: 6)

        var set = Set<ChopReference>()
        set.insert(ref1)
        XCTAssertTrue(set.contains(ref2))
        XCTAssertFalse(set.contains(ref3))
    }
}
