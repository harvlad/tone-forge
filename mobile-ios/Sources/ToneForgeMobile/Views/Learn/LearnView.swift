// LearnView.swift
//
// The LEARN surface (redesign Phase 8, first mockup): song progress
// bar, stats row (Sections Learned / Accuracy / Longest Streak), the
// Next Up card, and the section list with learned badges. While a
// practice pass is running the surface swaps to PracticeOverlay.
//
// Learn has no grid — practice pads voice chords directly on the
// PadSynth through LearnSessionController (D-019 bus bypass).

import SwiftUI
import ToneForgeEngine

/// Identifiable wrapper so a SectionEvent can drive `.sheet(item:)`.
struct LearnSectionSheetItem: Identifiable {
    let section: SectionEvent
    var id: String { LearnSessionController.sectionKey(for: section) }
}

struct LearnView: View {
    @ObservedObject var controller: LearnSessionController
    @EnvironmentObject private var appState: AppState

    @State private var detailItem: LearnSectionSheetItem?

    var body: some View {
        Group {
            if appState.currentBundle == nil {
                placeholder
            } else if controller.phase == .practicing {
                PracticeOverlay(controller: controller)
            } else {
                overview
            }
        }
        .sheet(item: $detailItem) { item in
            SectionDetailSheet(
                section: item.section, controller: controller)
        }
    }

    // MARK: - Song-less placeholder

    private var placeholder: some View {
        VStack(spacing: 8) {
            Image(systemName: "graduationcap")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)
            Text("Load a song to learn it")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Overview

    private var overview: some View {
        ScrollView {
            VStack(spacing: 12) {
                progressHeader
                statsRow
                if let next = controller.nextUpSection {
                    NextUpCard(
                        section: next,
                        controller: controller,
                        onOpenDetail: {
                            detailItem = LearnSectionSheetItem(section: next)
                        }
                    )
                } else if controller.totalSections > 0 {
                    allLearnedCard
                }
                sectionList
            }
            .padding(.vertical, 4)
        }
    }

    private var progressHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Song Progress")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                Spacer()
                Text("\(Int((controller.percentComplete * 100).rounded()))%")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
            }
            ProgressView(value: controller.percentComplete)
                .tint(Color.accentColor)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .tfCard()
        .padding(.horizontal, 12)
    }

    private var statsRow: some View {
        HStack(spacing: 8) {
            statCard(
                title: "Sections Learned",
                value: "\(controller.learnedCount)/\(controller.totalSections)"
            )
            statCard(
                title: "Accuracy",
                value: "\(Int((controller.overallAccuracy * 100).rounded()))%"
            )
            statCard(
                title: "Longest Streak",
                value: "\(controller.longestStreak)"
            )
        }
        .padding(.horizontal, 12)
    }

    private func statCard(title: String, value: String) -> some View {
        VStack(spacing: 4) {
            Text(value)
                .font(.title3.weight(.bold))
                .foregroundStyle(TFTheme.textPrimary)
            Text(title)
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .tfCard()
    }

    private var allLearnedCard: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundStyle(Color.accentColor)
            Text("All sections learned — nice work!")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(TFTheme.textPrimary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 14)
        .tfCard()
        .padding(.horizontal, 12)
    }

    // MARK: - Section list

    private var sectionList: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sections")
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)
                .padding(.horizontal, 12)
            ForEach(
                Array(controller.uniqueSections.enumerated()),
                id: \.offset
            ) { _, section in
                sectionRow(section)
            }
        }
    }

    private func sectionRow(_ section: SectionEvent) -> some View {
        let learned = controller.isLearned(section)
        let best = controller.progress?
            .progress(for: LearnSessionController.sectionKey(for: section))
            .bestAccuracy ?? 0
        return Button {
            detailItem = LearnSectionSheetItem(section: section)
        } label: {
            HStack(spacing: 10) {
                Image(systemName: learned
                    ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(
                        learned ? Color.accentColor : TFTheme.textSecondary)
                Text((section.label ?? "Section").capitalized)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                Spacer()
                if best > 0 {
                    Text("Best \(Int((best * 100).rounded()))%")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .tfCard()
            .padding(.horizontal, 12)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\((section.label ?? "Section").capitalized), "
                + (learned ? "learned" : "not learned yet")
        )
    }
}
