// PerformanceFXChain.swift
//
// Live controller for the performance-FX master insert (PERFORM_PARITY
// spec 1). Sits between mainMixer and the studio EQ:
//
//   mainMixer → perfInput → filter → flanger → throw → gater → masterEQ
//
// AudioEngine (app target) creates + attaches + wires the AVAudioNodes
// on the shared engine — all graph plumbing stays there per its own
// contract — then hands the node references here via `bind`. This class
// owns only the LIVE control: translating PerfFXState + PerfFXConfig +
// BeatClock into node parameters, on touch (`applyStatic`) and per
// modulation tick (`applyModulation`).
//
// It lives in ToneForgeEngine (not the app target) so the beat-synced
// intent math is unit-tested on macOS: the `*Intent` methods are pure
// and return nil when the effect is disengaged or timing is missing;
// the node-touching apply methods are thin wrappers over them.
//
// Stopper is not a node — it eases the transport rate to zero and fades
// gain. AudioEngine supplies `rateSink` (→ TransportClock.setRate) and
// the chain fades perfInput gain in tandem; release restores both.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

@MainActor
public final class PerformanceFXChain {

    // MARK: - Config + live state

    public var config: PerfFXConfig = .default
    public private(set) var state: PerfFXState = .idle

    /// Beat grid for tempo-synced gater / flanger / throw / stopper.
    /// Rebuilt by AudioEngine whenever a song activates.
    public var beatClock: BeatClock = BeatClock()

    // MARK: - Sinks (supplied by AudioEngine)

    /// Push a playback-rate multiplier (stopper). AudioEngine wires this
    /// to TransportClock.setRate.
    public var rateSink: ((Double) -> Void)?

    // MARK: - Bound nodes (nil in headless tests)

    #if canImport(AVFoundation)
    private weak var input: AVAudioMixerNode?
    private weak var filter: AVAudioUnitEQ?
    private weak var flanger: AVAudioUnitDelay?
    private weak var throwDelay: AVAudioUnitDelay?
    private weak var gater: AVAudioMixerNode?

    /// Wire the chain to the graph nodes AudioEngine built. The filter
    /// EQ must have at least one band.
    public func bind(
        input: AVAudioMixerNode,
        filter: AVAudioUnitEQ,
        flanger: AVAudioUnitDelay,
        throwDelay: AVAudioUnitDelay,
        gater: AVAudioMixerNode
    ) {
        self.input = input
        self.filter = filter
        self.flanger = flanger
        self.throwDelay = throwDelay
        self.gater = gater
        applyStatic()
    }
    #endif

    public init() {}

    // MARK: - State entry points

    /// Replace the live state (from touch / hardware). Applies the
    /// non-modulated params immediately; the modulation driver handles
    /// the rest per tick.
    public func setState(_ new: PerfFXState, now: Double) {
        let wasStopper = state.stopper
        state = new
        // Stopper engage/release edges: stamp / clear the brake origin.
        if new.stopper && !wasStopper {
            stopperEngagedAt = now
        } else if !new.stopper && wasStopper {
            stopperEngagedAt = nil
            rateSink?(1.0)     // snap transport back to full speed
        }
        applyStatic()
    }

    private var stopperEngagedAt: Double?

    // MARK: - Pure intent (unit-tested)

    /// Gater gain (0..1) at song-time `now`, or nil when disengaged /
    /// no beat grid. Cell length = `subdivisionBeats`.
    public func gaterGainIntent(now: Double) -> Double? {
        guard state.gater, let beat = beatClock.beatDuration else { return nil }
        let cellSec = config.gater.clamped().subdivisionBeats * beat
        guard cellSec > 0 else { return nil }
        // Anchor cells on the first beat marker so gate lines up with
        // the groove, not song-zero.
        let anchor = beatClock.beats.first ?? 0
        let cellPhase = ((now - anchor) / cellSec)
        return config.gater.gain(cellPhase: cellPhase)
    }

    /// Flanger delay in ms at song-time `now`, or nil when disengaged /
    /// no beat grid. LFO period = `rateBeats`.
    public func flangerDelayMsIntent(now: Double) -> Double? {
        guard state.flanger, let beat = beatClock.beatDuration else { return nil }
        let periodSec = config.flanger.clamped().rateBeats * beat
        guard periodSec > 0 else { return nil }
        let lfoPhase = (now / periodSec).truncatingRemainder(dividingBy: 1)
        return config.flanger.delayMs(lfoPhase: lfoPhase < 0 ? lfoPhase + 1 : lfoPhase)
    }

    /// Stopper rate multiplier (1 → 0) at song-time `now`, or nil when
    /// disengaged / no beat grid.
    public func stopperRateIntent(now: Double) -> Double? {
        guard state.stopper, let engagedAt = stopperEngagedAt,
              let beat = beatClock.beatDuration else { return nil }
        return config.stopper.rate(elapsedSec: now - engagedAt, beatDuration: beat)
    }

    /// Throw echo time in seconds, or nil when disengaged / no grid.
    public func throwTimeSecIntent() -> Double? {
        guard state.delayThrow, let beat = beatClock.beatDuration else { return nil }
        return config.delayThrow.timeSec(beatDuration: beat)
    }

    // MARK: - Node application

    /// Push the non-time-varying params: filter cutoff/reso/bypass,
    /// flanger/throw enable + feedback + time. Safe with no nodes bound.
    public func applyStatic() {
        #if canImport(AVFoundation)
        // Filter. Bypass the whole EQ unit (not just the band) when
        // disengaged so it costs nothing in the idle master path — a
        // wetDryMix/band-bypass alone still runs the AU every buffer,
        // which needlessly eats headroom (audible as stutter on the sim).
        if let filter {
            filter.bypass = !state.filter
            if state.filter, let band = filter.bands.first {
                let fc = config.filter.clamped()
                band.bypass = false
                band.filterType = (fc.type == .lowPass) ? .resonantLowPass : .resonantHighPass
                band.frequency = Float(fc.cutoffHz(x: state.filterX))
                // Higher resonance → narrower bandwidth (sharper peak).
                let y = max(0, min(1, state.filterY))
                band.bandwidth = Float(max(0.05, 1.0 - 0.9 * y))
                band.gain = Float(fc.resonanceDb(y: y))
            }
        }
        // Flanger: bypass the AU when off; feedback static, delay modulated.
        if let fl = flanger {
            fl.bypass = !state.flanger
            let fc = config.flanger.clamped()
            fl.feedback = Float(fc.feedback * 100)
            fl.wetDryMix = state.flanger ? 50 : 0
        }
        // Throw: bypass the AU when off; tempo-synced time + feedback.
        if let th = throwDelay {
            th.bypass = !state.delayThrow
            let dc = config.delayThrow.clamped()
            th.feedback = Float(dc.feedback * 100)
            if let t = throwTimeSecIntent() { th.delayTime = t }
            th.wetDryMix = state.delayThrow ? 50 : 0
        }
        // Gater passthrough when disengaged.
        if !state.gater { gater?.outputVolume = 1.0 }
        #endif
    }

    /// Push the time-varying params for one driver tick at song-time
    /// `now`: gater gain, flanger LFO delay, stopper rate + gain fade.
    /// No-op past the effects that are disengaged.
    public func applyModulation(now: Double) {
        #if canImport(AVFoundation)
        if let g = gaterGainIntent(now: now) {
            gater?.outputVolume = Float(g)
        }
        if let ms = flangerDelayMsIntent(now: now) {
            flanger?.delayTime = ms / 1000.0
        }
        if let rate = stopperRateIntent(now: now) {
            rateSink?(max(0.0001, rate))       // rate 0 would freeze the clock hard
            input?.outputVolume = Float(rate)  // fade out in tandem with the brake
        } else if !state.stopper {
            input?.outputVolume = 1.0
        }
        #endif
    }

    /// True when the modulation driver has work to do — lets AudioEngine
    /// idle the CADisplayLink when nothing is engaged.
    public var needsModulation: Bool {
        state.gater || state.flanger || state.stopper
    }
}
