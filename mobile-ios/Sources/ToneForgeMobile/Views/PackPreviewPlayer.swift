// PackPreviewPlayer.swift
//
// Single-AVPlayer preview playback for the Browse Packs sheet
// (Phase 10). One pack previews at a time: starting a new preview
// stops the old one, tapping the playing pack's button stops it, and
// the sheet calls `stop()` on dismiss so nothing keeps streaming
// under the Play tab.
//
// Deliberately separate from the engine graph — previews are
// disposable catalog streaming, not contribution audio, so an
// AVPlayer over the backend URL is the whole implementation.

import Foundation
import AVFoundation

@MainActor
final class PackPreviewPlayer: ObservableObject {

    /// Pack whose preview is currently playing; nil when idle.
    @Published private(set) var playingPackId: String?

    private var player: AVPlayer?
    private var endObserver: NSObjectProtocol?

    /// Toggle: play `url` tagged with `packId`, or stop if that pack
    /// is already playing.
    func toggle(packId: String, url: URL) {
        if playingPackId == packId {
            stop()
            return
        }
        stop()
        let item = AVPlayerItem(url: url)
        let player = AVPlayer(playerItem: item)
        endObserver = NotificationCenter.default.addObserver(
            forName: .AVPlayerItemDidPlayToEndTime,
            object: item,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.stop() }
        }
        self.player = player
        playingPackId = packId
        player.play()
    }

    func stop() {
        player?.pause()
        player = nil
        if let observer = endObserver {
            NotificationCenter.default.removeObserver(observer)
            endObserver = nil
        }
        playingPackId = nil
    }
}
