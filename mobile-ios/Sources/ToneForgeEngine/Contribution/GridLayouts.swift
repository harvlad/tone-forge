// GridLayouts.swift
//
// Pure meaning + visual providers for the 8×8 contribution grid.
// A layout answers two questions for any PadIndex:
//   meaning(at:) — what pressing the pad does (consumed by ModeRouter)
//   visual(at:)  — what the pad looks like (consumed by ModeGridView's
//                  Canvas painter and the Launchpad LED syncer)
//
// Layouts are immutable snapshots: ModeCoordinator rebuilds one when
// the mode, pack, song, or pad assignments change, then hands the same
// value to both the on-screen grid and the hardware LED path so the
// two surfaces can never disagree.
//
// Content flows in through PadContentProvider (P1: pack pads only;
// P3 adds local mic samples; P5 adds vocoded samples) as a plain
// dictionary so the layout stays Sendable + trivially testable.

import Foundation

/// Small glyph drawn in a pad's corner to mark sample provenance.
public enum PadBadge: String, Sendable, Equatable, Codable {
    case mic          // recorded from the microphone (P3)
    case vocoded      // produced by the vocoder (P5)
    case transformed  // has a non-empty PadTransform chain (P4)
    case loop         // pad loops while held/latched
}

/// Everything the grid painter needs to draw one pad. Kept UIKit-free
/// so the engine target stays platform-pure; the view maps colorHint
/// (0xRRGGBB) to a Color and the Launchpad path maps it to the nearest
/// palette entry / RGB SysEx.
public struct PadVisual: Sendable, Equatable {
    /// 0xRRGGBB. 0x000000 = unlit.
    public var colorHint: UInt32
    /// Short text drawn on the pad (degree label, sample name…).
    public var label: String?
    /// Bright vs dimmed rendering (chord tones bright in hybrid).
    public var isBright: Bool
    public var badge: PadBadge?

    public init(colorHint: UInt32, label: String? = nil, isBright: Bool = false, badge: PadBadge? = nil) {
        self.colorHint = colorHint
        self.label = label
        self.isBright = isBright
        self.badge = badge
    }

    public static let off = PadVisual(colorHint: 0x000000)
}

/// What occupies a sample pad. Built by ModeCoordinator from the
/// active pack (P1) and later from PadAssignmentStore (P3/P5).
public struct PadContent: Sendable, Equatable {
    public var label: String?
    /// 0xRRGGBB. Provenance colors: mic = 0xFF8C3A (warm orange),
    /// vocoded = 0x9B4DFF (purple) — set by the provider.
    public var colorHint: UInt32
    public var badge: PadBadge?
    public var loops: Bool

    public init(label: String? = nil, colorHint: UInt32, badge: PadBadge? = nil, loops: Bool = false) {
        self.label = label
        self.colorHint = colorHint
        self.badge = badge
        self.loops = loops
    }
}

/// Seam through which pad content reaches layouts. P1 implementation
/// wraps the active sample pack; P3 overlays local samples from
/// PadAssignmentStore; P5 adds vocoded entries.
@MainActor
public protocol PadContentProvider: AnyObject {
    /// Snapshot of the current grid content keyed by
    /// `PadIndex.rawValue`. Missing keys = empty pads.
    func gridContent() -> [Int: PadContent]
}

/// Meaning + visuals for one mode's grid.
public protocol GridLayoutProviding: Sendable {
    func meaning(at pad: PadIndex) -> PadMeaning
    func visual(at pad: PadIndex) -> PadVisual
}

// MARK: - Sample mode

/// 8×8, every pad a sample slot. `meaning(at:)` is `.none` for all
/// pads — sample triggering doesn't go through PadMeaning; ModeRouter
/// maps padDown directly to `triggerSample(pad.rawValue)` and the
/// scheduler answers `.padNotFound` for empty pads.
public struct SampleModeLayout: GridLayoutProviding {
    /// Keyed by PadIndex.rawValue.
    public let content: [Int: PadContent]

    public init(content: [Int: PadContent]) {
        self.content = content
    }

    public func meaning(at pad: PadIndex) -> PadMeaning { .none }

    public func visual(at pad: PadIndex) -> PadVisual {
        guard pad.isValid, let c = content[pad.rawValue] else { return .off }
        var badge = c.badge
        if badge == nil && c.loops { badge = .loop }
        return PadVisual(colorHint: c.colorHint, label: c.label, isBright: true, badge: badge)
    }
}

// MARK: - Hybrid mode

/// Rows 5–8: sample slots (same semantics as SampleModeLayout).
/// Rows 1–4: synth notes on the OpenJamGrid chromatic surface
/// (+1 semitone per column, +5 per row from E2 at pad 11), colored by
/// the song key; current-chord tones render bright.
public struct HybridModeLayout: GridLayoutProviding {
    public let keyLabel: String?
    /// Pitch classes of the currently sounding chord (bright pads).
    public let chordPitchClasses: Set<Int>
    /// Sample content for rows 5–8, keyed by PadIndex.rawValue.
    public let sampleContent: [Int: PadContent]

    private let jamGrid: OpenJamGrid

    public init(
        keyLabel: String?,
        chordPitchClasses: Set<Int>,
        sampleContent: [Int: PadContent]
    ) {
        self.keyLabel = keyLabel
        self.chordPitchClasses = chordPitchClasses
        self.sampleContent = sampleContent
        self.jamGrid = OpenJamGrid(
            key: MusicalKey.parse(keyLabel),
            chordPitchClasses: chordPitchClasses,
            outOfKeyMode: .dim
        )
    }

    /// True for the note half of the grid.
    public static func isNoteRow(_ row: Int) -> Bool { (1...4).contains(row) }

    public func meaning(at pad: PadIndex) -> PadMeaning {
        guard pad.isValid else { return .none }
        if Self.isNoteRow(pad.row) {
            return jamGrid.meaning(for: pad)
        }
        return .none  // sample rows: router bypasses meaning
    }

    public func visual(at pad: PadIndex) -> PadVisual {
        guard pad.isValid else { return .off }
        if Self.isNoteRow(pad.row) {
            let color = jamGrid.color(for: pad)
            let pc = OpenJamGrid.pitchClass(for: pad)
            let isChordTone = pc.map { chordPitchClasses.contains($0) } ?? false
            var label: String? = nil
            if case .note(_, _, let degreeLabel) = jamGrid.meaning(for: pad) {
                label = degreeLabel
            }
            return PadVisual(
                colorHint: Self.hint(color),
                label: label,
                isBright: isChordTone
            )
        }
        guard let c = sampleContent[pad.rawValue] else { return .off }
        var badge = c.badge
        if badge == nil && c.loops { badge = .loop }
        return PadVisual(colorHint: c.colorHint, label: c.label, isBright: true, badge: badge)
    }

    /// PadColor (8-bit RGB) → 0xRRGGBB hint.
    static func hint(_ c: PadColor) -> UInt32 {
        (UInt32(c.r) << 16) | (UInt32(c.g) << 8) | UInt32(c.b)
    }
}

// MARK: - Empty layout

/// Layout for unimplemented modes: dark grid, no meanings.
public struct EmptyLayout: GridLayoutProviding {
    public init() {}
    public func meaning(at pad: PadIndex) -> PadMeaning { .none }
    public func visual(at pad: PadIndex) -> PadVisual { .off }
}
