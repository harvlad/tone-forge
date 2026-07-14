// SequencerAudioAdapterTests.swift
//
// Headless mapping coverage for SequencerAudioAdapter through the
// SequencerChopTriggering seam: bundleChop references resolve
// against idx-sorted preset chops (LaunchpadController grid order),
// unknown presets / out-of-range indices no-op, customURL forwards
// its segment bounds, and reset() drops the resolution tables.
// No AVAudioEngine involved.

import XCTest
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class SequencerAudioAdapterTests: XCTestCase {

    // MARK: - Fixtures

    private final class RecordingSink: SequencerChopTriggering {
        struct ChopCall: Equatable {
            let assignment: PadAssignment
            let velocity: Float
            let pan: Float
        }
        struct FileCall: Equatable {
            let url: URL
            let startSec: Double?
            let endSec: Double?
            let velocity: Float
            let pan: Float
        }
        var chopCalls: [ChopCall] = []
        var fileCalls: [FileCall] = []

        func sequencerTriggerChop(
            _ assignment: PadAssignment, velocity: Float, pan: Float
        ) {
            chopCalls.append(.init(
                assignment: assignment, velocity: velocity, pan: pan
            ))
        }

        func sequencerTriggerFile(
            url: URL, startSec: Double?, endSec: Double?,
            velocity: Float, pan: Float
        ) {
            fileCalls.append(.init(
                url: url, startSec: startSec, endSec: endSec,
                velocity: velocity, pan: pan
            ))
        }
    }

    private func chop(
        _ idx: Int, start: Double = 0, end: Double = 1,
        symbol: String? = nil
    ) -> Chop {
        Chop(
            idx: idx, startSec: start, endSec: end,
            durationSec: end - start, kind: "chord",
            chordSymbol: symbol
        )
    }

    private func bundle(
        presets: [String: BundlePreset]
    ) -> SongBundle {
        SongBundle(
            bundleVersion: 1,
            analysisId: "a1",
            meta: BundleMeta(
                title: "T", artist: "A", sourceUrl: "",
                durationSec: 120, tempoBpm: 120
            ),
            timeline: BundleTimeline(
                chords: [], sections: [], beats: [], downbeats: []
            ),
            stems: [],
            presets: presets
        )
    }

    private var sink: RecordingSink!
    private var adapter: SequencerAudioAdapter!
    private var player: SequencerPlayer!

    override func setUp() {
        super.setUp()
        sink = RecordingSink()
        adapter = SequencerAudioAdapter(sink: sink)
        player = SequencerPlayer(eventBus: ContributionEventBus())
    }

    override func tearDown() {
        player.stop()
        super.tearDown()
    }

    /// Configure with chops deliberately out of idx order — the
    /// adapter must resolve chopIndex against idx-sorted order.
    private func configureHarmonic() {
        adapter.configure(bundle: bundle(presets: [
            "harmonic": BundlePreset(
                stem: "other", sliceMode: "chord",
                chops: [
                    chop(2, start: 20, end: 21, symbol: "C"),
                    chop(0, start: 0, end: 1, symbol: "Am"),
                    chop(1, start: 10, end: 11, symbol: "F"),
                ]
            ),
        ]))
    }

    // MARK: - bundleChop

    func testBundleChopResolvesIdxSortedOrder() {
        configureHarmonic()

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 0,
            velocity: 0.7, pan: -0.5
        )

        XCTAssertEqual(sink.chopCalls.count, 1)
        let call = sink.chopCalls[0]
        XCTAssertEqual(call.assignment.chop.idx, 0, "chopIndex 0 = lowest idx")
        XCTAssertEqual(call.assignment.chop.chordSymbol, "Am")
        XCTAssertEqual(call.assignment.stem, "other")
        XCTAssertEqual(call.velocity, 0.7)
        XCTAssertEqual(call.pan, -0.5)
    }

    func testBundleChopLastIndexResolvesHighestIdx() {
        configureHarmonic()

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 2,
            velocity: 1, pan: 0
        )

        XCTAssertEqual(sink.chopCalls.first?.assignment.chop.idx, 2)
        XCTAssertEqual(sink.chopCalls.first?.assignment.chop.chordSymbol, "C")
    }

    func testUnknownPresetKeyNoOps() {
        configureHarmonic()

        adapter.sequencerPlayer(
            player, playBundleChop: "sections", chopIndex: 0,
            velocity: 1, pan: 0
        )

        XCTAssertTrue(sink.chopCalls.isEmpty)
    }

    func testOutOfRangeChopIndexNoOps() {
        configureHarmonic()

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 3,
            velocity: 1, pan: 0
        )
        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: -1,
            velocity: 1, pan: 0
        )

        XCTAssertTrue(sink.chopCalls.isEmpty)
    }

    func testUnconfiguredAdapterNoOps() {
        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 0,
            velocity: 1, pan: 0
        )
        XCTAssertTrue(sink.chopCalls.isEmpty)
    }

    func testResetDropsResolution() {
        configureHarmonic()
        adapter.reset()

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 0,
            velocity: 1, pan: 0
        )

        XCTAssertTrue(sink.chopCalls.isEmpty)
    }

    func testReconfigureReplacesPreviousBundle() {
        configureHarmonic()
        adapter.configure(bundle: bundle(presets: [
            "sections": BundlePreset(
                stem: "drums", sliceMode: "section",
                chops: [chop(0, start: 0, end: 30)]
            ),
        ]))

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 0,
            velocity: 1, pan: 0
        )
        XCTAssertTrue(sink.chopCalls.isEmpty, "old preset gone")

        adapter.sequencerPlayer(
            player, playBundleChop: "sections", chopIndex: 0,
            velocity: 1, pan: 0
        )
        XCTAssertEqual(sink.chopCalls.first?.assignment.stem, "drums")
    }

    // MARK: - Chop edits

    func testConfigureWithEditsResolvesEditedBoundaries() {
        var edits = ChopEdits(presetKey: "harmonic")
        edits.boundaryEdits[1] = ChopBoundaryEdit(
            chopIndex: 1,
            originalStart: 10, originalEnd: 11,
            editedStart: 10.25, editedEnd: 10.75
        )
        adapter.configure(
            bundle: bundle(presets: [
                "harmonic": BundlePreset(
                    stem: "other", sliceMode: "chord",
                    chops: [
                        chop(2, start: 20, end: 21, symbol: "C"),
                        chop(0, start: 0, end: 1, symbol: "Am"),
                        chop(1, start: 10, end: 11, symbol: "F"),
                    ]
                ),
            ]),
            edits: ["harmonic": edits]
        )

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 1,
            velocity: 1, pan: 0
        )

        let chop = sink.chopCalls.first?.assignment.chop
        XCTAssertEqual(chop?.idx, 1)
        XCTAssertEqual(chop?.startSec, 10.25)
        XCTAssertEqual(chop?.endSec, 10.75)
    }

    func testConfigureWithEmptyEditsUsesBundleBoundaries() {
        adapter.configure(
            bundle: bundle(presets: [
                "harmonic": BundlePreset(
                    stem: "other", sliceMode: "chord",
                    chops: [chop(0, start: 0, end: 1, symbol: "Am")]
                ),
            ]),
            edits: ["harmonic": ChopEdits(presetKey: "harmonic")]
        )

        adapter.sequencerPlayer(
            player, playBundleChop: "harmonic", chopIndex: 0,
            velocity: 1, pan: 0
        )

        XCTAssertEqual(sink.chopCalls.first?.assignment.chop.startSec, 0)
        XCTAssertEqual(sink.chopCalls.first?.assignment.chop.endSec, 1)
    }

    // MARK: - customURL

    func testPlayURLForwardsSegmentBounds() {
        let url = URL(fileURLWithPath: "/tmp/kick.wav")

        adapter.sequencerPlayer(
            player, playURL: url, startSec: 1.5, endSec: 2.25,
            velocity: 0.9, pan: 0.25
        )

        XCTAssertEqual(sink.fileCalls, [.init(
            url: url, startSec: 1.5, endSec: 2.25,
            velocity: 0.9, pan: 0.25
        )])
    }

    func testPlayURLNilBoundsPassThrough() {
        let url = URL(fileURLWithPath: "/tmp/loop.wav")

        adapter.sequencerPlayer(
            player, playURL: url, startSec: nil, endSec: nil,
            velocity: 1, pan: 0
        )

        XCTAssertEqual(sink.fileCalls.first?.startSec, nil)
        XCTAssertEqual(sink.fileCalls.first?.endSec, nil)
    }

    // MARK: - Phase 4/5 sources stay silent

    func testFutureSourcesNoOp() {
        configureHarmonic()

        adapter.sequencerPlayer(
            player, playLocalSample: UUID(), velocity: 1, pan: 0
        )
        adapter.sequencerPlayer(
            player, playPackPad: "pack-1", padIdx: 0, velocity: 1, pan: 0
        )
        adapter.sequencerPlayer(
            player, playSynthChord: "Am", octaveShift: 0, velocity: 1
        )

        XCTAssertTrue(sink.chopCalls.isEmpty)
        XCTAssertTrue(sink.fileCalls.isEmpty)
    }
}
