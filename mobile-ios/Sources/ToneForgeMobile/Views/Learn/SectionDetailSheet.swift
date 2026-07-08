// SectionDetailSheet.swift
//
// Per-section detail sheet on the Learn surface (redesign Phase 8):
// time range, chord progression, the numbered bar strip (SectionBars),
// a practice-tips card, and a Loop Section toggle that arms the A/B
// loop without starting a scored practice pass.

import SwiftUI
import ToneForgeEngine

struct SectionDetailSheet: View {
    let section: SectionEvent
    @ObservedObject var controller: LearnSessionController
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    headerCard
                    progressionCard
                    barStripCard
                    tipsCard
                    startButton
                }
                .padding(12)
            }
            .background(TFTheme.background)
            .navigationTitle((section.label ?? "Section").capitalized)
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
    }

    // MARK: - Derived

    private var bars: [SectionBar] {
        SectionBars.bars(
            section: section,
            downbeats: appState.currentBundle?.timeline.downbeats ?? [],
            chords: appState.currentBundle?.timeline.chords ?? [],
            tempoBpm: appState.currentBundle?.meta.tempoBpm
        )
    }

    private var isLooping: Bool {
        appState.loopRegion?.startSec == section.start
            && appState.loopRegion?.endSec == section.end
    }

    private static func timeString(_ t: Double) -> String {
        let total = Int(t.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    // MARK: - Cards

    private var headerCard: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Time Range")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text("\(Self.timeString(section.start)) – \(Self.timeString(section.end))")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
            }
            Spacer()
            Toggle(isOn: loopBinding) {
                Text("Loop Section")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .toggleStyle(.switch)
            .fixedSize()
        }
        .padding(12)
        .tfCard()
    }

    private var loopBinding: Binding<Bool> {
        Binding(
            get: { isLooping },
            set: { on in
                if on {
                    appState.setLoop(LoopRegion(
                        startSec: section.start, endSec: section.end))
                } else {
                    appState.setLoop(nil)
                }
            }
        )
    }

    @ViewBuilder
    private var progressionCard: some View {
        let chords = controller.progressionChords(for: section)
        if !chords.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Progression")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
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
                if let perChord = barsPerChord {
                    Text("\(perChord) bars each")
                        .font(.caption2)
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }
            .padding(12)
            .tfCard()
        }
    }

    /// "4 bars each" readout — only when every chord in the section
    /// holds for the same whole number of bars.
    private var barsPerChord: Int? {
        let bars = self.bars
        guard !bars.isEmpty else { return nil }
        var runs: [Int] = []
        var runLength = 0
        var lastSymbol: String?
        for bar in bars {
            if bar.chordSymbol == lastSymbol {
                runLength += 1
            } else {
                if lastSymbol != nil { runs.append(runLength) }
                lastSymbol = bar.chordSymbol
                runLength = 1
            }
        }
        runs.append(runLength)
        guard let first = runs.first, first > 1,
              runs.allSatisfy({ $0 == first })
        else { return nil }
        return first
    }

    @ViewBuilder
    private var barStripCard: some View {
        let bars = self.bars
        if !bars.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("Bars")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 4) {
                        ForEach(bars, id: \.number) { bar in
                            VStack(spacing: 2) {
                                Text("\(bar.number)")
                                    .font(.caption2)
                                    .foregroundStyle(TFTheme.textSecondary)
                                Text(bar.chordSymbol ?? "·")
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(TFTheme.textPrimary)
                            }
                            .frame(minWidth: 34)
                            .padding(.vertical, 6)
                            .background(
                                TFTheme.chipFill,
                                in: RoundedRectangle(cornerRadius: 6)
                            )
                        }
                    }
                }
            }
            .padding(12)
            .tfCard()
        }
    }

    private var tipsCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Tips", systemImage: "lightbulb")
                .font(.caption.weight(.semibold))
                .foregroundStyle(TFTheme.textPrimary)
            Text(
                "Start slow: preview the section a few times before "
                + "practicing. Hit each chord as it changes — you pass "
                + "at 80% accuracy with every chord covered."
            )
            .font(.caption)
            .foregroundStyle(TFTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .tfCard()
    }

    private var startButton: some View {
        Button {
            dismiss()
            controller.startSection(section)
        } label: {
            Label("Start Section", systemImage: "play.fill")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(
                    Color.accentColor,
                    in: RoundedRectangle(cornerRadius: 10)
                )
                .foregroundStyle(.black)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Start practicing this section")
    }
}
