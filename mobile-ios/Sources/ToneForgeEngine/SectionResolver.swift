// SectionResolver.swift
//
// Pure lookup helper: given a song-time and the bundle's section
// events, tell me which section we're inside (if any), and whether a
// candidate trigger is allowed by a per-section gate.
//
// This is the "Play only in" logic from the mockup: the SamplePanel
// lets the user check/uncheck section labels ("Verse", "Chorus", …)
// and taps during unchecked sections are silently swallowed. The
// SampleScheduler consults `isAllowed(...)` before scheduling; the UI
// consumes `current(...)` to tint the transport / section chips.
//
// The sections array comes from `BundleTimeline.sections` and is
// backend-sorted by `start`, so `current(...)` is a plain O(log n)
// binary search. We tolerate overlaps by returning the first section
// whose `[start, end)` window contains `t`.

import Foundation

public enum SectionResolver {

    /// Section containing `t`, or nil if `t` falls in a gap. Half-open
    /// window `[start, end)` — a boundary time belongs to the section
    /// starting there, not the one ending there.
    public static func current(t: Double, in sections: [SectionEvent]) -> SectionEvent? {
        guard !sections.isEmpty else { return nil }

        // Binary search for the last section with start <= t.
        var lo = 0
        var hi = sections.count
        while lo < hi {
            let mid = (lo &+ hi) >> 1
            if sections[mid].start <= t { lo = mid + 1 } else { hi = mid }
        }
        let idx = lo - 1
        guard idx >= 0 else { return nil }
        let s = sections[idx]
        // Guard against t past the end (gap between this and next).
        return t < s.end ? s : nil
    }

    /// Convenience: label of the current section, uppercased-first-only
    /// to match the UI chip style ("Chorus", "Verse", …). Returns nil
    /// when in a gap or when the section has no label.
    public static func currentLabel(t: Double, in sections: [SectionEvent]) -> String? {
        guard let s = current(t: t, in: sections) else { return nil }
        return s.label
    }

    /// True when a trigger at song-time `t` should be allowed by the
    /// section gate. Semantics:
    ///   - `allowed == nil` (never configured) → allow.
    ///   - `allowed.isEmpty` (all unchecked) → deny.
    ///   - otherwise allow iff the current section's label is in `allowed`.
    ///   - `t` in a gap between sections → allow (musical "no section"
    ///     shouldn't punish the user; usually intros/outros).
    ///
    /// Comparison is case-insensitive so persisted allow-lists survive
    /// backends that emit different capitalisations of the same label.
    public static func isAllowed(
        t: Double,
        in sections: [SectionEvent],
        allowed: Set<String>?
    ) -> Bool {
        guard let allowed = allowed else { return true }
        guard let s = current(t: t, in: sections) else { return true }
        guard let label = s.label, !label.isEmpty else { return true }
        if allowed.isEmpty { return false }
        let key = label.lowercased()
        return allowed.contains(where: { $0.lowercased() == key })
    }

    /// Unique, order-preserving list of labels present in `sections`.
    /// Used by `SectionGateChips` to render the "Play only in" row.
    /// nil-labelled sections are dropped.
    public static func uniqueLabels(in sections: [SectionEvent]) -> [String] {
        var seen = Set<String>()
        var out: [String] = []
        for s in sections {
            guard let label = s.label, !label.isEmpty else { continue }
            let key = label.lowercased()
            if seen.insert(key).inserted {
                out.append(label)
            }
        }
        return out
    }
}
