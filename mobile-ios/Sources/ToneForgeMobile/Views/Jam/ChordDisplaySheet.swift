// ChordDisplaySheet.swift
//
// Chord-progress detail opened from the Current Chord panel
// (redesign Phase 7): the sounding chord large, the two suggested
// follow-ups as playable chips, "Next: X in N bars" from the song
// timeline, and the bar progress through the current section —
// all via BarMath so the numbers agree with Learn's Section View.
//
// Everything is derived from AppState @Published fields (songSeconds,
// currentChord, nextChord), so the sheet live-updates while the
// transport runs. Songless (sketch) it degrades to just the tonic
// chord + suggestions.

import SwiftUI
import ToneForgeEngine

struct ChordDisplaySheet: View {
    @ObservedObject var controller: JamInKeyController
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                // Sounding chord, front and center.
                VStack(spacing: 4) {
                    Text("Current Chord")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                    Text(controller.currentChordSymbol ?? "—")
                        .font(.system(size: 56, weight: .bold))
                        .foregroundStyle(TFTheme.textPrimary)
                }
                .padding(.top, 12)

                // Suggested follow-ups (tappable, voice on PadSynth).
                let suggested = controller.suggestedChords
                if !suggested.isEmpty {
                    VStack(spacing: 8) {
                        Text("Suggested next")
                            .font(.caption)
                            .foregroundStyle(TFTheme.textSecondary)
                        HStack(spacing: 10) {
                            ForEach(suggested, id: \.degree) { chord in
                                Button {
                                    controller.trigger(symbol: chord.symbol)
                                } label: {
                                    VStack(spacing: 2) {
                                        Text(chord.symbol)
                                            .font(.title3.weight(.bold))
                                            .foregroundStyle(TFTheme.textPrimary)
                                        Text(chord.romanNumeral)
                                            .font(.caption2)
                                            .foregroundStyle(TFTheme.textSecondary)
                                    }
                                    .frame(minWidth: 72)
                                    .padding(.vertical, 10)
                                    .tfCard()
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Play \(chord.symbol)")
                            }
                        }
                    }
                }

                // Timeline readouts — song only.
                if let next = nextChordInfo {
                    HStack(spacing: 6) {
                        Text("Next: \(next.symbol)")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(TFTheme.textPrimary)
                        if let bars = next.bars {
                            Text(bars == 0 ? "this bar" : "in \(bars) bar\(bars == 1 ? "" : "s")")
                                .font(.subheadline)
                                .foregroundStyle(TFTheme.textSecondary)
                        }
                    }
                }

                if let progress = sectionProgress {
                    VStack(spacing: 6) {
                        Text("\(progress.label) — bar \(progress.current)/\(progress.total)")
                            .font(TFTheme.readout)
                            .foregroundStyle(TFTheme.textSecondary)
                        ProgressView(
                            value: Double(progress.current),
                            total: Double(max(1, progress.total))
                        )
                        .tint(Color.accentColor)
                        .padding(.horizontal, 40)
                    }
                }

                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(TFTheme.background)
            .navigationTitle("Chords")
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

    // MARK: - Derived timeline readouts

    private var nextChordInfo: (symbol: String, bars: Int?)? {
        guard let next = appState.nextChord else { return nil }
        let bars = BarMath.barsUntil(
            next.start,
            from: appState.songSeconds,
            downbeats: appState.currentBundle?.timeline.downbeats ?? [],
            tempoBpm: appState.currentBundle?.meta.tempoBpm
        )
        return (next.symbol, bars)
    }

    private var sectionProgress: (label: String, current: Int, total: Int)? {
        guard
            let bundle = appState.currentBundle,
            let section = bundle.timeline.sections.first(where: {
                $0.start <= appState.songSeconds
                    && appState.songSeconds < $0.end
            })
        else { return nil }
        let downbeats = bundle.timeline.downbeats
        let tempo = bundle.meta.tempoBpm
        let total = BarMath.barCount(
            start: section.start, end: section.end,
            downbeats: downbeats, tempoBpm: tempo
        )
        guard total > 0 else { return nil }
        let elapsed = BarMath.barsUntil(
            appState.songSeconds, from: section.start,
            downbeats: downbeats, tempoBpm: tempo
        ) ?? 0
        return (
            section.label ?? "Section",
            min(total, elapsed + 1),
            total
        )
    }
}
