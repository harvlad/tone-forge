// KitGridMapper.swift
//
// SamplePack (the backend /api/song/{id}/kit manifest) → the Launchpad's
// (Chop, stem) assignment pairs, in SERVER order. The backend is the only
// builder of the kit — selection, ranking, category grouping, labels and
// colors all happen in kit_builder.py and arrive baked into the manifest
// (web parity: kit.js renders the pads verbatim; padIdx IS the grid slot).
// This mapper only translates the wire pads into the chop-based grid the
// desktop audio path plays, preserving order/label/color/category 1:1.
//
// Extracted from SessionController.loadAutoKit so the 64-pad flood
// (pads=64 requests fill the whole 8×8, kit=5+ category-grouped rows,
// "Drums beat Verse"-style labels) is pinned by unit tests instead of
// only being visible in the app.

import Foundation
import ToneForgeEngine

public enum KitGridMapper {

    /// Convert a kit manifest's pads into row-major grid assignments.
    ///
    /// - Parameters:
    ///   - pack: the server kit (pads carry `stemSlice` windows into the
    ///     song's stems — the audio is never in the manifest).
    ///   - sampleFiles: padIdx → locally downloaded clean composite (drum
    ///     kit `sampleUrl` pads). A pad with a file gets the `drumfile:`
    ///     sentinel assetId so the trigger path plays the file and the
    ///     usage-feedback loop skips it.
    /// - Returns: (chop, stem) pairs in manifest order. Pads without a
    ///   `stemSlice` are dropped (nothing to play); the server emits
    ///   contiguous padIdx for kit pads, so order == grid slot.
    public static func pairs(
        pack: SamplePack, sampleFiles: [Int: URL] = [:]
    ) -> [(chop: Chop, stem: String)] {
        pack.pads.compactMap { pad in
            guard let slice = pad.stemSlice else { return nil }
            let loopable = pad.loopable ?? ((pad.loopScore ?? 0) >= 0.55)
            let chop = Chop(
                idx: pad.padIdx,
                startSec: slice.startSec,
                endSec: slice.endSec,
                durationSec: max(0, slice.endSec - slice.startSec),
                kind: loopable ? "phrase" : "chord",
                // Descriptive kit name ("Drums beat Verse") shown on the pad.
                sectionLabel: pad.name,
                colorHint: pad.colorHint,
                // Carry the kit metadata so the grid can group/color by
                // category and Instant Groove can pick the best per role.
                contentType: pad.contentType,
                performanceScore: pad.performanceScore,
                difficulty: pad.difficulty,
                loopable: pad.loopable,
                loopScore: pad.loopScore,
                // Carry the analyzer's measured seam crossfade so held
                // loops use it (else SessionController's 15 ms floor).
                crossfadeMs: pad.crossfadeMs,
                // Usage feedback loop keys on the graph-asset id. Kit
                // pads with a downloaded clean composite carry the
                // `drumfile:` sentinel instead — onTrigger routes them
                // to the file player, and pad-usage skips them.
                assetId: sampleFiles[pad.padIdx] != nil
                    ? "drumfile:\(pad.padIdx)" : pad.assetId,
                // The SERVER'S category verbatim — the only way a
                // residual-`other` SYNTH pad colors/groups like web
                // (stem+contentType can't reproduce that call).
                category: pad.category
            )
            return (chop, slice.stemRole)
        }
    }
}
