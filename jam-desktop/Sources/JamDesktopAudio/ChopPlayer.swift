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
        pan: Float = 0
    ) {
        guard let file = files[assignment.stem] else { return }
        let chop = assignment.chop
        schedule(
            file: file,
            startSec: chop.startSec,
            endSec: chop.endSec,
            key: .chop(stem: assignment.stem, idx: chop.idx),
            effects: effects,
            velocity: velocity,
            pan: pan,
            afterSeconds: delaySeconds
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
        afterSeconds delaySeconds: Double
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
        var voice = voices[index]
        voice.node.stop()

        if voice.format != file.processingFormat {
            connectChain(voice, format: file.processingFormat)
            voice.format = file.processingFormat
        }
        applyEffects(effects.clamped(), to: voice)
        voice.mixer.outputVolume = min(max(velocity, 0), 1)
        voice.mixer.pan = min(max(pan, -1), 1)

        voice.node.scheduleSegment(
            file,
            startingFrame: startFrame,
            frameCount: AVAudioFrameCount(frameCount),
            at: nil,
            completionHandler: nil
        )
        voice.node.play(at: playTime(afterSeconds: delaySeconds))
        voice.key = key
        voices[index] = voice
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
            voices[index].node.stop()
            voices[index].key = nil
        }
    }

    public func stopAll() {
        for index in voices.indices {
            voices[index].node.stop()
            voices[index].key = nil
        }
    }

    /// Re-run connect wiring after ConnectCore rebuilds its graph
    /// (device flap) — attached nodes survive but their connections
    /// drop.
    public func reattach() {
        for index in voices.indices {
            voices[index].node.stop()
            voices[index].key = nil
            if let format = voices[index].format {
                connectChain(voices[index], format: format)
            }
        }
    }

    // MARK: - Voice chain

    /// Wire (or rewire) a voice's player→delay→EQ→mixer→destination
    /// chain in the stem's processing format.
    private func connectChain(_ voice: Voice, format: AVAudioFormat) {
        avEngine.connect(voice.node, to: voice.delay, format: format)
        avEngine.connect(voice.delay, to: voice.eq, format: format)
        avEngine.connect(voice.eq, to: voice.mixer, format: format)
        avEngine.connect(voice.mixer, to: destination, format: format)
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
