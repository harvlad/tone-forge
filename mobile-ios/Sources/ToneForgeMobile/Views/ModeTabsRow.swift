// ModeTabsRow.swift
//
// Top-level surface switcher for the Play tab (D-018 — a partial,
// deliberate reversal of D-016's "no mode tabs"): the redesign brings
// back a segmented row [Learn | Jam | Contribute | grid-icon] that
// selects which *surface* the tab shows. PlaySurface is a UI concept;
// engine behavior still flows through ModeCoordinator.setMode when a
// surface maps to an AppMode (Learn → .learnSong, Jam → .jamInKey,
// Contribute → last of .sample/.hybrid).
//
// Only Contribute is live today — the other segments render dimmed +
// disabled so the roadmap is discoverable, and each phase flips its
// segment on as it lands (Jam in Phase 7, Learn in Phase 8, Chord
// Pads in Phase 12).

import SwiftUI

/// The four Play-tab surfaces from the design mockups. Raw values are
/// persisted (SampleSettingsStore.playSurfaceRaw) so the app reopens
/// on the surface the user last used.
enum PlaySurface: String, CaseIterable {
    case learn
    case jam
    case contribute
    case chordPads

    /// Flipped on per phase as each surface ships.
    var isImplemented: Bool {
        self == .contribute
    }

    var title: String {
        switch self {
        case .learn: return "Learn"
        case .jam: return "Jam"
        case .contribute: return "Contribute"
        case .chordPads: return "Chord Pads"
        }
    }
}

struct ModeTabsRow: View {
    @Binding var surface: PlaySurface

    var body: some View {
        HStack(spacing: 4) {
            segment(.learn)
            segment(.jam)
            segment(.contribute)
            gridSegment
        }
        .padding(4)
        .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(TFTheme.stroke, lineWidth: 1)
        )
        .padding(.horizontal, 12)
    }

    private func segment(_ s: PlaySurface) -> some View {
        Button {
            surface = s
        } label: {
            Text(s.title)
                .font(TFTheme.chipFont)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 8)
                .foregroundStyle(
                    surface == s ? TFTheme.textPrimary : TFTheme.textSecondary
                )
                .background(
                    surface == s ? TFTheme.chipActiveFill : .clear,
                    in: RoundedRectangle(cornerRadius: 9)
                )
        }
        .buttonStyle(.plain)
        .disabled(!s.isImplemented)
        .opacity(s.isImplemented ? 1 : 0.4)
        .accessibilityLabel(
            s.isImplemented ? s.title : "\(s.title) — coming soon"
        )
    }

    /// Chord Pads segment — icon-only per the mockup, fixed width so
    /// the three text segments share the remaining space evenly.
    private var gridSegment: some View {
        Button {
            surface = .chordPads
        } label: {
            Image(systemName: "square.grid.2x2")
                .font(TFTheme.chipFont)
                .frame(width: 40)
                .padding(.vertical, 8)
                .foregroundStyle(
                    surface == .chordPads
                        ? TFTheme.textPrimary : TFTheme.textSecondary
                )
                .background(
                    surface == .chordPads ? TFTheme.chipActiveFill : .clear,
                    in: RoundedRectangle(cornerRadius: 9)
                )
        }
        .buttonStyle(.plain)
        .disabled(!PlaySurface.chordPads.isImplemented)
        .opacity(PlaySurface.chordPads.isImplemented ? 1 : 0.4)
        .accessibilityLabel(
            PlaySurface.chordPads.isImplemented
                ? "Chord Pads" : "Chord Pads — coming soon"
        )
    }
}
