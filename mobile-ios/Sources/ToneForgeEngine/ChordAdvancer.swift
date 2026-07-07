// ChordAdvancer.swift
//
// Walks the `bundle.timeline.chords` array against a monotonic song
// clock and produces:
//   - the currently-playing chord event
//   - the chord that will play next (if any)
//   - a 0..1 phase within the current chord (for pulse animations)
//
// The web version keys the on-screen active chord + next-chord pulse
// off Web Audio's `currentTime`. On iOS the driving clock is the
// TransportClock; ChordAdvancer is pure logic that takes a song-time
// in seconds and does a binary search.
//
// Not `@MainActor`: hot path called from the UI update loop and from
// audio callbacks. Immutable input (`chords`) is fine to read from
// any thread.

import Foundation

/// A frame at a specific song-time. Consumers redraw the ribbon /
/// pulse animation when `active` or `next` changes.
public struct ChordFrame: Sendable, Equatable {
    public let active: ChordEvent?
    public let next: ChordEvent?
    /// 0..1 within the active chord's duration. 0 when no active chord.
    public let phase: Double

    public init(active: ChordEvent?, next: ChordEvent?, phase: Double) {
        self.active = active
        self.next = next
        self.phase = phase
    }
}

/// Pure walker over a chord timeline. Immutable after construction —
/// swap the instance if the timeline changes (e.g. a new bundle
/// loads).
public struct ChordAdvancer: Sendable {

    public let chords: [ChordEvent]

    public init(chords: [ChordEvent]) {
        // Store sorted by start time so the binary search is valid.
        // Analyzer output is normally sorted already, but we don't want
        // to trust that.
        self.chords = chords.sorted { $0.start < $1.start }
    }

    /// Build a frame for the given song-time. Returns a zero frame
    /// when the timeline is empty.
    public func frame(at songSeconds: Double) -> ChordFrame {
        if chords.isEmpty {
            return ChordFrame(active: nil, next: nil, phase: 0)
        }

        // Find the largest index whose start <= songSeconds. That's
        // the active chord candidate.
        var lo = 0
        var hi = chords.count
        while lo < hi {
            let mid = (lo + hi) / 2
            if chords[mid].start <= songSeconds {
                lo = mid + 1
            } else {
                hi = mid
            }
        }
        let idx = lo - 1

        // Before the first chord: nothing active, upcoming is chords[0].
        if idx < 0 {
            return ChordFrame(active: nil, next: chords.first, phase: 0)
        }

        let candidate = chords[idx]
        // If the candidate's end <= songSeconds, we're in a gap — no
        // active chord but there may be a next one.
        if candidate.end <= songSeconds {
            let next = (idx + 1) < chords.count ? chords[idx + 1] : nil
            return ChordFrame(active: nil, next: next, phase: 0)
        }

        let duration = max(0.0001, candidate.end - candidate.start)
        let phase = min(1.0, max(0.0, (songSeconds - candidate.start) / duration))
        let next = (idx + 1) < chords.count ? chords[idx + 1] : nil
        return ChordFrame(active: candidate, next: next, phase: phase)
    }

    /// True if the timeline has zero chord events. Useful for the UI
    /// so it can render "no chords" state instead of empty ribbon.
    public var isEmpty: Bool { chords.isEmpty }

    /// Total duration covered by the timeline. Zero when empty.
    public var totalDuration: Double {
        chords.last?.end ?? 0
    }
}
