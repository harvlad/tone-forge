// WavetableSynth.swift
//
// Polyphonic mip-mapped wavetable synth for the hybrid-mode note rows.
// Pure DSP — no AVFoundation. The mobile target hosts it inside an
// AVAudioSourceNode (WavetableSynthNode) connected to the voiceBus.
//
// Band-limiting: one saw table per octave band. Band i serves
// fundamentals in [20·2^i, 20·2^(i+1)) Hz and is synthesised with only
// the harmonics that stay below Nyquist for the TOP of the band, so no
// playable note aliases. Tables are 4096 samples; with linear
// interpolation the worst-case image partial is ~π²h/(2N²) relative to
// the fundamental ≈ −64 dBFS for the lowest band — under the −60 dB
// alias gate with margin (see WavetableSynthTests).
//
// Threading mirrors PadSynth.VoicePool: `noteOn`/`noteOff`/`setParams`
// are callable from any thread and post into an NSLock-guarded pending
// queue; `render` (audio thread) drains the queue at block start and
// then touches voice state with no locks held during synthesis.
//
// Per-voice chain: 2 detuned table readers → LadderFilter → ADSR VCA.

import Foundation
import Accelerate

/// Control-rate parameters. Applied atomically at the next render.
public struct WavetableSynthParams: Sendable, Equatable {
    public var attackSec: Double
    public var decaySec: Double
    public var sustainLevel: Double
    public var releaseSec: Double
    public var cutoffHz: Double
    /// 0…1; ladder feedback (self-oscillation approached at 1).
    public var resonance: Double
    /// Total spread between the two oscillators (osc1 −c/2, osc2 +c/2).
    public var detuneCents: Double
    public var masterGain: Double

    public init(
        attackSec: Double = 0.004,
        decaySec: Double = 0.18,
        sustainLevel: Double = 0.65,
        releaseSec: Double = 0.22,
        cutoffHz: Double = 5_500,
        resonance: Double = 0.18,
        detuneCents: Double = 6,
        masterGain: Double = 0.8
    ) {
        self.attackSec = attackSec
        self.decaySec = decaySec
        self.sustainLevel = sustainLevel
        self.releaseSec = releaseSec
        self.cutoffHz = cutoffHz
        self.resonance = resonance
        self.detuneCents = detuneCents
        self.masterGain = masterGain
    }
}

public final class WavetableSynth: @unchecked Sendable {

    // MARK: - Wavetable bank

    /// Table length. 4096 keeps linear-interp images ≤ −64 dB even
    /// for the 600-harmonic bottom band (error ∝ π²h/(2N²)).
    static let tableSize = 4096
    /// Band i serves fundamentals in [bandBaseHz·2^i, bandBaseHz·2^(i+1)).
    static let bandBaseHz: Double = 20
    static let bandCount = 10

    /// One band-limited saw per octave band. Immutable after init.
    private let tables: [[Float]]

    // MARK: - Voices

    private enum EnvStage { case idle, attack, decay, sustain, release }

    private struct Voice {
        var active = false
        var midi = 0
        var phase1: Double = 0
        var phase2: Double = 0
        var inc1: Double = 0        // table-samples per audio sample
        var inc2: Double = 0
        var tableIndex = 0
        var gain: Float = 0         // velocity
        var stage: EnvStage = .idle
        var env: Float = 0
        var filter = LadderFilter()
        var startOrder: UInt64 = 0  // steal-oldest ordering
    }

    private var voices: [Voice]
    private var nextStartOrder: UInt64 = 1

    // MARK: - Cross-thread event queue

    private enum Pending {
        case noteOn(midi: Int, velocity: Float)
        case noteOff(midi: Int)
        case params(WavetableSynthParams)
        case allNotesOff
    }

    private let pendingLock = NSLock()
    private var pending: [Pending] = []

    // MARK: - Render-thread state

    private let sampleRate: Double
    private var params = WavetableSynthParams()
    // Envelope one-pole coefficients derived from params.
    private var attackCoef: Float = 0
    private var decayCoef: Float = 0
    private var releaseCoef: Float = 0
    /// Mono scratch for one voice's oscillator+filter output.
    private var scratch: [Float]

    public init(sampleRate: Double, maxVoices: Int = 16) {
        self.sampleRate = sampleRate
        self.voices = Array(repeating: Voice(), count: max(1, maxVoices))
        self.scratch = [Float](repeating: 0, count: 8192)
        self.tables = Self.buildTables(sampleRate: sampleRate)
        recomputeEnvCoefs()
    }

    // MARK: - Public API (any thread)

    public func noteOn(midi: Int, velocity: Float) {
        guard (0...127).contains(midi) else { return }
        pendingLock.lock()
        pending.append(.noteOn(midi: midi, velocity: max(0, min(1, velocity))))
        pendingLock.unlock()
    }

    public func noteOff(midi: Int) {
        pendingLock.lock()
        pending.append(.noteOff(midi: midi))
        pendingLock.unlock()
    }

    public func allNotesOff() {
        pendingLock.lock()
        pending.append(.allNotesOff)
        pendingLock.unlock()
    }

    public func setParams(_ p: WavetableSynthParams) {
        pendingLock.lock()
        pending.append(.params(p))
        pendingLock.unlock()
    }

    // MARK: - Render (audio thread)

    /// Render `frames` samples into `left`/`right`, OVERWRITING their
    /// contents (the host source node owns zeroing semantics — this
    /// fills, it does not mix).
    public func render(
        left: UnsafeMutablePointer<Float>,
        right: UnsafeMutablePointer<Float>,
        frames: Int
    ) {
        drainPending()

        vDSP_vclr(left, 1, vDSP_Length(frames))
        vDSP_vclr(right, 1, vDSP_Length(frames))
        guard frames > 0 else { return }
        if scratch.count < frames {
            // Render callbacks larger than the preallocated scratch are
            // not expected (maxFrames 4096); guard rather than allocate
            // on the audio thread.
            return
        }

        let masterGain = Float(params.masterGain)
        let cutoff = params.cutoffHz
        let resonance = params.resonance

        for v in voices.indices where voices[v].active {
            voices[v].filter.configure(
                cutoffHz: cutoff, resonance: resonance, sampleRate: sampleRate
            )
            let table = tables[voices[v].tableIndex]
            renderVoice(&voices[v], table: table, frames: frames)
            // Mix scratch into both channels (center pan).
            let voiceGain = voices[v].gain * masterGain
            scratch.withUnsafeBufferPointer { buf in
                var g = voiceGain
                vDSP_vsma(buf.baseAddress!, 1, &g, left, 1, left, 1, vDSP_Length(frames))
                vDSP_vsma(buf.baseAddress!, 1, &g, right, 1, right, 1, vDSP_Length(frames))
            }
        }
    }

    // MARK: - Private: render internals

    /// Oscillators → ladder → per-sample envelope, into `scratch`.
    private func renderVoice(_ voice: inout Voice, table: [Float], frames: Int) {
        let n = Double(Self.tableSize)
        var phase1 = voice.phase1
        var phase2 = voice.phase2
        let inc1 = voice.inc1
        let inc2 = voice.inc2

        // Oscillator fill.
        table.withUnsafeBufferPointer { t in
            let base = t.baseAddress!
            for i in 0..<frames {
                scratch[i] = 0.5 * (Self.read(base, phase: phase1) + Self.read(base, phase: phase2))
                phase1 += inc1; if phase1 >= n { phase1 -= n }
                phase2 += inc2; if phase2 >= n { phase2 -= n }
            }
        }
        voice.phase1 = phase1
        voice.phase2 = phase2

        // Filter in place.
        scratch.withUnsafeMutableBufferPointer { buf in
            voice.filter.process(buf.baseAddress!, count: frames)
        }

        // Envelope VCA.
        var env = voice.env
        var stage = voice.stage
        let sustain = Float(max(0, min(1, params.sustainLevel)))
        for i in 0..<frames {
            switch stage {
            case .attack:
                // Overshoot target so the exp segment actually reaches 1.
                env += (1.12 - env) * attackCoef
                if env >= 1 { env = 1; stage = .decay }
            case .decay:
                env += (sustain - env) * decayCoef
                if env - sustain < 0.0005 { env = sustain; stage = .sustain }
            case .sustain:
                env = sustain
            case .release:
                env += (0 - env) * releaseCoef
                if env < 0.0002 {
                    env = 0
                    stage = .idle
                }
            case .idle:
                env = 0
            }
            scratch[i] *= env
        }
        voice.env = env
        voice.stage = stage
        if stage == .idle { voice.active = false }
    }

    /// Linear-interpolated table read. Phase in [0, tableSize).
    @inline(__always)
    private static func read(_ table: UnsafePointer<Float>, phase: Double) -> Float {
        let i0 = Int(phase)
        let frac = Float(phase - Double(i0))
        let i1 = (i0 + 1) & (tableSize - 1)
        return table[i0] + (table[i1] - table[i0]) * frac
    }

    private func drainPending() {
        pendingLock.lock()
        let events = pending
        pending.removeAll(keepingCapacity: true)
        pendingLock.unlock()

        for event in events {
            switch event {
            case .params(let p):
                params = p
                recomputeEnvCoefs()
            case .noteOn(let midi, let velocity):
                startVoice(midi: midi, velocity: velocity)
            case .noteOff(let midi):
                for v in voices.indices
                where voices[v].active && voices[v].midi == midi && voices[v].stage != .release {
                    voices[v].stage = .release
                }
            case .allNotesOff:
                for v in voices.indices where voices[v].active {
                    voices[v].stage = .release
                }
            }
        }
    }

    private func startVoice(midi: Int, velocity: Float) {
        // Prefer inactive slot; else steal the oldest.
        var idx = -1
        for v in voices.indices where !voices[v].active { idx = v; break }
        if idx < 0 {
            var oldest: UInt64 = .max
            idx = 0
            for v in voices.indices where voices[v].startOrder < oldest {
                oldest = voices[v].startOrder
                idx = v
            }
        }

        let freq = 440.0 * pow(2.0, Double(midi - 69) / 12.0)
        let detune = params.detuneCents / 2
        let f1 = freq * pow(2.0, -detune / 1200.0)
        let f2 = freq * pow(2.0, +detune / 1200.0)
        let incScale = Double(Self.tableSize) / sampleRate

        var voice = Voice()
        voice.active = true
        voice.midi = midi
        voice.tableIndex = Self.bandIndex(forFrequency: freq)
        voice.inc1 = f1 * incScale
        voice.inc2 = f2 * incScale
        voice.phase1 = 0
        voice.phase2 = 0
        voice.gain = velocity
        voice.stage = .attack
        voice.env = 0
        voice.filter = LadderFilter()
        voice.startOrder = nextStartOrder
        nextStartOrder &+= 1
        voices[idx] = voice
    }

    private func recomputeEnvCoefs() {
        func coef(_ seconds: Double) -> Float {
            let s = max(0.0005, seconds)
            return Float(1 - exp(-1 / (s * sampleRate)))
        }
        attackCoef = coef(params.attackSec)
        decayCoef = coef(params.decaySec)
        releaseCoef = coef(params.releaseSec)
    }

    // MARK: - Table construction

    /// Band for a fundamental frequency: floor(log2(f / 20)), clamped.
    static func bandIndex(forFrequency freq: Double) -> Int {
        guard freq > bandBaseHz else { return 0 }
        let idx = Int(floor(log2(freq / bandBaseHz)))
        return min(max(idx, 0), bandCount - 1)
    }

    /// Number of harmonics band `i`'s table may carry: every harmonic
    /// of the band's TOP fundamental must stay below Nyquist.
    static func harmonicCount(band: Int, sampleRate: Double) -> Int {
        let topFundamental = bandBaseHz * pow(2.0, Double(band + 1))
        let nyquist = sampleRate / 2
        let n = Int(nyquist / topFundamental)
        // At least the fundamental; cap at the table's own Nyquist / 2
        // (2× oversampled table keeps interp images low).
        return min(max(n, 1), tableSize / 2 - 1)
    }

    /// Build all band tables. Saw series Σ sin(2πhx)/h, vectorised via
    /// vvsinf per harmonic + vDSP accumulate, then peak-normalised.
    static func buildTables(sampleRate: Double) -> [[Float]] {
        let n = tableSize
        // Base phase ramp 0…2π over the table.
        var ramp = [Float](repeating: 0, count: n)
        for i in 0..<n { ramp[i] = Float(2.0 * Double.pi * Double(i) / Double(n)) }

        var harmonicPhase = [Float](repeating: 0, count: n)
        var sine = [Float](repeating: 0, count: n)

        var result: [[Float]] = []
        result.reserveCapacity(bandCount)

        for band in 0..<bandCount {
            let harmonics = harmonicCount(band: band, sampleRate: sampleRate)
            var table = [Float](repeating: 0, count: n)
            for h in 1...harmonics {
                var hf = Float(h)
                vDSP_vsmul(ramp, 1, &hf, &harmonicPhase, 1, vDSP_Length(n))
                var count = Int32(n)
                vvsinf(&sine, harmonicPhase, &count)
                var amp = Float(1.0 / Double(h))
                vDSP_vsma(sine, 1, &amp, table, 1, &table, 1, vDSP_Length(n))
            }
            // Normalise to ±1 peak so every band sounds equally loud.
            var peak: Float = 0
            vDSP_maxmgv(table, 1, &peak, vDSP_Length(n))
            if peak > 0 {
                var scale = 1 / peak
                vDSP_vsmul(table, 1, &scale, &table, 1, vDSP_Length(n))
            }
            result.append(table)
        }
        return result
    }
}
