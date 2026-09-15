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
        pads: [(idx: Int, loop: Bool)]
    ) throws -> ResolvedSamplePack {
        var padObjs: [SamplePad] = []
        var urls: [Int: URL] = [:]
        for p in pads {
            let url = tmpDir.appendingPathComponent("\(packId)-\(p.idx).caf")
            try writeTone(to: url)
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
}
