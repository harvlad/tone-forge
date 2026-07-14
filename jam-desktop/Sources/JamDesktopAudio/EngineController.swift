// EngineController.swift
//
// Owns the in-process ConnectCore.AudioEngine (monitor chain + main
// mixer) and, via the `avEngine` seam, hosts the desktop stem
// subgraph: DesktopStemPlayer + ClickTrack + TransportClock. It is
// the TransportAudioSink the Core TransportController drives — the
// audio clock is ground truth for transport position.
//
// On `onGraphRebuilt` (device flap) the stem subgraph re-attaches
// and, if the transport was playing, re-schedules from the clock
// position — the ConnectCore reconfig path only rewires its own
// nodes.
//
// Grows in M4 (monitor/tone control surface).

import Foundation
import ConnectCore
import ToneForgeEngine
import JamDesktopCore

@MainActor
public final class EngineController {

    public let engine = AudioEngine()
    public let clock = TransportClock()
    /// Shared musical submix with the master FX chain (D-022). All
    /// musical sources (stems, chops, later sequencer/synth) land on
    /// `musicBus.input`; the monitor/guitar path stays untouched.
    public private(set) lazy var musicBus = MusicBus(avEngine: engine.avEngine)
    public private(set) lazy var stemPlayer = DesktopStemPlayer(avEngine: engine.avEngine)
    public private(set) lazy var clickTrack = ClickTrack(avEngine: engine.avEngine, clock: clock)

    /// Fired after the stem subgraph re-attaches following a device
    /// flap — external taps (input meter) die with the old graph and
    /// must reinstall here.
    public var onGraphReattached: (() -> Void)?

    /// Whether the metronome should click while playing.
    public var clickEnabled = false {
        didSet {
            guard clickEnabled != oldValue else { return }
            if clickEnabled, clock.state == .playing {
                clickTrack.start()
            } else if !clickEnabled {
                clickTrack.stop()
            }
        }
    }

    public init() {
        engine.onGraphRebuilt = { [weak self] in
            // ConnectCore dispatches this on the main queue already;
            // hop through MainActor to satisfy isolation.
            Task { @MainActor in self?.handleGraphRebuilt() }
        }
    }

    public func start() throws {
        // Attach MusicBus BEFORE starting engine - AVAudioEngine graph
        // modifications must happen while engine is stopped.
        musicBus.attach()
        stemPlayer.outputNode = musicBus.input
        clickTrack.attach()
        try engine.start()
    }

    public func stop() {
        clickTrack.stop()
        stemPlayer.stop()
        clock.stop()
        engine.stop()
    }

    // MARK: - Session

    /// Load the stem subgraph for a session and configure the click
    /// grid from the bundle tempo (if analyzed).
    public func loadSession(_ session: LoadedSession) async {
        clock.stop()
        await stemPlayer.load(bundle: session.bundle, localURLs: session.stemURLs)
        if let bpm = session.bundle.meta.tempoBpm, bpm > 0 {
            clickTrack.update(grid: ClickGrid(bpm: bpm, beatsPerBar: 4))
        }
    }

    // MARK: - Graph rebuild

    private func handleGraphRebuilt() {
        // Music bus first so stem/chop wiring lands on a live bus.
        musicBus.reattach()
        stemPlayer.reattach()
        clickTrack.reattach()
        if clock.state == .playing {
            stemPlayer.play(atSongSeconds: clock.nowSongSeconds)
            if clickEnabled { clickTrack.resync() }
        }
        onGraphReattached?()
    }
}

// MARK: - TransportAudioSink

extension EngineController: TransportAudioSink {

    public func play(atSongSeconds seconds: Double) {
        clock.seek(to: seconds)
        clock.play()
        stemPlayer.play(atSongSeconds: seconds)
        if clickEnabled { clickTrack.start() }
    }

    public func pause() {
        clock.pause()
        stemPlayer.pause()
        clickTrack.stop()
    }

    public func seek(toSongSeconds seconds: Double) {
        clock.seek(to: seconds)
        stemPlayer.seek(to: seconds)
        clickTrack.resync()
    }

    public func setPlaybackRate(_ rate: Double) {
        clock.setRate(rate)
        stemPlayer.setPlaybackRate(rate)
        clickTrack.resync()
    }
}
