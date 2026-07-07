// SampleSchedulerTransformTests.swift
//
// The P4 seams in SampleScheduler: `transformResolver` (rendered
// transform buffers swap in at trigger time), `loopResolver` (a
// `.loop` transform makes the voice loop and gives the pad
// hold-to-sustain release semantics), and `baseBuffer` (the
// untransformed source PadTransformHost renders from and bake reads).
//
// Fixture strategy mirrors SampleSchedulerLocalBufferTests: runtime
// tone files + in-memory packs; voice-level assertions skip on
// audio-less hosts.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class SampleSchedulerTransformTests: XCTestCase {

    private var engine: AudioEngine!
    private var bus: SampleBus!
    private var pool: SampleVoicePool!
    private var scheduler: SampleScheduler!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("transform-seams-\(UUID().uuidString)")
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

    /// Mono 48 kHz mic-shaped tone (setLocalBuffer converts to the
    /// canonical connection format on ingest).
    private func micBuffer(seconds: Double = 0.25) -> AVAudioPCMBuffer {
        let sr = 48_000.0
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let frames = AVAudioFrameCount(seconds * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        for i in 0..<Int(frames) {
            buf.floatChannelData![0][i] =
                Float(sin(2 * .pi * 440 * Double(i) / sr) * 0.5)
        }
        return buf
    }

    private func makeMeta(id: UUID = UUID()) -> PadSampleMetadata {
        PadSampleMetadata(
            id: id,
            source: .mic,
            classification: .percussion,
            confidence: 0.8,
            durationSec: 0.25,
            sampleRate: 48_000,
            channels: 1,
            colorHint: 0xFF8C3A
        )
    }

    /// Single-pack fixture. `loopPointSec: nil` makes the pad a
    /// one-shot so tests can prove the loopResolver alone flips the
    /// loop/release semantics.
    private func makePack(
        packId: String = "packA", padIdx: Int = 0,
        loopPointSec: Double? = nil
    ) throws -> ResolvedSamplePack {
        let sr = 44_100.0
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(0.25 * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        for c in 0..<2 {
            for i in 0..<Int(frames) {
                buf.floatChannelData![c][i] =
                    Float(sin(2 * .pi * 330 * Double(i) / sr) * 0.3)
            }
        }
        let url = tmpDir.appendingPathComponent("\(packId)-\(padIdx).caf")
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buf)

        let pad = SamplePad(
            padIdx: padIdx,
            name: "P\(padIdx)",
            family: .pads,
            filename: "\(packId)-\(padIdx).caf",
            chokeGroup: nil,
            loopPointSec: loopPointSec
        )
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: [pad]
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: [padIdx: url])
    }

    private func requireRunningEngine() throws {
        try XCTSkipUnless(
            engine.engine.isRunning,
            "AVAudioEngine failed to start on this host — skipping voice-level test"
        )
    }

    private func localKey(_ padIdx: Int) -> SamplePadKey {
        SamplePadKey(packId: SampleScheduler.localPackId, padIdx: padIdx)
    }

    // MARK: - baseBuffer accessor

    func testBaseBufferReturnsLocalBufferIdentity() {
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)
        let base = scheduler.baseBuffer(
            packId: SampleScheduler.localPackId, padIdx: 11
        )
        XCTAssertNotNil(base)
        XCTAssertGreaterThan(base?.frameLength ?? 0, 0)
        // Canonical connection format after ingest conversion.
        XCTAssertEqual(base?.format.sampleRate, engine.canonicalFormat.sampleRate)
        XCTAssertNil(scheduler.baseBuffer(
            packId: SampleScheduler.localPackId, padIdx: 12
        ))
    }

    func testBaseBufferReturnsPackBuffer() throws {
        let pack = try makePack(padIdx: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])
        XCTAssertNotNil(scheduler.baseBuffer(packId: "packA", padIdx: 0))
        XCTAssertNil(scheduler.baseBuffer(packId: "packA", padIdx: 1))
        XCTAssertNil(scheduler.baseBuffer(packId: "nope", padIdx: 0))
    }

    // MARK: - transformResolver

    func testTransformResolverReceivesLocalKeyAndBaseBuffer() throws {
        try requireRunningEngine()
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)
        let base = scheduler.baseBuffer(
            packId: SampleScheduler.localPackId, padIdx: 11
        )

        var calls: [(packId: String, padIdx: Int)] = []
        var receivedBase: AVAudioPCMBuffer?
        scheduler.transformResolver = { buffer, packId, padIdx in
            calls.append((packId, padIdx))
            receivedBase = buffer
            return buffer
        }

        _ = scheduler.trigger(padIdx: 11)
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.packId, SampleScheduler.localPackId)
        XCTAssertEqual(calls.first?.padIdx, 11)
        // Resolver must see the UNtransformed base so chains never
        // stack on a previous render.
        XCTAssertTrue(receivedBase === base)
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testTransformResolverReceivesPackKey() throws {
        try requireRunningEngine()
        let pack = try makePack(padIdx: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])

        var calls: [(packId: String, padIdx: Int)] = []
        scheduler.transformResolver = { buffer, packId, padIdx in
            calls.append((packId, padIdx))
            return buffer
        }

        _ = scheduler.trigger(padIdx: 0)
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls.first?.packId, "packA")
        XCTAssertEqual(calls.first?.padIdx, 0)
    }

    func testTransformResolverReplacementBufferSchedules() throws {
        try requireRunningEngine()
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        // Swap in a distinct canonical-format buffer (what
        // PadTransformHost hands back for a rendered chain) — the
        // voice must still schedule cleanly.
        let format = engine.canonicalFormat
        let frames = AVAudioFrameCount(0.1 * format.sampleRate)
        let replacement = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: frames
        )!
        replacement.frameLength = frames

        scheduler.transformResolver = { _, _, _ in replacement }

        let result = scheduler.trigger(padIdx: 11)
        guard case .scheduled = result else {
            return XCTFail("expected .scheduled, got \(result)")
        }
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testTriggerRawConsultsTransformResolver() throws {
        try requireRunningEngine()
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        var calls = 0
        scheduler.transformResolver = { buffer, packId, padIdx in
            XCTAssertEqual(packId, SampleScheduler.localPackId)
            XCTAssertEqual(padIdx, 11)
            calls += 1
            return buffer
        }

        _ = scheduler.triggerRaw(padIdx: 11)
        XCTAssertEqual(calls, 1)
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    // MARK: - loopResolver: local pads

    func testLoopingLocalPadReleasesOnTouchUpInHoldMode() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)
        scheduler.loopResolver = { packId, padIdx in
            packId == SampleScheduler.localPackId && padIdx == 11
        }
        var events: [LayerEvent] = []
        scheduler.onEvent = { events.append($0) }

        _ = scheduler.trigger(padIdx: 11)
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))

        scheduler.release(padIdx: 11)
        XCTAssertFalse(pool.isActive(padKey: localKey(11)))
        XCTAssertEqual(events.map(\.kind), [.sampleOn, .sampleOff])
        XCTAssertEqual(
            events[1].params.packIdOverride, SampleScheduler.localPackId
        )
    }

    func testNonLoopingLocalPadIgnoresReleaseEvenWithResolverSet() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)
        // Resolver present but says "no loop" — one-shot convention
        // must hold ("you can't un-hit a drum").
        scheduler.loopResolver = { _, _ in false }

        _ = scheduler.trigger(padIdx: 11)
        scheduler.release(padIdx: 11)
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testLoopingLocalReleaseDoesNotTouchShadowedPackPad() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        let pack = try makePack(padIdx: 0, loopPointSec: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])
        // Ring the pack pad via the explicit-packId path first.
        _ = scheduler.trigger(padIdx: 0, packId: "packA")
        let packKey = SamplePadKey(packId: "packA", padIdx: 0)
        XCTAssertTrue(pool.isActive(padKey: packKey))

        // Now shadow the index with a looping local pad and release.
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 0)
        scheduler.loopResolver = { packId, _ in
            packId == SampleScheduler.localPackId
        }
        _ = scheduler.trigger(padIdx: 0)
        scheduler.release(padIdx: 0)

        // Local voice released; the pack voice keeps ringing.
        XCTAssertFalse(pool.isActive(padKey: localKey(0)))
        XCTAssertTrue(pool.isActive(padKey: packKey))
    }

    // MARK: - loopResolver: pack pads

    func testLoopResolverMakesOneShotPackPadReleasable() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        // One-shot pad (no loopPointSec) — only the resolver says
        // loop, exactly what a `.loop` transform on a pack pad does.
        let pack = try makePack(padIdx: 0, loopPointSec: nil)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.loopResolver = { packId, padIdx in
            packId == "packA" && padIdx == 0
        }
        var events: [LayerEvent] = []
        scheduler.onEvent = { events.append($0) }

        _ = scheduler.trigger(padIdx: 0)
        let packKey = SamplePadKey(packId: "packA", padIdx: 0)
        XCTAssertTrue(pool.isActive(padKey: packKey))

        scheduler.release(padIdx: 0)
        XCTAssertFalse(pool.isActive(padKey: packKey))
        XCTAssertEqual(events.map(\.kind), [.sampleOn, .sampleOff])
        XCTAssertEqual(events[1].params.packIdOverride, "packA")
    }

    func testOneShotPackPadStillIgnoresReleaseWithoutLoopTransform() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        let pack = try makePack(padIdx: 0, loopPointSec: nil)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.loopResolver = { _, _ in false }

        _ = scheduler.trigger(padIdx: 0)
        let packKey = SamplePadKey(packId: "packA", padIdx: 0)
        scheduler.release(padIdx: 0)
        XCTAssertTrue(pool.isActive(padKey: packKey))
    }
}
