// SampleVoicePool.swift
//
// Fixed pool of AVAudioPlayerNode + per-voice AVAudioMixerNode slots
// backing every sample trigger the mobile app makes. All slots are
// pre-attached to the engine at boot time so first-tap latency is
// bounded by the engine's output buffer, not by node attachment.
//
// Slot count: 32. Comfortably covers a 4×4 grid with tails overlapping
// across chords + a couple of held loops running underneath.
//
// Allocation policy:
//   1. Prefer an inactive slot.
//   2. If all 32 are active, evict the slot with the oldest
//      `startedAtHostTime` (LRU).
//   3. Voices sharing a choke group cancel prior voices in that group
//      before allocation — so a pad tagged "hats" replaces its own
//      last hit, avoiding buildup.
//
// Fades:
//   - Attack: samples are assumed pre-shaped; the pool does not
//     synthesise an attack ramp on the mixer. This keeps the trigger
//     path allocation-free.
//   - Release: a 20 ms linear ramp on the per-voice mixer, driven from
//     a UI-thread Task. Sub-perceptual for click prevention; more than
//     precise enough for hold/toggle semantics.
//
// Toggle-mode support:
//   The pool tracks `padKey → active slot indices` so the UI layer
//   can ask "is this pad already playing?" for its toggle-tap logic.

import Foundation
import ToneForgeEngine
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Identifies a specific pad within a specific pack — the composite
/// key toggle-mode uses to answer "is this pad already looping?".
public struct SamplePadKey: Hashable, Sendable {
    public let packId: String
    public let padIdx: Int
    public init(packId: String, padIdx: Int) {
        self.packId = packId
        self.padIdx = padIdx
    }
}

/// Trigger request assembled by the SampleScheduler and handed to the
/// pool at the sample-accurate call boundary.
public struct SampleTrigger: Sendable {
    /// Which pad this trigger is for. Written into the slot so
    /// `release(padKey:)` can find and stop it.
    public let padKey: SamplePadKey
    /// Whether the voice should loop (toggle-mode with loop point) or
    /// play one-shot (hold-mode + toggle-mode when loopPointSec is nil).
    public let loop: Bool
    /// Optional choke group. All active voices in this group are
    /// released before allocation. nil = no choke.
    public let chokeGroup: Int?
    /// Voice gain in dB, applied to the per-voice mixer.
    public let gainDb: Double
    /// Stereo pan (-1 hard left … +1 hard right), applied to the
    /// per-voice mixer. Clamped inside the pool.
    public let pan: Float
    /// Per-pad realtime effect params (delay + resonant lowpass).
    /// Applied to the per-voice AVAudioUnitDelay + AVAudioUnitEQ on
    /// allocation; values are clamped inside the pool.
    public let effects: SamplePadEffects

    public init(
        padKey: SamplePadKey,
        loop: Bool,
        chokeGroup: Int?,
        gainDb: Double,
        pan: Float = 0,
        effects: SamplePadEffects = .neutral
    ) {
        self.padKey = padKey
        self.loop = loop
        self.chokeGroup = chokeGroup
        self.gainDb = gainDb
        self.pan = pan
        self.effects = effects
    }
}

@MainActor
public final class SampleVoicePool: ObservableObject {

    /// Fixed slot count. See file header for rationale.
    public static let voiceCount = 32

    /// Release-fade duration (linear ramp on per-voice mixer volume).
    public static let releaseFadeSec: Double = 0.020

    /// Pads with a currently-ringing *looping* voice, across all
    /// packs. Drives the "active pad" indicator on the pad grids.
    /// Looping voices only change state through pool methods (they
    /// never self-terminate), so event-driven recomputes are exact.
    /// One-shots are deliberately excluded — their slots stay
    /// `isActive` after the buffer ends (no completion handler), so
    /// they'd read as ringing forever.
    @Published public private(set) var ringingPadKeys: Set<SamplePadKey> = []

    /// Pads with a launch scheduled for a future (quantized) time that
    /// hasn't fired yet — the "armed"/queued state (blinking clip on a
    /// Launchpad). Cleared when the play fires or is cancelled.
    @Published public private(set) var pendingPadKeys: Set<SamplePadKey> = []

    #if canImport(AVFoundation)
    /// Slots are struct-wrapped so mutation stays value-typed; the
    /// nodes inside are reference types owned by the pool for the
    /// lifetime of the app.
    ///
    /// Per-voice signal path (post-Phase 6d):
    ///   player → delay → eq → mixer → sampleBus.voiceMixer
    /// The delay + eq nodes stay in-place across triggers; the pool
    /// just reprograms their params on `trigger(...)` to match the
    /// pad's effective SamplePadEffects. Idle voices leave delay at
    /// mix=0 and eq bypassed so an inactive slot renders zero cost.
    private struct Slot {
        var player: AVAudioPlayerNode
        var delay: AVAudioUnitDelay
        var eq: AVAudioUnitEQ
        var mixer: AVAudioMixerNode
        var padKey: SamplePadKey?
        var chokeGroup: Int?
        var isActive: Bool
        var isLooping: Bool
        var startedAtHostTime: UInt64
        var fadeTask: Task<Void, Never>?
        /// The DispatchWorkItem for a future-time `player.play()` call.
        /// Non-nil between allocation and the moment the workitem
        /// actually fires (which flips it back to nil). Kept so
        /// `releaseSlot` can cancel a still-pending play if the user
        /// lifts their finger before the quantize target — otherwise
        /// the deferred play() lands on an already-stopped player with
        /// a muted mixer and produces silence.
        var pendingPlayItem: DispatchWorkItem?
    }

    private var slots: [Slot] = []
    #endif

    private weak var engine: AudioEngine?
    private weak var bus: SampleBus?
    private var isAttached: Bool = false

    public init(engine: AudioEngine, bus: SampleBus) {
        self.engine = engine
        self.bus = bus
    }

    // MARK: - Attach

    /// Attach all N slots to the engine and connect them into the
    /// SampleBus voice-mixer input. Safe to call more than once —
    /// no-op when already attached.
    public func attach() {
        #if canImport(AVFoundation)
        guard let engine = engine, let bus = bus, let voiceInput = bus.voiceMixer,
              !isAttached else { return }
        // Canonical 48 kHz stereo (D-017) — matches the buffers the
        // scheduler produces at its single ingest resample point.
        let format = engine.canonicalFormat

        var built: [Slot] = []
        built.reserveCapacity(Self.voiceCount)
        for _ in 0..<Self.voiceCount {
            let player = AVAudioPlayerNode()
            let delay = AVAudioUnitDelay()
            // Configure delay to neutral so idle voices are inaudible:
            // wetDryMix=0 mutes the delay tap; feedback=0 stops any
            // buildup. Real params get pushed on trigger.
            delay.wetDryMix = 0
            delay.feedback = 0
            delay.delayTime = SamplePadEffects.neutral.delayTimeSec

            // Single-band EQ used as the pad's resonant lowpass. Left
            // bypassed at attach; enabled on trigger when the pad's
            // effective cutoff is below full audible range.
            let eq = AVAudioUnitEQ(numberOfBands: 1)
            let band = eq.bands[0]
            band.filterType = .resonantLowPass
            band.frequency = Float(SamplePadEffects.neutral.filterCutoffHz)
            band.bandwidth = Float(SamplePadEffects.neutral.filterResonanceDb)
            band.bypass = true

            let mixer = AVAudioMixerNode()

            engine.engine.attach(player)
            engine.engine.attach(delay)
            engine.engine.attach(eq)
            engine.engine.attach(mixer)
            engine.engine.connect(player, to: delay, format: format)
            engine.engine.connect(delay, to: eq, format: format)
            engine.engine.connect(eq, to: mixer, format: format)
            engine.engine.connect(mixer, to: voiceInput, format: format)
            mixer.outputVolume = 0
            built.append(Slot(
                player: player,
                delay: delay,
                eq: eq,
                mixer: mixer,
                padKey: nil,
                chokeGroup: nil,
                isActive: false,
                isLooping: false,
                startedAtHostTime: 0,
                fadeTask: nil,
                pendingPlayItem: nil
            ))
        }
        self.slots = built
        self.isAttached = true
        #endif
    }

    public func detach() {
        #if canImport(AVFoundation)
        guard let engine = engine, isAttached else { return }
        for slot in slots {
            slot.fadeTask?.cancel()
            slot.player.stop()
            engine.engine.detach(slot.player)
            engine.engine.detach(slot.delay)
            engine.engine.detach(slot.eq)
            engine.engine.detach(slot.mixer)
        }
        slots.removeAll()
        isAttached = false
        refreshRingingPadKeys()
        #endif
    }

    // MARK: - Trigger

    /// Fire a sample. If `at` is nil, plays immediately; otherwise the
    /// player node starts at the given AVAudioTime for sample-accurate
    /// timing (used by the SampleScheduler's song-time→AVAudioTime
    /// conversion).
    ///
    /// - Returns: the slot index used, or nil if the pool wasn't
    ///   attached (e.g. still booting).
    #if canImport(AVFoundation)
    @discardableResult
    public func trigger(
        _ req: SampleTrigger,
        buffer: AVAudioPCMBuffer,
        at time: AVAudioTime? = nil
    ) -> Int? {
        guard isAttached else { return nil }

        // Choke pass: any active slot sharing the choke group is
        // released before allocation. Scoped to the triggering pack —
        // choke groups are plain Ints in each pack's manifest, so
        // pack A's group 1 ("hats") must not silence pack B's
        // unrelated group 1 when both packs ring simultaneously
        // (multi-pack carousel).
        if let group = req.chokeGroup {
            for i in slots.indices
            where slots[i].isActive
                && slots[i].chokeGroup == group
                && slots[i].padKey?.packId == req.padKey.packId {
                releaseSlot(i)
            }
        }

        let idx = allocate()
        var slot = slots[idx]

        // Cancel any pending fade on this slot (LRU-stole a fading
        // voice, or reactivating a just-released one). Also cancel a
        // still-pending future-play dispatch — the slot is being
        // repurposed, the previous trigger's deferred play() must not
        // fire against the new buffer.
        slot.fadeTask?.cancel()
        slot.fadeTask = nil
        slot.pendingPlayItem?.cancel()
        slot.pendingPlayItem = nil
        slot.player.stop()

        slot.padKey = req.padKey
        slot.chokeGroup = req.chokeGroup
        slot.isActive = true
        slot.isLooping = req.loop
        slot.startedAtHostTime = mach_absolute_time()

        let options: AVAudioPlayerNodeBufferOptions = req.loop
            ? [.interrupts, .loops]
            : [.interrupts]

        // Voice gain: dB → linear, clamped to [0, 2].
        let linear = Float(pow(10.0, req.gainDb / 20.0))
        slot.mixer.outputVolume = max(0, min(2, linear))
        slot.mixer.pan = max(-1, min(1, req.pan))

        // Apply per-pad effects onto this slot's delay + eq. Clamp
        // once so a stale persisted override can't push
        // AVAudioUnitDelay.delayTime out of its valid range.
        applyEffects(req.effects.clamped(), to: slot)

        // Schedule the buffer for immediate playback in the player's
        // own timeline.
        slot.player.scheduleBuffer(buffer, at: nil, options: options, completionHandler: nil)

        // Future-time gating. Calling `play(at: AVAudioTime(hostTime:))`
        // directly throws NSException from AVAudioPlayerNodeImpl::StartImpl
        // when the engine hasn't produced any output yet (fresh boot,
        // first tap). Instead, compute the delay against mach_absolute_time
        // and hand the actual `.play()` call to a high-priority Swift
        // dispatch — precision drops from sample-accurate to ~1 ms,
        // well within the perceptual budget for chord/beat-quantized
        // pad triggers.
        //
        // The dispatch is wrapped in a DispatchWorkItem stored on the
        // slot so that `releaseSlot(_:)` can cancel it if the user
        // lifts their finger before the quantize target. Without this,
        // hold-mode + quantize + a short tap produced silence: the
        // release fade + player.stop() ran ~100 ms into a 400 ms
        // quantize wait, so when the deferred play() finally fired it
        // hit an already-stopped player with a muted mixer.
        if let t = time {
            let nowHost = mach_absolute_time()
            let futureHost = t.hostTime
            let delayTicks: UInt64 = futureHost > nowHost ? (futureHost - nowHost) : 0
            let delaySec = Double(delayTicks) / TransportClock.ticksPerSecond()
            if delaySec > 0.0005 {
                let player = slot.player
                let slotIdx = idx
                let item = DispatchWorkItem { [weak self] in
                    player.play()
                    Task { @MainActor [weak self] in
                        guard let self, self.slots.indices.contains(slotIdx) else { return }
                        self.slots[slotIdx].pendingPlayItem = nil
                        self.refreshRingingPadKeys()  // armed → playing
                    }
                }
                slot.pendingPlayItem = item
                DispatchQueue.global(qos: .userInteractive)
                    .asyncAfter(deadline: .now() + delaySec, execute: item)
            } else {
                slot.player.play()
            }
        } else {
            slot.player.play()
        }

        slots[idx] = slot
        refreshRingingPadKeys()
        return idx
    }
    #endif

    /// Fire a segment of a sample buffer. Used by the trimmer preview to
    /// play only the selected portion. Plays immediately (no quantize).
    /// Creates a slice buffer on the fly — acceptable for UI preview,
    /// not intended for latency-critical performance paths.
    #if canImport(AVFoundation)
    @discardableResult
    public func triggerSegment(
        _ req: SampleTrigger,
        buffer: AVAudioPCMBuffer,
        startFraction: Double,
        endFraction: Double
    ) -> Int? {
        guard isAttached else { return nil }
        guard startFraction < endFraction else { return nil }

        let totalFrames = Int(buffer.frameLength)
        let startFrame = Int(Double(totalFrames) * startFraction)
        let frameCount = Int(Double(totalFrames) * (endFraction - startFraction))
        guard frameCount > 0 else { return nil }

        // Create a sliced buffer containing only the selected frames
        guard let format = buffer.format as AVAudioFormat?,
              let slicedBuffer = AVAudioPCMBuffer(
                pcmFormat: format,
                frameCapacity: AVAudioFrameCount(frameCount)
              )
        else { return nil }

        slicedBuffer.frameLength = AVAudioFrameCount(frameCount)

        // Copy sample data from source to slice
        let channelCount = Int(format.channelCount)
        for ch in 0..<channelCount {
            if let src = buffer.floatChannelData?[ch],
               let dst = slicedBuffer.floatChannelData?[ch] {
                for i in 0..<frameCount {
                    dst[i] = src[startFrame + i]
                }
            }
        }

        // Choke any existing voice for this pad
        for i in slots.indices
        where slots[i].isActive && slots[i].padKey == req.padKey {
            releaseSlot(i)
        }

        let idx = allocate()
        var slot = slots[idx]

        slot.fadeTask?.cancel()
        slot.fadeTask = nil
        slot.pendingPlayItem?.cancel()
        slot.pendingPlayItem = nil
        slot.player.stop()

        slot.padKey = req.padKey
        slot.chokeGroup = nil
        slot.isActive = true
        slot.isLooping = false
        slot.startedAtHostTime = mach_absolute_time()

        let linear = Float(pow(10.0, req.gainDb / 20.0))
        slot.mixer.outputVolume = max(0, min(2, linear))
        slot.mixer.pan = max(-1, min(1, req.pan))
        applyEffects(req.effects.clamped(), to: slot)

        slot.player.scheduleBuffer(slicedBuffer, at: nil, options: [.interrupts], completionHandler: nil)
        slot.player.play()

        slots[idx] = slot
        refreshRingingPadKeys()
        return idx
    }
    #endif

    // MARK: - Release / query

    /// Stop every active voice belonging to `padKey` with a 20 ms
    /// linear release fade. Used for hold-mode touch-up and toggle-
    /// mode second-tap.
    public func release(padKey: SamplePadKey) {
        #if canImport(AVFoundation)
        for i in slots.indices where slots[i].isActive && slots[i].padKey == padKey {
            releaseSlot(i)
        }
        #endif
    }

    /// True iff at least one voice is currently active for `padKey`.
    /// Consulted by SampleScheduler for toggle-mode "already playing?"
    /// decisions.
    public func isActive(padKey: SamplePadKey) -> Bool {
        #if canImport(AVFoundation)
        for slot in slots where slot.isActive && slot.padKey == padKey {
            return true
        }
        #endif
        return false
    }

    /// Fade-and-stop every active voice. Called on song stop / tab
    /// switch / pack change.
    public func stopAll() {
        #if canImport(AVFoundation)
        for i in slots.indices where slots[i].isActive {
            releaseSlot(i)
        }
        #endif
    }

    // MARK: - Private

    #if canImport(AVFoundation)
    /// Return a slot index for a new trigger. Prefers inactive, falls
    /// back to LRU-oldest active. Precondition: `isAttached == true`.
    private func allocate() -> Int {
        // First pass: any inactive slot.
        for i in slots.indices where !slots[i].isActive { return i }
        // LRU steal.
        var oldestIdx = 0
        var oldestT: UInt64 = .max
        for i in slots.indices {
            if slots[i].startedAtHostTime < oldestT {
                oldestT = slots[i].startedAtHostTime
                oldestIdx = i
            }
        }
        // Force-clear the stolen slot so trigger's stop() is a clean
        // handoff rather than a fight with a fade Task.
        var stolen = slots[oldestIdx]
        stolen.fadeTask?.cancel()
        stolen.fadeTask = nil
        stolen.player.stop()
        stolen.isActive = false
        slots[oldestIdx] = stolen
        return oldestIdx
    }

    /// Push the pad's clamped effect params onto the slot's AU nodes.
    /// The filter band is bypassed when the cutoff sits at the top of
    /// its window (20 kHz) to save the DSP cost of an audibly-neutral
    /// biquad — the delay is *not* bypassed at mix=0 because we still
    /// want its tap to be silent (verified: `wetDryMix = 0` on
    /// AVAudioUnitDelay renders bit-identical to the dry signal).
    private func applyEffects(_ fx: SamplePadEffects, to slot: Slot) {
        slot.delay.delayTime = fx.delayTimeSec
        slot.delay.feedback = Float(fx.delayFeedback)
        slot.delay.wetDryMix = Float(fx.delayMix)

        let band = slot.eq.bands[0]
        band.frequency = Float(fx.filterCutoffHz)
        band.bandwidth = Float(fx.filterResonanceDb)
        band.bypass = fx.filterCutoffHz >= 19_999
    }

    /// Recompute the published ringing-loop set from slot truth.
    /// Assigns only on change so SwiftUI isn't poked on every
    /// one-shot trigger.
    private func refreshRingingPadKeys() {
        let now = Set(slots.compactMap { slot in
            slot.isActive && slot.isLooping ? slot.padKey : nil
        })
        if now != ringingPadKeys { ringingPadKeys = now }
        // Armed = a future-time play still pending on the slot.
        let pending = Set(slots.compactMap { slot in
            slot.pendingPlayItem != nil ? slot.padKey : nil
        })
        if pending != pendingPadKeys { pendingPadKeys = pending }
    }

    /// 20 ms linear release fade, then stop the player and mark the
    /// slot inactive.
    private func releaseSlot(_ idx: Int) {
        let startVol = slots[idx].mixer.outputVolume
        // Immediately mark inactive so re-triggers don't see this slot
        // as "playing" during the 20 ms fade window.
        slots[idx].isActive = false
        slots[idx].padKey = nil
        slots[idx].chokeGroup = nil
        refreshRingingPadKeys()

        // Fast path: quantize-deferred play() hasn't fired yet. Cancel
        // the dispatch, mute the slot, and skip the fade — there's
        // nothing audible to fade from. Without this the deferred
        // play() would still land against a stopped player + muted
        // mixer and consume a slot for a silent, phantom voice.
        if let pending = slots[idx].pendingPlayItem {
            pending.cancel()
            slots[idx].pendingPlayItem = nil
            slots[idx].fadeTask?.cancel()
            slots[idx].fadeTask = nil
            slots[idx].player.stop()
            slots[idx].mixer.outputVolume = 0
            refreshRingingPadKeys()  // clear armed state
            return
        }

        slots[idx].fadeTask?.cancel()
        let player = slots[idx].player
        let mixer = slots[idx].mixer

        slots[idx].fadeTask = Task { @MainActor [weak self] in
            let steps = 8
            let stepSec = Self.releaseFadeSec / Double(steps)
            for step in 1...steps {
                if Task.isCancelled { return }
                let frac = Float(steps - step) / Float(steps)
                mixer.outputVolume = startVol * frac
                try? await Task.sleep(nanoseconds: UInt64(stepSec * 1_000_000_000))
            }
            if Task.isCancelled { return }
            player.stop()
            mixer.outputVolume = 0
            self?.slots[idx].fadeTask = nil
        }
    }
    #endif
}
