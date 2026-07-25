// SectionSelector.swift
//
// The shared song-section strip for Learn, Jam and Perform. Same
// underlying section model (`SongBundle.timeline.sections`), three
// densities via `style`. Letters A/B/C… come from position; the name
// (Intro/Verse/…) from the section label. Tapping a section seeks to it.
//
// This is NOT "Scenes" — it is the existing Sections concept. A trailing
// "NEXT" affordance (Perform) anticipates the upcoming section without
// inventing scene-launch behaviour.

import SwiftUI
import ToneForgeEngine

extension SectionSelector {
    enum Style { case compact, prominent }
}

struct SectionSelector: View {
    let sections: [SectionEvent]
    /// Index of the section the playhead is currently in (nil = none).
    var currentIndex: Int?
    /// Index of the next section (Perform's "NEXT").
    var nextIndex: Int?
    var style: Style = .compact
    let onSelect: (SectionEvent) -> Void
    /// Optional trailing "NEXT" chip (Perform). Nil hides it.
    var showNext: Bool = false

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: TFTheme.Spacing.sm) {
                ForEach(Array(sections.enumerated()), id: \.offset) { i, s in
                    chip(index: i, section: s)
                }
                if showNext, let ni = nextIndex, sections.indices.contains(ni) {
                    nextChip(sections[ni], letter: letter(ni))
                }
            }
            .padding(.horizontal, TFTheme.Spacing.lg)
        }
    }

    private func letter(_ i: Int) -> String {
        i < 26 ? String(UnicodeScalar(65 + i)!) : "\(i + 1)"
    }

    private func chip(index i: Int, section s: SectionEvent) -> some View {
        let isCurrent = currentIndex == i
        let isNext = nextIndex == i
        return Button {
            Haptics.selectionChanged()
            onSelect(s)
        } label: {
            VStack(spacing: 2) {
                Text(letter(i))
                    .font(style == .prominent ? .title3.weight(.bold) : .headline.weight(.semibold))
                    .foregroundStyle(isCurrent ? TFTheme.textPrimary : TFTheme.textSecondary)
                Text(s.label ?? "Section \(i + 1)")
                    .font(TFTheme.sectionLabel)
                    .foregroundStyle(isCurrent ? TFTheme.textPrimary : TFTheme.textSecondary)
                    .lineLimit(1)
            }
            .frame(minWidth: style == .prominent ? 68 : 52)
            .frame(height: TFTheme.sectionHeight)
            .padding(.horizontal, TFTheme.Spacing.sm)
            .background(
                isCurrent ? TFTheme.accent.opacity(0.30) : TFTheme.surface2,
                in: RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
            )
            .overlay(
                RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
                    .stroke(isCurrent ? TFTheme.accent : (isNext ? TFTheme.accent.opacity(0.5) : TFTheme.border),
                            lineWidth: isCurrent ? 1.5 : 1)
            )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(Text("Section \(letter(i)), \(s.label ?? "")"))
        .accessibilityAddTraits(isCurrent ? [.isSelected] : [])
    }

    private func nextChip(_ s: SectionEvent, letter: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("NEXT").font(.caption2).foregroundStyle(TFTheme.textSecondary)
            Text("\(letter) \(s.label ?? "")")
                .font(TFTheme.sectionLabel)
                .foregroundStyle(TFTheme.textPrimary)
                .lineLimit(1)
        }
        .frame(height: TFTheme.sectionHeight)
        .padding(.horizontal, TFTheme.Spacing.md)
        .background(TFTheme.surface2, in: RoundedRectangle(cornerRadius: TFTheme.Radius.medium))
        .overlay(
            RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
                .stroke(TFTheme.accent.opacity(0.5), lineWidth: 1)
        )
        .accessibilityLabel(Text("Next section \(letter), \(s.label ?? "")"))
    }
}
