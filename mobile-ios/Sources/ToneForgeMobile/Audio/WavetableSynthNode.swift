// WavetableSynthNode.swift
//
// AVAudioSourceNode host for ToneForgeEngine.WavetableSynth — the
// hybrid-mode note-row instrument (D-013 topology):
//
//   sourceNode → engine.voiceBusInput (voiceBus → sharedBus)
//
// The synth renders at the canonical 48 kHz stereo format (D-017), so
// no SRC sits between the oscillators and the output. Reverb is the
// shared sharedBus branch — this node is a dry source, same as
// PadSynth.
//
// Threading: WavetableSynth is @unchecked Sendable with its own
// NSLock-guarded pending queue; noteOn/noteOff/setParams are callable
// from any thread and applied at the next render block. render()
// OVERWRITES the output buffers (it zeroes internally), so the render
// closure hands it the ABL pointers directly with no host-side
// clearing.

import Foundation
import ToneForgeEngine
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Hosts the engine-side wavetable synth on the mobile audio graph.
@MainActor
public final class WavetableSynthNode: ObservableObject {

    // MARK: - Public

    @Published public private(set) var params = WavetableSynthParams()

    // MARK: - Private

    #if canImport(AVFoundation)
    private var sourceNode: AVAudioSourceNode?
    #endif

    /// Created at attach time so it can be built at the canonical
    /// sample rate. Nil until attached.
    private var synth: WavetableSynth?

    private weak var engine: AudioEngine?

    public init(engine: AudioEngine) {
        self.engine = engine
    }

    // MARK: - Attach / detach

    /// Attach the source node to the engine graph. Idempotent.
    public func attach() {
        #if canImport(AVFoundation)
        guard let engine = engine, sourceNode == nil else { return }
        // Canonical 48 kHz stereo (D-017) — matches the contribution
        // graph so no SRC sits between the synth and the output.
        let format = engine.canonicalFormat

        let synth = WavetableSynth(sampleRate: format.sampleRate)
        synth.setParams(params)

        let source = AVAudioSourceNode(format: format) { [synth] _, _, frameCount, audioBufferList in
            let abl = UnsafeMutableAudioBufferListPointer(audioBufferList)
            let frames = Int(frameCount)
            guard let left = abl[0].mData?.assumingMemoryBound(to: Float.self) else {
                return noErr
            }
            // Mono fallback: render both channels into the same buffer
            // (render overwrites left first, then right — identical
            // content either way since the synth is centre-panned).
            let right = abl.count > 1
                ? (abl[1].mData?.assumingMemoryBound(to: Float.self) ?? left)
                : left
            synth.render(left: left, right: right, frames: frames)
            return noErr
        }

        engine.engine.attach(source)
        // Feed the voice sub-mixer, not mainMixer, so the "Your Layer"
        // fader (sharedBus downstream) controls hybrid-mode notes too.
        engine.engine.connect(source, to: engine.voiceBusInput, format: format)

        self.sourceNode = source
        self.synth = synth
        #endif
    }

    /// Detach and free the node. Called on scene teardown.
    public func detach() {
        #if canImport(AVFoundation)
        guard let engine = engine, let source = sourceNode else { return }
        engine.engine.detach(source)
        sourceNode = nil
        synth = nil
        #endif
    }

    // MARK: - Playing

    public func noteOn(midi: Int, velocity: Double) {
        synth?.noteOn(midi: midi, velocity: Float(max(0, min(1, velocity))))
    }

    public func noteOff(midi: Int) {
        synth?.noteOff(midi: midi)
    }

    public func allNotesOff() {
        synth?.allNotesOff()
    }

    // MARK: - Parameters

    /// Replace live params. Applied atomically at the next render
    /// block; also kept locally so a detach/re-attach cycle restores
    /// the same sound.
    public func update(params: WavetableSynthParams) {
        self.params = params
        synth?.setParams(params)
    }
}
