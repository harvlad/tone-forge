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
        /// mixer→destination wired (stereo, once per attach). Reset on
        /// reattach. Rewiring this leg on every per-voice format change
        /// crashed when a trigger raced a CoreAudio device reconfig
        /// (USB plug): AVAudioEngine.connect threw NSException mid-
        /// UpdateGraphAfterReconfig.
        var outputWired: Bool = false
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
        regionCache.removeAll()
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
    }

    // MARK: - Prewarm

    /// Decode-and-cache each assignment's region buffer WITHOUT playing
    /// it, so the first real press schedules from cache instead of
    /// paying the 8 s read+SRC in the touch path ("delay on pads").
    /// Yields between pads so a 16-pad kit doesn't hitch the UI. Safe
    /// to race a real trigger — regionBuffer re-checks the cache.
    public func prewarm(_ items: [(chop: Chop, stem: String)]) async {
        for item in items {
            guard let file = files[item.stem] else { continue }
            let sampleRate = file.fileFormat.sampleRate
            let startFrame = AVAudioFramePosition(max(0, item.chop.startSec) * sampleRate)
            let endFrame = min(
                AVAudioFramePosition(item.chop.endSec * sampleRate), file.length)
            let frameCount = endFrame - startFrame
            guard frameCount > 0, startFrame < file.length else { continue }
            _ = regionBuffer(file: file, startFrame: startFrame,
                             frameCount: AVAudioFrameCount(frameCount))
            await Task.yield()
        }
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
            // Buffer read/convert failed — skip the trigger. (No raw
            // scheduleSegment fallback: the file's native format may not
            // match the canonical chain, and a mismatched schedule is the
            // same crash class we're eliminating.)
            print("[ChopPlayer] dropped trigger: region read failed")
            voices[index] = voice
            return
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
        let gain = 0.63 / peak
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
            // Mark unwired; the next trigger lazily rewires at canonical.
            // (Not rewired eagerly here — reattach fires while the graph is
            // still settling from the device flap, exactly when connect
            // calls are dangerous.)
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
        // Pool full: steal — but NEVER a ringing loop if a one-shot voice
        // exists. Round-robin used to grab loop voices freely, so tapping
        // beats on pads killed running loops ("loops affected by taps").
        // Loops are only stolen when the whole pool is loops.
        for probe in 0..<voices.count {
            let i = (nextVoice + probe) % voices.count
            if voices[i].loopFrames == nil {
                nextVoice = i + 1
                endTakeover(i)
                return i
            }
        }
        let index = nextVoice % voices.count
        nextVoice += 1
        endTakeover(index)
        return index
    }

    private func playTime(afterSeconds delay: Double) -> AVAudioTime? {
        guard delay > 0.001 else { return nil }  // nil = play immediately
        let ticks = UInt64(delay * TransportClock.ticksPerSecond())
        return AVAudioTime(hostTime: mach_absolute_time() + ticks)
    }
}
