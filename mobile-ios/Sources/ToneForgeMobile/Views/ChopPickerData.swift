// ChopPickerData.swift
//
// Shared data sources for ChopPickerSheet. SamplePadGrid4x4's radial
// Add Sound and JamView's Sounds chip present the same picker; both
// pull their sections from these AppState accessors so the two entry
// points can never drift.

import Foundation
import ToneForgeEngine

extension AppState {

    /// Saved sequencer patterns available to assign to a pad.
    var pickerSequences: [SequenceInfo] {
        sequencerPatternStore.all().map { pattern in
            SequenceInfo(
                id: pattern.id,
                name: pattern.name,
                trackCount: pattern.tracks.count,
                stepCount: pattern.stepCount.rawValue
            )
        }
    }

    /// Bundle chops grouped by preset key from the current song, with
    /// section chops synthesized from the timeline when no preset
    /// carries them.
    var pickerBundleChops: [String: [Chop]] {
        guard let bundle = currentBundle else { return [:] }
        var result: [String: [Chop]] = [:]

        for (key, preset) in bundle.presets {
            if !preset.chops.isEmpty {
                result[key] = preset.chops
            }
        }

        if result["sections"] == nil && !bundle.timeline.sections.isEmpty {
            result["sections"] = bundle.timeline.sections.enumerated().map { idx, section in
                Chop(
                    idx: idx,
                    startSec: section.start,
                    endSec: section.end,
                    durationSec: section.end - section.start,
                    kind: "section",
                    root: nil,
                    sectionLabel: section.label,
                    chordSymbol: nil,
                    colorHint: nil
                )
            }
        }

        return result
    }

    /// Sample packs available for the picker: active pack first, then
    /// every other carousel page.
    var pickerSamplePacks: [SamplePackInfo] {
        var packs: [SamplePackInfo] = []

        if let active = activeSamplePack {
            let pads = active.pack.pads.map { pad in
                SamplePadInfo(padIdx: pad.padIdx, name: pad.name, family: pad.family)
            }
            packs.append(SamplePackInfo(
                id: active.pack.packId,
                name: active.pack.packId.replacingOccurrences(of: "-", with: " ").capitalized,
                padCount: pads.count,
                pads: pads
            ))
        }

        for page in carouselPages {
            if packs.contains(where: { $0.id == page.id }) { continue }
            if let resolved = resolvedPack(for: page) {
                let pads = resolved.pack.pads.map { pad in
                    SamplePadInfo(padIdx: pad.padIdx, name: pad.name, family: pad.family)
                }
                packs.append(SamplePackInfo(
                    id: resolved.pack.packId,
                    name: page.displayName,
                    padCount: pads.count,
                    pads: pads
                ))
            }
        }

        return packs
    }

    /// Curated catalog packs not yet downloaded — download rows so the
    /// user can pull them inline instead of leaving for the Library.
    var pickerDownloadablePacks: [DownloadablePackInfo] {
        let present = Set(pickerSamplePacks.map { $0.id })
        return curatedCatalog
            .filter { !present.contains($0.packId) }
            .map { entry in
                DownloadablePackInfo(
                    id: entry.packId,
                    name: entry.name,
                    family: entry.family,
                    padCount: entry.padCount
                )
            }
    }

    /// packIds with an in-flight (not-complete) curated download.
    var pickerDownloadingPackIds: Set<String> {
        Set(curatedDownloads.values
            .filter { !$0.isComplete }
            .map { $0.packId })
    }

    /// Fractional progress (0–1) per in-flight curated download —
    /// byte-weighted when the server declared sizes, else pad-count.
    var pickerDownloadFractions: [String: Double] {
        curatedDownloads.reduce(into: [:]) { dict, kv in
            let p = kv.value
            guard !p.isComplete else { return }
            if p.bytesTotal > 0 {
                dict[kv.key] = Double(p.bytesDownloaded) / Double(p.bytesTotal)
            } else if p.padsTotal > 0 {
                dict[kv.key] = Double(p.padsCompleted) / Double(p.padsTotal)
            } else {
                dict[kv.key] = 0
            }
        }
    }
}
