// SessionBounceRendererTests.swift
//
// P6 bounce contract tests. The headline test is the determinism
// gate: ten renders of the same session (reverb ON) must produce
// bit-identical WAV files. The rest pin the behaviour spec: the
// attestation tripwire, integer-frame placement, velocity gain,
// synth-track rendering, skip accounting, tail length, and the AAC
// convenience path.

import XCTest
import AVFoundation
@testable import ToneForgeEngine

final class SessionBounceRendererTests: XCTestCase {

    private let sr = 48_000.0

    // MARK: - Buffer helpers (48 kHz Float32, deinterleaved)

    private func monoBuffer(frames: Int, fill: (Int) -> Float) -> AVAudioPCMBuffer {
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 1)!
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(frames))!
        buf.frameLength = AVAudioFrameCount(frames)
        let p = buf.floatChannelData![0]
        for i in 0..<frames { p[i] = fill(i) }
        return buf
    }

    private func stereoBuffer(frames: Int, fill: (Int) -> Float) -> AVAudioPCMBuffer {
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(frames))!
        buf.frameLength = AVAudioFrameCount(frames)
        for ch in 0..<2 {
            let p = buf.floatChannelData![ch]
            for i in 0..<frames { p[i] = fill(i) }
        }
        return buf
    }

    private func sineMono(freq: Double, seconds: Double, amp: Float) -> AVAudioPCMBuffer {
        let frames = Int(seconds * sr)
        return monoBuffer(frames: frames) { i in
            amp * sinf(2 * .pi * Float(freq) * Float(i) / Float(sr))
        }
    }

    /// Single-frame unit impulse — the frame-placement probe.
    private func impulseBuffer() -> AVAudioPCMBuffer {
        monoBuffer(frames: 1) { _ in 1.0 }
    }

    // MARK: - Event/session helpers

    private func padDown(_ row: Int, _ col: Int, t: Double, vel: Double = 1.0) -> ContributionEvent {
        ContributionEvent(
            source: .touch, kind: .padDown(row: row, col: col),
            timestamp: t, hostTime: 0, velocity: vel)
    }

    private func padUp(_ row: Int, _ col: Int, t: Double) -> ContributionEvent {
        ContributionEvent(
            source: .touch, kind: .padUp(row: row, col: col),
            timestamp: t, hostTime: 0)
    }

    private func midiNote(_ note: Int, on: Bool, t: Double, vel: Int = 100) -> ContributionEvent {
        ContributionEvent(
            source: .midiKeyboard,
            kind: .midiNote(note: note, velocity: vel, on: on),
            timestamp: t, hostTime: 0, velocity: Double(vel) / 127.0)
    }

    private func gapEvent(t: Double, seconds: Double) -> ContributionEvent {
        ContributionEvent(
            source: .touch, kind: .gap(seconds: seconds),
            timestamp: t, hostTime: 0)
    }

    private func makeSession(
        _ events: [ContributionEvent],
        mode: AppMode = .sample,
        id: UUID = UUID()
    ) -> SessionCapture {
        SessionCapture(
            sessionId: id,
            songBackendId: nil,
            appMode: mode,
            capturedAt: Date(timeIntervalSince1970: 1_700_000_000),
            tempoBpm: nil,
            events: events,
            padMapping: [:]
        )
    }

    private let layout = SampleModeLayout(content: [:])

    private func tempDir() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("BounceTests-\(UUID().uuidString)")
    }

    /// Gains with the wet branch muted — used by placement/level
    /// tests where reverb energy would blur assertions.
    private func dryGains(
        voice: Float = 0.9, chop: Float = 0.55,
        layer: Float = 1.0, dry: Float = 0.9
    ) -> BounceGains {
        BounceGains(voice: voice, chop: chop, layer: layer, dry: dry, wet: 0)
    }

    // MARK: - Output readers

    private func readWav(_ url: URL) throws -> (left: [Float], right: [Float]) {
        let file = try AVAudioFile(forReading: url)
        let frames = AVAudioFrameCount(file.length)
        let buf = AVAudioPCMBuffer(
            pcmFormat: file.processingFormat, frameCapacity: frames)!
        try file.read(into: buf)
        let n = Int(buf.frameLength)
        let chans = Int(buf.format.channelCount)
        let l = Array(UnsafeBufferPointer(start: buf.floatChannelData![0], count: n))
        let r = chans > 1
            ? Array(UnsafeBufferPointer(start: buf.floatChannelData![1], count: n))
            : l
        return (l, r)
    }

    private func rms(_ x: ArraySlice<Float>) -> Float {
        guard !x.isEmpty else { return 0 }
        var acc: Float = 0
        for v in x { acc += v * v }
        return sqrt(acc / Float(x.count))
    }

    // MARK: - THE GATE: bit-identical renders

    func testTenRendersAreBitIdentical() throws {
        // Samples on three pads + two synth notes, reverb ON
        // (default gains, wet 0.3, 2 s tail).
        let pads: [Int: AVAudioPCMBuffer] = [
            11: sineMono(freq: 440, seconds: 0.2, amp: 0.5),
            12: stereoBuffer(frames: Int(0.15 * sr)) { i in
                0.4 * sinf(2 * .pi * 660 * Float(i) / Float(48_000))
            },
            23: monoBuffer(frames: Int(0.1 * sr)) { i in
                // Decaying click.
                expf(-Float(i) / 800) * (i == 0 ? 1 : 0.3)
            },
        ]
        let session = makeSession([
            padDown(1, 1, t: 0.0),
            midiNote(60, on: true, t: 0.1),
            padDown(1, 2, t: 0.25, vel: 0.7),
            midiNote(64, on: true, t: 0.3, vel: 90),
            padDown(2, 3, t: 0.5, vel: 0.9),
            midiNote(60, on: false, t: 0.6),
            midiNote(64, on: false, t: 0.7),
        ])

        var blobs: [Data] = []
        for _ in 0..<10 {
            let dir = tempDir()
            defer { try? FileManager.default.removeItem(at: dir) }
            let result = try SessionBounceRenderer.bounceSession(
                session,
                padBuffers: pads,
                layout: layout,
                outputDirectory: dir,
                tailSec: 1.0
            )
            blobs.append(try Data(contentsOf: result.url))
        }

        XCTAssertGreaterThan(blobs[0].count, 44, "empty wav")
        for (i, blob) in blobs.enumerated().dropFirst() {
            XCTAssertEqual(blob, blobs[0], "render \(i) differs from render 0")
        }
    }

    // MARK: - Attestation tripwire

    func testAttestationGateThrows() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.1, amp: 0.5)]
        let session = makeSession([padDown(1, 1, t: 0.0)])

        // Gate fires FIRST — even without any songAudio supplied.
        XCTAssertThrowsError(try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            songAudio: nil, includeOriginalSong: true,
            attestationAccepted: false,
            outputDirectory: tempDir()
        )) { error in
            XCTAssertEqual(
                error as? SessionBounceRenderer.RenderError,
                .attestationRequired)
        }

        // Attested + song provided: renders, and the song is audible
        // in a region where only the song plays (pad buffer ends at
        // 0.1 s; probe 0.5–0.9 s; wet muted so no pad reverb tail).
        let song = stereoBuffer(frames: Int(1.0 * sr)) { i in
            0.3 * sinf(2 * .pi * 220 * Float(i) / Float(48_000))
        }
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }
        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            gains: dryGains(),
            songAudio: song, includeOriginalSong: true,
            attestationAccepted: true,
            outputDirectory: dir, tailSec: 0.5
        )
        let out = try readWav(result.url)
        let probe = rms(out.left[Int(0.5 * sr)..<Int(0.9 * sr)])
        XCTAssertGreaterThan(probe, 0.05, "song not audible in song-only region")
    }

    // MARK: - Placement + gain

    func testFrameAccuratePlacement() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: impulseBuffer()]
        let session = makeSession([padDown(1, 1, t: 0.5)])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            gains: dryGains(voice: 1, chop: 1, layer: 1, dry: 1),
            outputDirectory: dir, tailSec: 0.5
        )
        let out = try readWav(result.url)

        // Nonzero exactly at frame 24_000, zero everywhere nearby.
        XCTAssertEqual(out.left[24_000], 1.0, accuracy: 1e-6)
        XCTAssertEqual(out.right[24_000], 1.0, accuracy: 1e-6)
        for i in 23_900..<24_100 where i != 24_000 {
            XCTAssertEqual(out.left[i], 0, "unexpected energy at frame \(i)")
        }
    }

    func testVelocityScalesSampleGain() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.05, amp: 0.5)]
        let dir1 = tempDir(), dir2 = tempDir()
        defer {
            try? FileManager.default.removeItem(at: dir1)
            try? FileManager.default.removeItem(at: dir2)
        }

        func peak(vel: Double, dir: URL) throws -> Float {
            let result = try SessionBounceRenderer.bounceSession(
                makeSession([padDown(1, 1, t: 0.0, vel: vel)]),
                padBuffers: pads, layout: layout,
                gains: dryGains(),
                outputDirectory: dir, tailSec: 0.1
            )
            let out = try readWav(result.url)
            return out.left.map(abs).max() ?? 0
        }

        let full = try peak(vel: 1.0, dir: dir1)
        let half = try peak(vel: 0.5, dir: dir2)
        XCTAssertGreaterThan(full, 0)
        XCTAssertEqual(half / full, 0.5, accuracy: 0.025)  // ±5 %
    }

    // MARK: - Synth track

    func testSynthNotesRender() throws {
        // .midiNote resolves to the synth in any implemented mode —
        // no pad buffers needed at all.
        let session = makeSession([
            midiNote(60, on: true, t: 0.1),
            midiNote(60, on: false, t: 0.5),
        ])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: [:], layout: layout,
            gains: dryGains(),
            outputDirectory: dir, tailSec: 0.5
        )
        XCTAssertEqual(result.renderedNoteEvents, 1)
        XCTAssertEqual(result.renderedSampleEvents, 0)
        XCTAssertEqual(result.skippedEvents, 0)

        let out = try readWav(result.url)
        // Silent before the note-on, sounding inside the note.
        XCTAssertEqual(rms(out.left[0..<Int(0.08 * sr)]), 0)
        XCTAssertGreaterThan(
            rms(out.left[Int(0.15 * sr)..<Int(0.45 * sr)]), 0.005,
            "synth note region is silent")
    }

    // MARK: - Skip accounting + gates

    func testNoRenderableEventsThrows() throws {
        let dir = tempDir()
        // Only releases: everything skips, nothing sounds.
        let session = makeSession([padUp(1, 1, t: 0.1), padUp(1, 2, t: 0.2)])
        XCTAssertThrowsError(try SessionBounceRenderer.bounceSession(
            session, padBuffers: [:], layout: layout, outputDirectory: dir
        )) { error in
            XCTAssertEqual(
                error as? SessionBounceRenderer.RenderError,
                .noRenderableEvents)
        }
        // Empty event list.
        XCTAssertThrowsError(try SessionBounceRenderer.bounceSession(
            makeSession([]), padBuffers: [:], layout: layout,
            outputDirectory: dir
        ))
        // Thrown BEFORE the file (or even the directory) exists.
        let expected = dir
            .appendingPathComponent(session.sessionId.uuidString)
            .appendingPathExtension("wav")
        XCTAssertFalse(FileManager.default.fileExists(atPath: expected.path))
    }

    func testNegativeTimestampsSkipped() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.05, amp: 0.5)]
        // Count-in hit at −0.5 s must neither render nor crash.
        let session = makeSession([
            padDown(1, 1, t: -0.5),
            padDown(1, 1, t: 0.0),
        ])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            gains: dryGains(),
            outputDirectory: dir, tailSec: 0.2
        )
        XCTAssertEqual(result.skippedEvents, 1)
        XCTAssertEqual(result.renderedSampleEvents, 1)

        let out = try readWav(result.url)
        XCTAssertGreaterThan(rms(out.left[0..<2_400]), 0.01)
        // Duration was not stretched by the negative event.
        XCTAssertEqual(result.durationSec, 0.05 + 0.2, accuracy: 0.02)
    }

    func testGapEventsIgnored() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.1, amp: 0.5)]
        let session = makeSession([
            padDown(1, 1, t: 0.0),
            gapEvent(t: 0.4, seconds: 1.0),
        ])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            gains: dryGains(),
            outputDirectory: dir, tailSec: 0.2
        )
        XCTAssertEqual(result.renderedSampleEvents, 1)
        XCTAssertEqual(result.skippedEvents, 1, "gap should count skipped")
        XCTAssertEqual(result.renderedNoteEvents, 0)
    }

    // MARK: - Duration + AAC

    func testDurationIncludesTail() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.2, amp: 0.5)]
        let session = makeSession([padDown(1, 1, t: 1.0)])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            gains: dryGains(),
            outputDirectory: dir, tailSec: 1.5
        )
        // lastEvent(1.0) + bufferLen(0.2) + tail(1.5).
        XCTAssertEqual(result.durationSec, 2.7, accuracy: 0.02)

        let file = try AVAudioFile(forReading: result.url)
        let fileSec = Double(file.length) / file.processingFormat.sampleRate
        XCTAssertEqual(fileSec, 2.7, accuracy: 0.02)
    }

    func testM4AProducesPlayableFile() throws {
        let pads: [Int: AVAudioPCMBuffer] = [11: sineMono(freq: 440, seconds: 0.2, amp: 0.5)]
        let session = makeSession([padDown(1, 1, t: 0.0)])
        let dir = tempDir()
        defer { try? FileManager.default.removeItem(at: dir) }

        let result = try SessionBounceRenderer.bounceSession(
            session, padBuffers: pads, layout: layout,
            format: .m4aAAC256,
            outputDirectory: dir, tailSec: 0.5
        )
        XCTAssertEqual(result.url.pathExtension, "m4a")
        XCTAssertTrue(FileManager.default.fileExists(atPath: result.url.path))
        let size = (try FileManager.default
            .attributesOfItem(atPath: result.url.path)[.size] as? Int) ?? 0
        XCTAssertGreaterThan(size, 0)

        // Decodable, with duration ≈ expected (encoder priming makes
        // this loose).
        let file = try AVAudioFile(forReading: result.url)
        let fileSec = Double(file.length) / file.processingFormat.sampleRate
        XCTAssertEqual(fileSec, 0.7, accuracy: 0.25)
    }
}
