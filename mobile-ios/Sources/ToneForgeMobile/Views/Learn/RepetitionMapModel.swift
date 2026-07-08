// RepetitionMapModel.swift
//
// Groups the section timeline by label into repetition rows for the
// Section Overview sheet (D-022): one row per distinct section name,
// with every occurrence as a time range — so "chorus" reads as one
// row with three bars on the song timeline instead of three
// interleaved list entries.

import Foundation
import ToneForgeEngine

enum RepetitionMapModel {

    struct Row: Equatable {
        /// Display label, capitalized ("Chorus").
        let label: String
        /// Every occurrence of this section, song-time seconds,
        /// clamped to [0, duration], timeline order.
        let occurrences: [ClosedRange<Double>]
    }

    /// Rows ordered by each label's first appearance. Unlabelled
    /// sections group under "Section". Degenerate/out-of-range
    /// sections are dropped.
    static func rows(
        sections: [SectionEvent], duration: Double
    ) -> [Row] {
        guard duration > 0 else { return [] }

        var order: [String] = []
        var byKey: [String: [ClosedRange<Double>]] = [:]

        for s in sections {
            let start = max(0, min(s.start, duration))
            let end = max(0, min(s.end, duration))
            guard end > start else { continue }

            let key = (s.label ?? "Section").lowercased()
            if byKey[key] == nil { order.append(key) }
            byKey[key, default: []].append(start...end)
        }

        return order.map { key in
            Row(label: key.capitalized, occurrences: byKey[key] ?? [])
        }
    }
}
