// PadTypes.swift
//
// Value types for the 8×8 pad grid. Matches the Programmer-Mode
// addressing used by the JS Launchpad engine so log/debug output can
// be cross-referenced 1:1 between web and mobile.
//
// PadIndex convention:
//   index = row * 10 + col, with row ∈ 1..8 (bottom→top) and col ∈ 1..8.
//   So the bottom-left pad is 11 and the top-right pad is 88.
//
// This matches the LP Pro MK3 SysEx addressing and the DOM mirror the
// web version paints. The mobile app uses the same numbering so the
// engine port is 1:1.

import Foundation

/// A pad address on the 8×8 grid. Only values with row and col in 1..8
/// are valid. Wraps a raw Int for cheap dictionary/set membership.
public struct PadIndex: Hashable, Sendable, Codable, ExpressibleByIntegerLiteral {
    public let rawValue: Int

    public init(integerLiteral value: Int) {
        self.rawValue = value
    }

    public init(_ raw: Int) {
        self.rawValue = raw
    }

    /// Row 1..8 (1 = bottom).
    public var row: Int { rawValue / 10 }
    /// Col 1..8 (1 = left).
    public var col: Int { rawValue % 10 }

    /// True iff both row and col are in 1..8.
    public var isValid: Bool {
        (1...8).contains(row) && (1...8).contains(col)
    }

    public static func at(row: Int, col: Int) -> PadIndex {
        PadIndex(row * 10 + col)
    }
}

/// 8-bit-per-channel RGB. Chosen over Float RGB because it matches the
/// SysEx wire format (0..127) after a `>> 1` cheap conversion — and
/// SwiftUI can trivially derive a Color from it via `.init(red:g:b:)`.
public struct PadColor: Hashable, Sendable, Equatable {
    public let r: UInt8
    public let g: UInt8
    public let b: UInt8

    public init(_ r: UInt8, _ g: UInt8, _ b: UInt8) {
        self.r = r
        self.g = g
        self.b = b
    }

    public static let off = PadColor(0, 0, 0)
}

/// What a pad currently means when pressed. Mirrors the shape returned
/// by `_meaningForPad(padIdx)` in launchpad.js. Cases exist for every
/// mode this engine supports; the enum is `Sendable` so pad-press
/// dispatch can hop threads safely.
public enum PadMeaning: Sendable, Equatable {
    /// Song-mode / theory-mode chord tile.
    case chord(symbol: String, family: ChordFamily, degreeLabel: String?)
    /// Instrument-mode single note (open-jam / synth / bass / melody).
    case note(midi: Int, pitchClass: Int, degreeLabel: String?)
    /// Drum-mode kit pad.
    case drum(slot: Int, name: String)
    /// Contribute-mode sample chop.
    case chop(chopIdx: Int, chop: Chop)
    /// No mapping (unused pad in the current mode).
    case none
}

/// Quality bucket for a parsed chord. Used both as a palette key and
/// as the pad-meaning family tag.
public enum ChordFamily: String, Sendable, Codable, CaseIterable {
    case major
    case minor
    case dom7
    case dim
    case aug
    case other
}
