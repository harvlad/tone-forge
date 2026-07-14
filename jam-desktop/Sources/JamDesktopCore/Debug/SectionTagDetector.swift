// SectionTagDetector.swift
//
// Port of debug.js detectSectionTags / TAG_REGISTRY — the same
// section-difficulty taxonomy jam.js uses (barre/colour/jumps/quick).
// Kept in lock-step by convention: if the rules change in jam.js,
// mirror them here AND in debug.js. Pure functions, no state.

import Foundation

public struct SectionTag: Equatable, Sendable, Identifiable {
    public let id: String
    public let label: String
    public let severity: Int
}

public enum SectionTagDetector {

    public static let barre = SectionTag(id: "barre", label: "Barre chord", severity: 3)
    public static let colour = SectionTag(id: "colour", label: "Colour chord", severity: 2)
    public static let jumps = SectionTag(id: "jumps", label: "Big jumps", severity: 2)
    public static let quick = SectionTag(id: "quick", label: "Fast changes", severity: 2)

    /// Display order for filter chips (matches debug.js TAG_ORDER).
    public static let allTags: [SectionTag] = [barre, colour, jumps, quick]

    // jam.js regexes verbatim. BARRE: F/F#/B/Bb roots (optionally minor)
    // not followed by a lowercase letter or digit — so Fmaj7 is NOT
    // barre (the "m" of "maj" is consumed by the optional m? then "a"
    // trips the lookahead) but F7 is blocked too? No: F then "7" trips
    // the (?![a-z0-9]) after the empty m? … F7 does NOT fire barre,
    // exactly like the web. COLOUR: any extension token anywhere,
    // case-insensitive.
    private static let barreRegex = try! NSRegularExpression(
        pattern: "^(F#?|B|Bb)(m?)(?![a-z0-9])")
    private static let colourRegex = try! NSRegularExpression(
        pattern: "(7|sus2|sus4|add9|maj7|m7)", options: [.caseInsensitive])

    private static func matches(_ regex: NSRegularExpression, _ s: String) -> Bool {
        regex.firstMatch(
            in: s, range: NSRange(s.startIndex..., in: s)) != nil
    }

    /// Tags for one section given the bundle-level chord list.
    /// Chord membership is by midpoint: mid in [start, end).
    public static func detectSectionTags(
        _ section: DebugSection, chords: [DebugChord]
    ) -> [SectionTag] {
        var tags: [SectionTag] = []
        let startS = section.startS ?? 0
        let endS = section.endS ?? 0

        var symbolsIn: [String] = []
        for chord in chords {
            guard let symbol = chord.symbol,
                  let cs = chord.startS, let ce = chord.endS,
                  cs.isFinite, ce.isFinite else { continue }
            let mid = 0.5 * (cs + ce)
            if mid >= startS && mid < endS { symbolsIn.append(symbol) }
        }

        if symbolsIn.contains(where: { matches(barreRegex, $0) }) {
            tags.append(barre)
        }
        if symbolsIn.contains(where: { matches(colourRegex, $0) }) {
            tags.append(colour)
        }

        let landmarks = section.landmarkNotes ?? []
        if landmarks.count >= 2 {
            let pitches = landmarks.compactMap { $0.pitch }.filter { $0.isFinite }
            if let lo = pitches.min(), let hi = pitches.max(), hi - lo > 10 {
                tags.append(jumps)
            }
        }

        let dur = max(0, endS - startS)
        if dur > 0 && dur < 4 { tags.append(quick) }
        return tags
    }

    /// Per-section tag rows for a whole bundle (debug.js tagSummary).
    public struct SectionTagRow: Sendable {
        public let index: Int
        public let tags: [SectionTag]
    }

    public static func tagSummary(_ bundle: DebugBundle) -> [SectionTagRow] {
        let chords = bundle.chords
        return bundle.sections.enumerated().map { idx, sec in
            SectionTagRow(index: idx, tags: detectSectionTags(sec, chords: chords))
        }
    }
}
