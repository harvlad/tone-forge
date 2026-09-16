// ChordTransitionHint.swift  (ToneForgeEngine)
//
// One-line chord-transition coaching hints — the Swift port of the
// web's _computeTransitionHint (backend/static/jam.js). Pure integer
// math over two 6-slot fret arrays; the output strings must stay
// IDENTICAL to the web's ("Same shape +2 frets", "Move one finger
// only", "Two-finger move", "Barre at fret 1") — parity doctrine
// treats divergent hint text as a bug even when both sides "work".
//
// The full transition-hint problem is rich (which fingers move, hand
// position, voice leading) — this is a best-effort heuristic that
// covers the most common useful cases without overpromising. Lives
// next to ChordFingering so the iOS Learn hero and desktop Rehearsal
// share one implementation.

import Foundation

public enum ChordTransitionHint {

    /// Hint for a NOW → NEXT symbol pair. Nil when either symbol is
    /// missing/unvoicable, when they are the same chord, or when no
    /// rule fires (web returns '' in those cases).
    public static func hint(from nowSymbol: String?, to nextSymbol: String?) -> String? {
        guard let nowSymbol, let nextSymbol, nowSymbol != nextSymbol,
              let nowShape = GuitarVoicing.shape(symbol: nowSymbol),
              let nextShape = GuitarVoicing.shape(symbol: nextSymbol)
        else { return nil }
        return hint(from: nowShape, to: nextShape)
    }

    public static func hint(
        from nowShape: GuitarChordShape, to nextShape: GuitarChordShape
    ) -> String? {
        let a = frets(of: nowShape)
        let b = frets(of: nextShape)
        guard a.count == 6, b.count == 6 else { return nil }

        // Same shape shifted: every non-muted string differs by the
        // same offset. Common for barre-chord movements.
        var deltas: [Int] = []
        var sameStructure = true
        for i in 0..<6 {
            let aMute = a[i] < 0
            let bMute = b[i] < 0
            if aMute != bMute { sameStructure = false; break }
            if aMute { continue }
            deltas.append(b[i] - a[i])
        }
        if sameStructure, let first = deltas.first,
           first != 0, deltas.allSatisfy({ $0 == first }) {
            let sign = first > 0 ? "+" : ""
            let word = abs(first) == 1 ? "fret" : "frets"
            return "Same shape \(sign)\(first) \(word)"
        }

        // Single-string-change hint: only one fretted string differs.
        let changedStrings = zip(a, b).filter { $0.0 != $0.1 }.count
        if changedStrings == 1 { return "Move one finger only" }
        if changedStrings == 2 { return "Two-finger move" }

        // Barre hint when the next shape uses a barre. The web reads
        // the registry's curated barre metadata; the Swift analogue is
        // the shared fingering assignment's barre detection.
        if let barreFret = ChordFingering.assign(shape: nextShape).barreFret {
            return "Barre at fret \(barreFret)"
        }
        return nil
    }

    /// GuitarChordShape → the web wire fret array: -1 muted, 0 open,
    /// n = absolute fret (low E → high e).
    static func frets(of shape: GuitarChordShape) -> [Int] {
        shape.strings.map { state in
            switch state {
            case .muted: return -1
            case .open: return 0
            case .fretted(let f): return f
            }
        }
    }
}
