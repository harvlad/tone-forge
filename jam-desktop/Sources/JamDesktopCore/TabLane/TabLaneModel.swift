// TabLaneModel.swift
//
// Pure layout math for the scrolling guitar-tab lane — port of
// picking-tab-lane.js (262 LOC). Notes are placed once at absolute
// x = start_s * pxPerSec; per-frame the whole group shifts by
// translation(at:) so the playhead (anchored ~30% in) lines up with
// the current playback time.
//
// Parity notes:
//   - pitchToStringFret: single-position heuristic, prefers the
//     string whose fret is closest to 5; range 0...17; low-E-first
//     tie-break (same iteration order as the JS).
//   - Tab convention: high E drawn on top (visual row = 5 - string).
//   - Glyphs: dot | fret | note.

import Foundation

public struct TabLaneNote: Equatable, Sendable {
    public let pitch: Int
    public let startS: Double

    public init(pitch: Int, startS: Double) {
        self.pitch = pitch
        self.startS = startS
    }
}

public struct TabLanePlacement: Equatable, Sendable {
    /// String index, 0 = low E ... 5 = high E.
    public let string: Int
    public let fret: Int
    public let noteName: String
    /// Absolute x in lane coordinates (before per-frame translation).
    public let x: Double
    public let y: Double
}

public enum TabLaneGlyph: String, CaseIterable, Sendable {
    case dot, fret, note
}

public struct TabLaneModel: Sendable {

    /// Standard tuning, MIDI note per open string, low E -> high E
    /// (chord_diagrams.js STANDARD_TUNING).
    public static let standardTuning = [40, 45, 50, 55, 59, 64]
    public static let maxFret = 17

    private static let noteNames = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ]

    // Geometry (JS defaults).
    public var width: Double = 640
    public var height: Double = 110
    public var padLeft: Double = 36
    public var padRight: Double = 12
    public var padTop: Double = 14
    public var padBottom: Double = 14
    public var playheadFracX: Double = 0.3

    public var lookaheadS: Double = 2.0 {
        didSet { if !(lookaheadS > 0) { lookaheadS = oldValue } }
    }
    public var glyph: TabLaneGlyph = .dot
    public var notes: [TabLaneNote] = []

    public init() {}

    // MARK: Derived geometry

    public var usableWidth: Double { width - padLeft - padRight }
    public var playheadX: Double { padLeft + playheadFracX * usableWidth }
    public var pxPerSec: Double { (1 - playheadFracX) * usableWidth / lookaheadS }

    public var stringTop: Double { padTop }
    public var stringBottom: Double { height - padBottom }
    public var stringSpacing: Double { (stringBottom - stringTop) / 5 }

    /// Y for a string index; tab convention puts high E on top.
    public func stringY(forString string: Int) -> Double {
        let visualRow = 5 - string
        return stringTop + Double(visualRow) * stringSpacing
    }

    /// Top-to-bottom gutter labels: E B G D A E.
    public static let stringLabelsTopToBottom = ["E", "B", "G", "D", "A", "E"]

    // MARK: Pitch mapping

    /// Prefers the string whose fret is closest to 5 (mid-neck);
    /// nil below low E or above fret 17 everywhere.
    public static func pitchToStringFret(_ pitch: Int) -> (string: Int, fret: Int)? {
        var best: (string: Int, fret: Int)?
        var bestScore = Int.max
        for string in 0..<6 {
            let fret = pitch - standardTuning[string]
            guard fret >= 0, fret <= maxFret else { continue }
            let score = abs(fret - 5)
            if score < bestScore {
                bestScore = score
                best = (string, fret)
            }
        }
        return best
    }

    public static func noteName(_ pitch: Int) -> String {
        noteNames[((pitch % 12) + 12) % 12]
    }

    // MARK: Layout

    /// One placement per mappable note, absolute coordinates.
    public func placements() -> [TabLanePlacement] {
        let pps = pxPerSec
        return notes.compactMap { note in
            guard let sf = Self.pitchToStringFret(note.pitch) else { return nil }
            return TabLanePlacement(
                string: sf.string,
                fret: sf.fret,
                noteName: Self.noteName(note.pitch),
                x: note.startS * pps,
                y: stringY(forString: sf.string)
            )
        }
    }

    /// Per-frame horizontal shift: note at time == t lands on the
    /// playhead (JS: tx = playheadX - currentT * pps).
    public func translation(at currentT: Double) -> Double {
        playheadX - currentT * pxPerSec
    }
}
