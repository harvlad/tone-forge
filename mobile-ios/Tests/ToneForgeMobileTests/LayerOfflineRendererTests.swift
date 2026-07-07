// LayerOfflineRendererTests.swift
//
// Coverage for `LayerOfflineRenderer`:
//   - No sampleOn events → `.noRenderableEvents`.
//   - Rendering with synthesized buffers writes an .m4a whose actual
//     duration on disk covers timeline.durationSec + tail, and whose
//     sample-rate matches the renderer target.
//   - noteOn/noteOff + sampleOff counts are reported as skipped
//     without failing the render.
//   - Two consecutive renders don't leak engine state (a fresh
//     renderer instance is spun up per call inside the class).
//
// Tests build tiny `AVAudioPCMBuffer` fixtures at 44.1 kHz stereo
// instead of loading pack files on disk — that keeps the tests hermetic
// under SwiftPM/xcodebuild with no bundle resources.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

final class LayerOfflineRendererTests: XCTestCase {

    private var tmpDir: URL!

    override func setUp() {
        super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("offline-render-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
    }

    override func tearDown() {
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        tmpDir = nil
        super.tearDown()
    }

    // MARK: - Fixtures

    /// A short sine-tone buffer at the renderer's target format. Used
    /// so the tests can inspect the resulting m4a's basic properties
    /// without shipping .caf assets.
    private func sineBuffer(
        durationSec: Double,
        frequencyHz: Double = 440
    ) -> AVAudioPCMBuffer {
        let sr = LayerOfflineRenderer.sampleRate
        let format = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(durationSec * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        guard let ch0 = buf.floatChannelData?[0],
              let ch1 = buf.floatChannelData?[1] else {
            return buf
        }
        let twoPi = 2.0 * Double.pi
        for i in 0..<Int(frames) {
            let sample = Float(sin(twoPi * frequencyHz * Double(i) / sr) * 0.4)
            ch0[i] = sample
            ch1[i] = sample
        }
        return buf
    }

    private func makeTimeline(events: [LayerEvent], durationSec: Double = 1.0)
        -> LayerTimeline
    {
        LayerTimeline(
            layerId: "L1",
            analysisId: "song-abc",
            name: "Test",
            createdAtEpoch: 0,
            durationSec: durationSec,
            events: events,
            activePackId: "starter"
        )
    }

    private func sampleOn(
        _ padIdx: Int, at t: Double, packId: String? = nil
    ) -> LayerEvent {
        LayerEvent(
            kind: .sampleOn,
            songTimeSec: t,
            params: LayerEvent.Params(
                padIdx: padIdx, velocity: 1.0, packIdOverride: packId
            )
        )
    }

    // MARK: - Tests

    func testNoRenderableEventsThrows() {
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("empty.m4a")
        let timeline = makeTimeline(events: [], durationSec: 1.0)
        XCTAssertThrowsError(try renderer.render(
            timeline: timeline,
            pads: [:],
            outputURL: out,
            tailSec: 0.1
        )) { err in
            switch err {
            case LayerOfflineRenderer.RenderError.noRenderableEvents: break
            default: XCTFail("wrong error: \(err)")
            }
        }
    }

    func testRendersM4AFileWithExpectedDurationAndFormat() throws {
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("simple.m4a")
        let pads: [Int: LayerOfflineRenderer.RenderablePad] = [
            0: .init(buffer: sineBuffer(durationSec: 0.15))
        ]
        let timeline = makeTimeline(
            events: [sampleOn(0, at: 0.0), sampleOn(0, at: 0.5)],
            durationSec: 1.0
        )
        let result = try renderer.render(
            timeline: timeline,
            pads: pads,
            outputURL: out,
            tailSec: 0.2
        )
        XCTAssertEqual(result.renderedSampleEvents, 2)
        XCTAssertEqual(result.skippedNoteEvents, 0)
        XCTAssertEqual(result.skippedSampleOffEvents, 0)

        // The output file should exist and be readable as an audio file.
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
        let file = try AVAudioFile(forReading: out)
        // AAC file is stored at 44.1 kHz stereo per renderer settings.
        XCTAssertEqual(file.fileFormat.sampleRate, 44_100, accuracy: 1)
        XCTAssertEqual(file.fileFormat.channelCount, 2)
        // Duration ≥ timeline duration + tail. AAC frame padding adds a
        // small delta so we assert with slack.
        let onDiskSec = Double(file.length) / file.fileFormat.sampleRate
        XCTAssertGreaterThanOrEqual(onDiskSec, 1.15, "on-disk sec: \(onDiskSec)")
        // And not absurdly larger than expected — one AAC block (~1024
        // frames) of overhead is plenty.
        XCTAssertLessThan(onDiskSec, 1.3, "on-disk sec: \(onDiskSec)")
    }

    func testReportsSkippedNoteAndOffEvents() throws {
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("mixed.m4a")
        let pads: [Int: LayerOfflineRenderer.RenderablePad] = [
            0: .init(buffer: sineBuffer(durationSec: 0.1))
        ]
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                LayerEvent(
                    kind: .sampleOff, songTimeSec: 0.1,
                    params: LayerEvent.Params(padIdx: 0)
                ),
                LayerEvent(
                    kind: .noteOn, songTimeSec: 0.2,
                    params: LayerEvent.Params(midiNote: 60, velocity: 0.8)
                ),
                LayerEvent(
                    kind: .noteOff, songTimeSec: 0.4,
                    params: LayerEvent.Params(midiNote: 60)
                ),
            ],
            durationSec: 0.5
        )
        let result = try renderer.render(
            timeline: timeline,
            pads: pads,
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertEqual(result.skippedSampleOffEvents, 1)
        XCTAssertEqual(result.skippedNoteEvents, 2)
    }

    func testMissingPadIsSkippedNotFailed() throws {
        // sampleOn events referencing padIdx not in the pads map are
        // treated as no-ops (defensive against stale timelines pointing
        // at pads that no longer exist).
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("missing.m4a")
        let pads: [Int: LayerOfflineRenderer.RenderablePad] = [
            0: .init(buffer: sineBuffer(durationSec: 0.1))
        ]
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                sampleOn(9, at: 0.1),  // no pad 9 in map
            ],
            durationSec: 0.5
        )
        let result = try renderer.render(
            timeline: timeline,
            pads: pads,
            outputURL: out,
            tailSec: 0.1
        )
        // Only the pad-0 event actually renders, but the render itself
        // succeeds without throwing on the missing pad.
        XCTAssertEqual(result.renderedSampleEvents, 1)
    }

    func testTwoConsecutiveRendersDoNotLeakState() throws {
        // Same renderer instance handling back-to-back calls must
        // produce two independently-valid files.
        let renderer = LayerOfflineRenderer()
        let pads: [Int: LayerOfflineRenderer.RenderablePad] = [
            0: .init(buffer: sineBuffer(durationSec: 0.1))
        ]
        let timeline = makeTimeline(
            events: [sampleOn(0, at: 0.0)],
            durationSec: 0.3
        )
        let out1 = tmpDir.appendingPathComponent("a.m4a")
        let out2 = tmpDir.appendingPathComponent("b.m4a")
        let r1 = try renderer.render(
            timeline: timeline, pads: pads, outputURL: out1, tailSec: 0.1
        )
        let r2 = try renderer.render(
            timeline: timeline, pads: pads, outputURL: out2, tailSec: 0.1
        )
        XCTAssertEqual(r1.renderedSampleEvents, 1)
        XCTAssertEqual(r2.renderedSampleEvents, 1)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out1.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: out2.path))
    }

    // MARK: - Multi-pack

    func testMultiPackTimelineRendersEventsFromEveryPack() throws {
        // One hit on the base pack (no override → activePackId
        // "starter") and one on a swapped-in pack — both must render.
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("multipack.m4a")
        let padsByPack: [String: [Int: LayerOfflineRenderer.RenderablePad]] = [
            "starter": [0: .init(buffer: sineBuffer(durationSec: 0.1))],
            "beats": [0: .init(buffer: sineBuffer(durationSec: 0.1,
                                                  frequencyHz: 880))],
        ]
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                sampleOn(0, at: 0.3, packId: "beats"),
            ],
            durationSec: 0.6
        )
        let result = try renderer.render(
            timeline: timeline,
            padsByPack: padsByPack,
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 2)
        XCTAssertEqual(result.unresolvedSampleEvents, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
    }

    func testEventFromUnavailablePackIsSkippedNotBorrowed() throws {
        // The "gone" pack's pad 0 also exists in "starter" — the event
        // must NOT fall back to starter's audio; it's counted as
        // unresolved instead.
        let renderer = LayerOfflineRenderer()
        let out = tmpDir.appendingPathComponent("gonepack.m4a")
        let padsByPack: [String: [Int: LayerOfflineRenderer.RenderablePad]] = [
            "starter": [0: .init(buffer: sineBuffer(durationSec: 0.1))],
        ]
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                sampleOn(0, at: 0.2, packId: "gone"),
            ],
            durationSec: 0.5
        )
        let result = try renderer.render(
            timeline: timeline,
            padsByPack: padsByPack,
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertEqual(result.unresolvedSampleEvents, 1)
    }

    func testPacksOverloadRendersMultiPackFromDisk() throws {
        // End-to-end through the ResolvedSamplePack path: two packs'
        // wav files on disk, a take that touches both, every hit
        // rendered.
        let starterWav = tmpDir.appendingPathComponent("kick.wav")
        let beatsWav = tmpDir.appendingPathComponent("snare.wav")
        try writeWav(sineBuffer(durationSec: 0.1), to: starterWav)
        try writeWav(sineBuffer(durationSec: 0.1, frequencyHz: 880),
                     to: beatsWav)

        func pack(_ id: String, padIdx: Int, file: URL) -> ResolvedSamplePack {
            ResolvedSamplePack(
                pack: SamplePack(
                    packId: id,
                    name: id,
                    family: .percussion,
                    pads: [SamplePad(
                        padIdx: padIdx, name: "p\(padIdx)",
                        family: .percussion,
                        filename: file.lastPathComponent
                    )]
                ),
                padFileURLs: [padIdx: file]
            )
        }
        let packs = [
            pack("starter", padIdx: 0, file: starterWav),
            pack("beats", padIdx: 5, file: beatsWav),
        ]
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                sampleOn(5, at: 0.25, packId: "beats"),
            ],
            durationSec: 0.6
        )
        let out = tmpDir.appendingPathComponent("multipack-disk.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: packs,
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 2)
        XCTAssertEqual(result.unresolvedSampleEvents, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
    }

    // MARK: - Stem-slice (song-DNA) pads

    /// A song-derived pack: no pad files, each pad a StemSlice window
    /// into the parent stem.
    private func dnaPack(
        padIdx: Int, stemRole: String, startSec: Double, endSec: Double
    ) -> ResolvedSamplePack {
        ResolvedSamplePack(
            pack: SamplePack(
                packId: "song-derived:abc:\(stemRole)-chord",
                name: "\(stemRole) — chord",
                family: .vocals,
                pads: [SamplePad(
                    padIdx: padIdx, name: "chop",
                    family: .vocals,
                    stemSlice: StemSlice(
                        stemRole: stemRole,
                        startSec: startSec,
                        endSec: endSec
                    )
                )]
            ),
            padFileURLs: [:]
        )
    }

    func testStemSlicePadRendersOnlyItsWindow() throws {
        // DNA pad slicing 0.25–0.75 s out of a 2 s stem. The render
        // must schedule the 0.5 s window, not the whole stem.
        let stemWav = tmpDir.appendingPathComponent("vocals.wav")
        try writeWav(sineBuffer(durationSec: 2.0), to: stemWav)

        let dna = dnaPack(
            padIdx: 3, stemRole: "vocals", startSec: 0.25, endSec: 0.75
        )
        let timeline = makeTimeline(
            events: [sampleOn(3, at: 0.0, packId: dna.pack.packId)],
            durationSec: 0.1
        )
        let out = tmpDir.appendingPathComponent("dna-slice.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: [dna],
            stemFiles: ["vocals": stemWav],
            outputURL: out,
            tailSec: 0.0
        )
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertEqual(result.unresolvedSampleEvents, 0)
        // Bounded by the slice window — a whole-stem load would push
        // this to ~2 s.
        XCTAssertEqual(result.durationSec, 0.5, accuracy: 0.05)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
    }

    func testStemSlicePadWithMissingStemIsUnresolved() throws {
        // No stemFiles entry for the pad's role — the event is
        // skipped and surfaced, never a crash or a borrowed buffer.
        let dna = dnaPack(
            padIdx: 3, stemRole: "vocals", startSec: 0.25, endSec: 0.75
        )
        let timeline = makeTimeline(
            events: [sampleOn(3, at: 0.0, packId: dna.pack.packId)],
            durationSec: 0.1
        )
        let out = tmpDir.appendingPathComponent("dna-missing-stem.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: [dna],
            stemFiles: [:],
            outputURL: out,
            tailSec: 0.0
        )
        XCTAssertEqual(result.renderedSampleEvents, 0)
        XCTAssertEqual(result.unresolvedSampleEvents, 1)
    }

    func testLoadBufferSliceIsNormalizedToLiveTarget() throws {
        // Sliced loads peak-normalize to the scheduler's -1 dBFS
        // target (0.891) so exported chops sit at live loudness. The
        // fixture sine peaks at 0.4, so a no-op would fail this.
        let stemWav = tmpDir.appendingPathComponent("stem.wav")
        try writeWav(sineBuffer(durationSec: 2.0), to: stemWav)
        let target = AVAudioFormat(
            standardFormatWithSampleRate: LayerOfflineRenderer.sampleRate,
            channels: 2
        )!
        let slice = StemSlice(
            stemRole: "vocals", startSec: 0.25, endSec: 0.75
        )
        let buf = try LayerOfflineRenderer.loadBuffer(
            at: stemWav, slice: slice, into: target
        )
        // Window length: 0.5 s at 44.1 kHz.
        XCTAssertEqual(Double(buf.frameLength), 0.5 * 44_100, accuracy: 4)
        var peak: Float = 0
        if let ch = buf.floatChannelData?[0] {
            for i in 0..<Int(buf.frameLength) {
                peak = max(peak, abs(ch[i]))
            }
        }
        XCTAssertEqual(peak, 0.891, accuracy: 0.02)
    }

    // MARK: - Device-local (mic/vocoded) pads

    /// A mic-shaped WAV: mono 48 kHz sine peaking at 0.4, like a
    /// quiet PadSampleStore capture (payloads are mono 48 kHz).
    private func writeLocalWav(to url: URL) throws {
        let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 48_000,
            channels: 1,
            interleaved: false
        )!
        let frames: AVAudioFrameCount = 4800 // 0.1 s
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData?[0] {
            let twoPi = 2.0 * Double.pi
            for i in 0..<Int(frames) {
                ch[i] = Float(sin(twoPi * 440 * Double(i) / 48_000) * 0.4)
            }
        }
        try writeWav(buf, to: url)
    }

    func testLocalPadRendersAlongsidePackEvents() throws {
        // A take mixing a pack hit and a mic-pad hit (grid raw 65,
        // packIdOverride "local") — both must render when the caller
        // supplies the local WAV map.
        let localWav = tmpDir.appendingPathComponent("mic.wav")
        try writeLocalWav(to: localWav)
        let packWav = tmpDir.appendingPathComponent("kick.wav")
        try writeWav(sineBuffer(durationSec: 0.1), to: packWav)

        let starter = ResolvedSamplePack(
            pack: SamplePack(
                packId: "starter",
                name: "starter",
                family: .percussion,
                pads: [SamplePad(
                    padIdx: 0, name: "kick",
                    family: .percussion,
                    filename: packWav.lastPathComponent
                )]
            ),
            padFileURLs: [0: packWav]
        )
        let timeline = makeTimeline(
            events: [
                sampleOn(0, at: 0.0),
                sampleOn(65, at: 0.25,
                         packId: SampleScheduler.localPackId),
            ],
            durationSec: 0.6
        )
        let out = tmpDir.appendingPathComponent("local-mixed.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: [starter],
            localPadFiles: [65: localWav],
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 2)
        XCTAssertEqual(result.unresolvedSampleEvents, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
    }

    func testLocalOnlyTakeRendersWithNoPacks() throws {
        // Sketch takes played entirely on mic pads reference no
        // resolvable pack — the local map alone must carry the render.
        let localWav = tmpDir.appendingPathComponent("mic-only.wav")
        try writeLocalWav(to: localWav)
        let timeline = makeTimeline(
            events: [sampleOn(65, at: 0.0,
                              packId: SampleScheduler.localPackId)],
            durationSec: 0.3
        )
        let out = tmpDir.appendingPathComponent("local-only.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: [],
            localPadFiles: [65: localWav],
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertEqual(result.unresolvedSampleEvents, 0)
        XCTAssertTrue(FileManager.default.fileExists(atPath: out.path))
    }

    func testLocalPadMissingFromMapIsUnresolved() throws {
        // Assignment cleared / sample deleted since the take — the
        // event is skipped and surfaced, never a crash.
        let timeline = makeTimeline(
            events: [sampleOn(65, at: 0.0,
                              packId: SampleScheduler.localPackId)],
            durationSec: 0.3
        )
        let out = tmpDir.appendingPathComponent("local-missing.m4a")
        let result = try LayerOfflineRenderer().render(
            timeline: timeline,
            packs: [],
            localPadFiles: [:],
            outputURL: out,
            tailSec: 0.1
        )
        XCTAssertEqual(result.renderedSampleEvents, 0)
        XCTAssertEqual(result.unresolvedSampleEvents, 1)
    }

    func testLoadBufferNormalizeFlagHitsLiveTarget() throws {
        // normalize: true peak-normalizes a whole file to the
        // scheduler's -1 dBFS target (0.891) — the loudness
        // setLocalBuffer gives mic pads live. The fixture peaks at
        // 0.4, so a no-op would fail this.
        let localWav = tmpDir.appendingPathComponent("mic-norm.wav")
        try writeLocalWav(to: localWav)
        let target = AVAudioFormat(
            standardFormatWithSampleRate: LayerOfflineRenderer.sampleRate,
            channels: 2
        )!
        let buf = try LayerOfflineRenderer.loadBuffer(
            at: localWav, normalize: true, into: target
        )
        XCTAssertEqual(buf.format.channelCount, 2)
        var peak: Float = 0
        if let ch = buf.floatChannelData?[0] {
            for i in 0..<Int(buf.frameLength) {
                peak = max(peak, abs(ch[i]))
            }
        }
        XCTAssertEqual(peak, 0.891, accuracy: 0.02)
    }

    /// Write a PCM buffer to a wav file, scoping the writer so it's
    /// flushed and closed before the caller reads it back.
    private func writeWav(_ buf: AVAudioPCMBuffer, to url: URL) throws {
        try autoreleasepool {
            let file = try AVAudioFile(
                forWriting: url, settings: buf.format.settings
            )
            try file.write(from: buf)
        }
    }

    // MARK: - Buffer loading

    func testLoadBufferConvertsFormats() throws {
        // Write a small mono 22050 Hz WAV, then load it via the
        // renderer's helper — expected to return a stereo 44.1 kHz
        // buffer thanks to the AVAudioConverter path.
        let src = tmpDir.appendingPathComponent("mono22k.wav")
        let srcFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 22_050,
            channels: 1,
            interleaved: false
        )!
        let frames: AVAudioFrameCount = 2205 // 0.1s
        let buf = AVAudioPCMBuffer(pcmFormat: srcFormat, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData?[0] {
            for i in 0..<Int(frames) { ch[i] = 0.1 }
        }
        // AVAudioFile flushes to disk on deinit; scope the writer so
        // the subsequent `loadBuffer` sees a closed, readable file.
        try autoreleasepool {
            let file = try AVAudioFile(forWriting: src, settings: srcFormat.settings)
            try file.write(from: buf)
        }

        let target = AVAudioFormat(
            standardFormatWithSampleRate: LayerOfflineRenderer.sampleRate,
            channels: 2
        )!
        let loaded = try LayerOfflineRenderer.loadBuffer(at: src, into: target)
        XCTAssertEqual(loaded.format.sampleRate, target.sampleRate)
        XCTAssertEqual(loaded.format.channelCount, 2)
        XCTAssertGreaterThan(loaded.frameLength, 0)
    }
}
