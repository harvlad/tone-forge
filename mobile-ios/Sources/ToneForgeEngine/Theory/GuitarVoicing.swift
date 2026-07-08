// GuitarVoicing.swift
//
// Chord symbol → guitar chord shape (D-022 Learn redesign). Feeds
// the Learn tab's fretboard diagrams. Standard tuning only, and the
// search is deliberately simple: a sliding 4-fret window with a
// bass-note rule, tuned so open-position folk shapes come out
// exactly as the charts everyone knows (Am → x02210, E → 022100,
// C → x32010, Dm → xx0231, G7 → 320001 — pinned by
// GuitarVoicingTests).
//
// Algorithm per window (frets base…base+3; open strings allowed only
// in the nut window, so higher windows produce moveable shapes):
//   1. Bass rule: the lowest string that can sound the ROOT inside
//      the window carries the bass; every string below it is muted.
//      (This is what mutes low-E on Am/C and both low strings on Dm.)
//   2. Strings above the bass take the lowest in-window fret whose
//      pitch class is a chord tone, else they are muted.
//   3. A window wins outright if the sounding strings cover every
//      chord tone; otherwise the best-covering window is kept as a
//      fallback.

import Foundation

/// One string of a chord shape, low E → high E order.
public enum GuitarStringState: Equatable, Sendable {
    case muted
    case open
    /// Absolute fret number (1-based; never 0 — that's `.open`).
    case fretted(Int)
}

/// A playable guitar chord shape in standard tuning.
public struct GuitarChordShape: Equatable, Sendable {
    /// Lowest fret of the diagram window, 1-based. 1 = open
    /// position (draw the nut).
    public let baseFret: Int
    /// Exactly six entries, low E → high E.
    public let strings: [GuitarStringState]

    public init(baseFret: Int, strings: [GuitarStringState]) {
        self.baseFret = baseFret
        self.strings = strings
    }
}

public enum GuitarVoicing {

    /// Standard tuning, MIDI note per open string, low E → high E.
    public static let standardTuning = [40, 45, 50, 55, 59, 64]

    /// Frets a chord-diagram window spans.
    private static let windowSize = 4

    /// Shape for a chord symbol; nil when the symbol doesn't parse
    /// or no window can voice the root.
    public static func shape(symbol: String) -> GuitarChordShape? {
        guard let parsed = ChordParser.parse(symbol) else { return nil }
        return shape(for: parsed)
    }

    public static func shape(for chord: ParsedChord) -> GuitarChordShape? {
        let rootPC = chord.root.rawValue
        let tones = Set(ChordVoicing.chordTones(for: chord).map {
            (rootPC + $0) % 12
        })

        var best: (shape: GuitarChordShape, covered: Int, sounding: Int)?

        for base in 0...9 {
            guard let candidate = voicing(
                inWindowStartingAt: base, rootPC: rootPC, tones: tones
            ) else { continue }

            if candidate.coveredTones.count == tones.count {
                return candidate.shape      // full coverage — done
            }
            let sounding = candidate.shape.strings.filter { $0 != .muted }.count
            if best == nil
                || candidate.coveredTones.count > best!.covered
                || (candidate.coveredTones.count == best!.covered
                    && sounding > best!.sounding) {
                best = (candidate.shape, candidate.coveredTones.count, sounding)
            }
        }
        return best?.shape
    }

    // MARK: - Window search

    private static func voicing(
        inWindowStartingAt base: Int,
        rootPC: Int,
        tones: Set<Int>
    ) -> (shape: GuitarChordShape, coveredTones: Set<Int>)? {
        // Frets playable in this window. Open strings only make
        // sense in the nut window — a moveable shape can't hold
        // them down.
        let frets: [Int] = base == 0
            ? Array(0..<windowSize)
            : Array(base..<(base + windowSize))

        // 1. Bass rule.
        guard let bassString = standardTuning.indices.first(where: { s in
            frets.contains { (standardTuning[s] + $0) % 12 == rootPC }
        }) else { return nil }

        var strings = [GuitarStringState](repeating: .muted, count: 6)
        var covered = Set<Int>()

        for s in standardTuning.indices where s >= bassString {
            let wantedPCs = s == bassString ? Set([rootPC]) : tones
            guard let fret = frets.first(where: {
                wantedPCs.contains((standardTuning[s] + $0) % 12)
            }) else { continue }
            strings[s] = fret == 0 ? .open : .fretted(fret)
            covered.insert((standardTuning[s] + fret) % 12)
        }

        let shape = GuitarChordShape(
            baseFret: max(1, base), strings: strings)
        return (shape, covered.intersection(tones))
    }
}
