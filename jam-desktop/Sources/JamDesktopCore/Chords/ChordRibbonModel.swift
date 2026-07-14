// ChordRibbonModel.swift
//
// Playhead → chord/section lookups for the Perform chord ribbon.
// Pure value type over a bundle timeline; binary search on the chord
// starts so a 60 fps display timer never scans the whole array.
//
// Section lookup delegates to ToneForgeEngine.SectionResolver (the
// same rule the mobile app and Launchpad logic use).

import Foundation
import ToneForgeEngine

public struct ChordRibbonModel: Sendable, Equatable {

    public let chords: [ChordEvent]
    public let sections: [SectionEvent]

    public init(timeline: BundleTimeline) {
        // Defensive sort: analysis output is ordered, but the ribbon's
        // binary search silently misbehaves on unsorted input.
        self.chords = timeline.chords.sorted { $0.start < $1.start }
        self.sections = timeline.sections
    }

    public init(chords: [ChordEvent], sections: [SectionEvent]) {
        self.chords = chords.sorted { $0.start < $1.start }
        self.sections = sections
    }

    /// Index of the chord sounding at `t`, or nil in a gap / outside
    /// the song. Binary search on `start`, then a containment check.
    public func chordIndex(at t: Double) -> Int? {
        guard !chords.isEmpty else { return nil }
        // Rightmost chord with start <= t.
        var lo = 0
        var hi = chords.count - 1
        var candidate: Int?
        while lo <= hi {
            let mid = (lo + hi) / 2
            if chords[mid].start <= t {
                candidate = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        guard let idx = candidate, t < chords[idx].end else { return nil }
        return idx
    }

    public func currentChord(at t: Double) -> ChordEvent? {
        chordIndex(at: t).map { chords[$0] }
    }

    /// The ribbon window: current chord (or next upcoming if in a
    /// gap) plus the following `count - 1` chords.
    public func window(at t: Double, count: Int) -> [ChordEvent] {
        guard count > 0, !chords.isEmpty else { return [] }
        let startIdx: Int
        if let idx = chordIndex(at: t) {
            startIdx = idx
        } else if let next = chords.firstIndex(where: { $0.start > t }) {
            startIdx = next
        } else {
            return []
        }
        return Array(chords[startIdx..<min(startIdx + count, chords.count)])
    }

    public func currentSection(at t: Double) -> SectionEvent? {
        SectionResolver.current(t: t, in: sections)
    }
}
