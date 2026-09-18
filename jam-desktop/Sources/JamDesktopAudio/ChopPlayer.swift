// ChopPlayer.swift
//
// One-shot chop playback for the Launchpad grid: plays [startSec,
// endSec] segments of stem files through a small AVAudioPlayerNode
// pool connected into the MusicBus (falls back to the host engine's
// main mixer). The distilled desktop counterpart of the mobile
// SampleScheduler/SampleVoicePool pair — no packs, no layers; just
// quantized segment triggers against the stems the session already
// downloaded, with the mobile per-voice effects chain:
//
//   voice player → AVAudioUnitDelay → AVAudioUnitEQ(resonantLowPass)
//               → AVAudioMixerNode(velocity/pan) → out
//
// SamplePadEffects params are pushed at trigger time (iOS
// SampleVoicePool.applyEffects parity: delay tap silent at mix=0, EQ
// band bypassed when cutoff sits at the top of its window). Velocity
// and pan land on the per-voice mixer, mirroring iOS slot.mixer.
//
// Besides bundle-chop pads, the sequencer triggers arbitrary local
// files (ChopReference.customURL) via `trigger(file:...)` — readers
// are cached per URL.
//
// Timing: LaunchpadController hands us a fire-at time in SONG seconds.
// The wall-clock delay is (fireAt − now) ÷ playbackRate (practice rate
// stretches the distance to the next beat), converted to mach host
// ticks for `AVAudioPlayerNode.play(at:)`.
//
// Pool: 16 voices, round-robin steal. Retriggering a sounding chop
// steals its own voice first (natural feel for pad drumming). Nodes
// are reconnected per trigger only when the stem's processing format
// differs from what the node was last wired with.

import Foundation
import AVFoundation
import os
import ToneForgeEngine
import JamDesktopCore

/// Epoch lock shared between a voice's press path (main actor) and its
/// detached release-fade: the press ADVANCES the epoch when it reuses
/// the slot; the fade's ramp steps and its terminal stop/re-park run
/// only while their epoch is still current, atomically vs the advance.
/// Without it a stale fade could drag the new voice's volume down or
/// stop a buffer a press just scheduled — a race the old main-actor
/// fade never had (main serialized cancel and ramp), introduced when
/// the ramp moved off-main for the 20-ms-means-20-ms release fix.
final class VoiceGate: @unchecked Sendable {
    private let lock = OSAllocatedUnfairLock(initialState: 0)
    @discardableResult func advance() -> Int {
        lock.withLock { $0 += 1; return $0 }
    }
    func current() -> Int { lock.withLock { $0 } }
    /// Runs `body` under the lock iff `epoch` is still current.
    func ifCurrent(_ epoch: Int, _ body: () -> Void) {
        lock.withLock { if $0 == epoch { body() } }
    }
    /// ATOMIC CLAIM (D-038): advance iff `epoch` is still current and
    /// return the new epoch, else nil. This is how the receive-thread
    /// fast press and the main-path claim arbitrate a parked voice —
    /// exactly one side can win, and the loser's refs are instantly
    /// stale. Compare-and-advance must be one critical section; a
    /// current() check followed by advance() would let both sides pass.
    func advanceIfCurrent(_ epoch: Int) -> Int? {
        lock.withLock { state -> Int? in
            guard state == epoch else { return nil }
            state += 1
            return state
        }
    }
}

@MainActor
public final class ChopPlayer {

    private struct Voice {
        let node: AVAudioPlayerNode
        let delay: AVAudioUnitDelay
        let eq: AVAudioUnitEQ
        /// Velocity (volume) and pan land here — iOS slot.mixer parity.
        let mixer: AVAudioMixerNode
        var format: AVAudioFormat?
        /// What the voice is sounding, nil when idle.
        var key: VoiceKey?
        /// Loop length in frames when this voice is hard-looping (nil = one-shot).
        /// Drives the per-pad playhead (loopProgress).
        var loopFrames: AVAudioFrameCount?
        /// Buffer offset the loop STARTED at (phase-locked join): the first
        /// pass began this many frames into the body. loopProgress must add
        /// it, or the drawn playhead reports the position the voice would
        /// have had starting from frame 0 — visually desynced pads over
        /// audio that IS phase-locked (the web "playheads at different
        /// positions" bug). 0 for one-shots and unlocked loops.
        var phaseFrames: AVAudioFrameCount = 0
        /// The song stem this voice is currently "taking over" (ducking),
        /// or nil. Cleared when the voice stops/completes/is stolen.
        var takeoverStem: String?
        /// Monotonic per-voice token so a late one-shot completion can't
        /// end a takeover the slot has since been reused for.
        var gen: Int = 0
        /// mixer→destination wired (stereo, once per attach). Reset on
        /// reattach. Rewiring this leg on every per-voice format change
        /// crashed when a trigger raced a CoreAudio device reconfig
        /// (USB plug): AVAudioEngine.connect threw NSException mid-
        /// UpdateGraphAfterReconfig.
        var outputWired: Bool = false
        /// Armed marker + cancel token for a quantized (future) start —
        /// present on BOTH start paths (mobile SampleVoicePool parity):
        /// on the sample-accurate path it only flips armed→started at
        /// the deadline; on the dispatch fallback it carries the actual
        /// deferred play(). release() cancels it so a voice released
        /// before its boundary dies silently instead of firing anyway.
        var pendingPlay: DispatchWorkItem?
        /// In-flight 20 ms release fade (releaseFadeSec). Cancelled when
        /// the slot is re-claimed so a late `stop()` can't kill the new
        /// voice.
        var fadeTask: Task<Void, Never>?
        /// PARKED: the node is `play()`ing with an EMPTY queue, so a
        /// scheduled buffer starts at the next render cycle with NO
        /// further control call. `play()` blocks its caller for up to a
        /// full render quantum (~10.7 ms measured at 512f/48k) — paying
        /// it per press serialized rapid same-pad hammering into
        /// 100–500 ms main-queue pileups. Voices are parked by
        /// `warmUpPool()` and re-parked by the release fade's terminal.
        var parked: Bool = false
        /// Player sample time at THIS launch. A parked node's sample
        /// clock keeps running from its park `play()`, so loopProgress
        /// must measure rendered frames from the launch baseline, not
        /// from zero.
        var startSampleTime: Int64 = 0
        /// Press ↔ detached-fade epoch lock (see VoiceGate).
        let gate = VoiceGate()
    }

    private enum VoiceKey: Hashable {
        /// A bundle chop: (stem role, chop idx).
        case chop(stem: String, idx: Int)
        /// A custom local file segment (sequencer customURL).
        case file(URL)
    }

    private let avEngine: AVAudioEngine
    private var voices: [Voice] = []
    private var nextVoice = 0

    /// Voices currently claimed by a pad (key != nil) — a stop nils the key.
    /// Exposed for regression tests: a re-tap must drop the count back to 0,
    /// including borrow/drumfile pads that play through `.file(url)` voices.
    public var soundingVoiceCount: Int { voices.filter { $0.key != nil }.count }
    /// PARKED voices (playing, empty queue — zero-control-call claims).
    /// Test seam: warmUpPool fills it, presses drain it, fade terminals
    /// refill it.
    var parkedVoiceCount: Int { voices.filter(\.parked).count }

    // MARK: - Receive-thread fast release

    /// What a receive-thread release needs to BEGIN the audible fade
    /// without the main actor: the voice's mixer, its epoch gate, and
    /// the epoch stamped at trigger time (stale epoch = the voice was
    /// reused/released since — the ramp no-ops). @unchecked: the nodes
    /// are only parameter-set, never re-wired, off-main.
    private struct FastReleaseRef: @unchecked Sendable {
        let mixer: AVAudioMixerNode
        let gate: VoiceGate
        let epoch: Int
    }
    /// padTag → the voice that pad last triggered. Written on the main
    /// actor at trigger time, read from the MIDI receive thread at
    /// pad-up — hence the lock, not actor isolation.
    private let fastReleaseRefs =
        OSAllocatedUnfairLock<[Int: FastReleaseRef]>(initialState: [:])

    /// RECEIVE-THREAD release: begins the audible `releaseFadeSec` fade
    /// for the voice last triggered with `tag`, WITHOUT touching the
    /// main actor. The authoritative main-path release still follows
    /// (bookkeeping, LED, terminal stop/re-park) and composes with
    /// this: it advances the voice's epoch — killing this ramp — and
    /// starts its own ramp from the already-lowered volume, so audio
    /// only ever fades downward, never re-blips.
    ///
    /// Why it exists: hardware pad-ups ride the MIDI→main hop, and the
    /// SwiftUI commit each press provokes can swallow that hop for
    /// 50–145 ms under same-pad hammering (presses land after commits,
    /// releases land during them) — the release then audibly "sticks"
    /// even though the main-path handling itself costs ~0.1 ms. This
    /// entry costs ~µs on the receive thread (dict lookup + task spawn)
    /// and the fade begins within a render quantum of the packet.
    @discardableResult
    nonisolated public func padReleased(tag: Int) -> Bool {
        guard let ref = fastReleaseRefs.withLock({ $0[tag] }),
              ref.gate.current() == ref.epoch   // reused/released
        else {
            // No live ref = the audible fade WAITS for the main hop —
            // exactly the miss the lane tally surfaces (a registry miss
            // on some route would otherwise read as "release lags" in
            // the hop log while the mechanism sits dead).
            fastLaneStats.withLock { $0.releaseMisses += 1 }
            return false
        }
        fastLaneStats.withLock { $0.releaseFires += 1 }
        Task.detached(priority: .userInitiated) {
            let steps = 8
            let stepSec = Self.releaseFadeSec / Double(steps)
            let startVol = ref.mixer.outputVolume
            for step in 1...steps {
                var live = false
                ref.gate.ifCurrent(ref.epoch) {
                    ref.mixer.outputVolume = startVol * Float(steps - step) / Float(steps)
                    live = true
                }
                // Epoch moved: the main release (or a re-trigger) owns the
                // voice now — its ramp/volume set supersedes this one.
                if !live { return }
                try? await Task.sleep(nanoseconds: UInt64(stepSec * 1_000_000_000))
            }
            // No terminal here: stop + re-park stay with the main-path
            // release, which always follows in the event stream.
        }
        return true
    }

    /// Audible-lane tallies (receive-thread fires vs main-path
    /// fallbacks), appended to the [PadLatency] hop lines so a captured
    /// session shows AT A GLANCE whether the fast lanes are engaged —
    /// the hop numbers measure bookkeeping, not audio, whenever the
    /// fire counters are moving. Lock-boxed: bumped on the receive
    /// thread, read from the main log line.
    private let fastLaneStats = OSAllocatedUnfairLock<
        (pressFires: Int, pressMisses: Int,
         releaseFires: Int, releaseMisses: Int)
    >(initialState: (0, 0, 0, 0))

    /// "press 12/0 · release 12/0" (fires/misses). Nonisolated so the
    /// instrumentation line can read it without another hop.
    nonisolated public var fastLaneSummary: String {
        let s = fastLaneStats.withLock { $0 }
        return "fastPress \(s.pressFires)/\(s.pressMisses) · "
            + "fastRelease \(s.releaseFires)/\(s.releaseMisses)"
    }

    /// Test seam: the current mixer volume of the voice `tag` last
    /// triggered (nil = no registration). Nonisolated so a test can
    /// observe the fast fade while the main actor is deliberately
    /// stalled.
    nonisolated func fastReleaseVolume(tag: Int) -> Float? {
        fastReleaseRefs.withLock { $0[tag] }?.mixer.outputVolume
    }

    // MARK: - Receive-thread fast press (D-038)
    //
    // The press twin of the fast release above. The parked-voice work
    // (D-032) removed play() from the press path, but the scheduleBuffer
    // itself still waited on the MIDI→main hop — measured 100–400 ms
    // under same-pad hammering, because each press's own SwiftUI commit
    // stalls the hop. Now the MAIN actor pre-arms a PLAN per pad (the
    // exact baked loop body + effects the main trigger would derive),
    // and the receive thread claims a parked voice and schedules the
    // plan within a render quantum of the packet. The main-path trigger
    // then ADOPTS the fired voice (bookkeeping only — key, takeover,
    // playhead baseline, rotation) or supersedes a stale fire. Only
    // INSTANT presses arm (One-Shot/Follow, no section gate): quantized
    // Latch launches need transport state and keep main authority — the
    // stamped clocks already make their math press-true.

    /// Start phase for a fast-fired plan. `.zero` = sample top
    /// (One-Shot); `.era` = join the current lock era's cycle phase
    /// (Follow), computed on the receive thread from the era snapshot.
    public enum FastPressJoin: Sendable { case zero, era }

    /// Everything the receive thread needs to fire one pad, pre-derived
    /// on main. @unchecked: the buffer is written once at bake and only
    /// read afterwards.
    private struct FastPressPlan: @unchecked Sendable {
        let generation: UInt64
        let body: AVAudioPCMBuffer
        let effects: SamplePadEffects
        let join: FastPressJoin
    }

    /// One pad to arm: the SAME inputs the live trigger derives its
    /// bake from, so plan and press share cache keys and content.
    public struct FastPressArming {
        public let padTag: Int
        public let assignment: PadAssignment
        /// Downloaded loop FILE (borrowfile pads); nil = stem chop.
        public let fileURL: URL?
        public let effects: SamplePadEffects
        public let crossfadeMs: Double
        public let loopBarSeconds: Double
        public let cycleSeconds: Double
        public init(
            padTag: Int, assignment: PadAssignment, fileURL: URL?,
            effects: SamplePadEffects, crossfadeMs: Double,
            loopBarSeconds: Double, cycleSeconds: Double
        ) {
            self.padTag = padTag
            self.assignment = assignment
            self.fileURL = fileURL
            self.effects = effects
            self.crossfadeMs = crossfadeMs
            self.loopBarSeconds = loopBarSeconds
            self.cycleSeconds = cycleSeconds
        }
    }

    /// A parked voice as the receive thread may claim it. Ownership is
    /// decided by `gate.advanceIfCurrent(parkEpoch)` — main claims go
    /// through the SAME box (claimVoiceAtomically), so both sides can
    /// never own one node. @unchecked: nodes are only scheduled/param-
    /// set off-main, never re-wired.
    private struct FastParkedRef: @unchecked Sendable {
        let index: Int
        let node: AVAudioPlayerNode
        let delay: AVAudioUnitDelay
        let eq: AVAudioUnitEQ
        let mixer: AVAudioMixerNode
        let gate: VoiceGate
        let parkEpoch: Int
    }

    /// A fire awaiting its main-path adoption.
    private struct FastFire: Sendable {
        let index: Int
        let epoch: Int
        let generation: UInt64
        let bodyFrames: AVAudioFrameCount
        let phaseFrames: AVAudioFrameCount
        let fireHostTime: UInt64
    }

    /// Era snapshot for the Follow join, pushed by the controller
    /// whenever an anchor moves (padDown is the only mutator).
    struct FastPressEra: Sendable {
        var freerunAnchorHostSeconds: Double?
        var lockAnchorSongSeconds: Double?
        var transportRolling = false
    }

    private let fastPlanBox = OSAllocatedUnfairLock<
        (generation: UInt64, plans: [Int: FastPressPlan])
    >(initialState: (0, [:]))
    private let fastParkedBox =
        OSAllocatedUnfairLock<[FastParkedRef]>(initialState: [])
    private let fastFireBox =
        OSAllocatedUnfairLock<[Int: FastFire]>(initialState: [:])
    private let fastEraBox =
        OSAllocatedUnfairLock<FastPressEra>(initialState: .init())

    /// Canonical rate as a nonisolated constant — `canonicalFormat` is
    /// main-isolated; the receive thread only needs the number. Keep in
    /// lockstep with `canonicalFormat`.
    nonisolated static let canonicalSampleRate: Double = 48_000

    /// mach ticks → seconds (receive-thread era math).
    nonisolated private static let hostTickSeconds: Double = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return Double(info.numer) / Double(info.denom) / 1_000_000_000
    }()

    /// Rebuild the plan registry for the current grid/mode/FX state.
    /// The generation bumps FIRST (before any bake), so a press racing
    /// the rebuild takes the main path instead of a stale plan; a newer
    /// arm superseding this one mid-build abandons it. Bakes are cache
    /// hits after prewarm; yields between pads keep main responsive.
    public func armFastPresses(
        _ items: [FastPressArming], join: FastPressJoin
    ) async {
        let gen = fastPlanBox.withLock { box -> UInt64 in
            box.generation &+= 1
            box.plans = [:]
            return box.generation
        }
        var plans: [Int: FastPressPlan] = [:]
        for item in items {
            var body: AVAudioPCMBuffer?
            if let url = item.fileURL {
                // Borrow loop FILE — the trigger(file:) derivation
                // verbatim (whole file, 12 ms crossfade) so the bake
                // cache key matches the live press.
                if let file = cachedFile(for: url) {
                    let duration =
                        Double(file.length) / file.fileFormat.sampleRate
                    let frames = Self.regionFrameCount(
                        startSec: 0, endSec: duration,
                        sampleRate: file.fileFormat.sampleRate,
                        fileLength: file.length)
                    if frames > 0 {
                        body = loopBuffer(
                            file: file, startFrame: 0,
                            frameCount: AVAudioFrameCount(frames),
                            crossfadeMs: 12)?.buffer
                    }
                }
            } else if let file = files[item.assignment.stem] {
                // Stem chop — the trigger(_:)/schedule() derivation
                // verbatim.
                let chop = item.assignment.chop
                let sr = file.fileFormat.sampleRate
                let endSec = Self.loopRegionEndSec(
                    chop: chop, loop: true,
                    loopBarSeconds: item.loopBarSeconds)
                let startFrame =
                    AVAudioFramePosition(max(0, chop.startSec) * sr)
                let frames = Self.regionFrameCount(
                    startSec: chop.startSec, endSec: endSec,
                    sampleRate: sr, fileLength: file.length)
                if frames > 0, startFrame < file.length {
                    let tile = chop.loopScore != nil ? item.cycleSeconds : 0
                    body = loopBuffer(
                        file: file, startFrame: startFrame,
                        frameCount: AVAudioFrameCount(frames),
                        crossfadeMs: item.crossfadeMs,
                        tileToCycleSec: tile)?.buffer
                }
            }
            guard let body else { continue }
            plans[item.padTag] = FastPressPlan(
                generation: gen, body: body,
                effects: item.effects, join: join)
            await Task.yield()
            // Superseded mid-build — abandon, the newer arm owns the box.
            guard fastPlanBox.withLock({ $0.generation }) == gen else { return }
        }
        let built = plans
        fastPlanBox.withLock { box in
            guard box.generation == gen else { return }
            box.plans = built
        }
    }

    /// Invalidate every plan NOW (grid remount, mode change, section
    /// gate armed, reattach). Synchronous + cheap: one generation bump
    /// under the lock — any in-flight or later fast press sees a stale
    /// generation and falls to the main path.
    public func disarmFastPresses() {
        fastPlanBox.withLock { box in
            box.generation &+= 1
            box.plans = [:]
        }
    }

    /// Era snapshot for the Follow join (nonisolated: the controller
    /// pushes it from padDown; the receive thread reads it).
    nonisolated public func updateFastPressEra(
        freerunAnchorHostSeconds: Double?,
        lockAnchorSongSeconds: Double?,
        transportRolling: Bool
    ) {
        fastEraBox.withLock {
            $0 = FastPressEra(
                freerunAnchorHostSeconds: freerunAnchorHostSeconds,
                lockAnchorSongSeconds: lockAnchorSongSeconds,
                transportRolling: transportRolling)
        }
    }

    /// RECEIVE-THREAD PRESS: fire `tag`'s armed plan on a parked voice
    /// within a render quantum of the packet — no main actor anywhere.
    /// Returns false (and does nothing) when no current plan exists or
    /// the parked pool is empty; the main-path press then plays
    /// normally. On success the fast-release ref is registered
    /// immediately (a pad-up can beat the press's main hop) and a
    /// FastFire is left for the main trigger to adopt.
    ///
    /// Deliberately dropped on this lane: the bake's `shiftSec` launch
    /// compensation (sub-50 ms grid alignment for QUANTIZED launches —
    /// instant gates fire now by definition) and per-press velocity
    /// (grid pads are gates on every surface).
    @discardableResult
    nonisolated public func padPressed(
        tag: Int, songSeconds: Double, hostTime: UInt64
    ) -> Bool {
        let (gen, plan) = fastPlanBox.withLock {
            ($0.generation, $0.plans[tag])
        }
        guard let plan, plan.generation == gen else {
            fastLaneStats.withLock { $0.pressMisses += 1 }
            return false
        }
        while let ref = fastParkedBox.withLock({ $0.popLast() }) {
            // Atomic ownership: stale refs (voice re-claimed since it
            // parked) fail the compare-and-advance and are discarded.
            guard let epoch = ref.gate.advanceIfCurrent(ref.parkEpoch)
            else { continue }
            Self.applyEffects(plan.effects, delay: ref.delay, eq: ref.eq)
            ref.mixer.outputVolume = 1
            ref.mixer.pan = 0
            var phase: AVAudioFrameCount = 0
            if case .era = plan.join {
                let era = fastEraBox.withLock { $0 }
                let offset: Double
                if era.transportRolling,
                   let anchor = era.lockAnchorSongSeconds {
                    offset = songSeconds - anchor
                } else if let anchor = era.freerunAnchorHostSeconds {
                    offset = Double(hostTime) * Self.hostTickSeconds - anchor
                } else {
                    offset = 0
                }
                phase = AVAudioFrameCount(Self.phaseLockFrames(
                    offsetSeconds: offset,
                    bodyFrames: Int64(plan.body.frameLength),
                    sampleRate: Self.canonicalSampleRate))
            }
            if phase > 0,
               let head = Self.tailSegment(of: plan.body, from: phase) {
                ref.node.scheduleBuffer(
                    head, at: nil, options: [], completionHandler: nil)
            }
            // Parked node: the queued body begins at the next render
            // cycle on its own — no control call, same as the main lane.
            ref.node.scheduleBuffer(
                plan.body, at: nil, options: [.loops],
                completionHandler: nil)
            fastReleaseRefs.withLock {
                $0[tag] = FastReleaseRef(
                    mixer: ref.mixer, gate: ref.gate, epoch: epoch)
            }
            let firedPhase = phase
            fastFireBox.withLock {
                $0[tag] = FastFire(
                    index: ref.index, epoch: epoch, generation: gen,
                    bodyFrames: plan.body.frameLength,
                    phaseFrames: firedPhase,
                    fireHostTime: hostTime)
            }
            fastLaneStats.withLock { $0.pressFires += 1 }
            return true
        }
        fastLaneStats.withLock { $0.pressMisses += 1 }   // pool empty
        return false
    }

    // Test seams (nonisolated: assertable while main is blocked).
    nonisolated func hasFastPlan(tag: Int) -> Bool {
        fastPlanBox.withLock { $0.plans[tag]?.generation == $0.generation }
    }
    /// Test seam: the join phase the voice sounding this chop carries
    /// (fast-fire adoption must preserve it for the playhead ring).
    func voicePhaseFrames(stem: String, idx: Int) -> AVAudioFrameCount? {
        voices.first { $0.key == .chop(stem: stem, idx: idx) }?.phaseFrames
    }
    nonisolated var fastParkedCount: Int {
        fastParkedBox.withLock { $0.count }
    }
    nonisolated var pendingFastFireCount: Int {
        fastFireBox.withLock { $0.count }
    }

    /// Push a freshly PARKED voice into the shared claim pool. Every
    /// parking site calls this (warmUpPool, fade terminal, natural
    /// one-shot end) — a parked voice not in the box would be claimable
    /// by neither lane's pop and strand.
    private func pushFastParked(_ index: Int) {
        let v = voices[index]
        fastParkedBox.withLock {
            $0.append(FastParkedRef(
                index: index, node: v.node, delay: v.delay, eq: v.eq,
                mixer: v.mixer, gate: v.gate,
                parkEpoch: v.gate.current()))
        }
    }

    /// Main-path adoption of a receive-thread fire: bookkeeping ONLY —
    /// the audio is already sounding. Never advances the gate (the
    /// fire's fast-release ref must stay live until release).
    private func adoptFastFire(_ fire: FastFire, key: VoiceKey) {
        let index = fire.index
        let prior = voices.firstIndex { $0.key == key }
        voices[index].pendingPlay?.cancel()
        voices[index].pendingPlay = nil
        voices[index].fadeTask?.cancel()
        voices[index].fadeTask = nil
        voices[index].gen &+= 1
        voices[index].key = key
        voices[index].parked = false
        voices[index].loopFrames = fire.bodyFrames
        voices[index].phaseFrames = fire.phaseFrames
        // Playhead baseline: the body started at the FIRE, not at this
        // (possibly much later) hop — subtract the elapsed frames so
        // loopProgress tracks what is audible.
        if let rt = voices[index].node.lastRenderTime,
           let pt = voices[index].node.playerTime(forNodeTime: rt) {
            let elapsed = Double(mach_absolute_time() &- fire.fireHostTime)
                * Self.hostTickSeconds
            voices[index].startSampleTime = max(0, pt.sampleTime
                - Int64((elapsed * Self.canonicalSampleRate).rounded()))
        } else {
            voices[index].startSampleTime = 0
        }
        let stem: String? = {
            if case .chop(let s, _) = key { return s }
            return nil
        }()
        if voices[index].takeoverStem != stem { endTakeover(index) }
        if let s = stem, voices[index].takeoverStem != s {
            beginTakeover(index, stem: s)
        }
        // Rotation, exactly like a main-lane retrigger: the superseded
        // prior voice fades off the press path.
        if let prior, prior != index, voices[prior].key == key {
            fadeOutAndStop(prior)
        }
    }

    /// Kill a fire whose adoption failed (plan generation moved, grid
    /// remounted): stop the node iff the fire still owns it. Left
    /// un-parked — the next warm-up or claim recovers the slot.
    private func abortFastFire(_ fire: FastFire) {
        guard voices.indices.contains(fire.index),
              fire.epoch == voices[fire.index].gate.current(),
              voices[fire.index].gate.advanceIfCurrent(fire.epoch) != nil
        else { return }
        voices[fire.index].node.stop()
        voices[fire.index].parked = false
        voices[fire.index].key = nil
        voices[fire.index].loopFrames = nil
    }

    /// Abort every un-adopted fire (grid swap / stopAll): their content
    /// is about to be wrong, and no main trigger will come for them.
    private func abortAllFastFires() {
        let fires = fastFireBox.withLock { box -> [FastFire] in
            let all = Array(box.values)
            box.removeAll()
            return all
        }
        for fire in fires { abortFastFire(fire) }
    }

    private var files: [String: AVAudioFile] = [:]
    /// Readers for sequencer customURL sources, cached per URL.
    private var fileCache: [URL: AVAudioFile] = [:]

    /// Decoded-region cache: converted-to-canonical (+faded/crossfaded)
    /// buffers keyed by source+range+variant. The canonical-format redesign
    /// moved an 8 s read+SRC into the trigger path — first press paid
    /// tens of ms ("delay on pads"). Repeat triggers now schedule the
    /// cached buffer immediately. Cleared on load/unload; soft-capped.
    private struct RegionKey: Hashable {
        let url: URL
        let startFrame: AVAudioFramePosition
        let frameCount: AVAudioFrameCount
        let loopXfadeMs: Int   // -1 = one-shot variant
    }
    private var regionCache: [RegionKey: AVAudioPCMBuffer] = [:]
    private static let regionCacheCap = 48

    /// Baked LOOP cache: the fully-prepared loop body (onset-shifted,
    /// seam-crossfaded, cycle-tiled) plus its launch-compensation
    /// shift, keyed on the PRE-shift inputs (the shift is a pure
    /// function of them). Every playback mode loops the voice now
    /// (One-Shot/Follow gate included), so before this cache EVERY
    /// press paid the onset-scan file read + crossfade bake (~ms), and
    /// the FIRST press of each pad missed `regionCache` entirely — the
    /// bake reads a shifted, extended region whose key prewarm never
    /// warmed — and paid the whole 8 s read+SRC (tens of ms) in the
    /// touch path: the "pad press isn't immediate" hardware bug.
    private struct LoopKey: Hashable {
        let url: URL
        let startFrame: AVAudioFramePosition
        let frameCount: AVAudioFrameCount
        /// Crossfade in µs so Double never lands in a Hashable key.
        let xfadeMicroSec: Int
        let cycleFrames: Int
    }
    private var loopCache: [LoopKey: (buffer: AVAudioPCMBuffer, shiftSec: Double)] = [:]
    private static let loopCacheCap = 48
    /// Actual bakes performed (cache misses) — regression tests pin
    /// that a repeat trigger and a prewarmed first press are cache hits.
    private(set) var loopBakeCount = 0

    private static let poolSize = 16

    /// Destination the voice chains feed. Defaults to the engine's
    /// main mixer; SessionController points it at the MusicBus so
    /// master FX color the pads.
    public var outputNode: AVAudioNode?

    /// Live stem takeover (song augmentation): fired when the number of
    /// sounding chop voices for a stem role crosses 0↔active, so the host
    /// can duck (active=true) / restore (active=false) the song's own stem.
    /// Only bundle-chop voices carry a stem; pack/local-file voices don't.
    public var onStemTakeoverChange: ((_ role: String, _ active: Bool) -> Void)?

    /// How many sounding voices currently take over each stem role.
    private var takeoverCounts: [String: Int] = [:]

    private var destination: AVAudioNode {
        outputNode ?? avEngine.mainMixerNode
    }

    public init(avEngine: AVAudioEngine) {
        self.avEngine = avEngine
    }

    // MARK: - Load

    /// Open the session's stem files (same local URLs the stem player
    /// uses — AVAudioFile readers are independent, so sharing the URL
    /// is safe). Replaces any previous session's files.
    public func load(stemURLs: [String: URL]) async {
        stopAll()
        // One task PER stem, not one detached task for all: each open
        // pays CoreAudio's whole-file frame-table scan on compressed
        // stems, and six of those back to back were the bulk of the
        // post-download "Preparing audio…" stall. Each reader is
        // created and finished inside its own child task, so nothing
        // is shared until the merge here (DesktopStemPlayer's
        // openStemFiles uses the same pattern).
        let opened = await withTaskGroup(
            of: (String, AVAudioFile)?.self
        ) { group -> [String: AVAudioFile] in
            for (role, url) in stemURLs {
                group.addTask {
                    do {
                        return (role, try AVAudioFile(forReading: url))
                    } catch {
                        print("[ChopPlayer] failed to open \(role) at \(url.path): \(error)")
                        return nil
                    }
                }
            }
            var out: [String: AVAudioFile] = [:]
            for await r in group { if let r { out[r.0] = r.1 } }
            return out
        }
        files = opened
        regionCache.removeAll()
        loopCache.removeAll()
        fastReleaseRefs.withLock { $0.removeAll() }
        // Plans bake against the OLD session's readers — dead now. The
        // session re-arms after the new grid mounts. (Parked refs stay:
        // the voices themselves are untouched by a stem swap.)
        disarmFastPresses()
    }

    /// 44-bin peak envelope for a pad's chop — the grid tiles draw
    /// mobile/plugin-style waveforms from this. Reads the SAME cached
    /// region buffer playback uses (post-normalize), so the drawn
    /// shape is exactly what sounds. Cached per chop.
    private var peaksCache: [String: [Float]] = [:]

    public func peaks(for assignment: PadAssignment, bins: Int = 44) -> [Float]? {
        let chop = assignment.chop
        let key = "\(assignment.stem)#\(chop.idx)#\(bins)"
        if let hit = peaksCache[key] { return hit }
        guard bins > 0, let file = files[assignment.stem] else { return nil }
        let sr = file.processingFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, chop.startSec * sr))
        let frameCount = AVAudioFrameCount(max(1, (chop.endSec - chop.startSec) * sr))
        guard let buf = regionBuffer(file: file, startFrame: startFrame,
                                     frameCount: frameCount),
              let ch = buf.floatChannelData else { return nil }
        let frames = Int(buf.frameLength)
        guard frames > 0 else { return nil }
        let channels = Int(buf.format.channelCount)
        let per = max(1, frames / bins)
        var out = [Float](repeating: 0, count: bins)
        for b in 0..<bins {
            let s = b * per
            let e = min(frames, s + per)
            guard s < e else { break }
            var peak: Float = 0
            for c in 0..<channels {
                let p = ch[c]
                for i in s..<e { peak = max(peak, abs(p[i])) }
            }
            out[b] = peak
        }
        if let m = out.max(), m > 0 {
            for i in 0..<bins { out[i] /= m }
        }
        peaksCache[key] = out
        return out
    }

    public func unload() {
        peaksCache.removeAll()
        stopAll()
        files.removeAll()
        fileCache.removeAll()
        regionCache.removeAll()
        loopCache.removeAll()
        fastReleaseRefs.withLock { $0.removeAll() }
    }

    // MARK: - Prewarm

    /// Seam-crossfade floor for pad loops when the chop carries no
    /// measured Riley fade — shared by the live trigger path
    /// (SessionController) and prewarm so both derive the SAME bake-
    /// cache key; a mismatch would warm a key no press ever asks for.
    public static let defaultPadCrossfadeMs: Double = 15

    /// Decode-and-cache each assignment's buffers WITHOUT playing
    /// them, so the first real press schedules from cache instead of
    /// paying the 8 s read+SRC in the touch path ("delay on pads").
    /// Warms BOTH variants: the plain region AND the baked LOOP body
    /// (onset-shifted + crossfaded + cycle-tiled) — every playback
    /// mode loops the voice now (One-Shot/Follow gates included), so
    /// the loop bake is what the first press actually asks for; before
    /// this it missed the warm cache and paid the whole read+SRC+bake
    /// at the press. `loopBarSeconds`/`cycleSeconds` must match what
    /// the trigger path passes (SessionController wires both from the
    /// same tempo/kit state). Yields between pads so a 16-pad kit
    /// doesn't hitch the UI. Safe to race a real trigger — both caches
    /// re-check.
    public func prewarm(
        _ items: [(chop: Chop, stem: String)],
        loopBarSeconds: Double = 0,
        cycleSeconds: Double = 0
    ) async {
        for item in items {
            guard let file = files[item.stem] else { continue }
            let sampleRate = file.fileFormat.sampleRate
            let startFrame = AVAudioFramePosition(max(0, item.chop.startSec) * sampleRate)
            // Same frame math as schedule() — the region cache is keyed on
            // (startFrame, frameCount), so a prewarm that computed the
            // count differently would warm a key the real trigger misses.
            let frameCount = Self.regionFrameCount(
                startSec: item.chop.startSec, endSec: item.chop.endSec,
                sampleRate: sampleRate, fileLength: file.length)
            guard frameCount > 0, startFrame < file.length else { continue }
            _ = regionBuffer(file: file, startFrame: startFrame,
                             frameCount: AVAudioFrameCount(frameCount))
            await Task.yield()
            // LOOP variant — same endSec / crossfade / tile derivation
            // as the live path (trigger() + SessionController).
            let rileyFade = item.chop.crossfadeMs ?? 0
            let xfade = rileyFade > 0 ? rileyFade : Self.defaultPadCrossfadeMs
            let loopEnd = Self.loopRegionEndSec(
                chop: item.chop, loop: true, loopBarSeconds: loopBarSeconds)
            let loopFrames = Self.regionFrameCount(
                startSec: item.chop.startSec, endSec: loopEnd,
                sampleRate: sampleRate, fileLength: file.length)
            if loopFrames > 0 {
                _ = loopBuffer(
                    file: file, startFrame: startFrame,
                    frameCount: AVAudioFrameCount(loopFrames),
                    crossfadeMs: xfade,
                    tileToCycleSec: item.chop.loopScore != nil ? cycleSeconds : 0)
            }
            await Task.yield()
        }
    }

    /// Warm downloaded FILE pads (`drumfile:` composites, borrow loops):
    /// open the reader and decode BOTH variants a press can ask for —
    /// the whole-file one-shot region and the 12 ms-crossfade loop bake
    /// `trigger(file:)` derives — so a flood kit's first press doesn't
    /// pay AVAudioFile open + read + SRC on the press path. The file
    /// twin of `prewarm(_:)`, which only covers stem-chop regions; the
    /// frame math mirrors `trigger(file:)` exactly so the cache keys
    /// match the live press. Yields between files; safe to race a press
    /// (both caches re-check).
    public func prewarmFiles(_ urls: [URL]) async {
        for url in urls {
            guard let file = cachedFile(for: url) else { continue }
            let duration = Double(file.length) / file.fileFormat.sampleRate
            let frames = Self.regionFrameCount(
                startSec: 0, endSec: duration,
                sampleRate: file.fileFormat.sampleRate,
                fileLength: file.length)
            guard frames > 0 else { continue }
            _ = regionBuffer(file: file, startFrame: 0,
                             frameCount: AVAudioFrameCount(frames))
            await Task.yield()
            _ = loopBuffer(file: file, startFrame: 0,
                           frameCount: AVAudioFrameCount(frames),
                           crossfadeMs: 12)
            await Task.yield()
        }
    }

    /// Test seam: file readers currently open (prewarmFiles fills it,
    /// trigger(file:) reuses it).
    var cachedFileCount: Int { fileCache.count }

    // MARK: - Trigger / release

    /// Play `assignment`'s chop after `delaySeconds` of wall-clock
    /// time (0 = now), with `effects` pushed onto the voice's
    /// delay/filter chain and `velocity`/`pan` on its mixer. No-op
    /// when the stem file is missing or the engine isn't running.
    public func trigger(
        _ assignment: PadAssignment,
        afterSeconds delaySeconds: Double,
        effects: SamplePadEffects = .neutral,
        velocity: Float = 1,
        pan: Float = 0,
        loop: Bool = false,
        crossfadeMs: Double = 0,
        loopBarSeconds: Double = 0,
        cycleSeconds: Double = 0,
        phaseOffsetSeconds: Double = 0,
        padTag: Int? = nil
    ) {
        guard let file = files[assignment.stem] else { return }
        let chop = assignment.chop
        let endSec = Self.loopRegionEndSec(
            chop: chop, loop: loop, loopBarSeconds: loopBarSeconds)
        // Shared-cycle lock (web c726ba58 parity): only pads with a REAL
        // analyzer loop region (loopScore present — the same set
        // LaunchpadController.loopLengthSeconds is derived from) tile their
        // seam-baked body up to the shared cycle so stacked latched loops
        // restart together and stay in unison. Region-less pads (constant-
        // tempo bar-snap, borrow whole-buffer loops) keep their own length —
        // the shared cycle isn't their musical period. Gate is nonzero only
        // when looping AND the chop carries an analyzer region.
        let tileToCycleSec = (loop && chop.loopScore != nil) ? cycleSeconds : 0
        schedule(
            file: file,
            startSec: chop.startSec,
            endSec: endSec,
            key: .chop(stem: assignment.stem, idx: chop.idx),
            effects: effects,
            velocity: velocity,
            pan: pan,
            afterSeconds: delaySeconds,
            loop: loop,
            crossfadeMs: crossfadeMs,
            tileToCycleSec: tileToCycleSec,
            phaseOffsetSeconds: phaseOffsetSeconds,
            padTag: padTag
        )
    }

    /// Frame count for a [startSec, endSec] region: ROUND the region
    /// LENGTH, never trunc each edge (trunc(end·sr) − trunc(start·sr)
    /// lands on N or N+1 frames depending on each edge's fractional
    /// phase). The shared-cycle lock compares against round(cycleSec·sr);
    /// a cycle-defining pad that trunc'd long skipped the tile and looped
    /// 1 frame longer than every pad tiled to N — a ~21 µs/cycle relative
    /// slip that walks phase-locked loops apart over minutes. Rounding
    /// the length keeps every pad consistent with the cycle computation
    /// (web padengine._bakePad parity). Clamped to the file's remainder;
    /// pure so both schedule() and prewarm() derive identical cache keys.
    nonisolated static func regionFrameCount(
        startSec: Double, endSec: Double,
        sampleRate: Double, fileLength: AVAudioFramePosition
    ) -> AVAudioFramePosition {
        guard sampleRate > 0 else { return 0 }
        let startFrame = AVAudioFramePosition(max(0, startSec) * sampleRate)
        let requested = AVAudioFramePosition(
            (max(0, endSec - startSec) * sampleRate).rounded())
        return min(requested, max(0, fileLength - startFrame))
    }

    /// The loop region's end after the phase-lock snap decision — pure
    /// (nonisolated, no engine) so tests can pin the verbatim rule.
    ///
    /// Looping constant-tempo regions snap to a whole number of bars so
    /// they stay aligned to the downbeat grid forever (a slightly-off
    /// length drifts). EXCEPT analyzer-provided regions (loopScore
    /// present): the kit builder exports those on the song's REAL local
    /// downbeats, which drift a few dozen ms per bar against the constant
    /// tempo — the constant re-snap cut the region short of the real
    /// downbeat, so the wrap landed in the pre-beat gap and the loop
    /// audibly paused (Doomsday drums: real 3 bars 7.570 s vs 7.545 s at
    /// constant BPM). Those regions are whole bars by construction — play
    /// them verbatim.
    nonisolated static func loopRegionEndSec(
        chop: Chop, loop: Bool, loopBarSeconds: Double
    ) -> Double {
        guard loop, loopBarSeconds > 0, chop.loopScore == nil else {
            return chop.endSec
        }
        let loopSec = chop.endSec - chop.startSec
        let bars = max(1, (loopSec / loopBarSeconds).rounded())
        return chop.startSec + bars * loopBarSeconds
    }

    /// Play a [startSec, endSec] segment of an arbitrary local file
    /// (sequencer customURL path). nil bounds = whole file. Readers
    /// are cached per URL; open failures are logged and dropped.
    public func trigger(
        file url: URL,
        startSec: Double?,
        endSec: Double?,
        effects: SamplePadEffects = .neutral,
        velocity: Float = 1,
        pan: Float = 0,
        afterSeconds delaySeconds: Double = 0,
        loop: Bool = false,
        phaseOffsetSeconds: Double = 0,
        padTag: Int? = nil
    ) {
        guard let file = cachedFile(for: url) else { return }
        let duration = Double(file.length) / file.fileFormat.sampleRate
        // Borrow loops are exactly 2 bars at the target tempo, so looping the
        // whole file phase-locks with no bar math needed.
        schedule(
            file: file,
            startSec: startSec ?? 0,
            endSec: endSec ?? duration,
            key: .file(url),
            effects: effects,
            velocity: velocity,
            pan: pan,
            afterSeconds: delaySeconds,
            loop: loop,
            crossfadeMs: loop ? 12 : 0,
            phaseOffsetSeconds: phaseOffsetSeconds,
            padTag: padTag
        )
    }

    private func schedule(
        file: AVAudioFile,
        startSec: Double,
        endSec: Double,
        key: VoiceKey,
        effects: SamplePadEffects,
        velocity: Float,
        pan: Float,
        afterSeconds delaySeconds: Double,
        loop: Bool = false,
        crossfadeMs: Double = 0,
        tileToCycleSec: Double = 0,
        phaseOffsetSeconds: Double = 0,
        padTag: Int? = nil
    ) {
        guard avEngine.isRunning else {
            print("[ChopPlayer] dropped trigger: engine not running")
            return
        }
        // FAST-PRESS ADOPTION (D-038): the receive thread may already
        // have fired this pad's plan on a parked voice within a render
        // quantum of the packet. This main trigger then only does the
        // bookkeeping the fast lane couldn't — never a second schedule.
        // A stale fire (plan generation moved: the grid/mode changed
        // between fire and hop, so THIS trigger's content differs from
        // what fired) is killed and the press replays normally.
        if let padTag,
           let fire = fastFireBox.withLock({ $0.removeValue(forKey: padTag) }) {
            if loop,
               fire.generation == fastPlanBox.withLock({ $0.generation }),
               voices.indices.contains(fire.index),
               voices[fire.index].gate.current() == fire.epoch {
                adoptFastFire(fire, key: key)
                return
            }
            abortFastFire(fire)
        }
        let sampleRate = file.fileFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, startSec) * sampleRate)
        let frameCount = Self.regionFrameCount(
            startSec: startSec, endSec: endSec,
            sampleRate: sampleRate, fileLength: file.length)
        guard frameCount > 0, startFrame < file.length else { return }

        // Prepare the audio BEFORE touching any voice: the parked-voice
        // fast path needs the effective start delay before a buffer
        // lands on a node — a parked (playing, empty-queue) player
        // begins a scheduled buffer at the next render cycle, so a
        // QUANTIZED start must un-park (stop) its node before queueing.
        var loopPlan: (body: AVAudioPCMBuffer, head: AVAudioPCMBuffer?,
                       phaseFrames: AVAudioFrameCount)?
        var oneShotBuffer: AVAudioPCMBuffer?
        var effectiveDelay = delaySeconds
        if loop, let baked = loopBuffer(file: file, startFrame: startFrame,
                                        frameCount: AVAudioFrameCount(frameCount),
                                        crossfadeMs: crossfadeMs,
                                        tileToCycleSec: tileToCycleSec) {
            // Phase-locked join: a loop tapped mid-jam must join at the SAME
            // cycle position as the loops already playing, so every pad's
            // playhead moves together (not just bar-aligned starts). Start
            // the first pass `phase` frames INTO the baked body — the
            // lattice offset (pre-shift boundary − lock anchor, supplied by
            // the controller) folded mod the body. It must NOT be measured
            // from the shifted launch: reading the per-pad onset shift back
            // in as buffer offset cancels the compensation below and pads
            // flam by their shift deltas. AVAudioPlayerNode can't start a
            // looping buffer mid-body, so queue the tail once, then the
            // whole body with .loops (web source.start(when, phase) twin).
            let phase = Self.phaseLockFrames(
                offsetSeconds: phaseOffsetSeconds,
                bodyFrames: Int64(baked.buffer.frameLength),
                sampleRate: Self.canonicalFormat.sampleRate)
            var head: AVAudioPCMBuffer?
            var phaseFrames: AVAudioFrameCount = 0
            if phase > 0, let tail = Self.tailSegment(
                of: baked.buffer, from: AVAudioFrameCount(phase)) {
                phaseFrames = AVAudioFrameCount(phase)
                head = tail
            }
            loopPlan = (baked.buffer, head, phaseFrames)
            // Launch compensation for the onset-phase snap: the region was
            // shifted so its cut sits just before the attack, which moves
            // the content's downbeat off the region start by `shiftSec`.
            // Delay the launch by the same amount so the downbeat still
            // lands ON the quantize grid — without this, pads with
            // different shifts (drums +9 ms, bass +47 ms measured) armed
            // to the same boundary but sounded at different times.
            effectiveDelay = max(0, delaySeconds + baked.shiftSec)
        } else if let buffer = regionBuffer(file: file, startFrame: startFrame,
                                            frameCount: AVAudioFrameCount(frameCount)) {
            oneShotBuffer = buffer
        } else {
            // Buffer read/convert failed — skip the trigger. (No raw
            // scheduleSegment fallback: the file's native format may not
            // match the canonical chain, and a mismatched schedule is the
            // same crash class we're eliminating.)
            print("[ChopPlayer] dropped trigger: region read failed")
            return
        }

        // RETRIGGER = ROTATE (web padengine parity — a re-trigger release-
        // fades the pad's prior source and starts a NEW one): the press
        // claims a parked/idle voice and the superseded voice fades out
        // underneath, off the press path (see bottom). The old same-slot
        // steal did stop()+play() on the press path — play() blocks its
        // caller up to a full render quantum (~10.7 ms measured), which
        // serialized rapid same-pad presses into 100–500 ms main-queue
        // pileups on hardware.
        let prior = voices.firstIndex { $0.key == key }

        let (index, fastEpoch) = claimVoiceAtomically(for: key)
        // Stem this trigger takes over (bundle chops only; file voices don't
        // duck the song). End the claimed slot's PRIOR takeover first — unless
        // it's the same stem (a same-stem retrigger keeps the duck, no blip).
        let takeoverStem: String? = { if case .chop(let s, _) = key { return s }; return nil }()
        if voices[index].takeoverStem != takeoverStem { endTakeover(index) }
        // Kill the slot's prior async state BEFORE reuse: a still-armed
        // start must not fire under the new voice, and an in-flight
        // release fade must not touch it — the epoch advance (done
        // ATOMICALLY inside the claim, so the receive-thread fast press
        // can never own the same node) is the atomic half of that
        // promise (VoiceGate): a detached fade between its cancel check
        // and its terminal can no longer stop or re-volume this slot.
        voices[index].pendingPlay?.cancel()
        voices[index].pendingPlay = nil
        voices[index].fadeTask?.cancel()
        voices[index].fadeTask = nil

        var voice = voices[index]
        voice.gen &+= 1
        let capturedGen = voice.gen

        // Node control: a PARKED node needs NO call for an instant start
        // (the queued buffer begins at the next render cycle on its own).
        // Stop only a sounding stolen node (clear its old queue) or a
        // parked node facing a QUANTIZED launch (a parked node cannot
        // defer a buffer; play(at:) needs a stopped player).
        if voice.node.isPlaying {
            if !voice.parked || effectiveDelay > 0.001 {
                voice.node.stop()
                voice.parked = false
            }
        } else {
            voice.parked = false
        }

        // Voice chains are wired ONCE at the canonical 48 kHz stereo format
        // (lazy, reset by reattach). Buffers are CONVERTED to canonical at
        // read instead of rewiring the chain per file format — per-format
        // engine.connect calls threw -10868 / NSException whenever a trigger
        // raced a device reconfig or crossed sample rates (drums 44.1 k).
        if !voice.outputWired {
            wireVoice(voice)
            voice.outputWired = true
            voice.format = Self.canonicalFormat
        }
        applyEffects(effects.clamped(), to: voice)
        voice.mixer.outputVolume = min(max(velocity, 0), 1)
        voice.mixer.pan = min(max(pan, -1), 1)

        if let plan = loopPlan {
            if let head = plan.head {
                voice.node.scheduleBuffer(head, at: nil, options: [], completionHandler: nil)
            }
            // Seamless looping: the [start,end] region is read into a buffer,
            // crossfaded (SeamlessLoop) and hard-looped so a held pad never clicks.
            voice.node.scheduleBuffer(plan.body, at: nil, options: [.loops], completionHandler: nil)
            voice.loopFrames = plan.body.frameLength
            voice.phaseFrames = plan.phaseFrames
        } else if let buffer = oneShotBuffer {
            // One-shot: read the region into a buffer and micro-fade its
            // edges so a slice that doesn't start/end on a zero-crossing
            // (stabs, drum hits) doesn't click on attack or tail. On natural
            // end (.dataPlayedBack = audible-complete, not merely consumed
            // by the render loop) restore the taken-over stem AND free the
            // slot (gen-guarded against reuse) — web source.onended,
            // padengine.js:1242-1256. Without the slot clear a finished
            // stab held its voice "sounding" forever and the free-run
            // re-anchor check saw a dead jam as live (iOS fix 77231913 #7).
            voice.node.scheduleBuffer(
                buffer, at: nil, options: [],
                completionCallbackType: .dataPlayedBack,
                completionHandler: oneShotCompletion(index, gen: capturedGen))
            voice.loopFrames = nil
            voice.phaseFrames = 0
        }
        voice.key = key
        voice.parked = false   // sounding (or armed) from here on
        voices[index] = voice
        scheduleStart(index: index, afterSeconds: effectiveDelay, gen: capturedGen)
        // Begin the new takeover AFTER the struct write-back (which would
        // otherwise clobber takeoverStem). Skip if already taking over this
        // same stem on this voice (same-stem retrigger — count unchanged).
        if let s = takeoverStem, voices[index].takeoverStem != s {
            beginTakeover(index, stem: s)
        }
        // Register the receive-thread fast-release ref for this pad: the
        // MIDI thread can begin THIS voice's audible fade the moment the
        // pad-up packet decodes, without waiting on the main hop. Epoch-
        // stamped, so once the voice is released or reused the ref is
        // inert.
        if let padTag {
            let ref = FastReleaseRef(
                mixer: voices[index].mixer,
                gate: voices[index].gate,
                epoch: fastEpoch)
            fastReleaseRefs.withLock { $0[padTag] = ref }
        }
        // Rotation: fade the superseded voice AFTER the new takeover began
        // (same-stem count goes 1→2→1 — never a 0-crossing duck/restore
        // blip), entirely off the press path. When the pool was so starved
        // that claimVoice stole the prior voice itself, this is a no-op
        // and behavior degrades to the old same-slot steal.
        if let prior, prior != index, voices[prior].key == key {
            fadeOutAndStop(prior)
        }
    }

    /// Completion handler for a one-shot voice: fired when the audio has
    /// PLAYED OUT (.dataPlayedBack; a stop() fires it early too). Hops to
    /// the main actor and — only if this slot hasn't since been reused
    /// (gen match) — ends the takeover and FREES the slot, so
    /// `soundingVoiceCount` reflects what is actually audible (the
    /// free-run re-anchor and idle-voice reuse both key on it).
    /// endTakeover is idempotent, so a stop-then-complete is safe; a
    /// stopped voice was already freed by release(), and the gen guard
    /// makes the late callback a no-op after any retrigger.
    private nonisolated func oneShotCompletion(
        _ index: Int, gen: Int
    ) -> AVAudioPlayerNodeCompletionHandler {
        { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                guard self.voices.indices.contains(index),
                      self.voices[index].gen == gen else { return }
                self.endTakeover(index)
                // Loops never arrive here (no completion scheduled); only
                // clear a one-shot's claim.
                if self.voices[index].loopFrames == nil {
                    self.voices[index].key = nil
                    // Played out NATURALLY → the node is still running
                    // with an empty queue: that IS the parked state, so
                    // the slot goes straight back into the zero-cost
                    // claim pool. An early completion from a stop()
                    // reports !isPlaying and stays un-parked.
                    let parked = self.voices[index].node.isPlaying
                    self.voices[index].parked = parked
                    if parked { self.pushFastParked(index) }
                }
            }
        }
    }

    /// Normalized playhead (0..<1) of a hard-looping pad, or nil if that pad
    /// isn't currently looping. Drives the on-pad playback ring. The player
    /// node's sampleTime counts total frames rendered since play; modulo the
    /// loop length gives the position within the current loop.
    public func loopProgress(stem: String, idx: Int) -> Double? {
        loopProgress(for: .chop(stem: stem, idx: idx))
    }

    /// Same, for a file-backed loop voice (borrow loops, sequencer customURL).
    /// Borrow pads play through `trigger(file:loop:)` keyed by `.file(url)`,
    /// not `.chop`, so the stem/idx lookup missed them and the on-pad playhead
    /// never drew. Resolve by URL for those.
    public func loopProgress(fileURL: URL) -> Double? {
        loopProgress(for: .file(fileURL))
    }

    private func loopProgress(for target: VoiceKey) -> Double? {
        for v in voices where v.key == target {
            guard let frames = v.loopFrames, frames > 0, v.node.isPlaying,
                  let rt = v.node.lastRenderTime,
                  let pt = v.node.playerTime(forNodeTime: rt) else { return nil }
            // Rendered frames SINCE THIS LAUNCH: a voice launched on a
            // parked node inherits the park's running sample clock, so
            // subtract the baseline captured at schedule time.
            let s = pt.sampleTime - v.startSampleTime
            guard s >= 0 else { return 0 }  // scheduled but not yet fired
            return Self.loopProgressValue(
                renderedFrames: s, phaseFrames: Int64(v.phaseFrames),
                bodyFrames: Int64(frames))
        }
        return nil
    }

    /// Normalized loop position for a voice that started `phaseFrames` into
    /// its body (phase-locked join). Rendered frames count from the LAUNCH,
    /// so the true buffer position is (rendered + phase) mod body — the
    /// phase must be added or the drawn playhead reports where the voice
    /// would be had it started at frame 0, visually desynced from audio
    /// that IS locked (web padProgress parity). Pure for the test suite.
    nonisolated static func loopProgressValue(
        renderedFrames: Int64, phaseFrames: Int64, bodyFrames: Int64
    ) -> Double {
        guard bodyFrames > 0 else { return 0 }
        return Double((renderedFrames + phaseFrames) % bodyFrames) / Double(bodyFrames)
    }

    /// Fold a lock-lattice offset (seconds since the lock-era anchor, wall
    /// domain) into a start offset within a `bodyFrames`-long loop. Double-
    /// mod so a negative offset (transport seeked behind the anchor) still
    /// lands in [0, body). Pure for the test suite — the web twin is
    /// `phase = ((boundary − anchor) % body + body) % body`.
    nonisolated static func phaseLockFrames(
        offsetSeconds: Double, bodyFrames: Int64, sampleRate: Double
    ) -> Int64 {
        guard bodyFrames > 0, sampleRate > 0, offsetSeconds.isFinite,
              offsetSeconds != 0 else { return 0 }
        let raw = Int64((offsetSeconds * sampleRate).rounded())
        return ((raw % bodyFrames) + bodyFrames) % bodyFrames
    }

    /// Copy of `src` from `startFrame` to its end — the first (partial)
    /// pass of a phase-locked join. Nil when the slice is empty/degenerate
    /// (caller falls back to a phase-0 start).
    nonisolated private static func tailSegment(
        of src: AVAudioPCMBuffer, from startFrame: AVAudioFrameCount
    ) -> AVAudioPCMBuffer? {
        let total = src.frameLength
        guard startFrame > 0, startFrame < total,
              let srcData = src.floatChannelData,
              let dst = AVAudioPCMBuffer(
                  pcmFormat: src.format, frameCapacity: total - startFrame),
              let dstData = dst.floatChannelData else { return nil }
        let count = Int(total - startFrame)
        for c in 0..<Int(src.format.channelCount) {
            dstData[c].update(from: srcData[c] + Int(startFrame), count: count)
        }
        dst.frameLength = total - startFrame
        return dst
    }

    /// Read a [startFrame, frameCount] region into an edge-faded PCM buffer.
    /// The micro-fades kill attack/tail clicks on one-shots and the first
    /// pass of a loop. Returns nil on read failure.
    private func regionBuffer(
        file: AVAudioFile, startFrame: AVAudioFramePosition,
        frameCount: AVAudioFrameCount
    ) -> AVAudioPCMBuffer? {
        let key = RegionKey(url: file.url, startFrame: startFrame,
                            frameCount: frameCount, loopXfadeMs: -1)
        if let cached = regionCache[key] { return cached }
        guard let raw = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: frameCount) else { return nil }
        do {
            file.framePosition = startFrame
            try file.read(into: raw, frameCount: frameCount)
        } catch {
            print("[ChopPlayer] region read failed: \(error)")
            return nil
        }
        // Canonical 48 kHz stereo — the ONLY format the voice chains speak.
        guard let buf = Self.toCanonical(raw) else {
            print("[ChopPlayer] region convert failed")
            return nil
        }
        // Loudness parity with the mobile scheduler + the jamn Kit
        // plugin: peak-normalize every chop to -4 dBFS. Raw stem
        // slices vary by tens of dB; un-normalized, quiet parts read
        // as "pad doesn't work".
        Self.normalizePeak(buf)
        SeamlessLoop.applyEdgeFades(buf)
        cacheRegion(buf, for: key)
        return buf
    }

    /// Peak-normalize to -4 dBFS (0.63 linear), matching mobile's
    /// SampleScheduler target. Effectively-silent buffers are left
    /// untouched (amplifying noise floor bursts on tap).
    private static func normalizePeak(_ buf: AVAudioPCMBuffer) {
        guard let channels = buf.floatChannelData else { return }
        let frames = Int(buf.frameLength)
        let channelCount = Int(buf.format.channelCount)
        guard frames > 0, channelCount > 0 else { return }
        var peak: Float = 0
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frames where abs(ptr[i]) > peak {
                peak = abs(ptr[i])
            }
        }
        guard peak > 1e-4 else { return }
        // Boost cap +12 dB (mobile parity): un-capped normalize turned
        // bleed-only quiet slices into foreground fuzz.
        let gain = min(0.63 / peak, 4.0)
        guard abs(gain - 1.0) > 0.01 else { return }
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frames {
                ptr[i] *= gain
            }
        }
    }

    private func cacheRegion(_ buf: AVAudioPCMBuffer, for key: RegionKey) {
        if regionCache.count >= Self.regionCacheCap {
            regionCache.removeAll()   // simple flush; next presses re-fill
        }
        regionCache[key] = buf
    }

    /// Read a [startFrame, frameCount] region and apply the seam crossfade
    /// for gapless looping. EVERY looping voice gets a seam — a measured
    /// length when supplied, else the default floor — so an unscored loop
    /// never hard-loops with a click. Returns nil on read failure.
    ///
    /// EXACT LENGTH: the seam is baked with SeamlessLoop.exactCrossfaded so
    /// the loop period stays exactly the bar-snapped region. The old
    /// `crossfaded()` trimmed the crossfade off the buffer, so every held
    /// loop ran 8–30 ms short of the grid and drifted (the bug the jamn Kit
    /// plugin fixed with its runtime dual-read). We read up to one crossfade
    /// of CONTINUATION audio past the region end and blend it into the head;
    /// when the region ends at the file's end the bake falls back to
    /// exact-length edge ramps instead of trimming.
    ///
    /// SHARED-CYCLE LOCK: when `tileToCycleSec > 0` (an analyzer-region loop
    /// pad, gated by the caller) the seam-baked body is TILED up to the shared
    /// cycle (`LaunchpadController.loopLengthSeconds` = the longest such region)
    /// so every latched loop shares one period and restarts in unison — a
    /// shorter section repeats inside the cycle instead of running on its own
    /// length and drifting. The longest pad already fills the cycle (no tiling).
    /// `voice.loopFrames` then reads the tiled length, so the playhead ring
    /// tracks the shared period. Web parity: padengine.js `_bakePad` (c726ba58).
    private func loopBuffer(
        file: AVAudioFile, startFrame: AVAudioFramePosition,
        frameCount: AVAudioFrameCount, crossfadeMs: Double,
        tileToCycleSec: Double = 0
    ) -> (buffer: AVAudioPCMBuffer, shiftSec: Double)? {
        let xfadeMs = crossfadeMs > 0 ? crossfadeMs : SeamlessLoop.defaultLoopCrossfadeMs
        // Bake cache: keyed on the pre-shift inputs (the onset shift is
        // deterministic in them), so repeat presses — and prewarmed
        // first presses — schedule instantly instead of re-reading and
        // re-baking the body in the touch path.
        let key = LoopKey(
            url: file.url, startFrame: startFrame, frameCount: frameCount,
            xfadeMicroSec: Int((xfadeMs * 1000).rounded()),
            cycleFrames: tileToCycleSec > 0
                ? Int((tileToCycleSec * Self.canonicalFormat.sampleRate).rounded())
                : 0)
        if let hit = loopCache[key] { return hit }
        loopBakeCount += 1
        let srcRate = file.processingFormat.sampleRate
        // Onset-phase snap: the grid's downbeat timestamps land tens of ms
        // AFTER the audible attack, so a grid-cut region starts just past
        // its own kick and the wrap plays tail → kickless head — an audible
        // pause even with an exact period. Shift BOTH edges (period kept)
        // so the cut sits ~5 ms before the strongest nearby onset; sustained
        // heads see no clear transient and shift 0.
        var start = startFrame
        if srcRate > 0 {
            let search = AVAudioFramePosition((0.060 * srcRate).rounded())
            let lo = max(0, startFrame - search)
            let scanLen = AVAudioFrameCount(
                max(0, min(file.length - lo, (startFrame - lo) + search)))
            if scanLen > 0,
               let scan = AVAudioPCMBuffer(pcmFormat: file.processingFormat,
                                           frameCapacity: scanLen) {
                do {
                    file.framePosition = lo
                    try file.read(into: scan, frameCount: scanLen)
                    let shift = SeamlessLoop.onsetAlignedShift(
                        scan, centerFrame: Int(startFrame - lo),
                        searchFrames: Int(search),
                        prerollFrames: Int(0.005 * srcRate))
                    let s2 = startFrame + AVAudioFramePosition(shift)
                    if s2 >= 0, s2 + AVAudioFramePosition(frameCount) <= file.length {
                        start = s2
                    }
                } catch {}
            }
        }
        var extra: AVAudioFrameCount = 0
        if srcRate > 0 {
            let want = AVAudioFramePosition((xfadeMs / 1000.0 * srcRate).rounded(.up))
            let avail = max(0, file.length - start - AVAudioFramePosition(frameCount))
            extra = AVAudioFrameCount(min(want, avail))
        }
        guard let buf = regionBuffer(file: file, startFrame: start,
                                     frameCount: frameCount + extra) else { return nil }
        // Loop body length in the canonical domain: regionBuffer converts to
        // 48 kHz, so rescale the file-domain frame count; converter jitter
        // (±a frame) lands in the continuation, never in the body.
        let ratio = srcRate > 0 ? Self.canonicalFormat.sampleRate / srcRate : 1
        let body = min(Int(buf.frameLength),
                       Int((Double(frameCount) * ratio).rounded()))
        let shiftSec = srcRate > 0 ? Double(start - startFrame) / srcRate : 0
        var looped = SeamlessLoop.exactCrossfaded(buf, loopFrames: body, crossfadeMs: xfadeMs)
        // Shared-cycle lock: tile the seam-baked body up to the shared cycle
        // (canonical-rate frames — `looped` is already at canonicalFormat).
        // No-op when the body already fills (or exceeds) the cycle — the
        // longest region pad. tileToLength returns the input unchanged for
        // target <= body, so this is safe even if rounding lands equal.
        if tileToCycleSec > 0 {
            let cycleFrames = Int((tileToCycleSec * Self.canonicalFormat.sampleRate).rounded())
            if cycleFrames > Int(looped.frameLength) {
                looped = SeamlessLoop.tileToLength(looped, targetFrames: cycleFrames)
            }
        }
        if loopCache.count >= Self.loopCacheCap {
            loopCache.removeAll()   // simple flush, matching regionCache
        }
        loopCache[key] = (looped, shiftSec)
        return (looped, shiftSec)
    }

    private func cachedFile(for url: URL) -> AVAudioFile? {
        if let file = fileCache[url] { return file }
        do {
            let file = try AVAudioFile(forReading: url)
            fileCache[url] = file
            return file
        } catch {
            print("[ChopPlayer] failed to open \(url.path): \(error)")
            return nil
        }
    }

    /// 20 ms release fade (web padengine.js release :1296; mobile
    /// SampleVoicePool.releaseFadeSec). A latched loop toggled off used
    /// to hard-stop mid-body — an audible click no other surface has.
    nonisolated public static let releaseFadeSec: Double = 0.020

    /// Stop the voice sounding `assignment`'s chop (pad released).
    public func release(_ assignment: PadAssignment) {
        let key = VoiceKey.chop(stem: assignment.stem, idx: assignment.chop.idx)
        for index in voices.indices where voices[index].key == key {
            fadeOutAndStop(index)
        }
    }

    /// Stop the voice sounding a downloaded FILE (borrow / drumfile pads play
    /// through `trigger(file:)`, keyed `.file(url)` — `release(assignment)`
    /// keys `.chop` and would never find them, so a re-tap left them looping).
    public func release(fileURL url: URL) {
        let key = VoiceKey.file(url)
        for index in voices.indices where voices[index].key == key {
            fadeOutAndStop(index)
        }
    }

    public func stopAll() {
        // Un-adopted receive-thread fires are sounding with a nil key —
        // the keyed sweep below can't see them, and after a grid swap no
        // main trigger will ever come to adopt them. Kill them first.
        abortAllFastFires()
        for index in voices.indices where voices[index].key != nil {
            fadeOutAndStop(index)
        }
    }

    /// Release a voice: free the slot IMMEDIATELY (accounting must not
    /// wait out the fade — re-triggers, `soundingVoiceCount` and the
    /// free-run silence check all read it), then stop the node.
    ///
    /// - Armed, not yet audible (pendingPlay set): cancel the deadline
    ///   item and hard-stop — `stop()` also discards a sample-accurate
    ///   scheduled start; there is nothing audible to fade.
    /// - Sounding: ramp the voice mixer to zero over `releaseFadeSec`
    ///   (8 steps, mobile releaseSlot parity) and stop at the bottom —
    ///   web's 20 ms gain ramp. Gen-guarded + cancellable so a slot
    ///   reused mid-fade is never stopped by the stale fade.
    private func fadeOutAndStop(_ index: Int) {
        endTakeover(index)
        voices[index].key = nil
        voices[index].loopFrames = nil
        voices[index].phaseFrames = 0
        if let pending = voices[index].pendingPlay {
            pending.cancel()
            voices[index].pendingPlay = nil
            voices[index].fadeTask?.cancel()
            voices[index].fadeTask = nil
            voices[index].gate.advance()
            voices[index].node.stop()
            // Left un-parked (parking here would block main ~a render
            // quantum for a rare case); the next claim pays one play().
            voices[index].parked = false
            return
        }
        voices[index].fadeTask?.cancel()
        let node = voices[index].node
        let mixer = voices[index].mixer
        let startVol = mixer.outputVolume
        let gen = voices[index].gen
        let gate = voices[index].gate
        let epoch = gate.advance()   // supersede any older fade
        let engine = avEngine
        // The ramp runs OFF the main actor: it used to await the main
        // actor between its 8 × 2.5 ms steps, so a busy UI (the full-
        // window grid repaint a pad press itself provokes) stretched
        // the 20 ms fade to 100+ ms of audible tail — the hardware
        // "release sticks" bug. Mixer volume and player stop()/play()
        // are thread-safe (scheduleStart's fallback already calls
        // play() from a dispatch thread); only slot bookkeeping hops
        // back. Every node touch is epoch-guarded (VoiceGate): a press
        // that reuses this slot advances the epoch atomically, so a
        // stale ramp step can't drag the new voice's volume down and a
        // stale terminal can't stop a buffer the press just scheduled.
        voices[index].fadeTask = Task.detached(priority: .userInitiated) { [weak self] in
            let steps = 8
            let stepSec = Self.releaseFadeSec / Double(steps)
            for step in 1...steps {
                if Task.isCancelled { return }
                gate.ifCurrent(epoch) {
                    mixer.outputVolume = startVol * Float(steps - step) / Float(steps)
                }
                try? await Task.sleep(nanoseconds: UInt64(stepSec * 1_000_000_000))
            }
            if Task.isCancelled { return }
            // Terminal: stop the node, then RE-PARK it (play() with an
            // empty queue) so the next press starts its buffer with no
            // control-call roundtrip. The gate is held only around
            // stop() and a pure epoch re-check — NEVER through play(),
            // which blocks up to a render quantum: holding it there
            // made a colliding press wait that long inside its own
            // epoch advance (measured 20–46 ms spikes at saturation).
            // A press interleaving anywhere here advances the epoch
            // BEFORE its first node op, so the re-check no-ops the park;
            // the one residual hole (press advances in the sub-µs after
            // the re-check passes) can start the node under a QUANTIZED
            // claim of this exact fading slot — reachable only at full
            // pool saturation since claims prefer other parked voices,
            // and worth one early loop start, not a wrong note.
            gate.ifCurrent(epoch) { node.stop() }
            var parkedNow = false
            if engine.isRunning {
                gate.ifCurrent(epoch) { parkedNow = true }
                if parkedNow { node.play() }
            }
            let didPark = parkedNow
            let player = self
            await MainActor.run {
                guard let player, player.voices.indices.contains(index),
                      player.voices[index].gen == gen else { return }
                player.voices[index].fadeTask = nil
                player.voices[index].parked = didPark
                // Back into the shared claim pool (fast press + main
                // claims both pop it) the moment the flag lands.
                if didPark { player.pushFastParked(index) }
            }
        }
    }

    // MARK: - Stem takeover (song augmentation)

    /// This voice begins taking over `stem`. Ref-counted per role; the host
    /// is notified only when a role goes from 0 → active.
    private func beginTakeover(_ index: Int, stem: String) {
        voices[index].takeoverStem = stem
        let c = takeoverCounts[stem] ?? 0
        takeoverCounts[stem] = c + 1
        if c == 0 { onStemTakeoverChange?(stem, true) }
    }

    /// This voice stops taking over its stem (if any). The host is notified
    /// only when the role's count returns to 0.
    private func endTakeover(_ index: Int) {
        guard let stem = voices[index].takeoverStem else { return }
        voices[index].takeoverStem = nil
        let c = takeoverCounts[stem] ?? 0
        if c <= 1 {
            takeoverCounts[stem] = nil
            onStemTakeoverChange?(stem, false)
        } else {
            takeoverCounts[stem] = c - 1
        }
    }

    /// Re-run connect wiring after ConnectCore rebuilds its graph
    /// (device flap) — attached nodes survive but their connections
    /// drop.
    public func reattach() {
        fastReleaseRefs.withLock { $0.removeAll() }
        // The whole fast-press surface is stale with the graph: plans
        // (bodies still valid but the session will re-arm), parked refs
        // (every node stops below) and fires (no node to adopt).
        disarmFastPresses()
        fastParkedBox.withLock { $0.removeAll() }
        fastFireBox.withLock { $0.removeAll() }
        for index in voices.indices {
            endTakeover(index)
            // Hard stop, no fade: reattach fires while the graph is
            // rebuilding — the one window where touching nodes gently
            // is NOT safer. Cancel any armed start / in-flight fade too
            // (epoch advance makes a mid-flight fade terminal a no-op).
            voices[index].pendingPlay?.cancel()
            voices[index].pendingPlay = nil
            voices[index].fadeTask?.cancel()
            voices[index].fadeTask = nil
            voices[index].gate.advance()
            voices[index].node.stop()
            voices[index].key = nil
            voices[index].parked = false
            voices[index].startSampleTime = 0
            // Mark unwired; the next trigger lazily rewires at canonical.
            // (Not rewired eagerly here — reattach fires while the graph is
            // still settling from the device flap, exactly when connect
            // calls are dangerous. The session re-warms/parks the pool
            // via warmUpPool() from onGraphReattached, once the bus is
            // back.)
            voices[index].outputWired = false
            voices[index].format = nil
        }
    }

    // MARK: - Voice chain

    /// The ONE voice-chain format: 48 kHz stereo. Buffers convert to it at
    /// read; chains never rewire per file. (Mirrors mobile's D-017 single-
    /// resample design.)
    static let canonicalFormat = AVAudioFormat(
        standardFormatWithSampleRate: 48_000, channels: 2)!

    /// Wire a voice's player→delay→EQ→mixer→destination chain at the
    /// canonical format. Called once per attach cycle (lazy at first
    /// trigger, and from reattach) — NEVER per trigger/per file format:
    /// engine.connect during a CoreAudio device reconfig or across sample
    /// rates raised NSException/-10868 and crashed the app.
    private func wireVoice(_ voice: Voice) {
        let f = Self.canonicalFormat
        avEngine.connect(voice.node, to: voice.delay, format: f)
        avEngine.connect(voice.delay, to: voice.eq, format: f)
        avEngine.connect(voice.eq, to: voice.mixer, format: f)
        avEngine.connect(voice.mixer, to: destination, format: f)
    }

    /// One-shot whole-buffer conversion to the canonical format (rate +
    /// channel layout; mono up-mixes to centered stereo — the one-side-of-
    /// the-speakers fix). Returns the input untouched when it already
    /// matches. Nil on converter failure.
    private static func toCanonical(_ src: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        let target = canonicalFormat
        if src.format == target { return src }
        guard let converter = AVAudioConverter(from: src.format, to: target)
        else { return nil }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        let ratio = target.sampleRate / src.format.sampleRate
        let capacity = AVAudioFrameCount(Double(src.frameLength) * ratio) + 32
        guard let dst = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity)
        else { return nil }
        var provided = false
        var err: NSError?
        _ = converter.convert(to: dst, error: &err) { _, outStatus in
            if provided { outStatus.pointee = .endOfStream; return nil }
            provided = true
            outStatus.pointee = .haveData
            return src
        }
        return err == nil ? dst : nil
    }

    /// iOS SampleVoicePool.applyEffects parity: the filter band is
    /// bypassed when the cutoff sits at the top of its window (20 kHz)
    /// to save an audibly-neutral biquad; the delay is NOT bypassed at
    /// mix=0 because wetDryMix=0 renders bit-identical to dry.
    private func applyEffects(_ fx: SamplePadEffects, to voice: Voice) {
        Self.applyEffects(fx, delay: voice.delay, eq: voice.eq)
    }

    /// AU parameter sets only — thread-safe, so the receive-thread fast
    /// press (D-038) applies its plan's effects through the same code.
    nonisolated private static func applyEffects(
        _ fx: SamplePadEffects, delay: AVAudioUnitDelay, eq: AVAudioUnitEQ
    ) {
        delay.delayTime = fx.delayTimeSec
        delay.feedback = Float(fx.delayFeedback)
        delay.wetDryMix = Float(fx.delayMix)

        let band = eq.bands[0]
        band.frequency = Float(fx.filterCutoffHz)
        band.bandwidth = Float(fx.filterResonanceDb)
        band.bypass = fx.filterCutoffHz >= 19_999
    }

    // MARK: - Pool

    /// Prefer a PARKED voice (playing, empty queue — zero-control-call
    /// start), else any fully idle voice, else grow the pool, else
    /// round-robin steal. The old same-key steal is gone — retriggers
    /// ROTATE (schedule() fades the prior voice off the press path) —
    /// and the old idle scan examined only the FIRST nil-key slot, so
    /// a still-fading voice there grew the pool on every rapid press.
    ///
    /// D-038 makes the claim ATOMIC vs the receive-thread fast press:
    /// parked voices are claimed by popping the SAME lock-boxed pool
    /// the fast lane pops, with ownership decided by the voice gate's
    /// compare-and-advance — so both lanes can never schedule onto one
    /// node (the old flow advanced the epoch a few statements AFTER
    /// picking the slot, a window where a concurrent fast fire would
    /// queue its looping body under the main press's buffer). Voices
    /// holding an un-adopted fire are skipped everywhere: their key is
    /// still nil on main, but their node is already sounding the fire.
    /// Returns the slot AND the claim's fresh epoch.
    private func claimVoiceAtomically(for key: VoiceKey) -> (index: Int, epoch: Int) {
        let reserved = fastFireBox.withLock { Set($0.values.map(\.index)) }
        // 1) Parked pool, shared with the fast press. Stale refs (voice
        //    re-claimed since parking) fail the compare-and-advance.
        while let ref = fastParkedBox.withLock({ $0.popLast() }) {
            guard !reserved.contains(ref.index),
                  voices.indices.contains(ref.index),
                  voices[ref.index].key == nil,
                  let epoch = ref.gate.advanceIfCurrent(ref.parkEpoch)
            else { continue }
            return (ref.index, epoch)
        }
        // 2) Parked flag without a pool ref (should not happen — every
        //    parking site pushes — but a stray flag must not strand the
        //    voice forever; the advance kills any stale fast ref).
        if let i = voices.firstIndex(where: {
            $0.key == nil && $0.parked
        }), !reserved.contains(i) {
            return (i, voices[i].gate.advance())
        }
        if let idle = voices.firstIndex(where: {
            $0.key == nil && !$0.node.isPlaying
        }), !reserved.contains(idle) {
            return (idle, voices[idle].gate.advance())
        }
        if voices.count < Self.poolSize {
            voices.append(makeVoice())
            let i = voices.count - 1
            return (i, voices[i].gate.advance())
        }
        // Pool full: steal — but NEVER a ringing loop if a one-shot voice
        // exists. Round-robin used to grab loop voices freely, so tapping
        // beats on pads killed running loops ("loops affected by taps").
        // Loops are only stolen when the whole pool is loops.
        for probe in 0..<voices.count {
            let i = (nextVoice + probe) % voices.count
            if voices[i].loopFrames == nil, !reserved.contains(i) {
                nextVoice = i + 1
                endTakeover(i)
                return (i, voices[i].gate.advance())
            }
        }
        var index = nextVoice % voices.count
        // Never steal an un-adopted fire even at saturation — probe past.
        for probe in 0..<voices.count
        where !reserved.contains((nextVoice + probe) % voices.count) {
            index = (nextVoice + probe) % voices.count
            break
        }
        nextVoice = index + 1
        endTakeover(index)
        return (index, voices[index].gate.advance())
    }

    /// One voice chain, attached but unwired/unparked. Neutral chain so
    /// an idle voice is inaudible: wetDryMix=0 mutes the delay tap,
    /// feedback=0 stops buildup; the EQ band starts bypassed. Real
    /// params land on trigger.
    private func makeVoice() -> Voice {
        let node = AVAudioPlayerNode()
        let delay = AVAudioUnitDelay()
        delay.wetDryMix = 0
        delay.feedback = 0
        delay.delayTime = SamplePadEffects.neutral.delayTimeSec
        let eq = AVAudioUnitEQ(numberOfBands: 1)
        let band = eq.bands[0]
        band.filterType = .resonantLowPass
        band.frequency = Float(SamplePadEffects.neutral.filterCutoffHz)
        band.bandwidth = Float(SamplePadEffects.neutral.filterResonanceDb)
        band.bypass = true
        let mixer = AVAudioMixerNode()
        avEngine.attach(node)
        avEngine.attach(delay)
        avEngine.attach(eq)
        avEngine.attach(mixer)
        return Voice(
            node: node, delay: delay, eq: eq, mixer: mixer,
            format: nil, key: nil
        )
    }

    // MARK: - Pool warm-up (press-path latency)

    /// Build, wire and PARK the whole voice pool OFF the press path. A
    /// parked voice is `play()`ing with an empty queue: scheduling a
    /// buffer on it starts at the next render cycle with no further
    /// control call. `play()` blocks its caller for up to one render
    /// quantum (~10.7 ms measured at 512f/48k) — paying that per press
    /// was the same-pad burst serializer. Parking runs on a detached
    /// task (the calls are thread-safe; 16 nodes ≈ 170 ms would hitch
    /// the caller otherwise); flags land back on the main actor,
    /// gen-guarded against slots a concurrent press claimed meanwhile.
    /// Call once the engine is running and the output bus is live
    /// (session attach, and again after a graph rebuild's reattach()).
    /// Idempotent; safe to race live presses.
    public func warmUpPool() async {
        guard avEngine.isRunning else { return }
        while voices.count < Self.poolSize {
            voices.append(makeVoice())
        }
        var toPark: [(index: Int, node: AVAudioPlayerNode, gen: Int)] = []
        for index in voices.indices {
            if !voices[index].outputWired {
                wireVoice(voices[index])
                voices[index].outputWired = true
                voices[index].format = Self.canonicalFormat
            }
            let v = voices[index]
            if v.key == nil, v.pendingPlay == nil, v.fadeTask == nil,
               !v.parked, !v.node.isPlaying {
                toPark.append((index, v.node, v.gen))
            }
        }
        guard !toPark.isEmpty else { return }
        let nodes = toPark.map(\.node)
        await Task.detached(priority: .userInitiated) {
            for node in nodes { node.play() }
        }.value
        for item in toPark {
            guard voices.indices.contains(item.index),
                  voices[item.index].gen == item.gen,
                  voices[item.index].key == nil,
                  voices[item.index].node.isPlaying else { continue }
            voices[item.index].parked = true
            pushFastParked(item.index)
        }
    }

    /// Start a claimed voice now or at a future boundary. Future starts
    /// are SAMPLE-ACCURATE via `play(at: AVAudioTime(hostTime:))` — but
    /// only once BOTH render clocks are live: `play(at:)` throws from
    /// AVAudioPlayerNodeImpl::StartImpl before the engine's first render,
    /// and a freshly-attached player whose own `lastRenderTime` is
    /// invalid silently IGNORES a hostTime start, leaving the voice
    /// armed forever (iOS 679a874e; the desktop pool attaches voices
    /// lazily, so a stack-another-pad press routinely lands on a
    /// never-rendered node). Those cases fall back to a deferred
    /// dispatch `play()` (~1 ms jitter, boot-window only). BOTH paths
    /// park a DispatchWorkItem on the slot as the armed marker + cancel
    /// token — releasing a not-yet-started voice must kill it
    /// (`player.stop()` discards a scheduled start; the fallback item is
    /// simply cancelled).
    /// play() calls made on the INSTANT press path (delay ≤ 1 ms). After
    /// warmUpPool every instant press should ride a parked voice and
    /// this stays 0 — the burst regression test pins it, because each
    /// such call blocks the caller up to a render quantum.
    private(set) var immediatePlayCount = 0

    private func scheduleStart(index: Int, afterSeconds delay: Double, gen: Int) {
        let node = voices[index].node
        guard delay > 0.001 else {
            if node.isPlaying {
                // Parked fast path: the queued buffer begins at the next
                // render cycle on its own — NO play() (which blocks the
                // caller up to a full render quantum, ~10.7 ms measured;
                // the same-pad burst serializer). Baseline the player's
                // running sample clock so loopProgress measures from
                // THIS launch, not from the park's play().
                if let rt = node.lastRenderTime,
                   let pt = node.playerTime(forNodeTime: rt) {
                    voices[index].startSampleTime = pt.sampleTime
                } else {
                    voices[index].startSampleTime = 0
                }
            } else {
                voices[index].startSampleTime = 0
                immediatePlayCount += 1
                node.play()
            }
            return
        }
        // Quantized launch: schedule() already un-parked (stopped) the
        // node, so the player clock restarts at 0 on either start path.
        voices[index].startSampleTime = 0
        let engineRendered = avEngine.outputNode
            .lastRenderTime?.isSampleTimeValid ?? false
        let playerRendered = node.lastRenderTime?.isSampleTimeValid ?? false
        let item: DispatchWorkItem
        if engineRendered && playerRendered {
            let ticks = UInt64(delay * TransportClock.ticksPerSecond())
            node.play(at: AVAudioTime(hostTime: mach_absolute_time() + ticks))
            // Sample-accurate path: the deadline item only clears the
            // armed marker (the start itself is already scheduled).
            item = DispatchWorkItem { [weak self] in
                Task { @MainActor in
                    guard let self, self.voices.indices.contains(index),
                          self.voices[index].gen == gen else { return }
                    self.voices[index].pendingPlay = nil
                }
            }
        } else {
            item = DispatchWorkItem { [weak self] in
                // play() straight from the dispatch thread (thread-safe,
                // and hopping actors first would add jitter); the
                // bookkeeping hop follows.
                node.play()
                Task { @MainActor in
                    guard let self, self.voices.indices.contains(index),
                          self.voices[index].gen == gen else { return }
                    self.voices[index].pendingPlay = nil
                }
            }
        }
        voices[index].pendingPlay = item
        DispatchQueue.global(qos: .userInteractive)
            .asyncAfter(deadline: .now() + delay, execute: item)
    }
}
