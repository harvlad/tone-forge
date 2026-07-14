// ChordDiagramModel.swift
//
// Chord symbol -> drawable fretboard diagram. Voicings come from
// ToneForgeEngine.GuitarVoicing (native, standard tuning) — no
// chord_shapes.json fetch. Open-position folk shapes match the web
// chart data exactly (pinned by the parity spot-check test); barre
// chords intentionally differ: GuitarVoicing prefers playable
// open-string voicings over full barres (e.g. F -> 103211, not
// 133211).

import Foundation
import ToneForgeEngine

public struct ChordDiagram: Equatable, Sendable {

    public struct Dot: Equatable, Sendable {
        /// String index, 0 = low E ... 5 = high E.
        public let string: Int
        /// Absolute fret (1-based).
        public let fret: Int

        public init(string: Int, fret: Int) {
            self.string = string
            self.fret = fret
        }
    }

    public struct Barre: Equatable, Sendable {
        public let fret: Int
        public let fromString: Int
        public let toString: Int

        public init(fret: Int, fromString: Int, toString: Int) {
            self.fret = fret
            self.fromString = fromString
            self.toString = toString
        }
    }

    public let symbol: String
    /// Lowest fret of the 4-fret diagram window, 1-based; 1 = open
    /// position (draw the nut).
    public let baseFret: Int
    public let dots: [Dot]
    public let openStrings: [Int]
    public let mutedStrings: [Int]
    /// Inferred: >=3 strings share the window's lowest fretted fret
    /// with no open/muted strings inside the span. Cosmetic only.
    public let barre: Barre?

    /// chord_shapes.json convention for parity tests: one entry per
    /// string low E -> high E; -1 muted, 0 open, else absolute fret.
    public var fretsArray: [Int] {
        var frets = [Int](repeating: -1, count: 6)
        for string in openStrings { frets[string] = 0 }
        for dot in dots { frets[dot.string] = dot.fret }
        return frets
    }

    public static func make(symbol: String) -> ChordDiagram? {
        guard let shape = GuitarVoicing.shape(symbol: symbol) else { return nil }

        var dots: [Dot] = []
        var open: [Int] = []
        var muted: [Int] = []
        for (string, state) in shape.strings.enumerated() {
            switch state {
            case .muted: muted.append(string)
            case .open: open.append(string)
            case .fretted(let fret): dots.append(Dot(string: string, fret: fret))
            }
        }

        return ChordDiagram(
            symbol: symbol,
            baseFret: shape.baseFret,
            dots: dots,
            openStrings: open,
            mutedStrings: muted,
            barre: inferBarre(dots: dots, open: open, muted: muted)
        )
    }

    static func inferBarre(dots: [Dot], open: [Int], muted: [Int]) -> Barre? {
        guard let minFret = dots.map(\.fret).min() else { return nil }
        let atMin = dots.filter { $0.fret == minFret }.map(\.string).sorted()
        guard atMin.count >= 3,
              let from = atMin.first, let to = atMin.last else { return nil }
        // Every string inside the span must be fretted at or above
        // the barre fret — an open or muted string breaks the bar.
        for string in from...to {
            guard dots.contains(where: { $0.string == string && $0.fret >= minFret })
            else { return nil }
        }
        return Barre(fret: minFret, fromString: from, toString: to)
    }
}
