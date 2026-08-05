// Theme.swift
//
// The JamN design system — one place for colors, typography, spacing,
// radius and the shared modifiers. Tokens are derived from the approved
// JamN Visual System Guide; screens consume these rather than hard-coding
// values. Existing member names are preserved (background/surface/
// textPrimary/familyTint/tfChip/…) so the refactor can adopt tokens
// incrementally without breaking current views.
//
// The palette is intentionally near-black with a single violet accent;
// pad families carry meaning via `familyTint` (which mirrors
// ModeCoordinator.familyColor, the engine-facing source of truth).

import SwiftUI
import ToneForgeEngine

enum TFTheme {
    // MARK: - Color palette (JamN Visual System Guide)

    /// Level 0 — application background (#0B0B0F).
    static let background = color(hex: 0x0B0B0F)
    /// Level 1 — grouped control / transport / harmonic context (#14141A).
    static let surface1 = color(hex: 0x14141A)
    /// Level 2 — interactive control / pad surface (#1C1C24).
    static let surface2 = color(hex: 0x1C1C24)
    /// Hairline borders + dividers (#2A2A36). Used sparingly — tonal
    /// separation is preferred over a border around every object.
    static let border = color(hex: 0x2A2A36)

    /// Primary JamN accent — violet (#8B5CF6). Selective use: selected
    /// nav, active sections, primary actions, current playback state.
    static let accent = color(hex: 0x8B5CF6)
    static let success = color(hex: 0x22C55E)
    static let danger = color(hex: 0xEF4444)

    /// Primary text — white. Secondary — muted neutral.
    static let textPrimary = Color.white
    static let textSecondary = color(hex: 0xA1A1AA)

    // MARK: - Back-compat aliases (existing views)

    /// Card / grouped fill (== surface1).
    static let surface = surface1
    /// Raised elements inside cards (== surface2).
    static let surfaceElevated = surface2
    /// Inactive chip fill.
    static let chipFill = surface2
    /// Active/selected chip fill.
    static let chipActiveFill = accent.opacity(0.28)
    /// Hairline strokes (== border).
    static let stroke = border
    /// Fader/slider accent (== accent).
    static let faderTint = accent
    /// Active segmented-control fill.
    static let segmentActiveFill = accent.opacity(0.30)

    /// Jam brand green (waveform logo, Open Library CTA).
    static let brandGreenLight = color(hex: 0xC6F24E)
    static let brandGreenDark = color(hex: 0x36C81A)
    static let brandGradient = LinearGradient(
        colors: [brandGreenLight, brandGreenDark],
        startPoint: .top,
        endPoint: .bottom
    )

    // MARK: - Family tints

    /// SwiftUI mirror of ModeCoordinator.familyColor (0xRRGGBB), aligned
    /// to the JamN guide's pad-family palette.
    static func familyTint(_ family: SampleFamily) -> Color {
        switch family {
        case .pads:       return color(hex: 0x6E5AF7)  // atmospheric / sustained
        case .percussion: return color(hex: 0xF97316)  // drums
        case .textures:   return color(hex: 0x14B8A6)  // loops / ambient
        case .stabs:      return color(hex: 0xEC4899)  // melodic / stab
        case .bass:       return color(hex: 0x3B82F6)  // low-frequency
        case .fx:         return color(hex: 0xEAB308)  // transitions / impacts
        case .vocals:     return color(hex: 0x22C55E)
        case .mixed:      return color(hex: 0x9CA3AF)
        }
    }

    static func color(hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0
        )
    }

    // MARK: - Typography roles (native SF, Dynamic-Type aware)

    /// "Midnight Drive" — dominates the metadata line.
    static let songTitle = Font.system(.title3, design: .default).weight(.semibold)
    /// Screen / nav titles.
    static let screenTitle = Font.system(.headline)
    /// The hero musical value — the big `C` / chord. Bold, prominent.
    static let primaryValue = Font.system(size: 40, weight: .bold, design: .default)
    /// Pad tile names.
    static let padTitle = Font.system(.subheadline, design: .default).weight(.medium)
    /// Control labels ("Quantize", "Loop").
    static let controlLabel = Font.system(.caption, design: .default).weight(.medium)
    /// Song metadata line ("C Major • 120 BPM").
    static let metadata = Font.system(.caption)
    /// Small section labels ("Intro", "Verse").
    static let sectionLabel = Font.system(.caption, design: .default).weight(.semibold)

    // Legacy font aliases.
    static let chipFont = controlLabel
    static let padLabel = Font.system(size: 11, weight: .medium)
    static let readout = Font.system(.caption, design: .monospaced).weight(.medium)

    // MARK: - Spacing (consistent breathing room)

    enum Spacing {
        static let xs: CGFloat = 4
        static let sm: CGFloat = 8
        static let md: CGFloat = 12   // grid gap
        static let lg: CGFloat = 16   // screen margin
        static let xl: CGFloat = 24
    }

    // MARK: - Corner radius

    enum Radius {
        static let small: CGFloat = 8     // small controls
        static let medium: CGFloat = 12   // buttons / selectors
        static let large: CGFloat = 16    // pads / grouped surfaces
        static let capsule: CGFloat = 999 // only where capsule geometry earns it
    }

    // MARK: - Layout constants

    /// Minimum comfortable touch target.
    static let minTouchTarget: CGFloat = 44
    /// Section chip height (guide).
    static let sectionHeight: CGFloat = 52
}

// MARK: - Modifiers

extension View {
    /// Capsule chip treatment used across the redesigned surfaces.
    func tfChip(active: Bool = false) -> some View {
        self
            .font(TFTheme.chipFont)
            .foregroundStyle(active ? TFTheme.textPrimary : TFTheme.textSecondary)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .background(
                active ? TFTheme.chipActiveFill : TFTheme.chipFill,
                in: Capsule()
            )
            .overlay(Capsule().stroke(TFTheme.stroke, lineWidth: 1))
    }

    /// Rounded card treatment (Now Playing card, Next Up card).
    func tfCard() -> some View {
        self
            .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 14))
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(TFTheme.stroke, lineWidth: 1)
            )
    }

    /// Library list row card — the mockup's spaced, rounded rows.
    /// `active` paints the purple selected treatment (currently loaded
    /// song / active pack / playing layer).
    func tfLibraryCard(active: Bool = false) -> some View {
        self
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                active ? TFTheme.faderTint.opacity(0.22) : TFTheme.surface,
                in: RoundedRectangle(cornerRadius: 14)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14)
                    .stroke(
                        active ? TFTheme.faderTint.opacity(0.85) : TFTheme.stroke,
                        lineWidth: active ? 1.5 : 1
                    )
            )
    }

    /// List-row chrome for library cards: no separators, transparent
    /// row fill (the card supplies its own), tight vertical gaps.
    func tfLibraryRowChrome() -> some View {
        self
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
            .listRowInsets(EdgeInsets(top: 5, leading: 16, bottom: 5, trailing: 16))
    }
}
