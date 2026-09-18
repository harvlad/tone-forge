// PadRadialActionTests.swift
//
// Regression suite for the pad radial menu's ACTIONS — every slice
// must act on the pad the user long-pressed, whatever its class:
//
//   (a) quadrant pad of the active (auto-kit) pack   — padBindings
//   (b) Jam-64 OVERFLOW section/chord chop           — jamOverflowPads
//       fallback (not in padBindings; resolves via padBinding)
//   (c) pad PINNED from another pack (song-DNA chop) — .packPad slot
//   (d) local-sample shadow                          — localPackId binding
//   (e) empty cell
//   (f) saved-sequence pad
//
// Live bugs pinned here (64-grid, Edit mode ON):
//   - radial Chop did NOTHING on overflow pads: padTrimmerTarget
//     required the ACTIVE pack, so the song-DNA chop resolved to nil
//     and the trimmer sheet never appeared.
//   - radial Effects opened the WRONG sheet on overflow pads: the same
//     active-pack guard nil'd padEffectsTarget, and padSheetTarget fell
//     through to the record/manage SOURCE sheet (the "vocoder" UI).
//   - Reset/Delete were silent no-ops on overflow pads (raw padBindings
//     lookups), and Delete on a PINNED pad hid a pad the assignment
//     immediately repainted.
//
// The anti-silent-no-op contract: `radialActions(row:col:)` offers a
// slice ONLY when its handler can act — Chop is hidden exactly when
// padTrimmerTarget would be nil, sequence pads get their own wheel,
// empty cells get the create wheel.
//
// Fixture strategy: hermetic runtime-tone packs (same as
// ModeCoordinatorRingingTests); song-DNA fixtures seed
// `app.songDnaPacks` directly (internal(set) test seam). Pure
// coordinator-level — no UI.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class PadRadialActionTests: XCTestCase {

    private var app: AppState!
    private var coord: ModeCoordinator!
    private var tmpDir: URL!
    private var savedPadMode: JamPadMode!
    /// Grid cells this test assigned slots to — cleared in tearDown so
    /// the persistent PadAssignmentStore can't leak into other suites.
    private var touchedCells: [Int] = []

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("radial-actions-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState()
        coord = app.modeCoordinator
        // The 64-grid overflow fallback only exists in Jam Samples mode;
        // force it (appMode/padMode restore from persisted settings).
        savedPadMode = app.jamSettings.padMode
        app.jamSettings.padMode = .samples
        coord.setMode(.jamInKey)
        coord.refreshLayout()
    }

    override func tearDown() async throws {
        for raw in touchedCells {
            app.padAssignmentStore.assign(nil, mode: coord.appMode, padIdx: raw)
        }
        app.jamSettings.padMode = savedPadMode
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        coord = nil
        try await super.tearDown()
    }

    // MARK: - Fixtures

    private func writeTone(to url: URL, durationSec: Double = 0.1) throws {
        let sr = 44_100.0
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(durationSec * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData {
            for c in 0..<2 {
                for i in 0..<Int(frames) {
                    ch[c][i] = Float(sin(2 * .pi * 440 * Double(i) / sr) * 0.3)
                }
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buf)
    }

    private func makePack(
        packId: String, padIdxs: [Int], names: [Int: String] = [:]
    ) throws -> ResolvedSamplePack {
        var padObjs: [SamplePad] = []
        var urls: [Int: URL] = [:]
        for idx in padIdxs {
            let url = tmpDir.appendingPathComponent("\(packId)-\(idx).caf")
                .standardizedFileURL
            try writeTone(to: url)
            padObjs.append(SamplePad(
                padIdx: idx,
                name: names[idx] ?? "P\(idx)",
                family: .pads,
                filename: url.lastPathComponent,
                chokeGroup: nil,
                loopPointSec: 0
            ))
            urls[idx] = url
        }
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: padObjs
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    /// Activate a hermetic 3-pad kit as the ACTIVE pack (quadrant
    /// class). Buffers loaded synchronously so trims/waveforms resolve.
    @discardableResult
    private func activateKit(preload: Bool = true) throws -> String {
        let packId = "kit-\(UUID().uuidString)"
        let pack = try makePack(packId: packId, padIdxs: [0, 1, 2])
        if preload {
            try app.sampleScheduler.preloadPack(pack, stemFiles: [:])
        }
        app.activateSamplePack(pack, stemFiles: [:])
        return packId
    }

    /// Seed a song-DNA pack whose chops paint the Jam-64 OVERFLOW cells
    /// (reading order: overflow index 0 = row 8, col 5).
    @discardableResult
    private func seedOverflow(
        padIdxs: [Int] = [0, 1, 2], preload: Bool = true
    ) throws -> String {
        let packId = "song-derived:test-\(UUID().uuidString):other-chop"
        let pack = try makePack(
            packId: packId, padIdxs: padIdxs, names: [0: "Intro"]
        )
        if preload {
            try app.sampleScheduler.preloadPack(pack, stemFiles: [:])
        }
        app.songDnaPacks = [SongDnaPack(
            presetKey: "other:chop", stem: "other", sliceMode: "chop",
            displayName: "Other — chop", chopCount: padIdxs.count, pack: pack
        )]
        coord.refreshLayout()
        return packId
    }

    /// Clear any persisted assignment at a cell (the store is durable
    /// across test runs) and remember it for tearDown.
    private func claimCell(row: Int, col: Int) -> Int {
        let raw = PadIndex.at(row: row, col: col).rawValue
        app.padAssignmentStore.assign(nil, mode: coord.appMode, padIdx: raw)
        touchedCells.append(raw)
        return raw
    }

    // MARK: - Wheel contents per pad class

    func testQuadrantKitPadGetsFullEditingWheel() throws {
        // Quadrant orientation: pad 0 lands BOTTOM-left → (row 5, col 1).
        _ = claimCell(row: 5, col: 1)
        try activateKit()
        coord.refreshLayout()
        XCTAssertEqual(
            coord.radialActions(row: 5, col: 1),
            PadRadialAction.assigned,
            "A loaded kit pad offers the full editing wheel, Chop included"
        )
    }

    func testOverflowPadGetsFullEditingWheelWhenLoaded() throws {
        _ = claimCell(row: 8, col: 5)
        try seedOverflow()
        XCTAssertEqual(
            coord.radialActions(row: 8, col: 5),
            PadRadialAction.assigned,
            "A loaded overflow chop is a first-class pad — same wheel as the kit"
        )
    }

    func testChopHiddenExactlyWhenTrimmerTargetIsNil() throws {
        // NOT preloaded: no resident buffer → nothing to trim. The rule:
        // padTrimmerTarget nil ⇔ Chop absent from the wheel — never a
        // shown slice that silently does nothing.
        _ = claimCell(row: 8, col: 5)
        try seedOverflow(preload: false)
        let actions = coord.radialActions(row: 8, col: 5)
        XCTAssertFalse(actions.contains(.chop),
                       "No decodable buffer → Chop must be hidden")
        XCTAssertNil(coord.padTrimmerTarget(row: 8, col: 5))
        XCTAssertEqual(
            actions,
            PadRadialAction.assigned.filter { $0 != .chop },
            "Only Chop drops off — the rest of the wheel still applies"
        )
    }

    func testEmptyCellGetsCreateWheel() throws {
        // No kit, no song DNA: a far cell resolves nothing.
        let raw = claimCell(row: 2, col: 2)
        coord.refreshLayout()
        XCTAssertNil(coord.padBinding(row: 2, col: 2))
        XCTAssertEqual(coord.radialActions(row: 2, col: 2),
                       PadRadialAction.empty)
        _ = raw
    }

    func testSequencePadGetsSequenceWheelEvenOverAnOverflowCell() throws {
        // A sequence assigned ON an overflow cell must NOT expose the
        // editing wheel of the chop painted underneath — Loop/Reset/Chop
        // would hit the hidden chop, not the sequence.
        _ = claimCell(row: 8, col: 5)
        try seedOverflow()
        coord.assignSequence(targetRow: 8, targetCol: 5, patternId: UUID())
        XCTAssertEqual(coord.radialActions(row: 8, col: 5),
                       PadRadialAction.sequencePad)
    }

    // MARK: - Effects routing (bug: "Effects only opens vocoder")

    func testEffectsTargetForQuadrantKitPad() throws {
        _ = claimCell(row: 5, col: 1)
        let packId = try activateKit()
        coord.refreshLayout()
        guard case .effects(let t)? = coord.padSheetTarget(row: 5, col: 1)
        else { return XCTFail("Quadrant pad long-press must be .effects") }
        XCTAssertEqual(t.packId, packId)
        XCTAssertEqual(t.padIdx, 0)
    }

    func testEffectsTargetForOverflowPad_notSourceSheet() throws {
        // REGRESSION (live bug): overflow pads fell through to the
        // .source record/manage sheet because padEffectsTarget demanded
        // the active pack.
        _ = claimCell(row: 8, col: 5)
        let packId = try seedOverflow()
        guard case .effects(let t)? = coord.padSheetTarget(row: 8, col: 5)
        else {
            return XCTFail(
                "Overflow pad long-press must open the EFFECTS editor, "
                + "not the source (record) sheet")
        }
        XCTAssertEqual(t.packId, packId)
        XCTAssertEqual(t.padIdx, 0)
        XCTAssertEqual(t.padName, "Intro",
                       "Name comes from the chop's own pack manifest")
    }

    func testEffectsTargetForPinnedSongDnaPad() throws {
        // A song-DNA chop pinned to an arbitrary cell (addSound/picker
        // flow) is a .packPad slot from a NON-active pack — it must
        // still open the effects editor.
        let raw = claimCell(row: 2, col: 2)
        let packId = try seedOverflow()
        coord.assignPadFromPack(
            targetRow: 2, targetCol: 2, sourcePackId: packId, sourcePadIdx: 1
        )
        guard case .effects(let t)? = coord.padSheetTarget(row: 2, col: 2)
        else { return XCTFail("Pinned pad long-press must be .effects") }
        XCTAssertEqual(t.packId, packId)
        XCTAssertEqual(t.padIdx, 1)
        _ = raw
    }

    func testLocalShadowRoutesToSourceSheet() throws {
        // Local-sample shadows keep the SOURCE sheet (manage/override) —
        // the one pad class where .source is the designed long-press
        // surface. Bind the scheduler's synthetic local pack directly.
        let raw = claimCell(row: 8, col: 1)
        coord.padBindings[raw] = (
            packId: SampleScheduler.localPackId, padIdx: raw
        )
        guard case .source? = coord.padSheetTarget(row: 8, col: 1)
        else { return XCTFail("Local shadow must route to .source") }
    }

    // MARK: - Chop / trimmer routing (bug: "Chop does nothing")

    func testTrimmerTargetForQuadrantKitPad() throws {
        _ = claimCell(row: 5, col: 1)
        let packId = try activateKit()
        coord.refreshLayout()
        let t = coord.padTrimmerTarget(row: 5, col: 1)
        XCTAssertEqual(t?.packId, packId)
        XCTAssertEqual(t?.padIdx, 0)
        XCTAssertEqual(t?.initialStart, 0)
        XCTAssertEqual(t?.initialEnd, 1)
    }

    func testTrimmerTargetForOverflowPad() throws {
        // REGRESSION (live bug): padTrimmerTarget was nil for every
        // overflow chop → radial Chop was a dead slice.
        _ = claimCell(row: 8, col: 5)
        let packId = try seedOverflow()
        let t = coord.padTrimmerTarget(row: 8, col: 5)
        XCTAssertNotNil(t, "Loaded overflow chop must be trimmable")
        XCTAssertEqual(t?.packId, packId)
        XCTAssertEqual(t?.padIdx, 0)
        XCTAssertEqual(t?.padName, "Intro")
        XCTAssertGreaterThan(t?.durationSec ?? 0, 0)
        XCTAssertFalse(t?.peaks.isEmpty ?? true)
    }

    func testTrimmerTargetForPinnedSongDnaPad() throws {
        _ = claimCell(row: 2, col: 2)
        let packId = try seedOverflow()
        coord.assignPadFromPack(
            targetRow: 2, targetCol: 2, sourcePackId: packId, sourcePadIdx: 1
        )
        let t = coord.padTrimmerTarget(row: 2, col: 2)
        XCTAssertEqual(t?.packId, packId)
        XCTAssertEqual(t?.padIdx, 1)
    }

    // MARK: - Loop toggle (extends the existing partial coverage)

    func testTogglePadLoopResolvesOverflowPad() throws {
        _ = claimCell(row: 8, col: 5)
        let packId = try seedOverflow()
        // loopPointSec = 0 (non-nil) → manifest default loops.
        XCTAssertTrue(coord.padLoops(row: 8, col: 5))
        coord.togglePadLoop(row: 8, col: 5)
        XCTAssertFalse(coord.padLoops(row: 8, col: 5),
                       "Radial Loop must flip the OVERFLOW pad's own key")
        XCTAssertFalse(
            app.sampleScheduler.padLoops(packId: packId, padIdx: 0),
            "The override lands on the song-DNA pack, not the active pack"
        )
        coord.togglePadLoop(row: 8, col: 5)  // restore
    }

    // MARK: - Reset

    func testResetResolvesOverflowPadAndClearsLoopOverride() throws {
        // REGRESSION: resetPadToDefault read raw padBindings → silent
        // no-op on overflow pads. Also pins the new promise that Reset
        // clears the radial Loop override (it only cleared the
        // transform-chain render before).
        _ = claimCell(row: 8, col: 5)
        try seedOverflow()
        coord.togglePadLoop(row: 8, col: 5)
        XCTAssertFalse(coord.padLoops(row: 8, col: 5))
        coord.resetPadToDefault(row: 8, col: 5)
        XCTAssertTrue(coord.padLoops(row: 8, col: 5),
                      "Reset returns the pad to its manifest loop default")
    }

    // MARK: - Delete

    func testDeleteHidesOverflowPadAndReflowsTheList() throws {
        // REGRESSION: Delete on an overflow pad fell into the local-
        // assignment clear (nothing assigned → nothing happened).
        // Decision under test: overflow Delete hides the chop in the
        // per-pack hidden set, and jamOverflowPads filters it so every
        // consumer (painter, touch, hardware, ringing) re-flows.
        _ = claimCell(row: 8, col: 5)
        let packId = try seedOverflow(padIdxs: [0, 1, 2])
        XCTAssertEqual(app.jamOverflowPads.count, 3)
        coord.hidePackPad(row: 8, col: 5)
        XCTAssertTrue(
            app.sampleSettings.isPadHidden(packId: packId, padIdx: 0))
        XCTAssertEqual(app.jamOverflowPads.count, 2)
        // The next chop slides into the freed cell — grid stays dense.
        XCTAssertEqual(coord.padBinding(row: 8, col: 5)?.padIdx, 1)
    }

    func testDeleteOnPinnedPadClearsThePinWithoutHidingTheChop() throws {
        // REGRESSION: Delete on a pinned pad called hidePad and left the
        // assignment — the pin repainted on the next rebuild (silent
        // no-op). Decision under test: un-PIN (clear the slot); the chop
        // itself stays available on the overflow grid.
        _ = claimCell(row: 2, col: 2)
        let packId = try seedOverflow()
        coord.assignPadFromPack(
            targetRow: 2, targetCol: 2, sourcePackId: packId, sourcePadIdx: 1
        )
        XCTAssertEqual(coord.padBinding(row: 2, col: 2)?.padIdx, 1)
        coord.hidePackPad(row: 2, col: 2)
        XCTAssertNil(
            app.padAssignmentStore.slot(
                mode: coord.appMode,
                padIdx: PadIndex.at(row: 2, col: 2).rawValue),
            "Delete on a pin clears the assignment slot")
        XCTAssertNil(coord.padBinding(row: 2, col: 2))
        XCTAssertFalse(
            app.sampleSettings.isPadHidden(packId: packId, padIdx: 1),
            "Un-pinning a foreign pack's pad must NOT hide the chop "
            + "from the overflow grid")
        XCTAssertEqual(app.jamOverflowPads.count, 3)
    }

    func testDeleteOnQuadrantPadHidesIt() throws {
        _ = claimCell(row: 5, col: 1)
        let packId = try activateKit()
        coord.refreshLayout()
        XCTAssertNotNil(coord.padBinding(row: 5, col: 1))
        coord.hidePackPad(row: 5, col: 1)
        XCTAssertTrue(
            app.sampleSettings.isPadHidden(packId: packId, padIdx: 0))
        XCTAssertNil(coord.padBinding(row: 5, col: 1))
    }

    func testDeleteOnEmptyCellIsHarmless() throws {
        _ = claimCell(row: 3, col: 7)
        coord.hidePackPad(row: 3, col: 7)  // must not crash or assign
        XCTAssertNil(coord.padBinding(row: 3, col: 7))
    }
}
