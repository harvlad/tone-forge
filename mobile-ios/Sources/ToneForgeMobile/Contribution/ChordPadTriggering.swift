// ChordPadTriggering.swift
//
// Chord Pads surface model (redesign Phase 12): the pure 4×4 grid
// builder plus the trigger seam over PadSynth.
//
// Grid content per the mockup: rows 1–3 are the key's diatonic
// triads (degrees 1–7, then degrees 1–5 again an octave up) and
// row 4 is seventh chords on degrees 1, 4, 6, 7 (Dm7 / Gm7 /
// Bbmaj7 / C7 in D minor). Everything derives from DiatonicChords,
// so scale variants (harmonic minor's major V etc.) flow through.
//
// Chord pads bypass the ContributionEventBus (D-019 — same precedent
// as the Jam degree pads): they voice directly on the PadSynth.
//
// Latch semantics v1: a latched pad stays lit and rings the synth's
// full releaseSec tail; per-bar retriggering while latched is out of
// scope (documented in D-019). Momentary pads trigger on press with
// the synth's default short strum and auto-release.

import Foundation
import ToneForgeEngine

/// One cell of the 4×4 chord grid.
public struct ChordPadCell: Identifiable, Equatable, Sendable {
    /// 0…15 row-major from the TOP-left (display order).
    public let index: Int
    /// Playable chord symbol ("Dm", "Bb", "Dm7") — always
    /// re-parseable by ChordParser so ChordVoicing can voice it.
    public let symbol: String
    /// Secondary label: quality-derived roman numeral ("i", "VI7").
    public let detail: String
    /// Built-in octave shift (+1 for the wrapped upper-row triads).
    public let octaveShift: Int

    public var id: Int { index }
}

public enum ChordPadGrid {

    /// The 16 cells for a key, display order (row-major from the
    /// top-left).
    public static func cells(key: MusicalKey) -> [ChordPadCell] {
        let triads = DiatonicChords.triads(key: key)
        guard triads.count == 7 else { return [] }

        var cells: [ChordPadCell] = []
        // Rows 1–3: degrees 1–7, then degrees 1–5 an octave up.
        for i in 0..<12 {
            let triad = triads[i % 7]
            cells.append(ChordPadCell(
                index: i,
                symbol: triad.symbol,
                detail: triad.romanNumeral,
                octaveShift: i >= 7 ? 1 : 0
            ))
        }
        // Row 4: sevenths on degrees 1, 4, 6, 7.
        let intervals = ScaleIntervals.intervals(for: key.scale)
        for (j, degree) in [1, 4, 6, 7].enumerated() {
            let (symbol, detail) = seventh(
                triad: triads[degree - 1], intervals: intervals)
            cells.append(ChordPadCell(
                index: 12 + j,
                symbol: symbol,
                detail: detail,
                octaveShift: 0
            ))
        }
        return cells
    }

    /// Seventh-chord symbol for a diatonic triad, derived from the
    /// scale's own 7th above the root (so natural minor's VII gets a
    /// dominant C7 while VI gets Bbmaj7). Qualities ChordParser can't
    /// spell (diminished/minMaj sevenths) fall back to the triad.
    static func seventh(
        triad: DiatonicChord, intervals: [Int]
    ) -> (symbol: String, detail: String) {
        let i = triad.degree - 1
        let n = intervals.count
        let rootOffset = intervals[i]
        let seventhInterval = intervals[(i + 6) % n]
            + ((i + 6) >= n ? 12 : 0) - rootOffset
        switch (triad.quality, seventhInterval) {
        case (.maj, 11):
            return (triad.symbol + "maj7", triad.romanNumeral + "7")
        case (.maj, 10), (.min, 10):
            return (triad.symbol + "7", triad.romanNumeral + "7")
        default:
            return (triad.symbol, triad.romanNumeral)
        }
    }
}

/// UI state + pad actions for the Chord Pads surface. Derives the
/// key the same way Jam does (per-song override ?? detected key,
/// scale variant applied) and falls back to C major song-less so the
/// grid is always playable.
@MainActor
public final class ChordPadController: ObservableObject {

    public enum TriggerMode: String, CaseIterable {
        case momentary
        case latch
    }

    @Published public var triggerMode: TriggerMode = .momentary
    /// Whole-octave shift applied to every pad, −3…+3 (matches Jam).
    @Published public private(set) var octaveShift: Int = 0
    /// Cells visually held down (momentary mode).
    @Published public private(set) var heldCells: Set<Int> = []
    /// Cells toggled on in latch mode.
    @Published public private(set) var latchedCells: Set<Int> = []

    private unowned let app: AppState

    public init(app: AppState) {
        self.app = app
    }

    // MARK: - Derived state

    /// Effective key: jam override ?? detected key, C major fallback.
    public var key: MusicalKey {
        app.jamSettings.effectiveKey(
            detectedKey: app.currentBundle?.meta.detectedKey,
            analysisId: app.currentBundle?.analysisId
        ) ?? MusicalKey(root: PitchClass(0), scale: .major)
    }

    /// Header label ("D Minor", "C Major").
    public var keyLabel: String {
        let k = key
        let root = NoteNames.name(pitchClass: k.root.rawValue, key: k)
        let scale: String
        switch k.scale {
        case .major:         scale = "Major"
        case .minor:         scale = "Minor"
        case .harmonicMinor: scale = "Harmonic Minor"
        case .melodicMinor:  scale = "Melodic Minor"
        case .dorian:        scale = "Dorian"
        case .phrygian:      scale = "Phrygian"
        case .lydian:        scale = "Lydian"
        case .mixolydian:    scale = "Mixolydian"
        case .locrian:       scale = "Locrian"
        }
        return "\(root) \(scale)"
    }

    public var cells: [ChordPadCell] {
        ChordPadGrid.cells(key: key)
    }

    // MARK: - Pad actions

    public func padDown(index: Int) {
        guard let cell = cells.first(where: { $0.index == index }) else {
            return
        }
        switch triggerMode {
        case .momentary:
            heldCells.insert(index)
            trigger(cell)
        case .latch:
            if latchedCells.contains(index) {
                // Unlatch — no retrigger; the ringing tail decays.
                latchedCells.remove(index)
            } else {
                latchedCells.insert(index)
                trigger(cell)
            }
        }
    }

    public func padUp(index: Int) {
        heldCells.remove(index)
    }

    public func setOctaveShift(_ shift: Int) {
        octaveShift = max(-3, min(3, shift))
    }

    /// Clear press/latch visuals (e.g. when leaving the surface).
    public func clearLatches() {
        heldCells.removeAll()
        latchedCells.removeAll()
    }

    private func trigger(_ cell: ChordPadCell) {
        let midis = ChordVoicing.midiNotes(
            symbol: cell.symbol,
            octaveShift: octaveShift + cell.octaveShift
        )
        guard !midis.isEmpty else { return }
        app.padSynth.triggerChord(midis: midis)
    }
}
