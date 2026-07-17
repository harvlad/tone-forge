// PackPadPlayer.swift
//
// Plays curated sample-pack pads (iOS parity P5). A resolved pack's
// pad file URLs are registered after activation; triggers route
// through the SequencerChopTriggering seam (ChopPlayer in
// production), so pack audio shares the chop voice pool and lands on
// musicBus like every other musical source — and mapping tests run
// without an AVAudioEngine.
//
// v1 simplification: pads are one-shot file triggers. Manifest
// loopPointSec / chokeGroup / per-pad effects are ignored, and
// song-derived pads (stemSlice, no filename) don't resolve — only
// file-backed pads appear in ResolvedSamplePack.padFileURLs anyway.

import Foundation
import ToneForgeEngine

@MainActor
public final class PackPadPlayer {

    private let sink: any SequencerChopTriggering

    /// packId → resolved pack (manifest + local pad file URLs).
    private var packs: [String: ResolvedSamplePack] = [:]

    public init(sink: any SequencerChopTriggering) {
        self.sink = sink
    }

    /// Make a pack triggerable. Called on activation and on sequencer
    /// pattern load for any packs its tracks reference.
    public func register(_ resolved: ResolvedSamplePack) {
        packs[resolved.pack.packId] = resolved
    }

    public func isRegistered(packId: String) -> Bool {
        packs[packId] != nil
    }

    /// Fire one pad. Velocity is scaled by the pad's manifest gainDb.
    /// Unknown pack / pad-index / non-file pads no-op.
    public func trigger(
        packId: String,
        padIdx: Int,
        velocity: Float = 1,
        pan: Float = 0
    ) {
        guard let resolved = packs[packId],
              let url = resolved.padFileURLs[padIdx]
        else {
            print("[PackPadPlayer] dropped trigger: unknown pack/pad \(packId)/\(padIdx)")
            return
        }
        let gainDb = resolved.pack.pads
            .first(where: { $0.padIdx == padIdx })?.gainDb ?? 0
        let scaled = velocity * Float(pow(10.0, gainDb / 20.0))
        sink.sequencerTriggerFile(
            url: url, startSec: nil, endSec: nil,
            velocity: max(0, min(1, scaled)), pan: pan
        )
    }
}
