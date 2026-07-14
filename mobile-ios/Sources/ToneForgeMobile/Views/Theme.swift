// Theme.swift
//
// Design-system tokens for the mockup-driven redesign. One small
// namespace so every restyled surface pulls colors/typography from
// the same place instead of scattering ad-hoc opacities.
//
// The palette is anchored to what already exists on screen: the grid
// canvas paints Color(white: 0.05) (ModeGridView), and pad tints key
// off SampleFamily via ModeCoordinator.familyColor. `familyTint`
// mirrors that hex table read-only — the coordinator's copy stays the
// source of truth for engine-facing colorHints (Launchpad LEDs), this
// one is for SwiftUI chrome.

import SwiftUI
import ToneForgeEngine

enum TFTheme {
    // MARK: - Colors

    /// App background — matches the grid canvas so the 8×8 sits
    /// seamlessly on the page.
    static let background = Color(white: 0.05)
    /// Card fill (Now Playing card, Next Up card, sheets).
    static let surface = Color(white: 0.11)
    /// Raised elements inside cards (chips on cards, pad tiles).
    static let surfaceElevated = Color(white: 0.17)
    /// Inactive chip fill.
    static let chipFill = Color(white: 0.15)
    /// Active/selected chip fill.
    static let chipActiveFill = Color.accentColor.opacity(0.28)
    /// Hairline strokes around cards and chips.
    static let stroke = Color.white.opacity(0.08)

    /// Fader/slider accent — the mockup's purple channel sliders.
    static let faderTint = color(hex: 0x8B5CF6)
    /// Active segmented-control fill (Mixer/FX segment highlight).
    static let segmentActiveFill = color(hex: 0x1E3A8A)

    static let textPrimary = Color.white.opacity(0.92)
    static let textSecondary = Color.white.opacity(0.55)

    // MARK: - Family tints

    /// SwiftUI mirror of ModeCoordinator.familyColor (0xRRGGBB).
    static func familyTint(_ family: SampleFamily) -> Color {
        switch family {
        case .pads:       return color(hex: 0xA855F7)
        case .percussion: return color(hex: 0xF97316)
        case .textures:   return color(hex: 0x14B8A6)
        case .stabs:      return color(hex: 0xEC4899)
        case .bass:       return color(hex: 0x3B82F6)
        case .fx:         return color(hex: 0xEAB308)
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

    // MARK: - Typography

    /// Chip labels ("Quantize 1/4", "128 BPM").
    static let chipFont = Font.caption.weight(.semibold)
    /// Pad tile names on the 4×4 grid.
    static let padLabel = Font.system(size: 11, weight: .medium)
    /// dB readouts and bar counters — monospaced so values don't
    /// jitter as digits change.
    static let readout = Font.system(.caption, design: .monospaced).weight(.medium)
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
