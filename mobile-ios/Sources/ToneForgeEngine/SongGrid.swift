// SongGrid.swift
//
// Port of the song-mode pad assignment from launchpad.js. Only
// implements Strategy 1 (encounter-order, lines 227-249) for v1 —
// pitch-layout and theory-layout can be ported later if the mobile
// app grows a mode toggle for them. Encounter-order is what the JS
// currently defaults to for shipped songs.
//
// Grid orientation is Programmer Mode: padIdx = (row + 1) * 10 +
// (col + 1) with row 0 = bottom, col 0 = left. So the first assigned
// chord lands on pad 11 (bottom-left), the eighth on pad 18 (bottom-
// right), the ninth on pad 21, and so on. Chords beyond the 64th are
// "overflow" — the JS drops them and so do we.

import Foundation

/// A chord assigned to a specific pad. Symbol preserved so pad
/// captions / tooltips can show the original ("Bbmaj7" instead of
/// "10:maj7").
public struct SongGridAssignment: Hashable, Sendable {
    public let padIdx: PadIndex
    public let symbol: String
    public let parsed: ParsedChord

    public init(padIdx: PadIndex, symbol: String, parsed: ParsedChord) {
        self.padIdx = padIdx
        self.symbol = symbol
        self.parsed = parsed
    }

    /// Convenience alias — the palette entry to paint for this pad.
    public var color: PadColor {
        Palette.songFamily(parsed.family)
    }
}

/// The full song-mode grid state: which pads are lit, what chord each
/// means, and which pads were dropped as overflow.
public struct SongGrid: Sendable {
    public let assignments: [PadIndex: SongGridAssignment]
    public let byCanonicalKey: [String: SongGridAssignment]
    public let overflow: [String]

    public init(
        assignments: [PadIndex: SongGridAssignment],
        byCanonicalKey: [String: SongGridAssignment],
        overflow: [String]
    ) {
        self.assignments = assignments
        self.byCanonicalKey = byCanonicalKey
        self.overflow = overflow
    }

    /// Look up the meaning of a pad press. Returns ``PadMeaning.none``
    /// for unassigned pads so callers can dispatch uniformly.
    public func meaning(for pad: PadIndex) -> PadMeaning {
        guard let a = assignments[pad] else { return .none }
        return .chord(symbol: a.symbol, family: a.parsed.family, degreeLabel: nil)
    }
}

/// Assignment strategies. Only ``.encounter`` is implemented in v1.
public enum SongGridStrategy: Sendable {
    /// Walk chords in time order, drop each unique canonical chord on
    /// the next free pad (row-major from bottom-left).
    case encounter
}

/// Static builder — pure function of the input chord list. Idempotent
/// (same list in, same grid out) so callers can memoise safely.
public enum SongGridBuilder {

    /// Assign chords to pads. ``symbols`` should be given in the order
    /// they appear in the song timeline; duplicates (same canonical
    /// key) are ignored after the first assignment.
    public static func build(
        symbols: [String],
        strategy: SongGridStrategy = .encounter
    ) -> SongGrid {
        switch strategy {
        case .encounter:
            return buildEncounter(symbols: symbols)
        }
    }

    // MARK: - Encounter-order

    private static func buildEncounter(symbols: [String]) -> SongGrid {
        var assignments: [PadIndex: SongGridAssignment] = [:]
        var byCanonicalKey: [String: SongGridAssignment] = [:]
        var overflow: [String] = []
        var cursor = 0  // 0..63; row-major from bottom-left

        for symbol in symbols {
            guard let parsed = ChordParser.parse(symbol) else { continue }
            let key = parsed.canonicalKey
            if byCanonicalKey[key] != nil {
                continue    // already placed this canonical chord
            }
            if cursor >= 64 {
                overflow.append(symbol)
                continue
            }
            let row = cursor / 8            // 0..7
            let col = cursor % 8            // 0..7
            let padIdx = PadIndex.at(row: row + 1, col: col + 1)
            let assignment = SongGridAssignment(
                padIdx: padIdx,
                symbol: symbol,
                parsed: parsed
            )
            assignments[padIdx] = assignment
            byCanonicalKey[key] = assignment
            cursor += 1
        }
        return SongGrid(
            assignments: assignments,
            byCanonicalKey: byCanonicalKey,
            overflow: overflow
        )
    }
}
