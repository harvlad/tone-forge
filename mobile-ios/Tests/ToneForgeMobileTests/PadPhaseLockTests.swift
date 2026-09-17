// PadPhaseLockTests.swift
//
// The pad-timing / phase-lock contract ported from the web engine
// (backend/static/padengine.js) — semantics must stay bit-comparable:
//
//   1. LOOP launches quantize to the BAR grid, never individual beats
//      (a multi-bar loop beat-quantized starts mid-bar, out of phase).
//   2. Free-run (transport stopped) loop launches snap to a SINGLE BAR
//      of the shared lock lattice, not the full loop cycle — a pad
//      tapped mid-cycle waits ≤ 1 bar, not ~6-8 s.
//   3. A loop tapped mid-jam joins `phase = (boundary − anchor) mod
//      body` seconds INTO its body, with `boundary` the PRE-shift
//      quantize boundary (measuring the onset shift back in would
//      cancel the launch compensation — pads flam by shift deltas).
//   4. The progress readout includes that phase offset, or the drawn
//      playheads desync while the audio is locked.
//   5. Loop body frame counts round the region LENGTH — trunc-per-edge
//      lands on N or N+1 by fractional phase and walks locked loops
//      apart ~23 µs/cycle.
//   6. The FIRST loop launch anchors the phase lattice (padengine.js
//      :1166), and a stale anchor is cleared when the pool is silent
//      (:1154) — one-shots count as "sounding" for that check.
//   7. Region pads tile to the pack's shared cycle (_bakePad :924-931)
//      and the phase join divides by that CYCLE, not the raw body.
//   8. EVERY looping trigger phase-joins — loop-lock off / unquantized
//      means boundary = now, still joined (:1269-1273).
//   9. Latch/toggle second tap releases IMMEDIATELY with the 20 ms
//      fade (kit.js padDown latch branch → engine.release), never at
//      the end of the loop pass.
//
// Fixture strategy mirrors SampleSchedulerMultiPackTests: tiny sine
// .caf files written at runtime + in-memory ResolvedSamplePacks.
// Voice-level tests need a running AVAudioEngine and skip (not fail)
// on hosts with no default audio device.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class PadPhaseLockTests: XCTestCase {

    private var engine: AudioEngine!
    private var bus: SampleBus!
    private var pool: SampleVoicePool!
    private var scheduler: SampleScheduler!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("phase-lock-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        engine = AudioEngine()
        bus = SampleBus(engine: engine)
        pool = SampleVoicePool(engine: engine, bus: bus)
        scheduler = SampleScheduler(engine: engine, bus: bus, pool: pool)
        bus.attach(destination: engine.engine.mainMixerNode)
        pool.attach()
        engine.start()
    }

    override func tearDown() async throws {
        pool.stopAll()
        engine.stop()
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        scheduler = nil
        pool = nil
        bus = nil
        engine = nil
        try await super.tearDown()
    }

    // MARK: - Fixtures

    private func writeTone(to url: URL, durationSec: Double = 0.25) throws {
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
        packId: String,
        pads: [(idx: Int, loop: Bool)],
        durationSec: Double = 0.25
    ) throws -> ResolvedSamplePack {
        var padObjs: [SamplePad] = []
        var urls: [Int: URL] = [:]
        for p in pads {
            let url = tmpDir.appendingPathComponent("\(packId)-\(p.idx).caf")
            try writeTone(to: url, durationSec: durationSec)
            padObjs.append(SamplePad(
                padIdx: p.idx,
                name: "P\(p.idx)",
                family: .pads,
                filename: "\(packId)-\(p.idx).caf",
                loopPointSec: p.loop ? 0 : nil
            ))
            urls[p.idx] = url
        }
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: padObjs
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    private func requireRunningEngine() throws {
        try XCTSkipUnless(
            engine.engine.isRunning,
            "AVAudioEngine failed to start on this host — skipping voice-level test"
        )
    }

    /// 120 BPM bundle: beats every 0.5 s, downbeats every 2 s.
    private func load120BpmBundle() {
        scheduler.updateBundle(
            timeline: BundleTimeline(
                beats: stride(from: 0.0, through: 16.0, by: 0.5).map { $0 },
                downbeats: stride(from: 0.0, through: 16.0, by: 2.0).map { $0 }
            ),
            meta: BundleMeta(
                title: "t", artist: "a", sourceUrl: "",
                durationSec: 16, tempoBpm: 120
            )
        )
    }

    // MARK: - 1. Loops quantize to the BAR grid, never beats

    func testLoopQuantizePromotesSubBarGridsToBar() {
        // Sub-bar grids promote to the bar for looping pads.
        XCTAssertEqual(SampleScheduler.loopQuantize(.eighth, willLoop: true), .bar)
        XCTAssertEqual(SampleScheduler.loopQuantize(.quarter, willLoop: true), .bar)
        XCTAssertEqual(SampleScheduler.loopQuantize(.half, willLoop: true), .bar)
        // .off / .bar / .phrase pass through (phrase is coarser than a
        // bar and already bar-aligned — no mid-bar start possible).
        XCTAssertEqual(SampleScheduler.loopQuantize(.off, willLoop: true), .off)
        XCTAssertEqual(SampleScheduler.loopQuantize(.bar, willLoop: true), .bar)
        XCTAssertEqual(SampleScheduler.loopQuantize(.phrase, willLoop: true), .phrase)
        // One-shots keep beat granularity.
        for mode in QuantizeMode.allCases {
            XCTAssertEqual(SampleScheduler.loopQuantize(mode, willLoop: false), mode)
        }
    }

    /// End-to-end through trigger(): a beat-level quantize on a LOOPING
    /// pad lands on the next downbeat; the same quantize on a one-shot
    /// keeps the beat.
    func testLoopTriggerLandsOnBarOneShotOnBeat() throws {
        let pack = try makePack(packId: "grid", pads: [(0, true), (1, false)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        load120BpmBundle()
        scheduler.loopLock = false          // exercise the Quantizer path
        scheduler.quantize = .quarter
        engine.clock.play()
        engine.clock.seek(to: 0.6)          // mid-beat, mid-bar

        guard case .scheduled(let loopT) = scheduler.trigger(padIdx: 0, packId: "grid")
        else { return XCTFail("loop pad did not schedule") }
        // Old behavior: next beat (1.0). Bar-locked: next downbeat (2.0).
        XCTAssertEqual(loopT, 2.0, accuracy: 1e-9,
                       "looping pad must quantize to the BAR grid")

        guard case .scheduled(let hitT) = scheduler.trigger(padIdx: 1, packId: "grid")
        else { return XCTFail("one-shot pad did not schedule") }
        XCTAssertEqual(hitT, 1.0, accuracy: 1e-9,
                       "one-shot keeps beat-level quantize")
    }

    /// The loop-lock path snaps to the song's REAL downbeats (the grid
    /// the user hears), not a synthetic origin lattice.
    func testLoopLockLaunchUsesRealDownbeats() throws {
        let pack = try makePack(packId: "lock", pads: [(0, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        // Downbeats deliberately OFF the constant-tempo lattice.
        scheduler.updateBundle(
            timeline: BundleTimeline(
                beats: [0.5, 1.0, 1.5, 2.0, 2.5],
                downbeats: [0.5, 2.5, 4.5, 6.5]
            ),
            meta: BundleMeta(
                title: "t", artist: "a", sourceUrl: "",
                durationSec: 8, tempoBpm: 120
            )
        )
        scheduler.loopLock = true
        engine.clock.play()
        engine.clock.seek(to: 1.0)

        guard case .scheduled(let t) = scheduler.trigger(padIdx: 0, packId: "lock")
        else { return XCTFail("loop pad did not schedule") }
        XCTAssertEqual(t, 2.5, accuracy: 1e-9,
                       "loop lock must launch on the real downbeat, not k*cycle")
    }

    // MARK: - 2. Free-run waits a single bar, not the full cycle

    func testLockGridBoundarySnapsToSpacing() {
        // Mid-interval → next multiple of the spacing.
        XCTAssertEqual(
            SampleScheduler.lockGridBoundary(after: 0.5, spacing: 2.0),
            2.0, accuracy: 1e-9)
        XCTAssertEqual(
            SampleScheduler.lockGridBoundary(after: 3.9, spacing: 2.0),
            4.0, accuracy: 1e-9)
        // Grace: just past a boundary fires immediately.
        XCTAssertEqual(
            SampleScheduler.lockGridBoundary(after: 2.05, spacing: 2.0),
            2.05, accuracy: 1e-9)
        // Anchored lattice: boundaries at anchor + k*spacing.
        XCTAssertEqual(
            SampleScheduler.lockGridBoundary(after: 1.9, spacing: 2.0, anchor: 1.0),
            3.0, accuracy: 1e-9)
        // Pre-anchor presses fold to immediate (web parity).
        XCTAssertEqual(
            SampleScheduler.lockGridBoundary(after: 0.5, spacing: 2.0, anchor: 1.0),
            0.5, accuracy: 1e-9)
    }

    func testFreeRunLockSpacingIsSingleBar() {
        // Tempo known → one bar, NOT the full loop cycle.
        scheduler.updateSyntheticContext(tempoBpm: 120)
        XCTAssertEqual(scheduler.freeRunLockSpacingSec, 2.0, accuracy: 1e-9)
        XCTAssertEqual(scheduler.loopLengthSeconds, 8.0, accuracy: 1e-9,
                       "cycle stays multi-bar — only the LAUNCH wait shrinks")
        // No tempo → full-cycle fallback (web _lockLaunchTime w/o barSec).
        scheduler.updateSyntheticContext(tempoBpm: nil)
        XCTAssertEqual(scheduler.freeRunLockSpacingSec, 8.0, accuracy: 1e-9)
    }

    /// Transport stopped, loop lock on: the first loop fires instantly
    /// (fresh anchor), a second loop tapped mid-bar arms and fires
    /// within ONE bar of the lattice.
    func testFreeRunSecondLoopWaitsAtMostOneBar() async throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "free", pads: [(0, true), (1, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.updateSyntheticContext(tempoBpm: 240)   // bar = 1.0 s
        scheduler.loopLock = true
        XCTAssertEqual(engine.clock.state, .stopped)

        let k0 = SamplePadKey(packId: "free", padIdx: 0)
        let k1 = SamplePadKey(packId: "free", padIdx: 1)

        scheduler.trigger(padIdx: 0, packId: "free")
        XCTAssertTrue(pool.ringingPadKeys.contains(k0),
                      "first free-run loop fires immediately (fresh anchor)")
        XCTAssertFalse(pool.pendingPadKeys.contains(k0))

        // Past the 0.08 s grace, well inside the bar.
        try await Task.sleep(nanoseconds: 300_000_000)
        scheduler.trigger(padIdx: 1, packId: "free")
        XCTAssertTrue(pool.pendingPadKeys.contains(k1),
                      "mid-bar tap arms for the bar boundary")

        // One bar minus the elapsed 0.3 s ≈ 0.7 s wait; by +0.9 s the
        // pad must be sounding (old full-cycle lattice: 8 s wait).
        try await Task.sleep(nanoseconds: 900_000_000)
        XCTAssertTrue(pool.ringingPadKeys.contains(k1),
                      "armed loop must fire within one bar, not the full cycle")
        XCTAssertFalse(pool.pendingPadKeys.contains(k1))
    }

    // MARK: - 3. Phase measured from the PRE-shift boundary

    func testPhaseJoinMeasuredFromPreShiftBoundary() {
        // Two bars past the anchor into a 4 s body → 3 s in (7 mod 4).
        XCTAssertEqual(
            SampleScheduler.phaseJoinSeconds(boundary: 17.0, anchor: 10.0, bodySec: 4.0),
            3.0, accuracy: 1e-9)
        // First launch: boundary == anchor → phase 0.
        XCTAssertEqual(
            SampleScheduler.phaseJoinSeconds(boundary: 10.0, anchor: 10.0, bodySec: 4.0),
            0.0, accuracy: 1e-9)
        // Negative delta folds into [0, body).
        XCTAssertEqual(
            SampleScheduler.phaseJoinSeconds(boundary: 9.0, anchor: 10.0, bodySec: 4.0),
            3.0, accuracy: 1e-9)
        // Degenerate body → no join.
        XCTAssertEqual(
            SampleScheduler.phaseJoinSeconds(boundary: 17.0, anchor: 10.0, bodySec: 0),
            0.0, accuracy: 1e-9)
        // THE flam bug: the shifted start time (boundary + onset shift)
        // is NOT a valid measuring point — feeding it in changes the
        // phase by exactly the shift, which the launch delay already
        // compensates. The scheduler must pass the boundary.
        let shift = 0.045
        let atBoundary = SampleScheduler.phaseJoinSeconds(
            boundary: 17.0, anchor: 10.0, bodySec: 4.0)
        let atShiftedStart = SampleScheduler.phaseJoinSeconds(
            boundary: 17.0 + shift, anchor: 10.0, bodySec: 4.0)
        XCTAssertEqual(atShiftedStart - atBoundary, shift, accuracy: 1e-9)
    }

    // MARK: - 4. Progress readout includes the phase offset

    func testLoopReadoutIncludesPhaseOffset() {
        // Started 3 s INTO a 4 s body, 0.5 s ago → 3.5/4 through.
        XCTAssertEqual(
            SampleVoicePool.loopReadout(elapsedSec: 0.5, phaseStartSec: 3.0, bodySec: 4.0),
            0.875, accuracy: 1e-9)
        // Wrap: 1 s elapsed + 3 s phase = one full pass → top of body.
        XCTAssertEqual(
            SampleVoicePool.loopReadout(elapsedSec: 1.0, phaseStartSec: 3.0, bodySec: 4.0),
            0.0, accuracy: 1e-9)
        // No phase → unchanged legacy readout.
        XCTAssertEqual(
            SampleVoicePool.loopReadout(elapsedSec: 0.5, phaseStartSec: 0, bodySec: 4.0),
            0.125, accuracy: 1e-9)
        XCTAssertEqual(
            SampleVoicePool.loopReadout(elapsedSec: 1.0, phaseStartSec: 0, bodySec: 0),
            0.0, accuracy: 1e-9)
    }

    /// Voice-level: a phase-joined loop's drawn playhead starts at the
    /// join offset, not at 0 (the audio starts there too — same buffer
    /// offset feeds both).
    func testPoolPhaseJoinOffsetsLivePlayhead() throws {
        try requireRunningEngine()
        let format = engine.canonicalFormat
        let sr = format.sampleRate
        // Long body: node setup + play() cost tens of ms of real elapsed
        // time before the readout; against 2 s that is ~1% of the body,
        // safely inside the tolerance.
        let frames = AVAudioFrameCount(2.0 * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData {
            for c in 0..<Int(format.channelCount) {
                for i in 0..<Int(frames) {
                    ch[c][i] = Float(sin(2 * .pi * 220 * Double(i) / sr) * 0.3)
                }
            }
        }
        let key = SamplePadKey(packId: "join", padIdx: 0)
        let req = SampleTrigger(
            padKey: key, loop: true, chokeGroup: nil, gainDb: 0,
            phaseSec: 0.8
        )
        XCTAssertNotNil(pool.trigger(req, buffer: buf, at: nil))
        guard let phase = pool.loopPhase(padKey: key) else {
            return XCTFail("looping voice must report a playhead")
        }
        // 0.8 s into a 2 s body = 0.4, plus a sliver of real elapsed.
        XCTAssertEqual(phase, 0.4, accuracy: 0.05)
    }

    // MARK: - 5. Loop body frames round the region length

    func testDecodeRoundsLoopBodyLengthNotPerEdge() throws {
        let sr = 44_100.0
        let stemURL = tmpDir.appendingPathComponent("stem.caf")
        try writeTone(to: stemURL, durationSec: 1.0)

        // Edges chosen so trunc-per-edge and round-length disagree:
        //   start = 0.3 frames  → trunc 0
        //   end   = 11025.9 fr  → trunc 11025 ⇒ per-edge body 11025
        //   length = 11025.6 fr → round 11026
        let pad = SamplePad(
            padIdx: 0, name: "loop", family: .pads,
            stemSlice: StemSlice(stemRole: "other", startSec: 0, endSec: 0.5),
            loopStartSec: 0.3 / sr,
            loopEndSec: 11_025.9 / sr,
            loopable: true
        )
        let pack = SamplePack(
            packId: "round", name: "round", family: .pads, pads: [pad]
        )
        let resolved = ResolvedSamplePack(pack: pack, padFileURLs: [:])

        let decoded = SampleScheduler.decodePackBuffers(
            resolved, stemFiles: ["other": stemURL], target: nil
        )
        XCTAssertEqual(
            decoded.loopBodyFrames[0], 11_026,
            "body must be round(length·sr); trunc-per-edge (11025) makes "
            + "this pad loop one frame short of every pad rounded to 11026 "
            + "and walk out of phase over minutes")
    }

    // MARK: - 6. Lattice anchoring (first launch / silence re-anchor)

    func testFirstFreeRunLoopLaunchAnchorsLattice() throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "anchor", pads: [(0, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.updateSyntheticContext(tempoBpm: 120)
        scheduler.loopLock = true

        XCTAssertNil(scheduler.loopLockAnchorHostSec)
        _ = scheduler.trigger(padIdx: 0, packId: "anchor")
        XCTAssertNotNil(
            scheduler.loopLockAnchorHostSec,
            "the FIRST loop launch must anchor the shared phase lattice "
            + "(padengine.js:1166) — anchoring only on the second launch "
            + "leaves clip 1 permanently out of unison")
        XCTAssertTrue(pool.ringingPadKeys.contains(
            SamplePadKey(packId: "anchor", padIdx: 0)),
            "the anchoring launch fires immediately at phase 0")
    }

    func testStaleAnchorClearedWhenPoolSilent() async throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "stale", pads: [(0, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.updateSyntheticContext(tempoBpm: 120)
        scheduler.loopLock = true

        _ = scheduler.trigger(padIdx: 0, packId: "stale")
        guard let first = scheduler.loopLockAnchorHostSec else {
            return XCTFail("first launch must anchor")
        }
        scheduler.release(padIdx: 0, packId: "stale")
        try await Task.sleep(nanoseconds: 150_000_000) // past the 20 ms fade

        _ = scheduler.trigger(padIdx: 0, packId: "stale")
        guard let second = scheduler.loopLockAnchorHostSec else {
            return XCTFail("relaunch must re-anchor")
        }
        XCTAssertGreaterThan(
            second, first,
            "a silent pool must abandon the previous jam's lattice "
            + "(padengine.js:1154) — a leftover anchor re-breaks the next "
            + "session's first launch")
        // Fresh anchor ⇒ the relaunch fired immediately at phase 0.
        XCTAssertTrue(pool.ringingPadKeys.contains(
            SamplePadKey(packId: "stale", padIdx: 0)))
    }

    func testAudibleOneShotHoldsAnchor() throws {
        try requireRunningEngine()
        // Pad 0 loops; pad 1 is a LONG one-shot still sounding when the
        // loop relaunches.
        let pack = try makePack(
            packId: "hold", pads: [(0, true), (1, false)], durationSec: 1.5)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.updateSyntheticContext(tempoBpm: 120)
        scheduler.loopLock = true

        _ = scheduler.trigger(padIdx: 0, packId: "hold")
        guard let anchor = scheduler.loopLockAnchorHostSec else {
            return XCTFail("first launch must anchor")
        }
        scheduler.release(padIdx: 0, packId: "hold")

        _ = scheduler.trigger(padIdx: 1, packId: "hold") // one-shot rings
        XCTAssertTrue(pool.soundingPadKeys.contains(
            SamplePadKey(packId: "hold", padIdx: 1)))

        _ = scheduler.trigger(padIdx: 0, packId: "hold")
        XCTAssertEqual(
            scheduler.loopLockAnchorHostSec ?? -1, anchor, accuracy: 1e-9,
            "web keeps _lockAnchor while ANY voice sounds (padengine.js:1154 "
            + "counts one-shots) — an audible stab must hold the lattice the "
            + "loop rejoins")
    }

    func testOneShotSlotClearsOnNaturalEnd() async throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "shot", pads: [(0, false)]) // 0.25 s
        try scheduler.setActivePack(pack, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0, packId: "shot")
        let key = SamplePadKey(packId: "shot", padIdx: 0)
        XCTAssertTrue(pool.soundingPadKeys.contains(key))
        // 0.25 s buffer + render/dispatch slop; .dataPlayedBack lands well
        // inside this window.
        try await Task.sleep(nanoseconds: 900_000_000)
        XCTAssertFalse(
            pool.soundingPadKeys.contains(key),
            "a played-out one-shot must release its slot (web: onended) — an "
            + "isActive-forever slot held the free-run lattice hostage")
    }

    // MARK: - 7. Shared-cycle tiling

    func testSharedCycleFramesGatesOnRealRegion() {
        // Region pad shorter than the cycle → tile target = round(cycle·sr).
        XCTAssertEqual(
            SampleScheduler.sharedCycleFrames(
                bodyFrames: 48_000, cycleSec: 2.0, sampleRate: 48_000,
                hasRegion: true),
            96_000)
        // The longest region pad already fills the cycle — no growth.
        XCTAssertEqual(
            SampleScheduler.sharedCycleFrames(
                bodyFrames: 96_000, cycleSec: 2.0, sampleRate: 48_000,
                hasRegion: true),
            96_000)
        // Region-less loop pads keep their own length (web hasRegion gate:
        // borrow packs / whole buffers have no shared musical cycle).
        XCTAssertEqual(
            SampleScheduler.sharedCycleFrames(
                bodyFrames: 48_000, cycleSec: 2.0, sampleRate: 48_000,
                hasRegion: false),
            0)
        // Degenerate args never tile.
        XCTAssertEqual(
            SampleScheduler.sharedCycleFrames(
                bodyFrames: 0, cycleSec: 2.0, sampleRate: 48_000,
                hasRegion: true),
            0)
    }

    /// Voice-level: the pool tiles a loop body up to `loopCycleFrames`,
    /// and the CYCLE becomes the loop period — a phase join deep into the
    /// second body repeat is only reachable inside the tiled cycle.
    func testPoolTilesLoopBodyToCycle() throws {
        try requireRunningEngine()
        let format = engine.canonicalFormat
        let sr = format.sampleRate
        let bodyFrames = Int(0.5 * sr)
        let buf = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: AVAudioFrameCount(bodyFrames))!
        buf.frameLength = AVAudioFrameCount(bodyFrames)
        if let ch = buf.floatChannelData {
            for c in 0..<Int(format.channelCount) {
                for i in 0..<bodyFrames {
                    ch[c][i] = Float(sin(2 * .pi * 220 * Double(i) / sr) * 0.3)
                }
            }
        }
        let key = SamplePadKey(packId: "tile", padIdx: 0)
        // 0.75 s into a 1.0 s tiled cycle; the raw 0.5 s body would clamp
        // this join to its last frame.
        let req = SampleTrigger(
            padKey: key, loop: true, chokeGroup: nil, gainDb: 0,
            loopCycleFrames: bodyFrames * 2, phaseSec: 0.75
        )
        XCTAssertNotNil(pool.trigger(req, buffer: buf, at: nil))
        guard let phase = pool.loopPhase(padKey: key) else {
            return XCTFail("looping voice must report a playhead")
        }
        XCTAssertEqual(
            phase, 0.75, accuracy: 0.05,
            "the tiled cycle is the loop period — a readout mod the raw "
            + "body desyncs the playhead from the shared lattice")
    }

    /// End-to-end: a 1-bar region pad launched mid-jam next to a 2-bar
    /// region pad reads the SAME playhead phase — it tiled to the shared
    /// cycle and joined mid-cycle. Loop-lock is OFF here, so this also
    /// pins the every-looping-trigger join (padengine.js:1269-1273):
    /// unquantized means boundary = now, still phase-joined.
    func testLateJoinerSharesCycleAndPhase() async throws {
        try requireRunningEngine()
        let stemURL = tmpDir.appendingPathComponent("cycle-stem.caf")
        try writeTone(to: stemURL, durationSec: 4.0)
        let padA = SamplePad(
            padIdx: 0, name: "A", family: .pads,
            stemSlice: StemSlice(stemRole: "other", startSec: 0, endSec: 2.0),
            loopStartSec: 0, loopEndSec: 2.0, loopable: true)
        let padB = SamplePad(
            padIdx: 1, name: "B", family: .pads,
            stemSlice: StemSlice(stemRole: "other", startSec: 0, endSec: 1.0),
            loopStartSec: 0, loopEndSec: 1.0, loopable: true)
        let pack = SamplePack(
            packId: "cycle", name: "cycle", family: .pads, pads: [padA, padB])
        try scheduler.setActivePack(
            ResolvedSamplePack(pack: pack, padFileURLs: [:]),
            stemFiles: ["other": stemURL])
        scheduler.updateSyntheticContext(tempoBpm: 120)
        scheduler.loopLock = false // unquantized: boundary = now, still joins

        _ = scheduler.trigger(padIdx: 0, packId: "cycle")
        try await Task.sleep(nanoseconds: 300_000_000)
        _ = scheduler.trigger(padIdx: 1, packId: "cycle")
        try await Task.sleep(nanoseconds: 100_000_000)

        guard
            let a = pool.loopPhase(padKey: SamplePadKey(packId: "cycle", padIdx: 0)),
            let b = pool.loopPhase(padKey: SamplePadKey(packId: "cycle", padIdx: 1))
        else { return XCTFail("both loops must be ringing") }
        XCTAssertEqual(
            a, b, accuracy: 0.05,
            "a late joiner must sit at the same cycle position as the pad "
            + "already ringing — same lattice, same shared 2 s cycle")
    }

    // MARK: - 8. Sample-accurate armed starts stay cancellable

    func testReleaseCancelsArmedQuantizedStart() async throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "armed", pads: [(0, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        load120BpmBundle()
        scheduler.loopLock = true
        engine.clock.play()
        engine.clock.seek(to: 0.5) // mid-bar: next downbeat at 2.0

        let key = SamplePadKey(packId: "armed", padIdx: 0)
        _ = scheduler.trigger(padIdx: 0, packId: "armed")
        XCTAssertTrue(pool.pendingPadKeys.contains(key), "quantized launch arms")

        scheduler.release(padIdx: 0, packId: "armed")
        XCTAssertFalse(pool.isActive(padKey: key))
        XCTAssertFalse(pool.pendingPadKeys.contains(key))

        // Past the boundary the cancelled voice must NOT sound — the
        // release fast path's player.stop() discards the scheduled
        // sample-accurate play(at:) start.
        try await Task.sleep(nanoseconds: 1_800_000_000)
        XCTAssertFalse(
            pool.ringingPadKeys.contains(key),
            "a voice released while armed must never fire at its boundary")
    }

    // MARK: - 9. Latch toggle-off releases immediately

    func testToggleSecondTapReleasesImmediately() throws {
        try requireRunningEngine()
        let pack = try makePack(packId: "latch", pads: [(0, true)])
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.holdMode = .toggle
        let key = SamplePadKey(packId: "latch", padIdx: 0)

        _ = scheduler.trigger(padIdx: 0, packId: "latch")
        XCTAssertTrue(pool.isActive(padKey: key))
        XCTAssertEqual(scheduler.trigger(padIdx: 0, packId: "latch"), .toggledOff)
        XCTAssertFalse(
            pool.isActive(padKey: key),
            "kit.js latch parity: the second tap releases NOW (20 ms fade), "
            + "not at the end of the loop pass — the old musical stop kept a "
            + "4-bar loop ringing ~7 s after the user stopped it")
    }
}

// MARK: - Jam Samples first-launch anchoring (ModeCoordinator, FIX 1)

/// The coordinator-level half of contract 6: the FIRST latched clip must
/// route through the full trigger path BEFORE the session clock rolls, so
/// it fires now AND anchors the lattice. No audio boot needed — the
/// anchor is scheduler state, set before the (unattached) pool is asked
/// to play anything.
@MainActor
final class JamFirstLaunchAnchorTests: XCTestCase {

    private var app: AppState!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("jam-anchor-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState()
    }

    override func tearDown() async throws {
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        try await super.tearDown()
    }

    /// Chop-style pack: no loopPointSec — a latched clip loops only via
    /// the coordinator's loopOverride, exactly like song chops.
    private func makeChopPack(packId: String) throws -> ResolvedSamplePack {
        let sr = 44_100.0
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(0.25 * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData {
            for c in 0..<2 {
                for i in 0..<Int(frames) {
                    ch[c][i] = Float(sin(2 * .pi * 440 * Double(i) / sr) * 0.3)
                }
            }
        }
        let url = tmpDir.appendingPathComponent("\(packId)-0.caf")
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buf)
        let pad = SamplePad(
            padIdx: 0, name: "chop", family: .pads,
            filename: "\(packId)-0.caf"
        )
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: [pad]
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: [0: url])
    }

    func testFirstLatchedClipAnchorsLatticeBeforeClockRolls() throws {
        let pack = try makeChopPack(packId: "jam")
        try app.sampleScheduler.setActivePack(pack, stemFiles: [:])
        XCTAssertNil(app.sampleScheduler.loopLockAnchorHostSec)
        XCTAssertNotEqual(app.audioEngine.clock.state, .playing)

        app.modeCoordinator.triggerJamSample(padIdx: 0, packId: "jam", mode: .latch)

        XCTAssertNotNil(
            app.sampleScheduler.loopLockAnchorHostSec,
            "clip 1 must anchor the lattice (padengine.js:1166) — the old "
            + "triggerRaw bypass never did, so clip 2's boundary defined the "
            + "grid and clip 1 stayed permanently out of unison")
        XCTAssertEqual(
            app.audioEngine.clock.state, .playing,
            "the latch launch still rolls the session clock — after the "
            + "trigger, so the free-run branch (fire now + anchor) handles "
            + "beat 1 instead of a song-bar wait")
    }
}
