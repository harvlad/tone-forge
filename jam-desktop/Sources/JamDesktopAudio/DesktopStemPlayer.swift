// DesktopStemPlayer.swift
//
// Multi-channel stem playback, ported from the mobile app's
// StemPlayer. One AVAudioPlayerNode per stem, each through its own
// gain-trim AVAudioMixerNode, summed on a `stemMixer` that feeds an
// AVAudioUnitTimePitch (practice speed, bypassed at 1.0) into the
// host engine's main mixer.
//
// Differences from mobile:
//   - Mixer STATE lives in JamDesktopCore.StemMixModel; this class
//     only exposes volume primitives (setVolume/setSongGain) that the
//     model drives via effectiveGain(for:). No duplicated solo/mute
//     logic here.
//   - The host graph is ConnectCore's AVAudioEngine (via the avEngine
//     seam). A device flap makes ConnectCore rewire only its own
//     nodes, so `reattach()` re-runs our connect wiring; the
//     EngineController calls it from onGraphRebuilt.
//
// Scheduling model (identical to mobile): song-position seconds map
// 1:1 to file frames regardless of practice rate — playback just
// proceeds slower and the TransportClock advances at the same reduced
// rate. seek() preserves play/pause state.

import Foundation
import AVFoundation
import ToneForgeEngine

@MainActor
public final class DesktopStemPlayer {

    private struct Channel {
        let role: String
        let file: AVAudioFile
        let player: AVAudioPlayerNode
        let gainNode: AVAudioMixerNode
        var fileSampleRate: Double { file.fileFormat.sampleRate }
        var totalFrames: AVAudioFramePosition { file.length }
    }

    private var channels: [Channel] = []
    private var stemMixer: AVAudioMixerNode?
    private var timePitch: AVAudioUnitTimePitch?
    private var playbackRate: Float = 1.0
    private var songGain: Float = 1.0

    private let avEngine: AVAudioEngine

    public private(set) var isLoaded = false

    /// Roles in stable bundle order, for the mixer model.
    public private(set) var loadedRoles: [String] = []

    /// The stem submix node, exposed so the click track can join the
    /// same submix→timePitch path (clicks stretch with the song).
    var submixNode: AVAudioMixerNode? { stemMixer }

    /// Destination the stem chain feeds. Defaults to the engine's
    /// main mixer; EngineController points it at the MusicBus so
    /// master FX color the stems.
    public var outputNode: AVAudioNode?

    private var destination: AVAudioNode {
        outputNode ?? avEngine.mainMixerNode
    }

    public init(avEngine: AVAudioEngine) {
        self.avEngine = avEngine
    }

    // MARK: - Load / unload

    /// Attach player nodes for each stem in `bundle`. `localURLs` maps
    /// stem role → local file URL (downloaded via BundleStore).
    /// Silently skips stems without a local URL.
    ///
    /// Opening AVAudioFiles is the slow disk-bound part, so it runs
    /// concurrently off the main actor; the cheap attach/connect
    /// wiring stays here.
    public func load(bundle: SongBundle, localURLs: [String: URL]) async {
        unload()

        let opened = await Self.openStemFiles(bundle: bundle, localURLs: localURLs)

        let mixer = AVAudioMixerNode()
        let pitch = AVAudioUnitTimePitch()
        avEngine.attach(mixer)
        avEngine.attach(pitch)
        avEngine.connect(mixer, to: pitch, format: nil)
        avEngine.connect(pitch, to: destination, format: nil)
        pitch.rate = playbackRate
        pitch.bypass = playbackRate == 1.0
        mixer.outputVolume = songGain
        stemMixer = mixer
        timePitch = pitch

        var newChannels: [Channel] = []
        for (role, file) in opened {
            let player = AVAudioPlayerNode()
            let gain = AVAudioMixerNode()
            avEngine.attach(player)
            avEngine.attach(gain)
            avEngine.connect(player, to: gain, format: file.processingFormat)
            avEngine.connect(gain, to: mixer, format: file.processingFormat)
            newChannels.append(Channel(role: role, file: file, player: player, gainNode: gain))
        }

        channels = newChannels
        loadedRoles = newChannels.map(\.role)
        isLoaded = !newChannels.isEmpty
    }

    /// Open the stem AVAudioFiles concurrently (off the main actor),
    /// returned in stable bundle-stem order so mixer layout is
    /// deterministic. A stem that fails to open is dropped with a log.
    private static func openStemFiles(
        bundle: SongBundle, localURLs: [String: URL]
    ) async -> [(role: String, file: AVAudioFile)] {
        let jobs: [(Int, String, URL)] = bundle.stems.enumerated().compactMap {
            idx, stem in
            guard let url = localURLs[stem.role] else { return nil }
            return (idx, stem.role, url)
        }
        let opened = await withTaskGroup(
            of: (Int, String, AVAudioFile)?.self
        ) { group -> [(Int, String, AVAudioFile)] in
            for (idx, role, url) in jobs {
                group.addTask {
                    do {
                        return (idx, role, try AVAudioFile(forReading: url))
                    } catch {
                        print("[DesktopStemPlayer] failed to open \(role) at \(url.path): \(error)")
                        return nil
                    }
                }
            }
            var acc: [(Int, String, AVAudioFile)] = []
            for await r in group { if let r { acc.append(r) } }
            return acc
        }
        return opened
            .sorted { $0.0 < $1.0 }
            .map { (role: $0.1, file: $0.2) }
    }

    /// Detach and free all nodes. Called before loading a new bundle.
    public func unload() {
        for ch in channels {
            ch.player.stop()
            avEngine.detach(ch.player)
            avEngine.detach(ch.gainNode)
        }
        if let mixer = stemMixer { avEngine.detach(mixer) }
        if let pitch = timePitch { avEngine.detach(pitch) }
        channels.removeAll()
        stemMixer = nil
        timePitch = nil
        loadedRoles = []
        isLoaded = false
    }

    /// Re-run the connect wiring after ConnectCore rebuilds its graph
    /// (device flap). Nodes stay attached across a rebuild but their
    /// connections into mainMixerNode are dropped. Players are left
    /// stopped; the caller re-schedules from the transport position.
    public func reattach() {
        guard let mixer = stemMixer, let pitch = timePitch else { return }
        avEngine.connect(mixer, to: pitch, format: nil)
        avEngine.connect(pitch, to: destination, format: nil)
        for ch in channels {
            ch.player.stop()
            avEngine.connect(ch.player, to: ch.gainNode, format: ch.file.processingFormat)
            avEngine.connect(ch.gainNode, to: mixer, format: ch.file.processingFormat)
        }
    }

    // MARK: - Transport

    /// Practice rate (D-022). Frame math elsewhere is unchanged —
    /// song-position seconds map 1:1 to file frames regardless of
    /// rate. Clamping (0.5–1.0) lives in TransportController.
    public func setPlaybackRate(_ rate: Double) {
        playbackRate = Float(rate)
        guard let pitch = timePitch else { return }
        pitch.rate = playbackRate
        pitch.bypass = playbackRate == 1.0
    }

    /// Schedule and start every stem from song-time `seconds`.
    public func play(atSongSeconds seconds: Double) {
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
            if !ch.player.isPlaying, avEngine.isRunning {
                ch.player.play()
            }
        }
    }

    public func pause() {
        for ch in channels { ch.player.pause() }
    }

    public func stop() {
        for ch in channels { ch.player.stop() }
    }

    /// Move to `seconds`, preserving play/pause state: resumes from
    /// the new position only if something was playing.
    public func seek(to seconds: Double) {
        let wasPlaying = channels.contains(where: { $0.player.isPlaying })
        stop()
        if wasPlaying {
            play(atSongSeconds: seconds)
        }
    }

    // MARK: - Volume primitives (driven by StemMixModel)

    /// Apply an effective per-stem volume (mute/solo already folded in
    /// by StemMixModel.effectiveGain).
    public func setVolume(_ volume: Float, forRole role: String) {
        channels.first(where: { $0.role == role })?
            .gainNode.outputVolume = max(0, min(1, volume))
    }

    /// Combined "Song" gain on the stem submix — the monitor/Your
    /// Layer path is unaffected.
    public func setSongGain(_ gain: Float) {
        songGain = max(0, min(1, gain))
        stemMixer?.outputVolume = songGain
    }
}
