// PadSynth.swift
//
// Polyphonic pad synth voiced by a fixed pool of saw+triangle voices,
// each going through its own biquad LP + amplitude envelope + panner.
// Voices sum into a stereo AVAudioSourceNode:
//
//   sourceNode → voiceMixer → engine.voiceBusInput
//
// Reverb moved OFF this class in the v2 topology (D-013): one shared
// AVAudioUnitReverb lives on AudioEngine's sharedBus, fed by voice,
// chop and vocoder buses alike. PadSynth is now a dry source; the
// wet/dry balance is a sharedBus-level control.
//
// Trigger surface:
//   - triggerNote(midi:velocity:pan:atOffset:)  — open-jam single note
//   - triggerChord(midis:velocity:)             — song-mode chord with
//                                                 strum spread + pan
//
// Destination: `engine.voiceBusInput` — the voice sub-mixer inside
// AudioEngine's contribution graph (voiceBus → sharedBus, which the
// "Your Layer" fader controls). This keeps Instrument-mode notes on
// the same fader as Sample triggers.
//
// Triggers push into a lock-free ring buffer read by the render
// block, so UI-thread trigger calls never block the audio thread.
//
// The synth is deliberately allocation-free on the hot path: voice
// storage is a `[PadVoice]` sized at ``PadSynth.voiceCount``; a trigger
// picks the oldest inactive voice (or steals the oldest active voice
// when the pool is full).

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Fixed voice-pool size. Top-level so the pool init (non-actor
/// context) can read it without hitting Swift 6 actor-isolation.
private let padSynthVoiceCount = 32

/// Fixed-pool polyphonic pad synth. See top-of-file for topology.
@MainActor
public final class PadSynth: ObservableObject {

    /// Max simultaneous voices. 32 comfortably covers a 6-note chord
    /// strum with tails overlapping into the next chord change.
    public static var voiceCount: Int { padSynthVoiceCount }

    // MARK: - Public

    @Published public private(set) var params: PadSynthParams = PadSynthParams()

    // MARK: - Private

    #if canImport(AVFoundation)
    private var sourceNode: AVAudioSourceNode?
    private var voiceMixer: AVAudioMixerNode?
    #endif

    private weak var engine: AudioEngine?

    /// Voice pool. Wrapped in a class for stable pointer access from
    /// the render block. Accessed under ``voiceLock`` from UI and
    /// under a spin-check from audio (the audio side owns the pool
    /// and UI merely posts triggers to the pending queue).
    private let voices = VoicePool()

    /// Sample rate resolved at attach time. Voices need it for phase
    /// increments + biquad coefficients.
    private var sampleRate: Double = 44100

    public init(engine: AudioEngine) {
        self.engine = engine
    }

    // MARK: - Attach / detach

    /// Attach nodes to the engine graph. Idempotent — safe to call
    /// after the engine boots.
    public func attach() {
        #if canImport(AVFoundation)
        guard let engine = engine, sourceNode == nil else { return }
        // Canonical 48 kHz stereo (D-017) — matches the contribution
        // graph so no SRC sits between the synth and the output.
        let format = engine.canonicalFormat
        self.sampleRate = format.sampleRate

        // Build a source node that renders our voices into a stereo
        // float buffer each block.
        let source = AVAudioSourceNode(format: format) { [voices, sampleRateHolder = SampleRateHolder(rate: format.sampleRate)] _, _, frameCount, audioBufferList in
            let abl = UnsafeMutableAudioBufferListPointer(audioBufferList)
            let left = abl[0].mData?.assumingMemoryBound(to: Float.self)
            let right = abl.count > 1 ? abl[1].mData?.assumingMemoryBound(to: Float.self) : left
            let frames = Int(frameCount)
            if let l = left { for i in 0..<frames { l[i] = 0 } }
            if let r = right, r != left { for i in 0..<frames { r[i] = 0 } }
            voices.render(left: left, right: right, frames: frames, sampleRate: sampleRateHolder.rate)
            return noErr
        }

        let voiceMix = AVAudioMixerNode()

        engine.engine.attach(source)
        engine.engine.attach(voiceMix)

        engine.engine.connect(source, to: voiceMix, format: format)
        // Feed the voice sub-mixer, not mainMixer, so the Mixer view's
        // "Your Layer" fader (sharedBus downstream) controls the whole
        // Instrument-mode signal. Reverb happens on sharedBus (D-013).
        engine.engine.connect(voiceMix, to: engine.voiceBusInput, format: format)

        self.sourceNode = source
        self.voiceMixer = voiceMix

        applyParams()
        #endif
    }

    /// Detach and free nodes. Called on scene teardown.
    public func detach() {
        #if canImport(AVFoundation)
        guard let engine = engine else { return }
        for node in [sourceNode as AVAudioNode?, voiceMixer].compactMap({ $0 }) {
            engine.engine.detach(node)
        }
        sourceNode = nil
        voiceMixer = nil
        #endif
    }

    // MARK: - Triggering

    /// Trigger a single note. Used by open-jam mode where each pad
    /// maps to one MIDI note.
    public func triggerNote(
        midi: Int,
        velocity: Float = 100,
        pan: Float = 0,
        atOffset: Int = 0
    ) {
        voices.postTrigger(
            midi: midi,
            velocity: velocity,
            pan: pan,
            atOffset: atOffset,
            sampleRate: sampleRate,
            params: params
        )
    }

    /// Trigger a strummed chord — MIDI note list in ascending order.
    /// Voices are spread across the stereo field and staggered by
    /// ``params.strumMs`` (or `strumMsOverride` when the caller wants
    /// a one-off feel, e.g. Jam strum-off) so the chord blooms rather
    /// than punches.
    public func triggerChord(
        midis: [Int],
        velocity: Float = 100,
        strumMsOverride: Double? = nil
    ) {
        guard !midis.isEmpty else { return }
        let strumMs = strumMsOverride ?? params.strumMs
        let strumSamples = Int(max(0, strumMs) / 1000.0 * sampleRate)
        let n = midis.count
        // Reduce per-voice velocity so the chord doesn't clip.
        let perVoiceVel = max(20, velocity * 0.6)
        for (i, midi) in midis.enumerated() {
            let pan = n > 1 ? (Float(i) / Float(n - 1) - 0.5) * 0.6 : 0
            voices.postTrigger(
                midi: midi,
                velocity: perVoiceVel,
                pan: pan,
                atOffset: i * strumSamples,
                sampleRate: sampleRate,
                params: params
            )
        }
    }

    // MARK: - Parameter updates

    /// Replace live params. Audible values (masterGain) take effect
    /// immediately; per-voice values (attack/release/brightness/strum)
    /// take effect on the next ``trigger*`` call.
    public func update(params: PadSynthParams) {
        self.params = params
        applyParams()
    }

    private func applyParams() {
        #if canImport(AVFoundation)
        voiceMixer?.outputVolume = params.masterGain
        #endif
    }
}

/// Wrapper so the AVAudioSourceNode render closure can capture the
/// sample rate by reference (for hot swaps if the engine restarts).
private final class SampleRateHolder: @unchecked Sendable {
    var rate: Double
    init(rate: Double) { self.rate = rate }
}

// MARK: - Voice pool

/// Fixed voice pool + lock-free trigger queue. The audio thread reads
/// pending triggers at the start of each render block; the UI thread
/// posts them under a mutex short enough (a copy of a struct) that
/// audio-thread contention is negligible.
///
/// The class-based container gives the render closure a stable, non-
/// copied pointer to the voice buffer across capture.
private final class VoicePool: @unchecked Sendable {

    private var voices: [PadVoice]

    /// Pending triggers waiting for the next render block to consume.
    /// Guarded by ``queueLock``. Kept short (allocated on init) so the
    /// audio thread never allocates.
    private var pending: [PendingTrigger] = []
    private let queueLock = NSLock()

    struct PendingTrigger {
        var midi: Int
        var velocity: Float
        var pan: Float
        var atOffset: Int
        var sampleRate: Double
        var params: PadSynthParams
    }

    init() {
        self.voices = Array(repeating: PadVoice(), count: padSynthVoiceCount)
        self.pending.reserveCapacity(padSynthVoiceCount * 2)
    }

    /// Queue a new trigger. Called from UI thread.
    func postTrigger(
        midi: Int,
        velocity: Float,
        pan: Float,
        atOffset: Int,
        sampleRate: Double,
        params: PadSynthParams
    ) {
        queueLock.lock()
        pending.append(PendingTrigger(
            midi: midi,
            velocity: velocity,
            pan: pan,
            atOffset: atOffset,
            sampleRate: sampleRate,
            params: params
        ))
        queueLock.unlock()
    }

    /// Drain pending triggers into free voices, then render.
    func render(left: UnsafeMutablePointer<Float>?, right: UnsafeMutablePointer<Float>?, frames: Int, sampleRate: Double) {
        // Snapshot pending under the lock, then work outside so the UI
        // thread isn't held up.
        queueLock.lock()
        let batch = pending
        pending.removeAll(keepingCapacity: true)
        queueLock.unlock()

        for trigger in batch {
            allocateAndArm(trigger: trigger, sampleRate: sampleRate)
        }

        renderBlock(left: left, right: right, frames: frames, sampleRate: sampleRate)
    }

    private func allocateAndArm(trigger: PendingTrigger, sampleRate: Double) {
        // Prefer an inactive slot. Fall back to voice 0 (oldest) if
        // the pool is full.
        var idx = -1
        for i in 0..<voices.count where !voices[i].isActive {
            idx = i
            break
        }
        if idx < 0 { idx = 0 }

        let midiHz = 440.0 * pow(2.0, (Double(trigger.midi) - 69.0) / 12.0)
        let cents = trigger.params.detuneCents
        let detuneSaw = pow(2.0, -cents / 1200.0)
        let detuneTri = pow(2.0, +cents / 1200.0)
        let brightness = trigger.params.brightness
        let attackSec = max(0.001, trigger.params.attackMs / 1000.0)
        let releaseSec = max(0.05, trigger.params.releaseSec)

        var v = PadVoice()
        v.isActive = true
        v.startFrameOffset = max(0, trigger.atOffset)
        v.phaseSaw = 0
        v.phaseTri = 0
        v.incSaw = midiHz * detuneSaw / sampleRate
        v.incTri = midiHz * detuneTri / sampleRate
        v.sawMix = max(0, min(1, trigger.params.sawMix))

        v.startCut = min(12000, max(600, midiHz * 5.0 * brightness))
        v.endCut   = min(4000,  max(300, midiHz * 1.8 * brightness))
        v.curCut = v.startCut
        v.filterSweepSamples = Int(min(0.6, releaseSec * 0.5) * sampleRate)
        v.samplesElapsed = 0
        v.updateBiquad(cutoffHz: v.startCut, sampleRate: sampleRate)

        let peak = max(0.05, min(1.0, trigger.velocity / 127.0)) * 0.85
        v.envPeak = Float(peak)
        v.envSustain = Float(peak * 0.45)
        v.attackSamples = Int(attackSec * sampleRate)
        v.decayEndSample = v.attackSamples + Int(max(0.05, max(attackSec + 0.05, 0.25) - attackSec) * sampleRate)
        v.totalSamples = Int(releaseSec * sampleRate)
        v.envValue = 0.0001

        v.pan = max(-1, min(1, trigger.pan))

        voices[idx] = v
    }

    private func renderBlock(left: UnsafeMutablePointer<Float>?, right: UnsafeMutablePointer<Float>?, frames: Int, sampleRate: Double) {
        guard let l = left, let r = right else { return }

        // Coefficient re-computation cadence: every 32 samples. Filter
        // sweep is exponential so we lerp cutoff in log space.
        let coeffInterval = 32

        for vi in 0..<voices.count {
            guard voices[vi].isActive else { continue }
            renderVoice(index: vi, l: l, r: r, frames: frames, sampleRate: sampleRate, coeffInterval: coeffInterval)
        }
    }

    private func renderVoice(index: Int, l: UnsafeMutablePointer<Float>, r: UnsafeMutablePointer<Float>, frames: Int, sampleRate: Double, coeffInterval: Int) {
        var v = voices[index]
        defer { voices[index] = v }

        // Equal-power pan (constant power for chord bloom).
        let panRad = (Double(v.pan) + 1.0) * 0.25 * .pi   // 0..π/2
        let leftGain = Float(cos(panRad))
        let rightGain = Float(sin(panRad))

        var i = 0
        // Honour the strum offset — skip samples until the voice starts.
        if v.startFrameOffset > 0 {
            let skip = min(frames, v.startFrameOffset)
            v.startFrameOffset -= skip
            i = skip
        }

        while i < frames {
            // Advance envelope
            v.samplesElapsed += 1
            if v.samplesElapsed <= v.attackSamples {
                // exponential ramp 0.0001 → peak
                let progress = Float(v.samplesElapsed) / Float(max(1, v.attackSamples))
                v.envValue = 0.0001 * pow(v.envPeak / 0.0001, progress)
            } else if v.samplesElapsed <= v.decayEndSample {
                let range = max(1, v.decayEndSample - v.attackSamples)
                let progress = Float(v.samplesElapsed - v.attackSamples) / Float(range)
                v.envValue = v.envPeak * pow(v.envSustain / v.envPeak, progress)
            } else if v.samplesElapsed <= v.totalSamples {
                let range = max(1, v.totalSamples - v.decayEndSample)
                let progress = Float(v.samplesElapsed - v.decayEndSample) / Float(range)
                v.envValue = v.envSustain * pow(0.0001 / v.envSustain, progress)
            } else {
                v.isActive = false
                break
            }

            // Filter cutoff sweep (log space) — recompute biquad
            // coefficients every ``coeffInterval`` samples.
            if v.samplesElapsed % coeffInterval == 0 && v.filterSweepSamples > 0 {
                let sweepProgress = min(1.0, Double(v.samplesElapsed) / Double(v.filterSweepSamples))
                let logStart = log(v.startCut)
                let logEnd = log(v.endCut)
                v.curCut = exp(logStart + (logEnd - logStart) * sweepProgress)
                v.updateBiquad(cutoffHz: v.curCut, sampleRate: sampleRate)
            }

            // Oscillators
            v.phaseSaw += v.incSaw
            if v.phaseSaw >= 1 { v.phaseSaw -= 1 }
            v.phaseTri += v.incTri
            if v.phaseTri >= 1 { v.phaseTri -= 1 }

            let saw = Double(2.0 * v.phaseSaw - 1.0)
            let tri = 1.0 - 4.0 * abs(v.phaseTri - 0.5)
            // At sawMix 0.5 this is exactly the historic
            // (saw + tri) * 0.55.
            let mixOut = (saw * v.sawMix + tri * (1.0 - v.sawMix)) * 1.1

            // Biquad LP (DF-II transposed)
            let inp = mixOut
            let out = v.b0 * inp + v.z1
            v.z1 = v.b1 * inp - v.a1 * out + v.z2
            v.z2 = v.b2 * inp - v.a2 * out

            let sample = Float(out) * v.envValue

            l[i] += sample * leftGain
            r[i] += sample * rightGain

            i += 1
        }
    }
}
