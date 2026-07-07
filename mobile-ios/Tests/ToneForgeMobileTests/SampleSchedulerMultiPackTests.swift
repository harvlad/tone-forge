// SampleSchedulerMultiPackTests.swift
//
// Multi-pack behavior of `SampleScheduler` + choke-group scoping in
// `SampleVoicePool` — the audio-side contract behind the pack
// carousel:
//
//   - Sequential setActivePack keeps BOTH packs resident.
//   - Voices keep ringing across a pack swap (no stopAll).
//   - trigger/release honour an explicit packId (non-active packs
//     stay playable; unknown packs → .padNotFound).
//   - Toggle-off works across a swap via the explicit packId.
//   - sampleOn/sampleOff events carry `packIdOverride`.
//   - unloadPack drops exactly one pack (+ its ringing voices).
//   - A choke-group Int in pack A must not choke the same Int in
//     pack B.
//
// Fixture strategy: tiny sine .caf files written at runtime (same
// hermetic approach as LayerOfflineRendererTests) + in-memory
// ResolvedSamplePacks pointing at them. Tests that need audible
// voices start a real AVAudioEngine; they skip (not fail) on hosts
// with no default audio device.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class SampleSchedulerMultiPackTests: XCTestCase {

    private var engine: AudioEngine!
    private var bus: SampleBus!
    private var pool: SampleVoicePool!
    private var scheduler: SampleScheduler!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("multi-pack-\(UUID().uuidString)")
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

    /// Write a short stereo sine to a .caf (AVAudioFile accepts the
    /// standard float non-interleaved format for CAF without fuss).
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

    /// In-memory ResolvedSamplePack whose pads point at runtime tone
    /// files. `loop: true` gives the pad a loopPointSec so its voice
    /// stays active until released (what the ringing tests need).
    private func makePack(
        packId: String,
        pads: [(idx: Int, chokeGroup: Int?, loop: Bool)]
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
                chokeGroup: p.chokeGroup,
                loopPointSec: p.loop ? 0 : nil
            ))
            urls[p.idx] = url
        }
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: padObjs
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    /// Voice-level assertions need a running engine (player.play()
    /// requires it). Skip rather than crash on audio-less hosts.
    private func requireRunningEngine() throws {
        try XCTSkipUnless(
            engine.engine.isRunning,
            "AVAudioEngine failed to start on this host — skipping voice-level test"
        )
    }

    private func key(_ packId: String, _ padIdx: Int) -> SamplePadKey {
        SamplePadKey(packId: packId, padIdx: padIdx)
    }

    // MARK: - Registry

    func testSequentialActivationKeepsBothPacksLoaded() throws {
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])
        try scheduler.setActivePack(b, stemFiles: [:])

        XCTAssertTrue(scheduler.isPackLoaded(packId: "packA"))
        XCTAssertTrue(scheduler.isPackLoaded(packId: "packB"))
        XCTAssertEqual(scheduler.activePackId, "packB")
    }

    func testUnloadAllPacksClearsRegistry() throws {
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])
        scheduler.unloadAllPacks()
        XCTAssertFalse(scheduler.isPackLoaded(packId: "packA"))
        XCTAssertNil(scheduler.activePackId)
    }

    // MARK: - Keep-ringing

    func testVoiceKeepsRingingAcrossPackSwap() throws {
        try requireRunningEngine()
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        scheduler.holdMode = .toggle

        try scheduler.setActivePack(a, stemFiles: [:])
        let result = scheduler.trigger(padIdx: 0)
        guard case .scheduled = result else {
            return XCTFail("expected .scheduled, got \(result)")
        }
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))

        // The moment of truth: swapping packs must NOT stop the voice.
        try scheduler.setActivePack(b, stemFiles: [:])
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))
    }

    // MARK: - Pack-aware trigger / release

    func testExplicitPackIdTriggersNonActivePack() throws {
        try requireRunningEngine()
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])
        try scheduler.setActivePack(b, stemFiles: [:])

        // packB is active, but an explicit packId reaches packA.
        let result = scheduler.trigger(padIdx: 0, packId: "packA")
        guard case .scheduled = result else {
            return XCTFail("expected .scheduled, got \(result)")
        }
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))
        XCTAssertFalse(pool.isActive(padKey: key("packB", 0)))
    }

    func testUnknownPackOrPadReturnsPadNotFound() throws {
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])

        XCTAssertEqual(scheduler.trigger(padIdx: 0, packId: "ghost"), .padNotFound)
        XCTAssertEqual(scheduler.trigger(padIdx: 9, packId: "packA"), .padNotFound)
        XCTAssertEqual(scheduler.triggerRaw(padIdx: 0, packId: "ghost"), .padNotFound)
    }

    func testToggleOffAcrossSwapViaExplicitPackId() throws {
        try requireRunningEngine()
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        scheduler.holdMode = .toggle

        try scheduler.setActivePack(a, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0)                       // toggle-on in A
        try scheduler.setActivePack(b, stemFiles: [:])          // swipe away
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))

        // Swipe back + tap the same pad: UI passes the page's packId.
        let result = scheduler.trigger(padIdx: 0, packId: "packA")
        XCTAssertEqual(result, .toggledOff)
        XCTAssertFalse(pool.isActive(padKey: key("packA", 0)))
    }

    // MARK: - Event packIdOverride

    func testSampleOnAndOffEventsCarryPackIdOverride() throws {
        try requireRunningEngine()
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        scheduler.holdMode = .toggle
        var events: [LayerEvent] = []
        scheduler.onEvent = { events.append($0) }

        try scheduler.setActivePack(a, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0)                        // on (A)
        try scheduler.setActivePack(b, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0, packId: "packA")       // off (A)

        XCTAssertEqual(events.map(\.kind), [.sampleOn, .sampleOff])
        XCTAssertEqual(events[0].params.packIdOverride, "packA")
        XCTAssertEqual(events[1].params.packIdOverride, "packA")
    }

    // MARK: - unloadPack

    func testUnloadPackDropsOnlyThatPackAndItsVoices() throws {
        try requireRunningEngine()
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        scheduler.holdMode = .toggle
        try scheduler.setActivePack(a, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0)                        // ringing in A
        try scheduler.setActivePack(b, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0)                        // ringing in B

        scheduler.unloadPack(packId: "packA")

        XCTAssertFalse(scheduler.isPackLoaded(packId: "packA"))
        XCTAssertTrue(scheduler.isPackLoaded(packId: "packB"))
        XCTAssertFalse(pool.isActive(padKey: key("packA", 0)))
        XCTAssertTrue(pool.isActive(padKey: key("packB", 0)))
        XCTAssertEqual(scheduler.trigger(padIdx: 0, packId: "packA"), .padNotFound)
        // packB stays active — unload of a non-active pack must not
        // clear activePackId.
        XCTAssertEqual(scheduler.activePackId, "packB")
    }

    func testUnloadActivePackClearsActivePackId() throws {
        let a = try makePack(packId: "packA", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])
        scheduler.unloadPack(packId: "packA")
        XCTAssertNil(scheduler.activePackId)
    }

    // MARK: - Ringing-pad publication (grid indicator + stop-all)

    func testRingingPadKeysTracksLoopLifecycle() throws {
        try requireRunningEngine()
        // Pad 0 loops; pad 1 has no loop point.
        let a = try makePack(packId: "packA", pads: [(0, nil, true), (1, nil, false)])
        let b = try makePack(packId: "packB", pads: [(0, nil, true)])
        try scheduler.setActivePack(a, stemFiles: [:])

        XCTAssertTrue(pool.ringingPadKeys.isEmpty)

        // Hold-mode one-shots never enter the ringing set — their
        // slots stay isActive after the buffer ends (no completion
        // handler), so they'd read as ringing forever. (In toggle
        // mode with quantize off even no-loop-point pads loop by
        // design — see SampleScheduler's "short loops" rule — and
        // those SHOULD show as ringing.)
        scheduler.holdMode = .hold
        _ = scheduler.trigger(padIdx: 1)
        XCTAssertTrue(pool.ringingPadKeys.isEmpty)
        scheduler.release(padIdx: 1)

        // Loop on in A, then another in B after a swap: both ring.
        scheduler.holdMode = .toggle
        _ = scheduler.trigger(padIdx: 0)
        XCTAssertEqual(pool.ringingPadKeys, [key("packA", 0)])
        try scheduler.setActivePack(b, stemFiles: [:])
        _ = scheduler.trigger(padIdx: 0)
        XCTAssertEqual(
            pool.ringingPadKeys,
            [key("packA", 0), key("packB", 0)]
        )

        // Toggle-off drops exactly that key.
        _ = scheduler.trigger(padIdx: 0, packId: "packA")
        XCTAssertEqual(pool.ringingPadKeys, [key("packB", 0)])

        // stopAll (the panic button) empties the set.
        pool.stopAll()
        XCTAssertTrue(pool.ringingPadKeys.isEmpty)
    }

    // MARK: - Choke-group scoping

    func testChokeGroupDoesNotCrossPacks() throws {
        try requireRunningEngine()
        // Same choke-group Int (1) in both packs.
        let a = try makePack(packId: "packA", pads: [(0, 1, true), (1, 1, true)])
        let b = try makePack(packId: "packB", pads: [(0, 1, true)])
        try scheduler.setActivePack(a, stemFiles: [:])
        try scheduler.setActivePack(b, stemFiles: [:])

        _ = scheduler.trigger(padIdx: 0, packId: "packA")
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))

        // Group 1 in packB must NOT choke group 1 in packA…
        _ = scheduler.trigger(padIdx: 0, packId: "packB")
        XCTAssertTrue(pool.isActive(padKey: key("packA", 0)))
        XCTAssertTrue(pool.isActive(padKey: key("packB", 0)))

        // …but group 1 within packA still chokes its own sibling.
        _ = scheduler.trigger(padIdx: 1, packId: "packA")
        XCTAssertFalse(pool.isActive(padKey: key("packA", 0)))
        XCTAssertTrue(pool.isActive(padKey: key("packA", 1)))
        XCTAssertTrue(pool.isActive(padKey: key("packB", 0)))
    }
}
