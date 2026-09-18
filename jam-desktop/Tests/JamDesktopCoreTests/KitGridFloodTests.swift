// KitGridFloodTests.swift
//
// Pins the web-parity 64-pad kit flood on desktop:
//
//   • KitGridMapper translates the SERVER-built kit manifest (the same
//     /api/song/{id}/kit payload web renders) into grid assignments
//     1:1 — count, padIdx order, "Drums beat Verse"-style labels,
//     category colors — never re-selecting or re-ordering client-side.
//   • A 64-pad manifest fills the WHOLE 8×8 (the regression: desktop
//     hard-coded pads=16 and left 48 dead cells in 64 mode).
//   • Category colors match the backend `_CATEGORY_HEX` palette web
//     shows, INCLUDING the residual-`other` SYNTH case that
//     stem+contentType recomputation used to paint lead-orange.
//   • The 16/64 toggle fires the reload hook (web kit.js
//     setPadCount → reloadKit parity) on non-borrow grids only — a
//     mounted borrow re-arranges locally and must not be refetched.
//   • Per-pad colorHints win VERBATIM over the category color (web
//     parseColor(pad.colorHint)) — kind=drums kits put category "DRUMS"
//     on every pad with PER-CLASS hints, so category-first flooded the
//     whole kit one flat red.
//   • isKitGridMounted tracks grid provenance — the host's 16/64 resize
//     refetch keys on it so a chop grid mounted behind the session's
//     back is never silently replaced by a re-fetched auto kit.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class KitGridFloodTests: XCTestCase {

    // MARK: - Fixtures

    private final class FakeTransport: LaunchpadTransport {
        var connectionState: LaunchpadConnectionState { .onScreen }
        var onPadDown: ((LaunchpadPad) -> Void)?
        var onPadUp: ((LaunchpadPad) -> Void)?
        var lights: [LaunchpadPad: LaunchpadLight] = [:]
        func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
            lights[pad] = light
        }
        func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
            for (p, l) in frame { lights[p] = l }
        }
        func clearLights() { lights.removeAll() }
    }

    private struct FakeFetcher: LaunchpadChopsFetching {
        func fetchChops(
            baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
        ) async throws -> [Chop] { [] }
    }

    private func makeController() -> LaunchpadController {
        let c = LaunchpadController(nowProvider: { 0 }, fetcher: FakeFetcher())
        c.attach(transport: FakeTransport())
        return c
    }

    private func kitPad(
        _ idx: Int, name: String, category: String, hex: String,
        stem: String, contentType: String, loopable: Bool = true
    ) -> SamplePad {
        SamplePad(
            padIdx: idx, name: name, family: .mixed, colorHint: hex,
            stemSlice: StemSlice(
                stemRole: stem,
                startSec: Double(idx) * 2, endSec: Double(idx) * 2 + 2),
            loopScore: 0.8, loopable: loopable, contentType: contentType,
            performanceScore: 0.9 - Double(idx) * 0.01, difficulty: 0.4,
            category: category, assetId: "asset-\(idx)"
        )
    }

    /// One category run per grid rows, mirroring the server's kit=5+
    /// category-grouped padIdx layout (`_CATEGORY_GROUP_ORDER`) and
    /// `_descriptive_label` names. Includes a SYNTH run cut from the
    /// residual "other" stem — the case a client CANNOT recompute from
    /// stem+contentType (kit_builder's `residual_is_synth`).
    private struct Run {
        let category: String, hex: String, stem: String
        let contentType: String, label: String, count: Int
    }
    private let webOrderRuns: [Run] = [
        Run(category: "DRUMS", hex: "#EF4444", stem: "drums",
            contentType: "rhythm_loop", label: "Drums beat Verse", count: 16),
        Run(category: "BASS", hex: "#22C55E", stem: "bass",
            contentType: "bass_groove", label: "Bass groove Chorus", count: 12),
        Run(category: "CHORDS", hex: "#F59E0B", stem: "other",
            contentType: "chord_loop", label: "Guitar chords Verse", count: 8),
        Run(category: "SYNTH", hex: "#14B8A6", stem: "other",
            contentType: "lead_loop", label: "Synth lead Chorus", count: 8),
        Run(category: "LEAD", hex: "#F97316", stem: "other",
            contentType: "lead_loop", label: "Guitar lead Bridge", count: 10),
        Run(category: "VOCAL", hex: "#EC4899", stem: "vocals",
            contentType: "lead_loop", label: "Vocal lead Chorus", count: 10),
    ]

    private func floodPack(pads: Int = 64) -> SamplePack {
        var out: [SamplePad] = []
        outer: for run in webOrderRuns {
            for _ in 0..<run.count {
                if out.count == pads { break outer }
                out.append(kitPad(
                    out.count, name: run.label, category: run.category,
                    hex: run.hex, stem: run.stem, contentType: run.contentType))
            }
        }
        return SamplePack(
            manifestVersion: 2, packId: "auto-song-intermediate",
            name: "Auto Kit", family: .mixed, pads: out)
    }

    /// The Run covering server pad index `idx`.
    private func run(at idx: Int) -> Run {
        var base = 0
        for run in webOrderRuns {
            if idx < base + run.count { return run }
            base += run.count
        }
        fatalError("index beyond fixture")
    }

    // MARK: - Mapper: server order/labels/colors verbatim

    func testFloodMapsAll64PadsInServerOrder() {
        let pack = floodPack()
        let pairs = KitGridMapper.pairs(pack: pack)
        XCTAssertEqual(pairs.count, 64)
        for (i, pair) in pairs.enumerated() {
            // Order IS the server's: padIdx passes through untouched.
            XCTAssertEqual(pair.chop.idx, pack.pads[i].padIdx)
            // Label verbatim (web: name.textContent = pad.name).
            XCTAssertEqual(pair.chop.sectionLabel, pack.pads[i].name)
            // Color + category verbatim.
            XCTAssertEqual(pair.chop.colorHint, pack.pads[i].colorHint)
            XCTAssertEqual(pair.chop.category, pack.pads[i].category)
            // Audio source: the pad's own stem role, never a hard-coded one.
            XCTAssertEqual(pair.stem, pack.pads[i].stemSlice?.stemRole)
        }
    }

    func testLoopableDrivesPhraseKind() {
        let looped = kitPad(0, name: "A", category: "DRUMS", hex: "#EF4444",
                            stem: "drums", contentType: "rhythm_loop",
                            loopable: true)
        let oneShot = kitPad(1, name: "B", category: "STAB", hex: "#8B5CF6",
                             stem: "other", contentType: "one_shot",
                             loopable: false)
        let pack = SamplePack(packId: "p", name: "P", family: .mixed,
                              pads: [looped, oneShot])
        let pairs = KitGridMapper.pairs(pack: pack)
        XCTAssertEqual(pairs[0].chop.kind, "phrase")
        XCTAssertEqual(pairs[1].chop.kind, "chord")
    }

    func testPadsWithoutStemSliceAreDropped() {
        let fileOnly = SamplePad(padIdx: 0, name: "File", family: .percussion,
                                 filename: "kick.m4a")
        let sliced = kitPad(1, name: "Drums beat", category: "DRUMS",
                            hex: "#EF4444", stem: "drums",
                            contentType: "rhythm_loop")
        let pack = SamplePack(packId: "p", name: "P", family: .mixed,
                              pads: [fileOnly, sliced])
        let pairs = KitGridMapper.pairs(pack: pack)
        XCTAssertEqual(pairs.count, 1)
        XCTAssertEqual(pairs[0].chop.idx, 1)
    }

    func testDownloadedSampleGetsDrumfileSentinel() {
        let pack = floodPack(pads: 3)
        let file = URL(fileURLWithPath: "/tmp/pad1.wav")
        let pairs = KitGridMapper.pairs(pack: pack, sampleFiles: [1: file])
        XCTAssertEqual(pairs[0].chop.assetId, "asset-0")
        XCTAssertEqual(pairs[1].chop.assetId, "drumfile:1")
        XCTAssertEqual(pairs[2].chop.assetId, "asset-2")
    }

    // MARK: - Mount: the whole 8×8 fills

    func testFloodMountFillsAll64GridCells() {
        let controller = makeController()
        controller.adoptAssignments(KitGridMapper.pairs(pack: floodPack()))
        XCTAssertEqual(controller.assignments.count, 64)
        // Every cell of the 8×8 is mounted and visible at padCount 64.
        for row in 0..<8 {
            for col in 0..<8 {
                let pad = LaunchpadPad(row: row, col: col)
                XCTAssertNotNil(controller.assignments[pad],
                                "cell (\(row),\(col)) must be filled")
                XCTAssertTrue(controller.isPadVisible(pad))
            }
        }
        // Row-major: top-left is server pad 0, bottom-right pad 63.
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 0, col: 0)]?.chop.idx, 0)
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 7, col: 7)]?.chop.idx, 63)
        // Labels land verbatim on the tiles.
        XCTAssertEqual(
            controller.padLabel(LaunchpadPad(row: 0, col: 0)),
            "Drums beat Verse")
        XCTAssertEqual(
            controller.padLabel(LaunchpadPad(row: 7, col: 7)),
            "Vocal lead Chorus")
    }

    func testSixteenPadKitStillMountsSixteen() {
        // 16 mode unchanged: a pads=16 manifest fills exactly rows 0–1.
        let controller = makeController()
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 16)))
        XCTAssertEqual(controller.assignments.count, 16)
        XCTAssertNil(controller.assignments[LaunchpadPad(row: 2, col: 0)])
    }

    // MARK: - Colors: web `_CATEGORY_HEX` palette, screen + LED single source

    func testCategoryColorsMatchWebPaletteAcrossTheFlood() {
        let controller = makeController()
        let pack = floodPack()
        controller.adoptAssignments(KitGridMapper.pairs(pack: pack))
        for idx in 0..<64 {
            let pad = LaunchpadPad(row: idx / 8, col: idx % 8)
            guard let assignment = controller.assignments[pad] else {
                return XCTFail("pad \(idx) unmounted")
            }
            let expected = UInt32(run(at: idx).hex.dropFirst(), radix: 16)!
            XCTAssertEqual(
                controller.displayColorHint(for: assignment, at: pad),
                expected,
                "pad \(idx) (\(run(at: idx).category)) must show the server color")
        }
    }

    func testResidualSynthPadShowsServerTealNotRecomputedLead() {
        // stem "other" + contentType "lead_loop" recomputes to LEAD orange —
        // only the server's explicit SYNTH category (residual_is_synth) can
        // color it web-teal. The regression this pins: desktop ignored the
        // manifest category and painted synth pads orange.
        let controller = makeController()
        let synth = kitPad(0, name: "Synth lead", category: "SYNTH",
                           hex: "#14B8A6", stem: "other",
                           contentType: "lead_loop")
        let pack = SamplePack(packId: "p", name: "P", family: .mixed,
                              pads: [synth])
        controller.adoptAssignments(KitGridMapper.pairs(pack: pack))
        let pad = LaunchpadPad(row: 0, col: 0)
        let assignment = controller.assignments[pad]!
        XCTAssertEqual(controller.displayColorHint(for: assignment, at: pad),
                       0x14B8A6)
        XCTAssertEqual(controller.category(for: pad), .synth)
    }

    /// kind=drums kits carry category "DRUMS" on EVERY pad but PER-CLASS
    /// colorHints (drum_kit.py _CLASS_HEX: kick red, snare amber, hats cyan,
    /// cymbal purple; grooves blue) and no contentType. Web paints
    /// parseColor(pad.colorHint) verbatim (kit.js buildPadTile), so
    /// category-over-hint precedence flattened the whole drum kit into one
    /// red block on tiles AND hardware LEDs — the "one color block" class
    /// (22bd58b6) again. The flood fixtures above set colorHint == category
    /// hex, which made that inversion untestable by construction; these
    /// hints deliberately differ from the DRUMS hex.
    func testDrumKitPadsKeepPerClassColorHintsNotFlatCategoryRed() {
        let transport = FakeTransport()
        let controller = LaunchpadController(
            nowProvider: { 0 }, fetcher: FakeFetcher())
        controller.attach(transport: transport)
        let classes: [(name: String, hex: String, rgb: UInt32, loopable: Bool)] = [
            ("Kick",       "#EF4444", 0xEF4444, false),
            ("Snare",      "#F59E0B", 0xF59E0B, false),
            ("Hat Closed", "#06B6D4", 0x06B6D4, false),
            ("Cymbal",     "#A855F7", 0xA855F7, false),
            ("Groove 1",   "#3B82F6", 0x3B82F6, true),
        ]
        let pads = classes.enumerated().map { idx, cls in
            SamplePad(
                padIdx: idx, name: cls.name, family: .percussion,
                colorHint: cls.hex,
                stemSlice: StemSlice(
                    stemRole: "drums",
                    startSec: Double(idx), endSec: Double(idx) + 1),
                loopable: cls.loopable,
                category: "DRUMS")   // the SAME category on every pad
        }
        let pack = SamplePack(packId: "drumkit-song", name: "Drum Kit",
                              family: .percussion, pads: pads)
        controller.adoptAssignments(KitGridMapper.pairs(pack: pack))
        for (idx, cls) in classes.enumerated() {
            let pad = LaunchpadPad(row: idx / 8, col: idx % 8)
            guard let assignment = controller.assignments[pad] else {
                return XCTFail("pad \(idx) unmounted")
            }
            XCTAssertEqual(
                controller.displayColorHint(for: assignment, at: pad),
                cls.rgb,
                "\(cls.name) keeps its per-class hint, not flat DRUMS red")
            // The hardware LED paints the same verbatim hint (single source).
            XCTAssertEqual(transport.lights[pad], .solid(colorHint: cls.rgb))
        }
    }

    func testKitPadWithMissingHintFallsBackToExplicitCategory() {
        // The category color is only the FALLBACK for an absent/unparseable
        // hint — and it must be the SERVER'S category, not the
        // stem+contentType recomputation (which would say LEAD orange here).
        let controller = makeController()
        let synth = SamplePad(
            padIdx: 0, name: "Synth lead", family: .mixed,
            stemSlice: StemSlice(stemRole: "other", startSec: 0, endSec: 2),
            loopable: true, contentType: "lead_loop", category: "SYNTH")
        let pack = SamplePack(packId: "p", name: "P", family: .mixed,
                              pads: [synth])
        controller.adoptAssignments(KitGridMapper.pairs(pack: pack))
        let pad = LaunchpadPad(row: 0, col: 0)
        XCTAssertEqual(
            controller.displayColorHint(
                for: controller.assignments[pad]!, at: pad),
            0x14B8A6,
            "missing hint → server category teal, never recomputed lead")
    }

    // MARK: - Kit-mounted flag (the 16/64 resize-reload guard's input)

    /// The host's resize refetch keys on the CONTROLLER's grid provenance,
    /// never its own activeGridPackId: the panel's stem/sliceMode Load
    /// mounts a chop grid via setChops with the session none the wiser,
    /// and the attach-time auto kit sets the pack id on EVERY song — the
    /// stale id turned the 16/64 toggle into a silent chop-grid → auto-kit
    /// replacement (pre-flood the toggle was display-only there).
    func testKitGridMountedFlagTracksGridProvenance() {
        let controller = makeController()
        XCTAssertFalse(controller.isKitGridMounted)
        // Kit mount (auto/drum/flip/donor kits) → flag up.
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 16)))
        XCTAssertTrue(controller.isKitGridMounted)
        // A chop grid mounted OVER the kit (panel Load → loadChops →
        // setChops) → flag down: the resize reload must not refetch.
        controller.setChops(
            [Chop(idx: 0, startSec: 0, endSec: 2, durationSec: 2,
                  kind: "chord")],
            stem: "other", sliceMode: "chord")
        XCTAssertFalse(controller.isKitGridMounted)
        // Kit again, then a borrow mount → down again (borrow re-arranges
        // locally; an auto-kit refetch would clobber the donor grid).
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 16)))
        XCTAssertTrue(controller.isKitGridMounted)
        controller.adoptBorrowAssignments([
            .init(chop: Chop(idx: 0, startSec: 0, endSec: 2, durationSec: 2,
                             kind: "phrase", sectionLabel: "Loop",
                             loopable: true, loopScore: 1.0),
                  stem: "drums", sourceLabel: "This song", source: .initial)
        ])
        XCTAssertFalse(controller.isKitGridMounted)
    }

    // MARK: - 16/64 toggle → reload hook (web setPadCount → reloadKit)

    func testPadCountToggleFiresReloadHookOnKitGrids() {
        let controller = makeController()
        controller.adoptAssignments(KitGridMapper.pairs(pack: floodPack()))
        var fired: [Int] = []
        controller.onPadCountChanged = { fired.append($0) }
        controller.padCount = 16
        controller.padCount = 16   // no change → no refetch
        controller.padCount = 64
        XCTAssertEqual(fired, [16, 64])
    }

    // MARK: - Compact 16 = the 4×4 BLOCK, screen AND hardware (D-037)

    func testCompactKitMountsAsFourByFourBlock() {
        // A pads=16 kit mounted while the surface is compact fills grid
        // rows 0–3 × cols 0–3 — the SAME shape the on-screen 4×4 draws —
        // never the top two 8-wide hardware rows (the 2×8 regression: the
        // hardware Launchpad lit a different shape than the screen).
        let controller = makeController()
        controller.padCount = 16
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 16)))
        XCTAssertEqual(controller.assignments.count, 16)
        for row in 0..<4 {
            for col in 0..<4 {
                let pad = LaunchpadPad(row: row, col: col)
                XCTAssertNotNil(controller.assignments[pad],
                                "block cell (\(row),\(col)) must be filled")
                XCTAssertTrue(controller.isPadVisible(pad))
                // Server order runs 4-wide through the block.
                XCTAssertEqual(controller.assignments[pad]?.chop.idx,
                               row * 4 + col)
            }
        }
        // Nothing outside the block — including the old 2×8 cells.
        XCTAssertNil(controller.assignments[LaunchpadPad(row: 0, col: 4)])
        XCTAssertNil(controller.assignments[LaunchpadPad(row: 1, col: 7)])
        XCTAssertFalse(controller.isPadVisible(LaunchpadPad(row: 0, col: 4)))
        XCTAssertFalse(controller.isPadVisible(LaunchpadPad(row: 4, col: 0)))
    }

    func testToggleRelaysKitBetweenBlockAndFullGrid() {
        // 64-flood mounted, then 16: the retained pairs re-lay as the 4×4
        // block (first 16, server order) IMMEDIATELY — the host's refetch
        // replaces them later, but the surface must never show a stale
        // shape. Back to 64: the full flood returns from the retained set.
        let controller = makeController()
        controller.adoptAssignments(KitGridMapper.pairs(pack: floodPack()))
        controller.padCount = 16
        XCTAssertEqual(controller.assignments.count, 16)
        for (pad, a) in controller.assignments {
            XCTAssertTrue(pad.row < 4 && pad.col < 4,
                          "compact kit pad \(pad) outside the 4×4 block")
            XCTAssertLessThan(a.chop.idx, 16, "compact shows the first 16")
        }
        XCTAssertTrue(controller.isKitGridMounted,
                      "re-lay must not drop kit provenance (the refetch keys on it)")
        controller.padCount = 64
        XCTAssertEqual(controller.assignments.count, 64, "64 restores the flood")
        XCTAssertEqual(
            controller.assignments[LaunchpadPad(row: 7, col: 7)]?.chop.idx, 63)
    }

    func testChopGridRelaysAcrossToggle() {
        // Chop grids (panel stem/sliceMode loads) re-lay from rawChops the
        // same way: 16 = first 16 chops in the block, 64 = all, restored.
        let controller = makeController()
        let chops = (0..<64).map {
            Chop(idx: $0, startSec: Double($0), endSec: Double($0) + 1,
                 durationSec: 1, kind: "chord")
        }
        controller.setChops(chops, stem: "other", sliceMode: "chord")
        XCTAssertEqual(controller.assignments.count, 64)
        controller.padCount = 16
        XCTAssertEqual(controller.assignments.count, 16)
        for (pad, a) in controller.assignments {
            XCTAssertTrue(pad.row < 4 && pad.col < 4)
            XCTAssertLessThan(a.chop.idx, 16)
        }
        controller.padCount = 64
        XCTAssertEqual(controller.assignments.count, 64,
                       "toggling back restores the whole chop grid")
    }

    // MARK: - ONE press pipeline: flood pads ride the stamped padDown path

    /// Hardware transport twin that delivers STAMPED pad events — what
    /// USBLaunchpadTransport does on the MIDI receive thread.
    private final class StampedFakeTransport: LaunchpadTransport,
                                              StampedPadTransport {
        var connectionState: LaunchpadConnectionState { .onScreen }
        var onPadDown: ((LaunchpadPad) -> Void)?
        var onPadUp: ((LaunchpadPad) -> Void)?
        var onPadDownStamped: ((LaunchpadPad, Double, UInt64) -> Void)?
        var onPadUpStamped: ((LaunchpadPad, Double, UInt64) -> Void)?
        var legacyDeliveries = 0
        func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {}
        func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {}
        func clearLights() {}
        /// Deliver exactly like the USB transport: stamped wins, legacy
        /// only as fallback (never both).
        func press(_ pad: LaunchpadPad, songSeconds: Double, hostTime: UInt64) {
            if let stamped = onPadDownStamped {
                stamped(pad, songSeconds, hostTime)
            } else {
                legacyDeliveries += 1
                onPadDown?(pad)
            }
        }
        func release(_ pad: LaunchpadPad, songSeconds: Double, hostTime: UInt64) {
            if let stamped = onPadUpStamped {
                stamped(pad, songSeconds, hostTime)
            } else {
                legacyDeliveries += 1
                onPadUp?(pad)
            }
        }
    }

    /// EVERY flood-mounted pad's hardware press must land in onTrigger —
    /// the single instrumented pipeline ([Trigger]/logPadLatency, the
    /// quantizer fed the receive-thread stamp, SessionController's padTag
    /// registration). A flood pad that sounds through any other lane is
    /// exactly the parallel-slow-lane regression this pins against.
    func testEveryFloodPadPressReachesOnTriggerViaStampedPath() {
        let transport = StampedFakeTransport()
        let controller = LaunchpadController(
            nowProvider: { 0 }, fetcher: FakeFetcher())
        controller.attach(transport: transport)
        controller.adoptAssignments(KitGridMapper.pairs(pack: floodPack()))
        controller.playbackMode = .oneShot   // instant gate: fires NOW

        var triggered: [LaunchpadPad] = []
        var released: [LaunchpadPad] = []
        controller.onTrigger = { pad, assignment, _, _ in
            triggered.append(pad)
            // The assignment under the pad is the one the press fires.
            XCTAssertEqual(controller.assignments[pad]?.chop.idx,
                           assignment.chop.idx)
        }
        controller.onRelease = { pad, _ in released.append(pad) }

        for row in 0..<8 {
            for col in 0..<8 {
                let pad = LaunchpadPad(row: row, col: col)
                transport.press(pad, songSeconds: 0, hostTime: 1)
                transport.release(pad, songSeconds: 0, hostTime: 2)
            }
        }
        XCTAssertEqual(triggered.count, 64,
                       "every flood pad rides the ONE stamped press pipeline")
        XCTAssertEqual(released.count, 64,
                       "every flood pad-up rides the same pipeline")
        XCTAssertEqual(transport.legacyDeliveries, 0,
                       "a stamped transport must never fall to the legacy lane")
    }

    /// Same pipeline pin at the compact 4×4: block presses fire, presses
    /// outside the block are dropped dark (no hidden-cell triggering).
    func testCompactBlockPressesFireAndOutsideBlockIsDropped() {
        let transport = StampedFakeTransport()
        let controller = LaunchpadController(
            nowProvider: { 0 }, fetcher: FakeFetcher())
        controller.attach(transport: transport)
        controller.padCount = 16
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 16)))
        controller.playbackMode = .oneShot

        var triggered: [LaunchpadPad] = []
        controller.onTrigger = { pad, _, _, _ in triggered.append(pad) }
        for row in 0..<8 {
            for col in 0..<8 {
                transport.press(LaunchpadPad(row: row, col: col),
                                songSeconds: 0, hostTime: 1)
            }
        }
        XCTAssertEqual(triggered.count, 16, "exactly the block fires")
        XCTAssertTrue(triggered.allSatisfy { $0.row < 4 && $0.col < 4 })
    }

    func testPadCountToggleDoesNotFireReloadHookForMountedBorrow() {
        // A borrow re-arranges locally (applyBorrowLayout) — refetching the
        // auto kit would clobber the donor grid.
        let controller = makeController()
        let mounts: [LaunchpadController.BorrowMount] =
            ["drums", "bass", "vocals", "other"].enumerated().map { i, stem in
                .init(
                    chop: Chop(idx: i, startSec: 0, endSec: 2, durationSec: 2,
                               kind: "phrase", sectionLabel: "Loop \(i)",
                               loopable: true, loopScore: 1.0),
                    stem: stem, sourceLabel: "This song", source: .initial)
            }
        controller.adoptBorrowAssignments(mounts)
        var fired: [Int] = []
        controller.onPadCountChanged = { fired.append($0) }
        controller.padCount = 16
        controller.padCount = 64
        XCTAssertEqual(fired, [], "borrow toggles must not trigger a kit refetch")
        // The borrow is still mounted (re-arranged, not clipped or replaced).
        XCTAssertFalse(controller.assignments.isEmpty)
        XCTAssertFalse(controller.borrowSourceLabels.isEmpty)
    }
}
