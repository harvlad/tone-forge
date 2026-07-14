// MusicBus.swift
//
// Shared submix for all MUSICAL sources (stems, chop voices, later
// sequencer/synth/packs) with the D-022 master FX chain, ported from
// the iOS AudioEngine master FX graph. Sits between the sources and
// ConnectCore's mainMixerNode so master FX color the music but never
// the monitor/guitar path:
//
//   sources → input(mixer) ─→ masterEQ → masterComp ─→ mainMixer
//                        └──→ fxSend → reverb → delay → fxReturn ─→ mainMixer
//
// Parameter mapping is 1:1 with iOS AudioEngine.setFXSettings /
// buildMasterFXGraph (FXSettings value ranges match the AU
// conventions, so no scaling maths here).
//
// Device-flap contract: nodes stay attached across a ConnectCore
// graph rebuild but their connections drop — EngineController calls
// `reattach()` from handleGraphRebuilt.

import Foundation
import AVFoundation
import AudioToolbox
import ToneForgeEngine

@MainActor
public final class MusicBus {

    private let avEngine: AVAudioEngine

    /// The mixer musical sources connect into.
    public let input = AVAudioMixerNode()

    private let eq = AVAudioUnitEQ(numberOfBands: 3)
    private let comp: AVAudioUnitEffect
    private let fxSend = AVAudioMixerNode()
    private let reverb = AVAudioUnitReverb()
    private let delay = AVAudioUnitDelay()
    private let fxReturn = AVAudioMixerNode()

    public private(set) var settings: FXSettings = .neutral

    public init(avEngine: AVAudioEngine) {
        self.avEngine = avEngine

        let compDesc = AudioComponentDescription(
            componentType: kAudioUnitType_Effect,
            componentSubType: kAudioUnitSubType_DynamicsProcessor,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        comp = AVAudioUnitEffect(audioComponentDescription: compDesc)

        // Band layout mirrors iOS buildMasterFXGraph.
        eq.bypass = false
        eq.bands[0].filterType = .lowShelf
        eq.bands[0].frequency = 200
        eq.bands[0].gain = 0
        eq.bands[0].bypass = false
        eq.bands[1].filterType = .parametric
        eq.bands[1].frequency = 1000
        eq.bands[1].bandwidth = 1.0
        eq.bands[1].gain = 0
        eq.bands[1].bypass = false
        eq.bands[2].filterType = .highShelf
        eq.bands[2].frequency = 6000
        eq.bands[2].gain = 0
        eq.bands[2].bypass = false
    }

    // MARK: - Graph lifecycle

    /// Attach all nodes and run the connect wiring. Idempotent —
    /// attach on an already-attached node is a no-op, and connect
    /// replaces existing wiring.
    public func attach() {
        for node in [input, eq, comp, fxSend, reverb, delay, fxReturn]
            as [AVAudioNode] {
            if node.engine == nil { avEngine.attach(node) }
        }
        wire()
        apply(settings)
    }

    /// Re-run the connect wiring after ConnectCore rebuilds its graph
    /// (device flap). Nodes stay attached; connections drop.
    public func reattach() {
        wire()
        apply(settings)
    }

    private func wire() {
        let main = avEngine.mainMixerNode
        // Fan the bus out to both the insert chain and the FX send.
        avEngine.connect(
            input,
            to: [
                AVAudioConnectionPoint(node: eq, bus: 0),
                AVAudioConnectionPoint(node: fxSend, bus: 0),
            ],
            fromBus: 0,
            format: nil
        )
        avEngine.connect(eq, to: comp, format: nil)
        avEngine.connect(comp, to: main, format: nil)
        avEngine.connect(fxSend, to: reverb, format: nil)
        avEngine.connect(reverb, to: delay, format: nil)
        avEngine.connect(delay, to: fxReturn, format: nil)
        avEngine.connect(fxReturn, to: main, format: nil)
    }

    // MARK: - FX parameters (mirror of iOS setFXSettings)

    /// Push new FX settings into the graph. Params only — never
    /// mutates topology.
    public func apply(_ newSettings: FXSettings) {
        let s = newSettings.clamped()
        settings = s

        // EQ
        let e = s.eq
        eq.bands[0].frequency = Float(e.lowFreq)
        eq.bands[0].gain = Float(e.lowGainDb)
        eq.bands[1].frequency = Float(e.midFreq)
        eq.bands[1].gain = Float(e.midGainDb)
        eq.bands[2].frequency = Float(e.highFreq)
        eq.bands[2].gain = Float(e.highGainDb)

        // Compressor (kAudioUnitSubType_DynamicsProcessor params:
        // 0 threshold, 1 headroom, 4 attack, 5 release, 6 overall gain).
        let c = s.comp
        let au = comp.audioUnit
        AudioUnitSetParameter(
            au, 0, kAudioUnitScope_Global, 0, Float(c.thresholdDb), 0)
        AudioUnitSetParameter(
            au, 1, kAudioUnitScope_Global, 0, Float(c.amountDb), 0)
        AudioUnitSetParameter(
            au, 4, kAudioUnitScope_Global, 0, Float(c.attackMs / 1000), 0)
        AudioUnitSetParameter(
            au, 5, kAudioUnitScope_Global, 0, Float(c.releaseMs / 1000), 0)
        AudioUnitSetParameter(
            au, 6, kAudioUnitScope_Global, 0, Float(c.makeupDb), 0)

        // Reverb: fully wet in the send chain; audibility rides the
        // send gain (max of reverb/delay mix, iOS parity).
        reverb.loadFactoryPreset(Self.presetForSeconds(s.reverb.sizeSeconds))
        reverb.wetDryMix = 100

        // Delay blends within the wet chain.
        delay.delayTime = s.delay.timeSec
        delay.feedback = Float(s.delay.feedback)
        delay.wetDryMix = Float(s.delay.mix)

        fxSend.outputVolume = Float(max(s.reverb.mix, s.delay.mix) / 100)

        // Doubling guard: return silent when both wet FX are off.
        let wetActive = !s.reverb.isNeutral || !s.delay.isNeutral
        fxReturn.outputVolume = wetActive
            ? Self.linearFromDb(Float(s.fxReturnDb)) : 0
    }

    /// Same discrete preset picker as iOS AudioEngine.presetForSeconds.
    nonisolated static func presetForSeconds(_ seconds: Double) -> AVAudioUnitReverbPreset {
        switch seconds {
        case ..<0.8: return .smallRoom
        case ..<1.4: return .mediumRoom
        case ..<2.2: return .largeRoom
        case ..<2.8: return .mediumHall
        case ..<3.6: return .largeHall
        default: return .cathedral
        }
    }

    nonisolated static func linearFromDb(_ db: Float) -> Float {
        pow(10, db / 20)
    }
}
