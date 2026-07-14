// DesktopSynthNode.swift
//
// AVAudioSourceNode host for ToneForgeEngine.WavetableSynth on the
// desktop graph (iOS parity P5) — port of the mobile
// WavetableSynthNode hosting pattern:
//
//   sourceNode → musicBus.input  (so master FX color the synth)
//
// The synth is created at attach time at the output node's sample
// rate, so no SRC sits between the oscillators and the mix. render()
// OVERWRITES the output buffers, so the render closure hands it the
// ABL pointers directly.
//
// Device-flap contract: a ConnectCore graph rebuild can change the
// canonical rate, so `reattach()` detaches and rebuilds the synth at
// the new rate. noteOn/noteOff/setParams are lock-queued inside
// WavetableSynth and safe from any thread; this wrapper stays
// @MainActor like every other node owner.

import Foundation
import AVFoundation
import ToneForgeEngine

@MainActor
public final class DesktopSynthNode {

    public private(set) var params = WavetableSynthParams()

    private let avEngine: AVAudioEngine
    private var sourceNode: AVAudioSourceNode?
    /// Built at attach time so it matches the live sample rate.
    private var synth: WavetableSynth?
    /// Remembered so reattach() can rewire after a device flap.
    private weak var output: AVAudioNode?

    public init(avEngine: AVAudioEngine) {
        self.avEngine = avEngine
    }

    // MARK: - Attach / reattach

    /// Attach the source node feeding `output` (musicBus.input).
    /// Idempotent while attached to the same output.
    public func attach(to output: AVAudioNode) {
        if sourceNode != nil, self.output === output { return }
        detach()
        self.output = output

        let mixRate = avEngine.mainMixerNode.outputFormat(forBus: 0).sampleRate
        let sampleRate = mixRate > 0 ? mixRate : 48_000
        guard let format = AVAudioFormat(
            standardFormatWithSampleRate: sampleRate, channels: 2
        ) else { return }

        let synth = WavetableSynth(sampleRate: sampleRate)
        synth.setParams(params)

        let source = AVAudioSourceNode(format: format) {
            [synth] _, _, frameCount, audioBufferList in
            let abl = UnsafeMutableAudioBufferListPointer(audioBufferList)
            let frames = Int(frameCount)
            guard let left = abl[0].mData?.assumingMemoryBound(to: Float.self)
            else { return noErr }
            // Mono fallback: render both channels into the same buffer.
            let right = abl.count > 1
                ? (abl[1].mData?.assumingMemoryBound(to: Float.self) ?? left)
                : left
            synth.render(left: left, right: right, frames: frames)
            return noErr
        }

        avEngine.attach(source)
        avEngine.connect(source, to: output, format: format)
        sourceNode = source
        self.synth = synth
    }

    /// Rebuild after a ConnectCore graph rebuild (the rate may have
    /// changed, so the synth is recreated; params carry over, held
    /// notes are dropped — same as the stem players).
    public func reattach() {
        guard let output else { return }
        detach(keepOutput: true)
        attach(to: output)
    }

    public func detach() { detach(keepOutput: false) }

    private func detach(keepOutput: Bool) {
        if let source = sourceNode { avEngine.detach(source) }
        sourceNode = nil
        synth = nil
        if !keepOutput { output = nil }
    }

    // MARK: - Playing

    public func noteOn(midi: Int, velocity: Float) {
        synth?.noteOn(midi: midi, velocity: max(0, min(1, velocity)))
    }

    public func noteOff(midi: Int) {
        synth?.noteOff(midi: midi)
    }

    public func allNotesOff() {
        synth?.allNotesOff()
    }

    /// One-shot chord for the sequencer's .synthChord steps: voice the
    /// symbol, hold ~sustainSeconds, release. No-op pre-attach.
    public func playChord(
        symbol: String,
        octaveShift: Int = 0,
        velocity: Float = 1,
        sustainSeconds: Double = 0.35
    ) {
        guard synth != nil else { return }
        let notes = ChordVoicing.midiNotes(
            symbol: symbol, octaveShift: octaveShift, baseMidi: 48)
        guard !notes.isEmpty else { return }
        for midi in notes { noteOn(midi: midi, velocity: velocity) }
        let held = notes
        DispatchQueue.main.asyncAfter(deadline: .now() + sustainSeconds) {
            [weak self] in
            for midi in held { self?.noteOff(midi: midi) }
        }
    }

    // MARK: - Parameters

    /// Kept locally so a device-flap rebuild restores the same sound.
    public func update(params: WavetableSynthParams) {
        self.params = params
        synth?.setParams(params)
    }
}
