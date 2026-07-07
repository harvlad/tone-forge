// Palette.swift
//
// Port of the launchpad.js palette tables (FAMILY_RGB, ROOT_RGB,
// CHORD_TONE_RGB, DEGREE_RGB, CHROMATIC_DIM_RGB) — the numbers must
// match the JS exactly so the on-screen mirror in the web app and the
// touch pads in the iOS app render the same color for a given chord
// or degree.
//
// Byte range: launchpad.js emits 0..127 (Novation SysEx). We keep the
// same range here so callers don't have to think about scaling — the
// PadColor UInt8 fields max out at 127 in practice, and the SwiftUI
// bridge multiplies by (1/127) to get Color RGB.

import Foundation

/// Static color tables. All values are Novation-scale 0..127.
public enum Palette {

    // MARK: - Song mode

    /// launchpad.js FAMILY_RGB (lines 69–76). Applied to the "current
    /// chord" pad. Next-chord pulse uses the same base color but the
    /// hardware chooses a fixed-palette pulse index; on screen we
    /// animate the same RGB.
    public static func songFamily(_ family: ChordFamily) -> PadColor {
        switch family {
        case .major:  return PadColor(127, 80, 0)
        case .minor:  return PadColor(0, 60, 127)
        case .dom7:   return PadColor(127, 20, 20)
        case .dim:    return PadColor(80, 0, 80)
        case .aug:    return PadColor(20, 120, 20)
        case .other:  return PadColor(60, 60, 60)
        }
    }

    // MARK: - Open-jam mode

    /// Bright gold when the pad's pitch class matches the song key
    /// root (launchpad.js ROOT_RGB, line 89).
    public static let openJamRoot = PadColor(127, 100, 0)

    /// Teal boost when the pad's pitch class is in the current chord
    /// (launchpad.js CHORD_TONE_RGB, line 90). Highest priority — beats
    /// the root highlight.
    public static let openJamChordTone = PadColor(0, 110, 110)

    /// Barely-lit dim RGB for out-of-key chromatic pads when
    /// out-of-key mode is `.dim` (launchpad.js CHROMATIC_DIM_RGB,
    /// line 91).
    public static let openJamChromaticDim = PadColor(4, 4, 4)

    /// Per-degree base RGB for in-key pads. Degree is 1-based (I=1,
    /// vii°=7). launchpad.js DEGREE_RGB (lines 79-88). These are the
    /// full-brightness values; ``openJamDegreeDimmed`` returns the 60%
    /// version actually painted on the grid.
    public static func openJamDegreeBase(degree: Int) -> PadColor {
        // JS uses `DEGREE_RGB[deg - 1] || DEGREE_RGB[0]` so out-of-range
        // degrees fall back to I.
        let table: [PadColor] = [
            PadColor(127, 90, 0),    // I   — gold
            PadColor(40, 40, 90),    // ii  — dim purple
            PadColor(40, 40, 90),    // iii — dim purple
            PadColor(0, 40, 90),     // IV  — dim blue
            PadColor(10, 80, 20),    // V   — dim green
            PadColor(40, 40, 90),    // vi  — dim purple
            PadColor(80, 10, 10),    // vii°— dim red
        ]
        let idx = (degree - 1)
        guard idx >= 0, idx < table.count else { return table[0] }
        return table[idx]
    }

    /// 60%-scaled degree color — this is what actually paints on the
    /// grid. Matches `_scaledRgb(rgb, 0.6)` in launchpad.js:1015.
    public static func openJamDegreeDimmed(degree: Int) -> PadColor {
        let base = openJamDegreeBase(degree: degree)
        return scaled(base, 0.6)
    }

    // MARK: - Utilities

    /// Uniform brightness scale (matches JS `_scaledRgb`). Clamps to
    /// UInt8's 0..255, but the JS palette is 0..127 so we never approach
    /// the top of the range in practice.
    public static func scaled(_ color: PadColor, _ factor: Double) -> PadColor {
        let clamp = { (v: Double) -> UInt8 in
            let clamped = max(0.0, min(255.0, v))
            return UInt8(clamped.rounded())
        }
        return PadColor(
            clamp(Double(color.r) * factor),
            clamp(Double(color.g) * factor),
            clamp(Double(color.b) * factor)
        )
    }
}
