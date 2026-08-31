// LinkSync.swift
//
// Ableton Link session sync (V1: FOLLOW-only). When enabled, jamn
// joins the local Link session — Live (or any Link peer) provides the
// shared tempo + bar phase; jamn follows:
//   - the step sequencer runs at the Link tempo,
//   - loop launches land on the NEXT LINK BAR instead of the song
//     transport's grid, so pads fired here stack in phase with clips
//     playing in Live.
// jamn never proposes a tempo in V1 — the DAW is the authority; a
// solo session (no peers) just keeps the song tempo.
//
// Backed by the vendored ableton::Link via the AbletonLinkShim C
// facade (GPLv2+ — obtain Ableton's proprietary Link license before
// commercial distribution).

import AbletonLinkShim
import Foundation

@MainActor
final class LinkSync: ObservableObject {

    @Published private(set) var enabled = false
    @Published private(set) var peers = 0
    @Published private(set) var tempo: Double = 120

    /// Fired (main actor) whenever the session tempo changes while
    /// enabled — SessionController retunes the sequencer with it.
    var onTempoChanged: ((Double) -> Void)?

    /// One shared Link bar = 4 beats (the kit's whole world is 4/4).
    static let quantum: Double = 4

    private var handle: OpaquePointer?
    private var pollTimer: Timer?

    init(initialBpm: Double = 120) {
        handle = link_create(initialBpm)
        tempo = initialBpm
    }

    deinit {
        // Actor-isolated deinit isn't available on our toolchain floor;
        // handle teardown happens in disable/app exit. Leaking one Link
        // instance at process death is harmless.
    }

    func setEnabled(_ on: Bool) {
        guard let handle else { return }
        link_enable(handle, on)
        enabled = on
        if on {
            startPolling()
        } else {
            pollTimer?.invalidate()
            pollTimer = nil
            peers = 0
        }
    }

    func toggle() { setEnabled(!enabled) }

    /// Seconds until the next shared Link bar boundary. 0 when not
    /// enabled (callers fall back to the song grid).
    func secondsToNextBar() -> Double {
        guard enabled, let handle else { return 0 }
        return link_seconds_to_next_quantum(handle, Self.quantum)
    }

    /// Seed the session tempo — used ONLY when joining with no peers,
    /// so a solo jam still runs at the song's tempo. With peers
    /// present the session tempo wins (follow-only).
    func seedTempoIfAlone(_ bpm: Double) {
        guard enabled, let handle, peers == 0, bpm > 0 else { return }
        link_set_tempo(handle, bpm)
    }

    private func startPolling() {
        pollTimer?.invalidate()
        let timer = Timer(timeInterval: 0.25, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.poll() }
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
    }

    private func poll() {
        guard enabled, let handle else { return }
        let newPeers = Int(link_num_peers(handle))
        if newPeers != peers { peers = newPeers }
        let newTempo = link_tempo(handle)
        if abs(newTempo - tempo) > 0.001 {
            tempo = newTempo
            onTempoChanged?(newTempo)
        }
    }
}
