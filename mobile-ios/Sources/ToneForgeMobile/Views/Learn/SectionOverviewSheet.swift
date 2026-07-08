// SectionOverviewSheet.swift
//
// Song-structure sheet for the Learn tab (D-022). This is where the
// WaveformScrubber lives now that tab bodies are scroll-free: full
// waveform + seek, a repetition map (one row per distinct section
// label, every occurrence as a bar on the song timeline), and the
// per-section list with learned badges, time ranges and practice
// entry points. Sheets are allowed to scroll — the no-scrolling
// contract binds tabs only.

import SwiftUI
import ToneForgeEngine

struct SectionOverviewSheet: View {
    @ObservedObject var controller: LearnSessionController
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var detailItem: LearnSectionSheetItem?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    scrubberCard
                    repetitionCard
                    sectionList
                }
                .padding(12)
            }
            .background(TFTheme.background)
            .navigationTitle("Sections")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .sheet(item: $detailItem) { item in
            SectionDetailSheet(section: item.section, controller: controller)
        }
    }

    // MARK: - Derived

    private var duration: Double {
        appState.currentBundle?.meta.durationSec ?? 0
    }

    private var repetitionRows: [RepetitionMapModel.Row] {
        RepetitionMapModel.rows(
            sections: appState.currentBundle?.timeline.sections ?? [],
            duration: duration
        )
    }

    private static func timeString(_ t: Double) -> String {
        let total = max(0, Int(t.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    // MARK: - Scrubber

    private var scrubberCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Timeline")
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
            WaveformScrubber(
                songSeconds: appState.songSeconds,
                durationSec: duration,
                peaks: appState.waveformPeaks,
                onSeek: { appState.seek(to: $0) }
            )
            .frame(height: 56)
        }
        .padding(.vertical, 12)
        .tfCard()
    }

    // MARK: - Repetition map

    @ViewBuilder
    private var repetitionCard: some View {
        let rows = repetitionRows
        if !rows.isEmpty, duration > 0 {
            VStack(alignment: .leading, spacing: 8) {
                Text("Structure")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                ForEach(rows, id: \.label) { row in
                    repetitionRow(row)
                }
            }
            .padding(12)
            .tfCard()
        }
    }

    private func repetitionRow(_ row: RepetitionMapModel.Row) -> some View {
        HStack(spacing: 8) {
            Text(row.label)
                .font(.caption.weight(.medium))
                .foregroundStyle(TFTheme.textPrimary)
                .frame(width: 64, alignment: .leading)
                .lineLimit(1)
            GeometryReader { proxy in
                let w = proxy.size.width
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(TFTheme.chipFill)
                    ForEach(
                        Array(row.occurrences.enumerated()), id: \.offset
                    ) { _, range in
                        let x = w * range.lowerBound / duration
                        let width = max(
                            3, w * (range.upperBound - range.lowerBound) / duration)
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.accentColor.opacity(0.7))
                            .frame(width: width)
                            .offset(x: x)
                            .onTapGesture {
                                appState.seek(to: range.lowerBound)
                            }
                    }
                }
            }
            .frame(height: 14)
        }
        .frame(height: 18)
    }

    // MARK: - Section list

    private var sectionList: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Sections")
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
            ForEach(
                Array(controller.uniqueSections.enumerated()), id: \.offset
            ) { _, section in
                sectionRow(section)
            }
        }
    }

    private func sectionRow(_ section: SectionEvent) -> some View {
        let learned = controller.isLearned(section)
        return Button {
            detailItem = LearnSectionSheetItem(section: section)
        } label: {
            HStack(spacing: 10) {
                Image(systemName: learned ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(
                        learned ? Color.accentColor : TFTheme.textSecondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text((section.label ?? "Section").capitalized)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                    Text("\(Self.timeString(section.start)) – \(Self.timeString(section.end))")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(TFTheme.textSecondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .tfCard()
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            "\((section.label ?? "Section").capitalized), "
                + (learned ? "learned" : "not learned yet")
        )
    }
}
