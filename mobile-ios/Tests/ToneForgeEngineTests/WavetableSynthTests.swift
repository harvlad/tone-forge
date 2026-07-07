// WavetableSynthTests.swift
//
// Gates the band-limited wavetable oscillator: mip-band selection,
// harmonic caps below Nyquist, and the P1 ship criterion — alias
// partials < −60 dBFS relative to the fundamental (Blackman–Harris
// windowed spectrum, guard bands around true harmonics). Plus voice
// lifecycle: release-to-silence, idle zero-fill, steal safety.

import XCTest
import Accelerate
@testable import ToneForgeEngine

final class WavetableSynthTests: XCTestCase {

    private let fs: Double = 48_000

    /// Render `seconds` of audio in 512-frame blocks (mono = left).
    private func render(_ synth: WavetableSynth, seconds: Double) -> [Float] {
        let total = Int(seconds * fs)
        var left = [Float](repeating: 0, count: total)
        var right = [Float](repeating: 0, count: total)
        let block = 512
        var offset = 0
        while offset < total {
            let frames = min(block, total - offset)
            left.withUnsafeMutableBufferPointer { l in
                right.withUnsafeMutableBufferPointer { r in
                    synth.render(
                        left: l.baseAddress! + offset,
                        right: r.baseAddress! + offset,
                        frames: frames
                    )
                }
            }
            offset += frames
        }
        return left
    }

    // MARK: - Band construction

    func testBandIndexSelection() {
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 10), 0)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 20), 0)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 39), 0)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 40), 1)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 440), 4)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 2_093), 6)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 19_000), 9)
        XCTAssertEqual(WavetableSynth.bandIndex(forFrequency: 30_000), 9)
    }

    func testEveryBandsHarmonicsStayBelowNyquist() {
        for band in 0..<WavetableSynth.bandCount {
            let n = WavetableSynth.harmonicCount(band: band, sampleRate: fs)
            let topFundamental = WavetableSynth.bandBaseHz * pow(2.0, Double(band + 1))
            XCTAssertGreaterThanOrEqual(n, 1)
            XCTAssertLessThanOrEqual(
                Double(n) * topFundamental, fs / 2,
                "band \(band): harmonic \(n) of top fundamental aliases"
            )
        }
    }

    func testTablesAreNormalized() {
        let tables = WavetableSynth.buildTables(sampleRate: fs)
        XCTAssertEqual(tables.count, WavetableSynth.bandCount)
        for (i, table) in tables.enumerated() {
            XCTAssertEqual(table.count, WavetableSynth.tableSize)
            let peak = table.map(abs).max()!
            XCTAssertEqual(peak, 1.0, accuracy: 1e-3, "band \(i) peak off")
        }
    }

    // MARK: - Alias gate (< −60 dBFS)

    func testAliasPartialsBelowMinus60dB() throws {
        let synth = WavetableSynth(sampleRate: fs, maxVoices: 4)
        // Deterministic tone: no detune, no filter coloration beyond
        // a fixed wide-open cutoff, flat sustain.
        synth.setParams(WavetableSynthParams(
            attackSec: 0.001, decaySec: 0.01, sustainLevel: 1.0,
            releaseSec: 0.05, cutoffHz: 20_000, resonance: 0,
            detuneCents: 0, masterGain: 1.0
        ))
        let midi = 96  // C7 ≈ 2093 Hz — high note, worst alias exposure
        synth.noteOn(midi: midi, velocity: 1.0)
        let audio = render(synth, seconds: 1.0)

        // Analyse the steady-state tail.
        let fftSize = 32_768
        let tail = Array(audio.suffix(fftSize))
        let window = DSPTestSupport.blackmanHarris(fftSize)
        let mags = DSPTestSupport.magnitudeSpectrum(
            DSPTestSupport.applyWindow(tail, window)
        )

        let f0 = 440.0 * pow(2.0, Double(midi - 69) / 12.0)
        let binHz = fs / Double(fftSize)

        // Guard ±6 bins around every true harmonic (BH-92 mainlobe)
        // and the first few DC bins.
        var guarded = Set<Int>(0...6)
        var h = 1
        while Double(h) * f0 < fs / 2 {
            let center = Int((Double(h) * f0 / binHz).rounded())
            for b in (center - 6)...(center + 6) where b >= 0 && b < mags.count {
                guarded.insert(b)
            }
            h += 1
        }

        let fundamentalBin = Int((f0 / binHz).rounded())
        let fundamental = ((fundamentalBin - 2)...(fundamentalBin + 2))
            .map { mags[$0] }.max()!
        XCTAssertGreaterThan(fundamental, 0)

        var worstDb: Float = -200
        var worstBin = 0
        for b in mags.indices where !guarded.contains(b) {
            let db = DSPTestSupport.dB(mags[b], over: fundamental)
            if db > worstDb {
                worstDb = db
                worstBin = b
            }
        }
        XCTAssertLessThan(
            worstDb, -60,
            "alias/spurious partial at \(Double(worstBin) * binHz) Hz is \(worstDb) dB"
        )
    }

    // MARK: - Voice lifecycle

    func testIdleSynthRendersSilence() {
        let synth = WavetableSynth(sampleRate: fs)
        let audio = render(synth, seconds: 0.05)
        XCTAssertEqual(audio.map(abs).max()!, 0)
    }

    func testNoteProducesAudio() {
        let synth = WavetableSynth(sampleRate: fs)
        synth.noteOn(midi: 60, velocity: 1.0)
        let audio = render(synth, seconds: 0.2)
        let peak = audio.map(abs).max()!
        XCTAssertGreaterThan(peak, 0.05, "note-on should be audible")
        XCTAssertLessThanOrEqual(peak, 1.0, "must not clip at velocity 1")
    }

    func testVelocityScalesLevel() {
        func peak(_ velocity: Float) -> Float {
            let synth = WavetableSynth(sampleRate: fs)
            synth.noteOn(midi: 60, velocity: velocity)
            return render(synth, seconds: 0.2).map(abs).max()!
        }
        XCTAssertGreaterThan(peak(1.0), peak(0.3) * 2)
    }

    func testNoteOffReleasesToSilence() {
        let synth = WavetableSynth(sampleRate: fs)
        synth.setParams(WavetableSynthParams(releaseSec: 0.05))
        synth.noteOn(midi: 60, velocity: 1.0)
        _ = render(synth, seconds: 0.2)
        synth.noteOff(midi: 60)
        let tail = render(synth, seconds: 1.0)
        // After 20 release time-constants the voice must be dead.
        let lastChunk = Array(tail.suffix(4_800))
        XCTAssertEqual(lastChunk.map(abs).max()!, 0, "voice should go fully idle")
    }

    func testAllNotesOffSilencesChord() {
        let synth = WavetableSynth(sampleRate: fs)
        synth.setParams(WavetableSynthParams(releaseSec: 0.05))
        for midi in [60, 64, 67] { synth.noteOn(midi: midi, velocity: 0.8) }
        _ = render(synth, seconds: 0.1)
        synth.allNotesOff()
        let tail = render(synth, seconds: 1.0)
        XCTAssertEqual(Array(tail.suffix(4_800)).map(abs).max()!, 0)
    }

    func testVoiceStealSurvivesOverflow() {
        let synth = WavetableSynth(sampleRate: fs, maxVoices: 8)
        for midi in 40..<72 { synth.noteOn(midi: midi, velocity: 0.5) }
        let audio = render(synth, seconds: 0.1)
        XCTAssertTrue(audio.allSatisfy(\.isFinite))
        XCTAssertGreaterThan(audio.map(abs).max()!, 0)
    }

    func testStereoChannelsMatch() {
        let synth = WavetableSynth(sampleRate: fs)
        synth.noteOn(midi: 60, velocity: 1.0)
        var left = [Float](repeating: 0, count: 4_800)
        var right = [Float](repeating: 0, count: 4_800)
        left.withUnsafeMutableBufferPointer { l in
            right.withUnsafeMutableBufferPointer { r in
                synth.render(left: l.baseAddress!, right: r.baseAddress!, frames: 4_800)
            }
        }
        XCTAssertEqual(left, right, "center-panned voices must be identical L/R")
    }
}
