// Theme.swift
//
// Shared design tokens for the desktop app, aligned with the web jam
// palette (zinc dark: #0f0f11 body, #18181b cards, purple gradient
// accents) and the mobile TFTheme token names so the three surfaces
// read as one product.

import SwiftUI

enum JamTheme {

    // MARK: Colors

    /// Window/body background (web: #0f0f11).
    static let background = Color(white: 0.06)
    /// Card fill (web: #18181b).
    static let surface = Color(white: 0.11)
    /// Elevated card / hover fill.
    static let surfaceElevated = Color(white: 0.17)
    /// Hairline card border.
    static let stroke = Color.white.opacity(0.08)

    /// Primary accent (mobile faderTint 0x8B5CF6).
    static let accent = Color(hex: 0x8B5CF6)
    /// Primary-action gradient (web button: #a855f7 → #6366f1).
    static let accentGradient = LinearGradient(
        colors: [Color(hex: 0xA855F7), Color(hex: 0x6366F1)],
        startPoint: .topLeading,
        endPoint: .bottomTrailing
    )

    /// Brand green (light, 0xC6F24E).
    static let brandGreenLight = Color(hex: 0xC6F24E)
    /// Brand green (dark, 0x36C81A).
    static let brandGreenDark = Color(hex: 0x36C81A)
    /// Brand gradient for logo and CTAs.
    static let brandGradient = LinearGradient(
        colors: [brandGreenLight, brandGreenDark],
        startPoint: .top,
        endPoint: .bottom
    )

    static let textPrimary = Color.white.opacity(0.92)
    static let textSecondary = Color.white.opacity(0.55)
    /// Error text (web: #f87171).
    static let error = Color(hex: 0xF87171)

    // MARK: Debug window (debug.css palette)

    /// Guidance-mode colors (chord/riff/lead), matching debug.css.
    static func guidanceColor(_ mode: String?) -> Color {
        switch mode {
        case "chord": Color(hex: 0x60A5FA)
        case "riff": Color(hex: 0xF97316)
        case "lead": Color(hex: 0xF43F5E)
        default: Color(white: 0.4)
        }
    }

    /// Section-difficulty tag colors (barre/colour/jumps/quick).
    static func tagColor(_ id: String) -> Color {
        switch id {
        case "barre": Color(hex: 0xEF4444)
        case "colour": Color(hex: 0xF59E0B)
        case "jumps": Color(hex: 0xA78BFA)
        case "quick": Color(hex: 0x2DD4BF)
        default: Color(white: 0.4)
        }
    }

    // MARK: Metrics

    static let cardCornerRadius: CGFloat = 12
    static let chipCornerRadius: CGFloat = 8
}

extension Color {
    /// 0xRRGGBB literal, matching the mobile TFTheme convention.
    init(hex: UInt32) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    /// Parse "#RRGGBB" or "RRGGBB" string, returns nil if invalid.
    init?(hex: String?) {
        guard var str = hex?.trimmingCharacters(in: .whitespaces), !str.isEmpty else {
            return nil
        }
        if str.hasPrefix("#") { str.removeFirst() }
        guard str.count == 6, let value = UInt32(str, radix: 16) else {
            return nil
        }
        self.init(hex: value)
    }
}

extension View {
    /// Standard card chrome: surface fill + hairline stroke.
    func jamCard(cornerRadius: CGFloat = JamTheme.cardCornerRadius) -> some View {
        background(
            RoundedRectangle(cornerRadius: cornerRadius)
                .fill(JamTheme.surface)
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .strokeBorder(JamTheme.stroke)
                )
        )
    }

    /// Slightly raised variant for rows/tiles inside a card.
    func jamTile(cornerRadius: CGFloat = JamTheme.chipCornerRadius) -> some View {
        background(
            RoundedRectangle(cornerRadius: cornerRadius)
                .fill(JamTheme.surfaceElevated)
                .overlay(
                    RoundedRectangle(cornerRadius: cornerRadius)
                        .strokeBorder(JamTheme.stroke)
                )
        )
    }
}
