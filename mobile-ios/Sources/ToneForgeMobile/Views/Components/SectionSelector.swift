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
    /// Index of the SECTION-LOCKED section (the transport's A/B loop
    /// covers it). The locked chip carries a lock glyph — the loop state
    /// used to be invisible outside the Loop chip, so a song "stuck on
    /// the intro" read as a playback bug.
    var lockedIndex: Int?
    var style: Style = .compact
    let onSelect: (SectionEvent) -> Void
    /// Long-press a chip: lock/unlock playback to that section.
    var onToggleLock: ((SectionEvent) -> Void)?
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
        let isLocked = lockedIndex == i
        // Suppress the name line when the label is just the position
        // letter (generic "A"/"B" sections) — otherwise it reads "A / A".
        let name = s.label
        let showName = if let name, !name.isEmpty {
            name.caseInsensitiveCompare(letter(i)) != .orderedSame
        } else { false }
        return Button {
            Haptics.selectionChanged()
            onSelect(s)
        } label: {
            VStack(spacing: 2) {
                HStack(spacing: 3) {
                    Text(letter(i))
                        .font(style == .prominent ? .title3.weight(.bold) : .headline.weight(.semibold))
                        .foregroundStyle(isCurrent ? TFTheme.textPrimary : TFTheme.textSecondary)
                    if isLocked {
                        Image(systemName: "lock.fill")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(TFTheme.accent)
                    }
                }
                if showName, let name {
                    Text(name)
                        .font(TFTheme.sectionLabel)
                        .foregroundStyle(isCurrent ? TFTheme.textPrimary : TFTheme.textSecondary)
                        .lineLimit(1)
                }
            }
            .frame(minWidth: style == .prominent ? 68 : 52)
            .frame(height: TFTheme.sectionHeight)
            .padding(.horizontal, TFTheme.Spacing.sm)
            .background(
                isLocked ? TFTheme.accent.opacity(0.45)
                    : (isCurrent ? TFTheme.accent.opacity(0.30) : TFTheme.surface2),
                in: RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
            )
            .overlay(
                RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
                    .stroke(isLocked || isCurrent ? TFTheme.accent
                                : (isNext ? TFTheme.accent.opacity(0.5) : TFTheme.border),
                            lineWidth: isLocked || isCurrent ? 1.5 : 1)
            )
        }
        .buttonStyle(.plain)
        // Long-press = lock/unlock playback to this section. Simultaneous
        // so a quick tap still selects.
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0.4).onEnded { _ in
                guard let onToggleLock else { return }
                Haptics.toggle()
                onToggleLock(s)
            }
        )
        .accessibilityLabel(Text(
            "Section \(letter(i)), \(s.label ?? "")\(isLocked ? ", locked" : "")"))
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
