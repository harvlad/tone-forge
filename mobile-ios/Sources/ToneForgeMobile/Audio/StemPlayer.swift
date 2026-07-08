// StemPlayer.swift
//
// Multi-channel song playback via AVAudioPlayerNode. One node per stem
// (drums, bass, other, vocals), all summed through a `stemMixer`
// AVAudioMixerNode that hangs off the engine's main mixer. Each stem
// gets its own gain + mute + solo state, matching the web app's stems
// mixer.
//
// Scheduling model:
//   - `load(bundle:localURLs:)` opens an AVAudioFile per stem and
//     attaches nodes to the engine graph.
//   - `play(atSongSeconds:)` computes an `AVAudioTime` offset from the
//     TransportClock's current host time and schedules every player
//     node to start at that instant. Because AVAudioPlayerNode does
//     the wall-clock alignment for us, all four stems are sample-
//     accurate — no manual drift correction needed.
//   - `pause()` calls `.pause()` on every node.
//   - `seek(to:)` stops all nodes, computes new frame offsets, and
//     re-schedules from there.
//
// The player does NOT own the TransportClock; the AudioEngine passes
// it in. That means transport state is single-sourced (D-005) and
// tests can inject a mock clock.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif
import ToneForgeEngine

/// Manages the per-stem AVAudioPlayerNodes + mix bus.
@MainActor
public final class StemPlayer: ObservableObject {

    /// Per-stem mixer state exposed to the UI.
    public struct StemState: Sendable, Equatable, Identifiable {
        public var id: String { role }
        public let role: String
        public var gain: Float          // 0..1 linear
        public var isMuted: Bool
        public var isSoloed: Bool
    }

    /// Published so the mixer view redraws when stems load or change.
    @Published public private(set) var stems: [StemState] = []
    @Published public private(set) var isLoaded: Bool = false

    /// Test/preview seam: seed mixer UI state without touching the
    /// audio graph (snapshot tests render channel strips with no
    /// engine and no local stem files).
    func seedStemStatesForSnapshot(_ states: [StemState]) {
        stems = states
        isLoaded = !states.isEmpty
    }

    // MARK: - Private

    #if canImport(AVFoundation)
    private struct Channel {
        let role: String
        let file: AVAudioFile
        let player: AVAudioPlayerNode
        var gainNode: AVAudioMixerNode  // per-stem gain trim
        var fileSampleRate: Double { file.fileFormat.sampleRate }
        var totalFrames: AVAudioFramePosition { file.length }
    }

    private var channels: [Channel] = []
    private var stemMixer: AVAudioMixerNode?
    /// Shared practice-speed unit on the stem submix (D-022):
    /// stemMixer → timePitch → mainMixer. Bypassed at rate 1.0 so
    /// normal playback stays bit-transparent.
    private var timePitch: AVAudioUnitTimePitch?
    #endif

    /// Current practice rate; survives load/unload so a reload keeps
    /// the user's speed. Clamping (0.5–1.0) lives in AppState.
    private var playbackRate: Float = 1.0

    private weak var engine: AudioEngine?

    public init(engine: AudioEngine) {
        self.engine = engine
    }

    // MARK: - Load / unload

    /// Attach player nodes for each stem in ``bundle``. ``localURLs``
    /// maps stem role → local file URL (already downloaded via
    /// BundleStore). Silently skips stems without a local URL.
    public func load(bundle: SongBundle, localURLs: [String: URL]) throws {
        unload()
        #if canImport(AVFoundation)
        guard let engine = engine else { return }
        let mixer = AVAudioMixerNode()
        let pitch = AVAudioUnitTimePitch()
        engine.engine.attach(mixer)
        engine.engine.attach(pitch)
        engine.engine.connect(mixer, to: pitch, format: nil)

        // Connect timePitch to mainMixer AND fxSendMixer (if available)
        // using the multi-connection-point pattern to avoid the
        // sequential-connect trap (D-022 master FX topology).
        if let fxSend = engine.fxSendMixerInput {
            engine.engine.connect(
                pitch,
                to: [
                    AVAudioConnectionPoint(node: engine.engine.mainMixerNode, bus: 0),
                    AVAudioConnectionPoint(node: fxSend, bus: 0)
                ],
                fromBus: 0,
                format: nil
            )
        } else {
            engine.engine.connect(pitch, to: engine.engine.mainMixerNode, format: nil)
        }
        pitch.rate = playbackRate
        pitch.bypass = playbackRate == 1.0
        self.stemMixer = mixer
        self.timePitch = pitch

        var newChannels: [Channel] = []
        var newStates: [StemState] = []

        for stem in bundle.stems {
            guard let url = localURLs[stem.role] else { continue }
            do {
                let file = try AVAudioFile(forReading: url)
                let player = AVAudioPlayerNode()
                let gain = AVAudioMixerNode()

                engine.engine.attach(player)
                engine.engine.attach(gain)
                engine.engine.connect(player, to: gain, format: file.processingFormat)
                engine.engine.connect(gain, to: mixer, format: file.processingFormat)

                newChannels.append(Channel(role: stem.role, file: file, player: player, gainNode: gain))
                newStates.append(StemState(role: stem.role, gain: 1.0, isMuted: false, isSoloed: false))
            } catch {
                print("[StemPlayer] failed to open \(stem.role) at \(url.path): \(error)")
            }
        }

        self.channels = newChannels
        self.stems = newStates
        self.isLoaded = !newChannels.isEmpty
        applyGains()
        #endif
    }

    /// Detach and free all nodes. Called before loading a new bundle
    /// or on scene teardown.
    public func unload() {
        #if canImport(AVFoundation)
        for ch in channels {
            ch.player.stop()
            engine?.engine.detach(ch.player)
            engine?.engine.detach(ch.gainNode)
        }
        if let mixer = stemMixer {
            engine?.engine.detach(mixer)
        }
        if let pitch = timePitch {
            engine?.engine.detach(pitch)
        }
        channels.removeAll()
        stemMixer = nil
        timePitch = nil
        #endif
        stems.removeAll()
        isLoaded = false
    }

    // MARK: - Transport

    /// Set the practice playback rate (D-022). The shared timePitch
    /// unit stretches the stem submix; bypassed at 1.0 so normal
    /// playback stays bit-transparent. Frame math elsewhere is
    /// unchanged — song-position seconds map 1:1 to file frames
    /// regardless of rate (playback just proceeds slower and the
    /// TransportClock advances at the same reduced rate).
    public func setPlaybackRate(_ rate: Double) {
        playbackRate = Float(rate)
        #if canImport(AVFoundation)
        guard let pitch = timePitch else { return }
        pitch.rate = playbackRate
        pitch.bypass = playbackRate == 1.0
        #endif
    }

    /// Start (or resume) every stem so that song-time ``seconds`` is
    /// heard at the same host time the AudioEngine's clock reports.
    /// Idempotent when already playing (no-op).
    public func play(atSongSeconds seconds: Double) {
        #if canImport(AVFoundation)
        guard let engine = engine else { return }
        for ch in channels {
            let sampleRate = ch.fileSampleRate
            let startFrame = AVAudioFramePosition(max(0, seconds) * sampleRate)
            let clampedStart = min(startFrame, ch.totalFrames)
            let remaining = ch.totalFrames - clampedStart
            guard remaining > 0 else { continue }

            ch.player.stop()
            ch.player.scheduleSegment(
                ch.file,
                startingFrame: clampedStart,
                frameCount: AVAudioFrameCount(remaining),
                at: nil,
                completionHandler: nil
            )
            if !ch.player.isPlaying {
                if engine.engine.isRunning {
                    ch.player.play()
                }
            }
        }
        #endif
    }

    public func pause() {
        #if canImport(AVFoundation)
        for ch in channels {
            ch.player.pause()
        }
        #endif
    }

    public func stop() {
        #if canImport(AVFoundation)
        for ch in channels {
            ch.player.stop()
        }
        #endif
    }

    /// Move to `seconds`. Reschedules every stem from the new offset.
    /// Preserves play/pause state: if any player node was playing,
    /// resumes from the new position; if all were stopped/paused,
    /// leaves them stopped so a caller-initiated `play()` starts
    /// cleanly. The prior implementation unconditionally restarted
    /// playback, which caused section-chip seeks to start stems while
    /// leaving `TransportClock` in `.stopped` (so `nowSongSeconds`
    /// stayed at 0 and quantized pad taps snapped to `beats[0]`
    /// 10+ s into the future).
    public func seek(to seconds: Double) {
        #if canImport(AVFoundation)
        let wasPlaying = channels.contains(where: { $0.player.isPlaying })
        stop()
        if wasPlaying {
            play(atSongSeconds: seconds)
        }
        #endif
    }

    // MARK: - Mixer

    public func setGain(role: String, gain: Float) {
        if let idx = stems.firstIndex(where: { $0.role == role }) {
            stems[idx].gain = max(0, min(1, gain))
        }
        applyGains()
    }

    public func toggleMute(role: String) {
        if let idx = stems.firstIndex(where: { $0.role == role }) {
            stems[idx].isMuted.toggle()
        }
        applyGains()
    }

    public func toggleSolo(role: String) {
        if let idx = stems.firstIndex(where: { $0.role == role }) {
            stems[idx].isSoloed.toggle()
        }
        applyGains()
    }

    // MARK: - Private helpers

    private func applyGains() {
        #if canImport(AVFoundation)
        let anySolo = stems.contains(where: { $0.isSoloed })
        for state in stems {
            guard let ch = channels.first(where: { $0.role == state.role }) else { continue }
            let effective: Float
            if state.isMuted {
                effective = 0
            } else if anySolo && !state.isSoloed {
                effective = 0
            } else {
                effective = state.gain
            }
            ch.gainNode.outputVolume = effective
        }
        #endif
    }
}
