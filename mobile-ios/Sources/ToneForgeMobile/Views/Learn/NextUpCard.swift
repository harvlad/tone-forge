// NextUpCard.swift
//
// "Next Up" card on the Learn surface (redesign Phase 8): the first
// unlearned section with its chord progression, a [Current] badge
// when the playhead is inside it, a preview-play button, and the
// Start Section button that kicks off a practice loop.

import SwiftUI
import ToneForgeEngine

struct NextUpCard: View {
    let section: SectionEvent
    @ObservedObject var controller: LearnSessionController
    var onOpenDetail: () -> Void
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            chordChips
            actions
        }
        .padding(12)
        .tfCard()
        .padding(.horizontal, 12)
    }

    private var isCurrent: Bool {
        guard let now = SectionResolver.current(
            t: appState.songSeconds,
            in: appState.currentBundle?.timeline.sections ?? []
        ) else { return false }
        return LearnSessionController.sectionKey(for: now)
            == LearnSessionController.sectionKey(for: section)
    }

    private var header: some View {
        HStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Next Up")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text((section.label ?? "Section").capitalized)
                    .font(.headline)
                    .foregroundStyle(TFTheme.textPrimary)
            }
            if isCurrent {
                Text("Current")
                    .font(.caption2.weight(.semibold))
                    .tfChip(active: true)
            }
            Spacer()
            Button(action: onOpenDetail) {
                Image(systemName: "info.circle")
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .accessibilityLabel("Section details")
        }
    }

    @ViewBuilder
    private var chordChips: some View {
        let chords = controller.progressionChords(for: section)
        if !chords.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(
                        Array(chords.enumerated()), id: \.offset
                    ) { _, symbol in
                        Text(symbol)
                            .font(TFTheme.chipFont)
                            .tfChip(active: false)
                    }
                }
            }
        }
    }

    private var actions: some View {
        HStack(spacing: 10) {
            Button {
                controller.startSection(section)
            } label: {
                Label("Start Section", systemImage: "play.fill")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 10)
                    .background(
                        Color.accentColor,
                        in: RoundedRectangle(cornerRadius: 10)
                    )
                    .foregroundStyle(.black)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Start practicing this section")

            // Preview: just play the section from its top (no loop,
            // no scoring).
            Button {
                appState.seekAndPlay(to: section.start)
            } label: {
                Image(systemName: "ear")
                    .font(.title3)
                    .foregroundStyle(TFTheme.textPrimary)
                    .padding(10)
                    .background(
                        TFTheme.chipFill,
                        in: RoundedRectangle(cornerRadius: 10)
                    )
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Preview this section")
        }
    }
}
