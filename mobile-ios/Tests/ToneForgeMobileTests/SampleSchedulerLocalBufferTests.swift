// SampleSchedulerLocalBufferTests.swift
//
// The P3 local-sample path in SampleScheduler: mic recordings
// assigned to grid pads via `setLocalBuffer`, consulted BEFORE pack
// lookup so a local assignment shadows the active pack's pad at the
// same index. Local pads are one-shots (fire immediately, ignore
// quantize, no touch-up release) keyed under the synthetic "local"
// packId.
//
// Fixture strategy mirrors SampleSchedulerMultiPackTests: runtime
// tone files + in-memory packs; voice-level assertions skip on
// audio-less hosts.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class SampleSchedulerLocalBufferTests: XCTestCase {

    private var engine: AudioEngine!
    private var bus: SampleBus!
    private var pool: SampleVoicePool!
    private var scheduler: SampleScheduler!
    private var tmpDir: URL!

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("local-buffer-\(UUID().uuidString)")
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

    /// Mono 48 kHz Float32 tone buffer — the exact shape the mic
    /// pipeline delivers (MicRecorder → PadSampleStore.loadBuffer).
    /// NOT the canonical stereo connection format, so a passing
    /// trigger also proves `setLocalBuffer`'s conversion (a format
    /// mismatch would NSException inside scheduleBuffer).
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

    /// Single-pack fixture with a looping pad at `padIdx` (loop so
    /// the pack voice stays active — lets tests tell local voices
    /// apart from pack voices).
    private func makePack(
        packId: String = "packA", padIdx: Int = 0
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
            loopPointSec: 0
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

    // MARK: - Assignment bookkeeping

    func testLocalMetadataRoundTrip() {
        let meta = makeMeta()
        scheduler.setLocalBuffer(micBuffer(), meta: meta, for: 11)
        XCTAssertEqual(scheduler.localMetadata(for: 11), meta)
        XCTAssertNil(scheduler.localMetadata(for: 12))

        scheduler.clearLocalBuffer(for: 11)
        XCTAssertNil(scheduler.localMetadata(for: 11))
    }

    func testClearAllLocalBuffersDropsEverything() {
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 88)
        scheduler.clearAllLocalBuffers()
        XCTAssertNil(scheduler.localMetadata(for: 11))
        XCTAssertNil(scheduler.localMetadata(for: 88))
    }

    // MARK: - Triggering

    func testTriggerFiresLocalVoiceWithMonoMicFormat() throws {
        try requireRunningEngine()
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        let result = scheduler.trigger(padIdx: 11)
        guard case .scheduled = result else {
            return XCTFail("expected .scheduled, got \(result)")
        }
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testLocalAssignmentShadowsPackPadAtSameIndex() throws {
        try requireRunningEngine()
        let pack = try makePack(padIdx: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 0)

        _ = scheduler.trigger(padIdx: 0)
        XCTAssertTrue(pool.isActive(padKey: localKey(0)))
        XCTAssertFalse(pool.isActive(padKey: SamplePadKey(packId: "packA", padIdx: 0)))
    }

    func testExplicitPackIdBypassesLocalShadow() throws {
        try requireRunningEngine()
        let pack = try makePack(padIdx: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 0)

        // The UI names the carousel page's pack explicitly — that
        // must still reach the pack pad, not the local shadow.
        _ = scheduler.trigger(padIdx: 0, packId: "packA")
        XCTAssertTrue(pool.isActive(padKey: SamplePadKey(packId: "packA", padIdx: 0)))
        XCTAssertFalse(pool.isActive(padKey: localKey(0)))
    }

    func testClearRestoresPackPath() throws {
        try requireRunningEngine()
        let pack = try makePack(padIdx: 0)
        try scheduler.setActivePack(pack, stemFiles: [:])
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 0)
        scheduler.clearLocalBuffer(for: 0)

        _ = scheduler.trigger(padIdx: 0)
        XCTAssertTrue(pool.isActive(padKey: SamplePadKey(packId: "packA", padIdx: 0)))
        XCTAssertFalse(pool.isActive(padKey: localKey(0)))
    }

    func testToggleModeSecondTapReturnsToggledOff() throws {
        try requireRunningEngine()
        scheduler.holdMode = .toggle
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        guard case .scheduled = scheduler.trigger(padIdx: 11) else {
            return XCTFail("expected .scheduled")
        }
        XCTAssertEqual(scheduler.trigger(padIdx: 11), .toggledOff)
        XCTAssertFalse(pool.isActive(padKey: localKey(11)))
    }

    func testReleaseIsNoOpForLocalOneShots() throws {
        try requireRunningEngine()
        scheduler.holdMode = .hold
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        _ = scheduler.trigger(padIdx: 11)
        scheduler.release(padIdx: 11)
        // One-shot plays to completion — the release must not have
        // stopped the voice.
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testTriggerRawFiresLocalVoice() throws {
        try requireRunningEngine()
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        let result = scheduler.triggerRaw(padIdx: 11)
        guard case .scheduled = result else {
            return XCTFail("expected .scheduled, got \(result)")
        }
        XCTAssertTrue(pool.isActive(padKey: localKey(11)))
    }

    func testEventsCarryLocalPackIdOverride() throws {
        try requireRunningEngine()
        scheduler.holdMode = .toggle
        var events: [LayerEvent] = []
        scheduler.onEvent = { events.append($0) }
        scheduler.setLocalBuffer(micBuffer(), meta: makeMeta(), for: 11)

        _ = scheduler.trigger(padIdx: 11)   // on
        _ = scheduler.trigger(padIdx: 11)   // toggle-off

        XCTAssertEqual(events.map(\.kind), [.sampleOn, .sampleOff])
        XCTAssertEqual(events[0].params.packIdOverride, SampleScheduler.localPackId)
        XCTAssertEqual(events[1].params.packIdOverride, SampleScheduler.localPackId)
    }
}
