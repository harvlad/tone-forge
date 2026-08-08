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
import ToneForgeEngine
import JamDesktopCore

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
        /// The song stem this voice is currently "taking over" (ducking),
        /// or nil. Cleared when the voice stops/completes/is stolen.
        var takeoverStem: String?
        /// Monotonic per-voice token so a late one-shot completion can't
        /// end a takeover the slot has since been reused for.
        var gen: Int = 0
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
    private var files: [String: AVAudioFile] = [:]
    /// Readers for sequencer customURL sources, cached per URL.
    private var fileCache: [URL: AVAudioFile] = [:]

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
        let opened = await Task.detached {
            var out: [String: AVAudioFile] = [:]
            for (role, url) in stemURLs {
                do {
                    out[role] = try AVAudioFile(forReading: url)
                } catch {
                    print("[ChopPlayer] failed to open \(role) at \(url.path): \(error)")
                }
            }
            return out
        }.value
        files = opened
    }

    public func unload() {
        stopAll()
        files.removeAll()
        fileCache.removeAll()
    }

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
        loopBarSeconds: Double = 0
    ) {
        guard let file = files[assignment.stem] else { return }
        let chop = assignment.chop
        // Phase-lock: snap the loop length to a whole number of bars so it stays
        // aligned to the downbeat grid forever (a slightly-off length drifts).
        var endSec = chop.endSec
        if loop, loopBarSeconds > 0 {
            let loopSec = chop.endSec - chop.startSec
            let bars = max(1, (loopSec / loopBarSeconds).rounded())
            endSec = chop.startSec + bars * loopBarSeconds
        }
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
            crossfadeMs: crossfadeMs
        )
    }

    /// Play a [startSec, endSec] segment of an arbitrary local file
    /// (sequencer customURL path). nil bounds = whole file. Readers
    /// are cached per URL; open failures are logged and dropped.
    public func trigger(
        file url: URL,
        startSec: Double?,
        endSec: Double?,
        velocity: Float = 1,
        pan: Float = 0,
        afterSeconds delaySeconds: Double = 0
    ) {
        guard let file = cachedFile(for: url) else { return }
        let duration = Double(file.length) / file.fileFormat.sampleRate
        schedule(
            file: file,
            startSec: startSec ?? 0,
            endSec: endSec ?? duration,
            key: .file(url),
            effects: .neutral,
            velocity: velocity,
            pan: pan,
            afterSeconds: delaySeconds
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
        crossfadeMs: Double = 0
    ) {
        guard avEngine.isRunning else {
            print("[ChopPlayer] dropped trigger: engine not running")
            return
        }
        let sampleRate = file.fileFormat.sampleRate
        let startFrame = AVAudioFramePosition(max(0, startSec) * sampleRate)
        let endFrame = min(
            AVAudioFramePosition(endSec * sampleRate), file.length)
        let frameCount = endFrame - startFrame
        guard frameCount > 0, startFrame < file.length else { return }

        let index = claimVoice(for: key)
        // Stem this trigger takes over (bundle chops only; file voices don't
        // duck the song). End the claimed slot's PRIOR takeover first — unless
        // it's the same stem (a same-stem retrigger keeps the duck, no blip).
        let takeoverStem: String? = { if case .chop(let s, _) = key { return s }; return nil }()
        if voices[index].takeoverStem != takeoverStem { endTakeover(index) }

        var voice = voices[index]
        voice.node.stop()
        voice.gen &+= 1
        let capturedGen = voice.gen

        if voice.format != file.processingFormat {
            connectChain(voice, format: file.processingFormat)
            voice.format = file.processingFormat
        }
        applyEffects(effects.clamped(), to: voice)
        voice.mixer.outputVolume = min(max(velocity, 0), 1)
        voice.mixer.pan = min(max(pan, -1), 1)

        if loop, let buffer = loopBuffer(file: file, startFrame: startFrame,
                                         frameCount: AVAudioFrameCount(frameCount),
                                         crossfadeMs: crossfadeMs) {
            // Seamless looping: the [start,end] region is read into a buffer,
            // crossfaded (SeamlessLoop) and hard-looped so a held pad never clicks.
            voice.node.scheduleBuffer(buffer, at: nil, options: [.loops], completionHandler: nil)
            voice.loopFrames = buffer.frameLength
        } else if let buffer = regionBuffer(file: file, startFrame: startFrame,
                                            frameCount: AVAudioFrameCount(frameCount)) {
            // One-shot: read the region into a buffer and micro-fade its
            // edges so a slice that doesn't start/end on a zero-crossing
            // (stabs, drum hits) doesn't click on attack or tail. On natural
            // end, restore the taken-over stem (gen-guarded against reuse).
            voice.node.scheduleBuffer(buffer, at: nil, options: [],
                                      completionHandler: oneShotCompletion(index, gen: capturedGen))
            voice.loopFrames = nil
        } else {
            // Fallback: buffer read failed — schedule straight from the file.
            voice.node.scheduleSegment(
                file,
                startingFrame: startFrame,
                frameCount: AVAudioFrameCount(frameCount),
                at: nil,
                completionHandler: oneShotCompletion(index, gen: capturedGen)
            )
            voice.loopFrames = nil
        }
        voice.node.play(at: playTime(afterSeconds: delaySeconds))
        voice.key = key
        voices[index] = voice
        // Begin the new takeover AFTER the struct write-back (which would
        // otherwise clobber takeoverStem). Skip if already taking over this
        // same stem on this voice (same-stem retrigger — count unchanged).
        if let s = takeoverStem, voices[index].takeoverStem != s {
            beginTakeover(index, stem: s)
        }
    }

    /// Completion handler for a one-shot voice: on the audio thread when the
    /// buffer finishes (or the node is stopped). Hops to the main actor and
    /// ends the takeover only if this slot hasn't since been reused (gen
    /// match). endTakeover is idempotent, so a stop-then-complete is safe.
    private nonisolated func oneShotCompletion(_ index: Int, gen: Int) -> AVAudioNodeCompletionHandler {
        { [weak self] in
            Task { @MainActor in
                guard let self else { return }
                guard self.voices.indices.contains(index),
                      self.voices[index].gen == gen else { return }
                self.endTakeover(index)
            }
        }
    }

    /// Normalized playhead (0..<1) of a hard-looping pad, or nil if that pad
    /// isn't currently looping. Drives the on-pad playback ring. The player
    /// node's sampleTime counts total frames rendered since play; modulo the
    /// loop length gives the position within the current loop.
    public func loopProgress(stem: String, idx: Int) -> Double? {
        let target = VoiceKey.chop(stem: stem, idx: idx)
        for v in voices where v.key == target {
            guard let frames = v.loopFrames, frames > 0, v.node.isPlaying,
                  let rt = v.node.lastRenderTime,
                  let pt = v.node.playerTime(forNodeTime: rt) else { return nil }
            let s = pt.sampleTime
            guard s >= 0 else { return 0 }  // scheduled but not yet fired
            return Double(s % Int64(frames)) / Double(frames)
        }
        return nil
    }

    /// Read a [startFrame, frameCount] region into an edge-faded PCM buffer.
    /// The micro-fades kill attack/tail clicks on one-shots and the first
    /// pass of a loop. Returns nil on read failure.
    private func regionBuffer(
        file: AVAudioFile, startFrame: AVAudioFramePosition,
        frameCount: AVAudioFrameCount
    ) -> AVAudioPCMBuffer? {
        guard let buf = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: frameCount) else { return nil }
        do {
            file.framePosition = startFrame
            try file.read(into: buf, frameCount: frameCount)
        } catch {
            print("[ChopPlayer] region read failed: \(error)")
            return nil
        }
        SeamlessLoop.applyEdgeFades(buf)
        return buf
    }

    /// Read a [startFrame, frameCount] region and apply the seam crossfade
    /// for gapless looping. EVERY looping voice gets a seam — a measured
    /// length when supplied, else the default floor — so an unscored loop
    /// never hard-loops with a click. Returns nil on read failure.
    private func loopBuffer(
        file: AVAudioFile, startFrame: AVAudioFramePosition,
        frameCount: AVAudioFrameCount, crossfadeMs: Double
    ) -> AVAudioPCMBuffer? {
        guard let buf = regionBuffer(file: file, startFrame: startFrame,
                                     frameCount: frameCount) else { return nil }
        let xfadeMs = crossfadeMs > 0 ? crossfadeMs : SeamlessLoop.defaultLoopCrossfadeMs
        return SeamlessLoop.crossfaded(buf, crossfadeMs: xfadeMs)
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

    /// Stop the voice sounding `assignment`'s chop (pad released).
    public func release(_ assignment: PadAssignment) {
        let key = VoiceKey.chop(stem: assignment.stem, idx: assignment.chop.idx)
        for index in voices.indices where voices[index].key == key {
            endTakeover(index)
            voices[index].node.stop()
            voices[index].key = nil
        }
    }

    public func stopAll() {
        for index in voices.indices {
            endTakeover(index)
            voices[index].node.stop()
            voices[index].key = nil
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
        for index in voices.indices {
            endTakeover(index)
            voices[index].node.stop()
            voices[index].key = nil
            if let format = voices[index].format {
                connectChain(voices[index], format: format)
            }
        }
    }

    // MARK: - Voice chain

    /// Wire (or rewire) a voice's player→delay→EQ→mixer→destination
    /// chain. The player→…→mixer legs run in the stem's processing format
    /// (mono for center-extracted stems), but the mixer→destination leg is
    /// forced STEREO so a MONO chop is up-mixed and equal-power-panned to
    /// both channels. Wiring the mixer output mono routed it to a single
    /// side — the "audio drops on one side when I trigger a pad" bug.
    private func connectChain(_ voice: Voice, format: AVAudioFormat) {
        let stereo = AVAudioFormat(
            standardFormatWithSampleRate: format.sampleRate, channels: 2
        ) ?? format
        avEngine.connect(voice.node, to: voice.delay, format: format)
        avEngine.connect(voice.delay, to: voice.eq, format: format)
        avEngine.connect(voice.eq, to: voice.mixer, format: format)
        avEngine.connect(voice.mixer, to: destination, format: stereo)
    }

    /// iOS SampleVoicePool.applyEffects parity: the filter band is
    /// bypassed when the cutoff sits at the top of its window (20 kHz)
    /// to save an audibly-neutral biquad; the delay is NOT bypassed at
    /// mix=0 because wetDryMix=0 renders bit-identical to dry.
    private func applyEffects(_ fx: SamplePadEffects, to voice: Voice) {
        voice.delay.delayTime = fx.delayTimeSec
        voice.delay.feedback = Float(fx.delayFeedback)
        voice.delay.wetDryMix = Float(fx.delayMix)

        let band = voice.eq.bands[0]
        band.frequency = Float(fx.filterCutoffHz)
        band.bandwidth = Float(fx.filterResonanceDb)
        band.bypass = fx.filterCutoffHz >= 19_999
    }

    // MARK: - Pool

    /// Prefer stealing the voice already sounding `key` (retrigger),
    /// else the next idle voice, else round-robin steal. Grows the
    /// pool lazily up to `poolSize`.
    private func claimVoice(for key: VoiceKey) -> Int {
        if let own = voices.firstIndex(where: { $0.key == key }) {
            return own
        }
        if let idle = voices.firstIndex(where: { $0.key == nil }),
           !voices[idle].node.isPlaying
        {
            return idle
        }
        if voices.count < Self.poolSize {
            let node = AVAudioPlayerNode()
            // Neutral chain so an idle voice is inaudible: wetDryMix=0
            // mutes the delay tap, feedback=0 stops buildup; the EQ
            // band starts bypassed. Real params land on trigger.
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
            voices.append(Voice(
                node: node, delay: delay, eq: eq, mixer: mixer,
                format: nil, key: nil
            ))
            return voices.count - 1
        }
        let index = nextVoice % voices.count
        nextVoice += 1
        return index
    }

    private func playTime(afterSeconds delay: Double) -> AVAudioTime? {
        guard delay > 0.001 else { return nil }  // nil = play immediately
        let ticks = UInt64(delay * TransportClock.ticksPerSecond())
        return AVAudioTime(hostTime: mach_absolute_time() + ticks)
    }
}
