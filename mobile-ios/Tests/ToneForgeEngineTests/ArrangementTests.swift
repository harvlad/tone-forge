// ArrangementTests.swift
//
// Pins Arrangement to the web kit.js live-capture arrangement helpers
// (collapseSections / blockIndexAtTime / arrangementDiff / parse+serialize).
// Port-parity rule: these assertions mirror kit.test.mjs so a song captured
// on one surface replays identically on another.

import XCTest
@testable import ToneForgeEngine

final class ArrangementTests: XCTestCase {

    private func sec(_ type: String, _ s: Double, _ e: Double) -> ArrangementSectionInput {
        ArrangementSectionInput(type: type, start: s, end: e)
    }

    // ---- collapseSections ----

    func testCollapseMergesConsecutiveSameType() {
        let raw = [sec("intro", 0, 4), sec("verse", 4, 8), sec("verse", 8, 12), sec("chorus", 12, 16)]
        let blocks = Arrangement.collapseSections(raw)
        XCTAssertEqual(blocks.map { $0.label }, ["Intro", "Verse", "Chorus"])
        XCTAssertEqual(blocks[1].start, 4)
        XCTAssertEqual(blocks[1].end, 12) // extended to lastEnd
    }

    func testCollapseTitleCasesAndDropsGarbage() {
        let raw = [sec("VERSE", 0, 4), sec("", 4, 8), sec("chorus", 10, 5) /* end<=start */]
        let blocks = Arrangement.collapseSections(raw)
        XCTAssertEqual(blocks.count, 1)
        XCTAssertEqual(blocks[0].label, "Verse")
        XCTAssertEqual(blocks[0].type, "VERSE") // raw type preserved
    }

    func testCollapseSortsOutOfOrder() {
        let raw = [sec("chorus", 8, 12), sec("intro", 0, 4), sec("intro", 4, 8)]
        let blocks = Arrangement.collapseSections(raw)
        XCTAssertEqual(blocks.map { $0.label }, ["Intro", "Chorus"])
        XCTAssertEqual(blocks[0].start, 0)
        XCTAssertEqual(blocks[0].end, 8)
    }

    func testCollapseEmpty() {
        XCTAssertTrue(Arrangement.collapseSections([]).isEmpty)
    }

    // ---- blockIndexAtTime ----

    func testBlockIndexBasic() {
        let b = Arrangement.collapseSections([sec("intro", 0, 4), sec("verse", 4, 8)])
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, 2), 0)
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, 4), 1)   // boundary owned by next
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, 8), 1)   // final block owns its end
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, -1), -1) // before first
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, 99), -1) // after last
        XCTAssertEqual(Arrangement.blockIndexAtTime([], 1), -1)
        XCTAssertEqual(Arrangement.blockIndexAtTime(b, .nan), -1)
    }

    // ---- diff ----

    func testDiff() {
        let d = Arrangement.diff(prev: [1, 2, 3], next: [2, 3, 4])
        XCTAssertEqual(d.toArm, [4])
        XCTAssertEqual(d.toRelease, [1])
    }

    func testDiffEmpties() {
        XCTAssertEqual(Arrangement.diff(prev: [], next: [5, 6]).toArm, [5, 6])
        XCTAssertEqual(Arrangement.diff(prev: [5, 6], next: []).toRelease, [5, 6])
    }

    // ---- parse / serialize round-trip ----

    func testParseValid() {
        let m = Arrangement.parse("{\"0\":[3,1,1,2],\"2\":[5]}")
        XCTAssertEqual(m?[0], [1, 2, 3]) // deduped + sorted
        XCTAssertEqual(m?[2], [5])
    }

    func testParseRejectsGarbage() {
        XCTAssertNil(Arrangement.parse(""))
        XCTAssertNil(Arrangement.parse("not json"))
        XCTAssertNil(Arrangement.parse("[1,2,3]"))          // array, not object
        XCTAssertNil(Arrangement.parse("{\"-1\":[1]}"))      // negative key
        XCTAssertNil(Arrangement.parse("{\"0\":[]}"))        // empty pads → all-invalid → nil
        XCTAssertNil(Arrangement.parse("{\"a\":[1]}"))       // non-int key
    }

    func testSerializeAndRoundTrip() {
        let map: [Int: [Int]] = [0: [3, 1, 2], 5: [7]]
        let json = Arrangement.serialize(map)
        XCTAssertNotNil(json)
        let back = Arrangement.parse(json!)
        XCTAssertEqual(back?[0], [1, 2, 3])
        XCTAssertEqual(back?[5], [7])
    }

    func testSerializeEmptyIsNil() {
        XCTAssertNil(Arrangement.serialize([:]))
        XCTAssertNil(Arrangement.serialize([0: []]))
        XCTAssertNil(Arrangement.serialize([-1: [1]]))
    }

    // ---- ArrangementRuntime (capture/replay state machine) ----

    private func twoBlocks() -> [ArrangementBlock] {
        Arrangement.collapseSections([sec("intro", 0, 4), sec("verse", 4, 8)])
    }

    func testRuntimeRecordsActivePadsPerBlock() {
        var rt = ArrangementRuntime(blocks: twoBlocks())
        _ = rt.startRecording()
        // In block 0, pads 1 and 3 on.
        var r = rt.tick(time: 2, isPlaying: true, activePads: [1, 3])
        XCTAssertTrue(r.capturedChanged)
        XCTAssertEqual(rt.captured[0], [1, 3])
        XCTAssertEqual(r.activeBlock, 0)
        // Same pads again → no new change.
        r = rt.tick(time: 2.5, isPlaying: true, activePads: [1, 3])
        XCTAssertFalse(r.capturedChanged)
        // Block 1, pad 5 on.
        r = rt.tick(time: 5, isPlaying: true, activePads: [5])
        XCTAssertEqual(rt.captured[1], [5])
    }

    func testRuntimeNoRecordWhilePaused() {
        var rt = ArrangementRuntime(blocks: twoBlocks())
        _ = rt.startRecording()
        let r = rt.tick(time: 2, isPlaying: false, activePads: [1])
        XCTAssertFalse(r.capturedChanged)
        XCTAssertNil(rt.captured[0])
    }

    func testRuntimeReplayArmsAndReleasesAtBoundaries() {
        var rt = ArrangementRuntime(blocks: twoBlocks(), captured: [0: [1, 3], 1: [5]])
        rt.startPlaying()
        // Enter block 0 → arm its set.
        var r = rt.tick(time: 1, isPlaying: true, activePads: [])
        XCTAssertEqual(r.toArm.sorted(), [1, 3])
        XCTAssertTrue(r.toRelease.isEmpty)
        // Still block 0 → no change.
        r = rt.tick(time: 2, isPlaying: true, activePads: [1, 3])
        XCTAssertTrue(r.toArm.isEmpty)
        XCTAssertTrue(r.toRelease.isEmpty)
        // Cross into block 1 → release 1,3 that aren't wanted, arm 5.
        r = rt.tick(time: 5, isPlaying: true, activePads: [1, 3])
        XCTAssertEqual(r.toArm, [5])
        XCTAssertEqual(r.toRelease.sorted(), [1, 3])
    }

    func testRuntimeStopReplayReleasesHeld() {
        var rt = ArrangementRuntime(blocks: twoBlocks(), captured: [0: [1, 3]])
        rt.startPlaying()
        _ = rt.tick(time: 1, isPlaying: true, activePads: [])
        let released = rt.stopReplay()
        XCTAssertEqual(released.sorted(), [1, 3])
        XCTAssertFalse(rt.playing)
    }

    func testRuntimeClearReleasesAndForgets() {
        var rt = ArrangementRuntime(blocks: twoBlocks(), captured: [0: [1, 3]])
        rt.startPlaying()
        _ = rt.tick(time: 1, isPlaying: true, activePads: [])
        let released = rt.clear()
        XCTAssertEqual(released.sorted(), [1, 3])
        XCTAssertTrue(rt.captured.isEmpty)
    }

    func testRuntimeRecStopsReplay() {
        var rt = ArrangementRuntime(blocks: twoBlocks(), captured: [0: [1, 3]])
        rt.startPlaying()
        _ = rt.tick(time: 1, isPlaying: true, activePads: [])
        let released = rt.startRecording()
        XCTAssertEqual(released.sorted(), [1, 3]) // replay pads released
        XCTAssertTrue(rt.recording)
        XCTAssertFalse(rt.playing)
    }

    func testRuntimePlayheadFraction() {
        var rt = ArrangementRuntime(blocks: twoBlocks()) // span 0..8
        let r = rt.tick(time: 4, isPlaying: false, activePads: [])
        XCTAssertEqual(r.playheadFrac ?? -1, 0.5, accuracy: 1e-9)
        let r2 = rt.tick(time: .nan, isPlaying: false, activePads: [])
        XCTAssertNil(r2.playheadFrac)
    }
}
