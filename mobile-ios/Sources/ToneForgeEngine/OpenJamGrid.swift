// OpenJamGrid.swift
//
// Port of the open-jam pad layout from launchpad.js (lines 994-1025).
// The grid is a chromatic playing surface: bottom-left = MIDI 40
// (E2), +1 semitone per column (rightward), +5 semitones per row
// (upward, i.e. a fourth per row — the standard bass/guitar tuning
// interval, which makes scale fingerings feel natural).
//
// Colors are picked by a priority walker:
//   1. In-chord pitch class → teal (Palette.openJamChordTone)
//   2. Key root pitch class → gold (Palette.openJamRoot)
//   3. In-key scale degree → dimmed degree color (Palette.openJamDegreeDimmed)
//   4. Out-of-key           → chromatic dim OR off, per OutOfKeyMode

import Foundation

/// The base MIDI note at (row=1, col=1) — i.e. pad 11. E2 = MIDI 40.
public let OPEN_JAM_BASE_MIDI: Int = 40

public enum ScaleType: Sendable, Equatable {
    case major       // Ionian
    case minor       // Aeolian
    case dorian
    case phrygian
    case lydian
    case mixolydian
    case locrian
    case harmonicMinor  // Aeolian with a raised 7th (major V)
    case melodicMinor   // ascending form: raised 6th + 7th
}

/// Semitone intervals for each mode from the root. Anyma's backend
/// emits mode names like "C dorian" for modal songs, so we accept
/// every diatonic mode rather than only major/minor.
public enum ScaleIntervals {
    public static let major: [Int]       = [0, 2, 4, 5, 7, 9, 11] // Ionian
    public static let minor: [Int]       = [0, 2, 3, 5, 7, 8, 10] // Aeolian
    public static let dorian: [Int]      = [0, 2, 3, 5, 7, 9, 10]
    public static let phrygian: [Int]    = [0, 1, 3, 5, 7, 8, 10]
    public static let lydian: [Int]      = [0, 2, 4, 6, 7, 9, 11]
    public static let mixolydian: [Int]  = [0, 2, 4, 5, 7, 9, 10]
    public static let locrian: [Int]     = [0, 1, 3, 5, 6, 8, 10]
    public static let harmonicMinor: [Int] = [0, 2, 3, 5, 7, 8, 11]
    public static let melodicMinor: [Int]  = [0, 2, 3, 5, 7, 9, 11]

    public static func intervals(for type: ScaleType) -> [Int] {
        switch type {
        case .major:         return major
        case .minor:         return minor
        case .dorian:        return dorian
        case .phrygian:      return phrygian
        case .lydian:        return lydian
        case .mixolydian:    return mixolydian
        case .locrian:       return locrian
        case .harmonicMinor: return harmonicMinor
        case .melodicMinor:  return melodicMinor
        }
    }
}

/// The song key. Only the root + scale type are needed for the open-jam
/// palette; the key name string is preserved so callers can display it.
public struct MusicalKey: Sendable, Equatable {
    public let root: PitchClass
    public let scale: ScaleType

    public init(root: PitchClass, scale: ScaleType) {
        self.root = root
        self.scale = scale
    }

    /// Parse a detectedKey string of the shape "C major" / "F# minor"
    /// / "C dorian" (the format the analysis backend emits). Returns
    /// ``nil`` for anything we don't recognise.
    public static func parse(_ raw: String?) -> MusicalKey? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty
        else { return nil }
        // Split on the last space: root name may itself contain a
        // sharp/flat but not whitespace.
        let parts = raw.split(separator: " ", omittingEmptySubsequences: true)
        // Bare root with no scale word ("C", "F#", "Bb") — assume major,
        // the conventional default. Restricted to a genuine note token
        // (≤2 chars) so ChordParser's lenient leading-letter parse can't
        // turn a stray word like "bogus" into B major.
        if parts.count == 1 {
            let token = String(parts[0])
            guard token.count <= 2, let parsed = ChordParser.parse(token) else { return nil }
            return MusicalKey(root: parsed.root, scale: .major)
        }
        guard parts.count >= 2 else { return nil }

        // Two-word scale names first ("D harmonic minor"), then the
        // single-word modes.
        var scaleWordCount = 1
        let scale: ScaleType
        let lastTwo = parts.count >= 3
            ? parts.suffix(2).joined(separator: " ").lowercased()
            : ""
        switch lastTwo {
        case "harmonic minor":
            scale = .harmonicMinor
            scaleWordCount = 2
        case "melodic minor":
            scale = .melodicMinor
            scaleWordCount = 2
        default:
            switch parts.last!.lowercased() {
            case "major", "maj", "ionian":  scale = .major
            case "minor", "min", "aeolian": scale = .minor
            case "dorian":                  scale = .dorian
            case "phrygian":                scale = .phrygian
            case "lydian":                  scale = .lydian
            case "mixolydian":              scale = .mixolydian
            case "locrian":                 scale = .locrian
            default: return nil
            }
        }
        let rootName = parts.dropLast(scaleWordCount).joined(separator: " ")
        guard let parsed = ChordParser.parse(rootName) else { return nil }
        return MusicalKey(root: parsed.root, scale: scale)
    }
}

/// Out-of-key rendering policy. Matches launchpad.js `_outOfKeyMode`.
public enum OutOfKeyMode: Sendable, Equatable {
    /// Chromatic pads are barely lit (Palette.openJamChromaticDim).
    case dim
    /// Chromatic pads are unlit — off.
    case off
}

/// The open-jam grid layout. A struct rather than a static function so
/// callers can hold a snapshot per song + hand pad indices back and
/// forth without repassing the key.
public struct OpenJamGrid: Sendable {
    public let key: MusicalKey?
    public let chordPitchClasses: Set<Int>
    public let outOfKeyMode: OutOfKeyMode

    public init(
        key: MusicalKey?,
        chordPitchClasses: Set<Int> = [],
        outOfKeyMode: OutOfKeyMode = .dim
    ) {
        self.key = key
        self.chordPitchClasses = chordPitchClasses
        self.outOfKeyMode = outOfKeyMode
    }

    /// MIDI note number for the pad at (row, col). row and col are
    /// 1-based (matching PadIndex convention: row 1 = bottom, col 1 =
    /// left). Direct port of launchpad.js `_midiForPad`.
    public static func midi(row: Int, col: Int) -> Int {
        // JS uses 0-indexed rows/cols internally; we convert.
        return OPEN_JAM_BASE_MIDI + (row - 1) * 5 + (col - 1)
    }

    /// MIDI note for a given pad index. Returns ``nil`` if the pad is
    /// out of the 1..8 × 1..8 range.
    public static func midi(for pad: PadIndex) -> Int? {
        guard pad.isValid else { return nil }
        return midi(row: pad.row, col: pad.col)
    }

    /// Pitch class 0..11 for a given pad.
    public static func pitchClass(for pad: PadIndex) -> Int? {
        guard let m = midi(for: pad) else { return nil }
        return ((m % 12) + 12) % 12
    }

    /// The color the pad should render as, given the current key +
    /// chord + out-of-key mode. Returns ``PadColor.off`` when the pad
    /// is out-of-key and out-of-key mode is ``.off``.
    public func color(for pad: PadIndex) -> PadColor {
        guard let pc = OpenJamGrid.pitchClass(for: pad) else { return .off }

        // Priority 1: in-chord (teal) — beats even the root.
        if chordPitchClasses.contains(pc) {
            return Palette.openJamChordTone
        }
        // Priority 2: matches the song key root (gold).
        if let key = key, key.root.rawValue == pc {
            return Palette.openJamRoot
        }
        // Priority 3: in-key scale degree (dimmed).
        if let key = key, let deg = scaleDegree(pc: pc, key: key) {
            return Palette.openJamDegreeDimmed(degree: deg)
        }
        // Priority 4: out-of-key or no key at all.
        return outOfKeyMode == .off ? .off : Palette.openJamChromaticDim
    }

    /// Meaning of the pad when pressed. Wraps the MIDI value in a
    /// ``PadMeaning.note`` case so callers can dispatch to a synth.
    public func meaning(for pad: PadIndex) -> PadMeaning {
        guard let m = OpenJamGrid.midi(for: pad),
              let pc = OpenJamGrid.pitchClass(for: pad)
        else { return .none }
        let degLabel: String?
        if let key = key, let deg = scaleDegree(pc: pc, key: key) {
            degLabel = degreeLabel(deg: deg, scale: key.scale)
        } else if let key = key, key.root.rawValue == pc {
            degLabel = "R"
        } else {
            degLabel = nil
        }
        return .note(midi: m, pitchClass: pc, degreeLabel: degLabel)
    }

    // MARK: - Private helpers

    /// 1-based scale degree, or nil if the pitch class isn't in the key.
    /// Direct port of `_scaleDegreeInKey` in launchpad.js:987-992.
    private func scaleDegree(pc: Int, key: MusicalKey) -> Int? {
        let diff = ((pc - key.root.rawValue) % 12 + 12) % 12
        let intervals = ScaleIntervals.intervals(for: key.scale)
        if let idx = intervals.firstIndex(of: diff) {
            return idx + 1
        }
        return nil
    }

    /// Roman-numeral degree label (I, ii, III, …). Major scales use
    /// upper/lower/upper for I/ii/iii/IV/V/vi/vii°; minor scales use
    /// lower/half-dim for the natural-minor pattern.
    private func degreeLabel(deg: Int, scale: ScaleType) -> String {
        let major = ["I", "ii", "iii", "IV", "V", "vi", "vii°"]
        let minor = ["i", "ii°", "III", "iv", "v", "VI", "VII"]
        let table = scale == .major ? major : minor
        guard deg >= 1, deg <= table.count else { return "?" }
        return table[deg - 1]
    }
}
