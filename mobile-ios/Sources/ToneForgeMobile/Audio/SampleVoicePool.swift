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
    /// Seam crossfade length (ms) for a looping voice — from the
    /// Performance-Intelligence loop scorer (`loopScore`/optimized seam).
    /// 0 = hard loop (legacy behavior). When > 0 and looping, the loop
    /// buffer is overlap-add crossfaded so the seam is inaudible.
    public let crossfadeMs: Double
    /// Frames of `buffer` that form the LOOP BODY when the decoded buffer
    /// carries continuation audio past the loop's end (read from the source
    /// stem so the seam can be baked at EXACT bar-snapped length — see
    /// SeamlessLoop.exactCrossfaded). 0 = the whole buffer is the loop
    /// (no continuation available; seam falls back to edge ramps).
    public let loopBodyFrames: Int
    /// Shared-cycle lock (web _bakePad, padengine.js:924-931): target
    /// frame count the seam-baked loop body is TILED up to, so every pad
    /// with a real analyzer region loops over the SAME period and stays
    /// in unison. 0 (or ≤ body) = no tiling — the pad keeps its own
    /// length (region-less loops, the longest region pad).
    public let loopCycleFrames: Int
    /// Phase-locked join: seconds INTO the loop body playback begins at,
    /// so a loop launched mid-jam sits at the same musical position as
    /// the loops already ringing (web twin: `source.start(when, phase)`).
    /// 0 = start at the top (one-shots, the first loop of a jam).
    public let phaseSec: Double

    public init(
        padKey: SamplePadKey,
        loop: Bool,
        chokeGroup: Int?,
        gainDb: Double,
        pan: Float = 0,
        effects: SamplePadEffects = .neutral,
        crossfadeMs: Double = 0,
        loopBodyFrames: Int = 0,
        loopCycleFrames: Int = 0,
        phaseSec: Double = 0
    ) {
        self.padKey = padKey
        self.loop = loop
        self.chokeGroup = chokeGroup
        self.gainDb = gainDb
        self.pan = pan
        self.effects = effects
        self.crossfadeMs = crossfadeMs
        self.loopBodyFrames = loopBodyFrames
        self.loopCycleFrames = loopCycleFrames
        self.phaseSec = phaseSec
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
    /// One-shots are deliberately excluded — the indicator is a loop
    /// affordance (tap-to-stop) and a 200 ms stab flashing it reads as
    /// flicker. Their slots DO clear on natural end now (the
    /// .dataPlayedBack completion below); `soundingPadKeys` is the set
    /// that includes them.
    @Published public private(set) var ringingPadKeys: Set<SamplePadKey> = []

    /// Pads with ANY active voice — loops AND one-shots (whose natural
    /// end clears the slot via the .dataPlayedBack completion handler).
    /// The scheduler's free-run re-anchor check reads THIS, not
    /// `ringingPadKeys`: the web engine keeps its lattice anchor while
    /// any voice sounds (padengine.js:1154 checks `_voices.size`,
    /// one-shots included), so a still-audible one-shot must hold the
    /// anchor here too.
    public var soundingPadKeys: Set<SamplePadKey> {
        #if canImport(AVFoundation)
        return Set(slots.compactMap { $0.isActive ? $0.padKey : nil })
        #else
        return []
        #endif
    }

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
        /// Host time playback becomes AUDIBLE (the quantize target for
        /// future-gated triggers, allocation time otherwise). Loop-end
        /// math must use this, not `startedAtHostTime`, or an armed
        /// clip's wait would count as elapsed loop time.
        var audibleStartHostTime: UInt64
        /// One loop pass of the scheduled buffer, in seconds.
        var bufferDurationSec: Double
        /// Buffer offset (seconds) the voice STARTED at — the phase-
        /// locked join. loopPhase/releaseAtLoopEnd must add this, or
        /// they'd report the position the voice would have had starting
        /// from 0: visually desynced playheads (and early/late "musical
        /// stop") on pads whose AUDIO is phase-locked.
        var phaseStartSec: Double = 0
        /// A scheduled "complete the current loop pass, then stop"
        /// (releaseAtLoopEnd). Cancelled on retrigger/release.
        var pendingStopItem: DispatchWorkItem?
        var fadeTask: Task<Void, Never>?
        /// The DispatchWorkItem for a future-time `player.play()` call.
        /// Non-nil between allocation and the moment the workitem
        /// actually fires (which flips it back to nil). Kept so
        /// `releaseSlot` can cancel a still-pending play if the user
        /// lifts their finger before the quantize target — otherwise
        /// the deferred play() lands on an already-stopped player with
        /// a muted mixer and produces silence.
        var pendingPlayItem: DispatchWorkItem?
        /// Host time the pending play fires at — the armed indicator
        /// only shows for waits a human can perceive (>120 ms), not
        /// the few-ms scheduling hop of an unquantized tap.
        var pendingStartHostTime: UInt64 = 0
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
                audibleStartHostTime: 0,
                bufferDurationSec: 0,
                pendingStopItem: nil,
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

    // MARK: - Loop seam-bake cache (D-038 press path)

    #if canImport(AVFoundation)
    /// Bake identity: the SOURCE buffer plus every parameter that
    /// shapes the bake. crossfade keyed by bit pattern (exact-Double
    /// equality — both producers resolve it from the same pad fields).
    private struct LoopBakeKey: Hashable {
        let buffer: ObjectIdentifier
        let bodyFrames: Int
        let crossfadeBits: UInt64
        let cycleFrames: Int
    }
    /// Values retain the SOURCE buffer too: a freed buffer's
    /// ObjectIdentifier can be reused by a new allocation, and a stale
    /// hit would then play the wrong audio — retention pins the
    /// identity for the entry's lifetime (hits also verify `===`).
    private var loopBakeCache:
        [LoopBakeKey: (source: AVAudioPCMBuffer, baked: AVAudioPCMBuffer)] = [:]
    /// Tiled 4-bar stereo bakes run to MBs each; a per-kit working set
    /// is ~12–24 loops, so cap well above that and clear wholesale on
    /// overflow (entries re-memo on the next press/preload — LRU
    /// bookkeeping isn't worth it here).
    private static let loopBakeCacheCap = 64

    /// Press-path lookup: memo hit or bake-now-and-store.
    func cachedLoopBake(
        _ buffer: AVAudioPCMBuffer,
        bodyFrames: Int, crossfadeMs: Double, cycleFrames: Int
    ) -> AVAudioPCMBuffer {
        let key = LoopBakeKey(
            buffer: ObjectIdentifier(buffer), bodyFrames: bodyFrames,
            crossfadeBits: crossfadeMs.bitPattern, cycleFrames: cycleFrames)
        if let hit = loopBakeCache[key], hit.source === buffer {
            return hit.baked
        }
        let baked = Self.bakeLoop(
            buffer, bodyFrames: bodyFrames, crossfadeMs: crossfadeMs,
            cycleFrames: cycleFrames)
        storeLoopBake(key: key, source: buffer, baked: baked)
        return baked
    }

    /// Preload-time prewarm: bake OFF the main actor and memoize, so
    /// the first press of a kit loop is already a cache hit. Takes the
    /// RAW SampleTrigger-shaped params and resolves them exactly like
    /// the press path (one derivation, no drift).
    public func prewarmLoopBake(
        buffer: AVAudioPCMBuffer,
        loopBodyFrames: Int, crossfadeMs: Double, loopCycleFrames: Int
    ) {
        let xfadeMs = crossfadeMs > 0 ? crossfadeMs : SeamlessLoop.defaultLoopCrossfadeMs
        let body = loopBodyFrames > 0
            ? min(loopBodyFrames, Int(buffer.frameLength))
            : Int(buffer.frameLength)
        let key = LoopBakeKey(
            buffer: ObjectIdentifier(buffer), bodyFrames: body,
            crossfadeBits: xfadeMs.bitPattern, cycleFrames: loopCycleFrames)
        if let hit = loopBakeCache[key], hit.source === buffer { return }
        Task.detached(priority: .utility) { [weak self] in
            let baked = SampleVoicePool.bakeLoop(
                buffer, bodyFrames: body, crossfadeMs: xfadeMs,
                cycleFrames: loopCycleFrames)
            guard let self else { return }
            await MainActor.run {
                self.storeLoopBake(key: key, source: buffer, baked: baked)
            }
        }
    }

    private func storeLoopBake(
        key: LoopBakeKey, source: AVAudioPCMBuffer, baked: AVAudioPCMBuffer
    ) {
        if loopBakeCache.count >= Self.loopBakeCacheCap {
            loopBakeCache.removeAll(keepingCapacity: true)
        }
        loopBakeCache[key] = (source, baked)
    }

    /// The seam bake, extracted pure + nonisolated so the preload
    /// prewarm can run it off the main actor.
    ///
    /// A scored loop carries a measured crossfade length (loopScore →
    /// ms); an unscored one (Jam latch/loopOverride, loop-point, local
    /// `.loop`, Instant Groove via triggerRaw) falls back to the
    /// default floor instead of hard-looping. Non-loop one-shots never
    /// reach here (they play the raw, already edge-faded buffer).
    ///
    /// EXACT LENGTH: the seam bake must never change the loop period.
    /// The old `crossfaded()` returned n − x frames, so every held loop
    /// ran 8–30 ms short of the bar-snapped grid and drifted (each pad
    /// by a different x). `exactCrossfaded` keeps the period at the
    /// loop body length, using the buffer's continuation frames
    /// (loopBodyFrames split) when the decoder supplied them.
    ///
    /// Shared-cycle lock (web _bakePad, padengine.js:924-931): tile
    /// the seam-baked body up to the common cycle so a short region
    /// repeats INSIDE it and every latched pad shares ONE period.
    /// Without this a 1-bar pad looped at its own length against
    /// 4-bar neighbors — individually seamless, collectively
    /// drifting out of unison every pass. The scheduler gates
    /// loopCycleFrames on a real analyzer region, exactly like the
    /// web's hasRegion check; tileToLength is a no-op for
    /// target <= body (the longest region pad fills the cycle).
    nonisolated static func bakeLoop(
        _ buffer: AVAudioPCMBuffer,
        bodyFrames: Int, crossfadeMs: Double, cycleFrames: Int
    ) -> AVAudioPCMBuffer {
        var baked = SeamlessLoop.exactCrossfaded(
            buffer, loopFrames: bodyFrames, crossfadeMs: crossfadeMs)
        if cycleFrames > Int(baked.frameLength) {
            baked = SeamlessLoop.tileToLength(baked, targetFrames: cycleFrames)
        }
        return baked
    }
    #endif

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
        slot.pendingStopItem?.cancel()
        slot.pendingStopItem = nil
        slot.player.stop()

        slot.padKey = req.padKey
        slot.chokeGroup = req.chokeGroup
        slot.isActive = true
        slot.isLooping = req.loop
        slot.startedAtHostTime = mach_absolute_time()
        // Loop-end release math (releaseAtLoopEnd) needs when audio
        // actually starts and how long one pass is.
        slot.audibleStartHostTime = time?.hostTime ?? slot.startedAtHostTime
        slot.bufferDurationSec = buffer.format.sampleRate > 0
            ? Double(buffer.frameLength) / buffer.format.sampleRate
            : 0

        // Seamless looping: EVERY looping voice gets an overlap-add
        // seam (see bakeLoop). MEMOIZED (D-038 press-path hazard): the
        // seam bake + cycle tiling are per-press DSP on the main actor
        // — a press stalled audibly behind buffer-length memcpy/fade
        // math on every trigger. The scheduler prewarms this cache at
        // pack preload, so a press normally reduces to a dictionary
        // hit; a miss (transform/trim output, latch-forced loop on an
        // unscored pad) bakes once and memoizes.
        let xfadeMs = req.crossfadeMs > 0 ? req.crossfadeMs : SeamlessLoop.defaultLoopCrossfadeMs
        let playBuffer: AVAudioPCMBuffer
        if req.loop {
            let body = req.loopBodyFrames > 0
                ? min(req.loopBodyFrames, Int(buffer.frameLength))
                : Int(buffer.frameLength)
            playBuffer = cachedLoopBake(
                buffer, bodyFrames: body, crossfadeMs: xfadeMs,
                cycleFrames: req.loopCycleFrames)
        } else {
            playBuffer = buffer
        }
        // One pass length reflects the exact-length loop body (loop) or the
        // raw buffer (one-shot) — releaseAtLoopEnd/loopProgress key off it.
        slot.bufferDurationSec = playBuffer.format.sampleRate > 0
            ? Double(playBuffer.frameLength) / playBuffer.format.sampleRate
            : slot.bufferDurationSec

        let options: AVAudioPlayerNodeBufferOptions = req.loop
            ? [.interrupts, .loops]
            : [.interrupts]

        // Voice gain: dB → linear, clamped to [0, 1]. Buffers are peak-
        // normalized to -1 dBFS on load, so unity is already near full
        // scale — a per-voice boost only invites clipping once several
        // voices sum at the unity voiceMixer. Attenuation (gainDb < 0, for
        // balancing) still works; boosts are capped at 0 dB.
        let linear = Float(pow(10.0, req.gainDb / 20.0))
        slot.mixer.outputVolume = max(0, min(1, linear))
        slot.mixer.pan = max(-1, min(1, req.pan))

        // Apply per-pad effects onto this slot's delay + eq. Clamp
        // once so a stale persisted override can't push
        // AVAudioUnitDelay.delayTime out of its valid range.
        applyEffects(req.effects.clamped(), to: slot)

        // Schedule the buffer in the player's own timeline. A phase-
        // locked join starts `phaseSec` INTO the loop body (web:
        // `source.start(when, phase)`). AVAudioPlayerNode has no start
        // offset for a looping buffer, so play the first partial pass
        // [phaseFrame..<body] as its own segment, then queue the whole
        // body looping — queued buffers run back-to-back sample-
        // accurately, and the partial's tail meets the body's head
        // across the baked seam, so the splice is as clean as the loop
        // wrap itself.
        slot.phaseStartSec = 0
        var scheduledPhaseJoin = false
        if req.loop, req.phaseSec > 0, playBuffer.format.sampleRate > 0 {
            let sr = playBuffer.format.sampleRate
            let frames = Int(playBuffer.frameLength)
            let phaseFrame = min(max(0, Int((req.phaseSec * sr).rounded())),
                                 max(0, frames - 1))
            if phaseFrame > 0,
               let partial = Self.sliceFrom(playBuffer, startFrame: phaseFrame) {
                // Store the frame-quantized offset — what actually plays —
                // so the drawn playhead matches the audio exactly.
                slot.phaseStartSec = Double(phaseFrame) / sr
                slot.player.scheduleBuffer(
                    partial, at: nil, options: [.interrupts],
                    completionHandler: nil)
                // No .interrupts here: it would cut the partial pass off.
                slot.player.scheduleBuffer(
                    playBuffer, at: nil, options: [.loops],
                    completionHandler: nil)
                scheduledPhaseJoin = true
            }
        }
        if !scheduledPhaseJoin {
            if req.loop {
                slot.player.scheduleBuffer(
                    playBuffer, at: nil, options: options,
                    completionHandler: nil)
            } else {
                // Natural-end tracking for one-shots (web: source.onended,
                // padengine.js:1242-1256): the slot must read inactive once
                // the buffer has PLAYED OUT — otherwise a finished stab held
                // `isActive` forever, and anything keyed on "is anything
                // sounding?" (the free-run re-anchor check) saw a dead jam
                // as live. .dataPlayedBack fires when the audio is audible-
                // complete, not merely consumed by the render loop. stop()/
                // retrigger also fire this handler, so it no-ops unless the
                // slot still holds THIS voice (token = startedAtHostTime,
                // the web onended `voices.get(padIdx) === voice` guard).
                let token = slot.startedAtHostTime
                slot.player.scheduleBuffer(
                    playBuffer, at: nil, options: options,
                    completionCallbackType: .dataPlayedBack
                ) { [weak self] _ in
                    Task { @MainActor [weak self] in
                        guard let self, self.slots.indices.contains(idx),
                              self.slots[idx].isActive,
                              !self.slots[idx].isLooping,
                              self.slots[idx].startedAtHostTime == token
                        else { return }
                        self.slots[idx].isActive = false
                        self.slots[idx].padKey = nil
                        self.slots[idx].chokeGroup = nil
                        self.refreshRingingPadKeys()
                    }
                }
            }
        }

        // Future-time gating. Once the engine has rendered at least once
        // (outputNode.lastRenderTime is sample-time valid) the launch is
        // handed to `play(at: AVAudioTime(hostTime:))` — SAMPLE-ACCURATE,
        // the web engine's `source.start(startTime)`. The DispatchQueue
        // fallback below has ~1 ms of scheduler jitter, which is audible
        // as flam when several pads arm to the same quantize boundary.
        //
        // Pre-first-render the fallback is mandatory: `play(at:)` throws
        // NSException from AVAudioPlayerNodeImpl::StartImpl when the
        // engine hasn't produced any output yet (fresh boot, first tap),
        // so that boot window keeps the deferred `.play()` dispatch.
        //
        // BOTH paths store a DispatchWorkItem on the slot: it is the
        // armed marker AND the cancel token `releaseSlot(_:)`/retrigger
        // use to kill a not-yet-started voice (player.stop() discards a
        // scheduled future start). Without it, hold-mode + quantize + a
        // short tap produced silence: the release fade + player.stop()
        // ran ~100 ms into a 400 ms quantize wait, so when the deferred
        // play() finally fired it hit an already-stopped player with a
        // muted mixer. On the sample-accurate path the item no longer
        // calls play() — it only flips armed → playing at the deadline.
        if let t = time {
            let nowHost = mach_absolute_time()
            let futureHost = t.hostTime
            let delayTicks: UInt64 = futureHost > nowHost ? (futureHost - nowHost) : 0
            let delaySec = Double(delayTicks) / TransportClock.ticksPerSecond()
            if delaySec > 0.0005 {
                let player = slot.player
                let slotIdx = idx
                let engineRendered = engine?.engine.outputNode
                    .lastRenderTime?.isSampleTimeValid ?? false
                // play(at:) needs the PLAYER's own render clock, not just
                // the engine's: a freshly-attached player with an invalid
                // lastRenderTime silently ignores a hostTime start (the
                // armed voice never fires). Fall back to the dispatch
                // path for exactly that case.
                let playerRendered = player.lastRenderTime?.isSampleTimeValid ?? false
                Diag.padsync("arm slot=\(slotIdx) pad=\(req.padKey.padIdx) delay=\(String(format: "%.3f", delaySec))s engineRendered=\(engineRendered) playerRendered=\(playerRendered)")
                let item: DispatchWorkItem
                if engineRendered && playerRendered {
                    player.play(at: AVAudioTime(hostTime: futureHost))
                    item = DispatchWorkItem { [weak self] in
                        Task { @MainActor [weak self] in
                            guard let self, self.slots.indices.contains(slotIdx) else { return }
                            Diag.padsync("deadline slot=\(slotIdx) sampleAccurate playing=\(self.slots[slotIdx].player.isPlaying)")
                            self.slots[slotIdx].pendingPlayItem = nil
                            self.refreshRingingPadKeys()  // armed → playing
                        }
                    }
                } else {
                    item = DispatchWorkItem { [weak self] in
                        player.play()
                        Task { @MainActor [weak self] in
                            guard let self, self.slots.indices.contains(slotIdx) else { return }
                            Diag.padsync("deadline slot=\(slotIdx) dispatchPath playing=\(self.slots[slotIdx].player.isPlaying)")
                            self.slots[slotIdx].pendingPlayItem = nil
                            self.refreshRingingPadKeys()  // armed → playing
                        }
                    }
                }
                slot.pendingPlayItem = item
                slot.pendingStartHostTime = futureHost
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

        // Same natural-end tracking as one-shots in trigger(): a finished
        // preview must not hold its slot "active" forever.
        let token = slot.startedAtHostTime
        slot.player.scheduleBuffer(
            slicedBuffer, at: nil, options: [.interrupts],
            completionCallbackType: .dataPlayedBack
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.slots.indices.contains(idx),
                      self.slots[idx].isActive,
                      !self.slots[idx].isLooping,
                      self.slots[idx].startedAtHostTime == token
                else { return }
                self.slots[idx].isActive = false
                self.slots[idx].padKey = nil
                self.slots[idx].chokeGroup = nil
                self.refreshRingingPadKeys()
            }
        }
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

    /// Musical stop for latched clips: let the CURRENT loop pass finish,
    /// then release. Ableton-style — toggling a clip off mid-bar doesn't
    /// chop the audio, it completes the phrase.
    ///
    /// Non-looping voices, unknown durations, and still-armed voices
    /// (quantize wait, nothing audible yet) release immediately — an
    /// armed clip toggled off should simply un-arm.
    public func releaseAtLoopEnd(padKey: SamplePadKey) {
        #if canImport(AVFoundation)
        for i in slots.indices where slots[i].isActive && slots[i].padKey == padKey {
            let slot = slots[i]
            let duration = slot.bufferDurationSec
            guard slot.isLooping, duration > 0.01, slot.pendingPlayItem == nil else {
                releaseSlot(i)
                continue
            }
            // Time until the current pass completes.
            let now = mach_absolute_time()
            let start = slot.audibleStartHostTime
            let elapsed = now > start
                ? Double(now - start) / TransportClock.ticksPerSecond()
                : 0
            // A phase-joined voice began mid-body: its pass completes when
            // the BUFFER position wraps, i.e. (elapsed + phaseStart) hits
            // the body length — not `elapsed` alone.
            let inPass = (elapsed + slot.phaseStartSec)
                .truncatingRemainder(dividingBy: duration)
            let remainder = duration - inPass

            // Identity token: if the slot is stolen/retriggered before
            // the deadline, the stale stop must not kill the new voice.
            let token = slot.startedAtHostTime
            let item = DispatchWorkItem { [weak self] in
                Task { @MainActor [weak self] in
                    guard let self, self.slots.indices.contains(i) else { return }
                    guard self.slots[i].isActive,
                          self.slots[i].padKey == padKey,
                          self.slots[i].startedAtHostTime == token else { return }
                    self.slots[i].pendingStopItem = nil
                    self.releaseSlot(i)
                }
            }
            slots[i].pendingStopItem?.cancel()
            slots[i].pendingStopItem = item
            DispatchQueue.global(qos: .userInteractive)
                .asyncAfter(deadline: .now() + max(0.01, remainder), execute: item)
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

    /// Copy of `buffer` from `startFrame` to its end — the first
    /// partial pass of a phase-joined loop. nil for degenerate ranges
    /// (caller falls back to a plain top-of-body schedule).
    private static func sliceFrom(
        _ buffer: AVAudioPCMBuffer, startFrame: Int
    ) -> AVAudioPCMBuffer? {
        let total = Int(buffer.frameLength)
        let frames = total - startFrame
        guard startFrame > 0, frames > 0,
              let out = AVAudioPCMBuffer(
                  pcmFormat: buffer.format,
                  frameCapacity: AVAudioFrameCount(frames)),
              let src = buffer.floatChannelData,
              let dst = out.floatChannelData
        else { return nil }
        for ch in 0..<Int(buffer.format.channelCount) {
            dst[ch].update(from: src[ch] + startFrame, count: frames)
        }
        out.frameLength = AVAudioFrameCount(frames)
        return out
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

    /// Normalized loop position (0..<1) for a currently-looping pad, or nil
    /// if it isn't ringing a loop. Drives the on-pad loop playhead — the UI
    /// polls this from a 30 Hz TimelineView. Derived from how long the voice
    /// has been audible modulo one loop pass (host-clock based, no per-frame
    /// bookkeeping).
    public func loopPhase(padKey: SamplePadKey) -> Double? {
        for slot in slots where slot.isActive && slot.isLooping
            && slot.padKey == padKey && slot.bufferDurationSec > 0 {
            let now = mach_absolute_time()
            guard now > slot.audibleStartHostTime else { return 0 }
            let elapsedTicks = Double(now - slot.audibleStartHostTime)
            let elapsedSec = elapsedTicks / TransportClock.ticksPerSecond()
            return Self.loopReadout(
                elapsedSec: elapsedSec,
                phaseStartSec: slot.phaseStartSec,
                bodySec: slot.bufferDurationSec)
        }
        return nil
    }

    /// Normalized loop position for `elapsedSec` of audible playback on a
    /// voice that STARTED `phaseStartSec` into its body. The offset must
    /// be included: without it the drawn playhead reports the position
    /// the voice would have had starting from 0 — visually desynced pads
    /// whose AUDIO is phase-locked (the "playheads at different
    /// positions" bug). Web twin: padengine.js `padProgress`.
    static func loopReadout(
        elapsedSec: Double, phaseStartSec: Double, bodySec: Double
    ) -> Double {
        guard bodySec > 0 else { return 0 }
        let phase = (elapsedSec + phaseStartSec)
            .truncatingRemainder(dividingBy: bodySec)
        return max(0, min(0.9999, phase / bodySec))
    }

    /// Recompute the published ringing-loop set from slot truth.
    /// Assigns only on change so SwiftUI isn't poked on every
    /// one-shot trigger.
    private func refreshRingingPadKeys() {
        let now = Set(slots.compactMap { slot in
            slot.isActive && slot.isLooping ? slot.padKey : nil
        })
        if now != ringingPadKeys { ringingPadKeys = now }
        // Armed = a future-time play still pending on the slot — but
        // only when the wait is perceivable. Unquantized taps also
        // schedule a few ms out (the play() dispatch hop) and were
        // flashing the hourglass on every tap.
        let nowHost = mach_absolute_time()
        let minArmedTicks =
            UInt64(0.12 * TransportClock.ticksPerSecond())
        let pending = Set(slots.compactMap { slot -> SamplePadKey? in
            guard slot.pendingPlayItem != nil,
                  slot.pendingStartHostTime > nowHost,
                  slot.pendingStartHostTime - nowHost > minArmedTicks
            else { return nil }
            return slot.padKey
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
        slots[idx].pendingStopItem?.cancel()
        slots[idx].pendingStopItem = nil
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
