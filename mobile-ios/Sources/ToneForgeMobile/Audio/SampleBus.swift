// SampleBus.swift
//
// Sub-mixer for the SampleVoicePool. In the v2 topology (D-013) the
// per-bus reverb branch is gone — one shared AVAudioUnitReverb lives
// on AudioEngine's sharedBus, fed by voice, chop and vocoder buses
// alike. This bus is now a thin fan-in point:
//
//   SampleVoicePool voices ──> voiceMixer ──> destination
//                                            (engine.chopBusInput)
//
// `voiceMixer` is public because SampleVoicePool's per-voice mixer
// nodes need somewhere to fan into. `destination` is passed at
// attach time (AudioEngine's chop sub-mixer). Level control lives on
// the chop bus itself (`chopGainLinear` → `AudioEngine.setChopGain`);
// voiceMixer stays at unity.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Sample playback bus: fan-in mixer for the SampleVoicePool.
@MainActor
public final class SampleBus: ObservableObject {

    #if canImport(AVFoundation)
    /// Public input the voice pool fans into. Attached to the engine
    /// on `attach()`. Fixed at unity gain — the user-facing level is
    /// the chop bus downstream.
    public private(set) var voiceMixer: AVAudioMixerNode?
    #endif

    private weak var engine: AudioEngine?
    private var isAttached: Bool = false

    public init(engine: AudioEngine) {
        self.engine = engine
    }

    // MARK: - Attach

    /// Attach the bus to the engine graph, connecting the mixer to
    /// `destination`. Safe to call multiple times — no-op when
    /// already attached. If `destination` needs to change, call
    /// `detach()` and re-attach.
    #if canImport(AVFoundation)
    public func attach(destination: AVAudioNode) {
        guard let engine = engine, !isAttached else { return }
        // Canonical 48 kHz stereo (D-017) — matches the contribution
        // graph so the single resample point stays at scheduler ingest.
        let format = engine.canonicalFormat

        let vmix = AVAudioMixerNode()
        engine.engine.attach(vmix)
        // AVAudioMixerNode auto-picks a free input bus per connect, so
        // summing this alongside other sources into `destination` is
        // safe. (The historical dry/wet fan-OUT trap now lives inside
        // AudioEngine.buildContributionGraph — see the note there.)
        engine.engine.connect(vmix, to: destination, format: format)
        vmix.outputVolume = 1.0

        self.voiceMixer = vmix
        self.isAttached = true
    }
    #endif

    /// Detach + free the mixer node. The voice pool must be detached
    /// first — this bus does not know about pool voices.
    public func detach() {
        #if canImport(AVFoundation)
        guard let engine = engine, isAttached else { return }
        if let vmix = voiceMixer {
            engine.engine.detach(vmix)
        }
        voiceMixer = nil
        isAttached = false
        #endif
    }
}
