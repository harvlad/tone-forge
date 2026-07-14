// SequencerAudioAdapter.swift
//
// SequencerPlayerDelegate → ChopPlayer bridge: resolves the engine's
// ChopReference trigger callbacks into actual sample playback on the
// desktop voice pool. The desktop counterpart of the iOS
// SampleScheduler conformance.
//
//   .bundleChop  → resolve (presetKey, chopIndex) against the loaded
//                  bundle's presets (chops sorted by idx — the same
//                  order the Launchpad grid and pickers present) and
//                  fire the chop voice immediately.
//   .customURL   → file-segment trigger (readers cached in ChopPlayer).
//   .packPad     → PackPadPlayer (registered packs; else no-op).
//   .synthChord  → DesktopSynthNode one-shot chord.
//   .localSample → no-op (no local-sample store on desktop).
//
// Triggers fire immediately (afterSeconds: 0) — step timing is the
// SequencerClock's job; adding quantize here would double-delay.

import Foundation
import ToneForgeEngine
import JamDesktopCore

/// The slice of ChopPlayer the adapter needs — a seam so mapping
/// tests run without an AVAudioEngine.
@MainActor
public protocol SequencerChopTriggering: AnyObject {
    func sequencerTriggerChop(
        _ assignment: PadAssignment, velocity: Float, pan: Float)
    func sequencerTriggerFile(
        url: URL, startSec: Double?, endSec: Double?,
        velocity: Float, pan: Float)
}

extension ChopPlayer: SequencerChopTriggering {
    public func sequencerTriggerChop(
        _ assignment: PadAssignment, velocity: Float, pan: Float
    ) {
        trigger(assignment, afterSeconds: 0, velocity: velocity, pan: pan)
    }

    public func sequencerTriggerFile(
        url: URL, startSec: Double?, endSec: Double?,
        velocity: Float, pan: Float
    ) {
        trigger(
            file: url, startSec: startSec, endSec: endSec,
            velocity: velocity, pan: pan
        )
    }
}

@MainActor
public final class SequencerAudioAdapter {

    private let sink: any SequencerChopTriggering

    /// Pack pad playback (P5). Weak — SessionController owns both.
    public weak var packPlayer: PackPadPlayer?
    /// Synth chord playback (P5). Weak — SessionController owns both.
    public weak var synth: DesktopSynthNode?

    /// Per-preset chops sorted by idx — chopIndex in a ChopReference
    /// indexes into this order.
    private var sortedChops: [String: [Chop]] = [:]
    /// Per-preset stem role the chops slice.
    private var stems: [String: String] = [:]

    public init(sink: any SequencerChopTriggering) {
        self.sink = sink
    }

    /// Adopt a song's presets so bundleChop references resolve.
    /// `edits` (ChopEditStore, keyed by presetKey) overlay boundary
    /// edits via resolvedChops — the same display order the
    /// Launchpad grid and sequencer add-track menu present.
    public func configure(bundle: SongBundle, edits: [String: ChopEdits] = [:]) {
        sortedChops = Dictionary(
            uniqueKeysWithValues: bundle.presets.map { key, preset in
                let sorted = preset.chops.sorted { $0.idx < $1.idx }
                if let presetEdits = edits[key], presetEdits.hasEdits {
                    return (key, resolvedChops(
                        bundleChops: sorted, edits: presetEdits
                    ).map { $0.toChop() })
                }
                return (key, sorted)
            }
        )
        stems = bundle.presets.mapValues(\.stem)
    }

    /// Song unloaded — bundleChop references stop resolving.
    public func reset() {
        sortedChops = [:]
        stems = [:]
    }
}

// MARK: - SequencerPlayerDelegate

extension SequencerAudioAdapter: SequencerPlayerDelegate {

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playBundleChop presetKey: String,
        chopIndex: Int,
        velocity: Float,
        pan: Float
    ) {
        guard let chops = sortedChops[presetKey],
              let stem = stems[presetKey],
              chops.indices.contains(chopIndex)
        else { return }
        sink.sequencerTriggerChop(
            PadAssignment(chop: chops[chopIndex], stem: stem),
            velocity: velocity, pan: pan
        )
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playURL url: URL,
        startSec: Double?,
        endSec: Double?,
        velocity: Float,
        pan: Float
    ) {
        sink.sequencerTriggerFile(
            url: url, startSec: startSec, endSec: endSec,
            velocity: velocity, pan: pan
        )
    }

    // No local-sample store on desktop yet (Phase 4/5).
    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playLocalSample id: UUID,
        velocity: Float,
        pan: Float
    ) {}

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playPackPad packId: String,
        padIdx: Int,
        velocity: Float,
        pan: Float
    ) {
        packPlayer?.trigger(
            packId: packId, padIdx: padIdx, velocity: velocity, pan: pan)
    }

    public func sequencerPlayer(
        _ player: SequencerPlayer,
        playSynthChord symbol: String,
        octaveShift: Int,
        velocity: Float
    ) {
        synth?.playChord(
            symbol: symbol, octaveShift: octaveShift, velocity: velocity)
    }
}
