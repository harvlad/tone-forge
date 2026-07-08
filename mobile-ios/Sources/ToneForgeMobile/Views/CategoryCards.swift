// CategoryCards.swift
//
// Family browse shortcuts — a compact horizontal chip row (one chip
// per SampleFamily, minus .mixed which is a pack-level catch-all).
// Started life as the mockup's 64pt "What do you want to add?" card
// strip; compacted to ~32pt icon+name chips so the Play tab fits a
// phone screen without scrolling. Tapping a chip opens Browse Packs
// pre-filtered to that family.

import SwiftUI
import ToneForgeEngine

struct CategoryCards: View {
    let onSelect: (SampleFamily) -> Void

    private static let families: [SampleFamily] =
        SampleFamily.allCases.filter { $0 != .mixed }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Self.families, id: \.rawValue) { family in
                    chip(family)
                }
            }
            .padding(.horizontal, 12)
        }
    }

    private func chip(_ family: SampleFamily) -> some View {
        Button {
            onSelect(family)
        } label: {
            HStack(spacing: 5) {
                Image(systemName: Self.icon(for: family))
                    .font(.caption)
                    .foregroundStyle(TFTheme.familyTint(family))
                Text(Self.title(for: family))
                    .font(TFTheme.chipFont)
                    .foregroundStyle(TFTheme.textPrimary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Capsule().fill(TFTheme.surfaceElevated))
            .overlay(
                Capsule().stroke(
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
