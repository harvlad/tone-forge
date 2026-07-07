// CategoryCards.swift
//
// "What do you want to add?" — the horizontal card strip from the
// Now Playing mockup. One card per SampleFamily (minus .mixed, which
// is a pack-level catch-all, not something a user reaches for).
// Tapping a card opens Browse Packs; the family pre-filter hook wires
// up in Phase 10 when the sheet grows filter chips.

import SwiftUI
import ToneForgeEngine

struct CategoryCards: View {
    let onSelect: (SampleFamily) -> Void

    private static let families: [SampleFamily] =
        SampleFamily.allCases.filter { $0 != .mixed }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("What do you want to add?")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(TFTheme.textPrimary)
                .padding(.horizontal, 12)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(Self.families, id: \.rawValue) { family in
                        card(family)
                    }
                }
                .padding(.horizontal, 12)
            }
        }
    }

    private func card(_ family: SampleFamily) -> some View {
        Button {
            onSelect(family)
        } label: {
            VStack(spacing: 6) {
                Image(systemName: Self.icon(for: family))
                    .font(.title3)
                    .foregroundStyle(TFTheme.familyTint(family))
                Text(Self.title(for: family))
                    .font(TFTheme.chipFont)
                    .foregroundStyle(TFTheme.textPrimary)
            }
            .frame(width: 88, height: 64)
            .background(
                TFTheme.surfaceElevated,
                in: RoundedRectangle(cornerRadius: 12)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(
                        TFTheme.familyTint(family).opacity(0.35),
                        lineWidth: 1
                    )
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Browse \(Self.title(for: family)) packs")
    }

    static func title(for family: SampleFamily) -> String {
        switch family {
        case .pads: return "Pads"
        case .percussion: return "Percussion"
        case .textures: return "Textures"
        case .stabs: return "Stabs"
        case .bass: return "Bass"
        case .fx: return "FX"
        case .vocals: return "Vocals"
        case .mixed: return "Mixed"
        }
    }

    static func icon(for family: SampleFamily) -> String {
        switch family {
        case .pads: return "pianokeys"
        case .percussion: return "metronome"
        case .textures: return "wind"
        case .stabs: return "bolt.fill"
        case .bass: return "speaker.wave.2.fill"
        case .fx: return "sparkles"
        case .vocals: return "music.mic"
        case .mixed: return "square.grid.2x2"
        }
    }
}
