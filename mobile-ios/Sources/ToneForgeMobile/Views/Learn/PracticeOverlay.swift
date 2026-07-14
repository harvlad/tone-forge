// PracticeOverlay.swift
//
// The in-practice surface (redesign Phase 8): expected chord front
// and centre, the section's progression as playable pads (each press
// is scored), streak + accuracy readouts with an accuracy ring, the
// last pass's score banner, and Stop.
//
// Presses voice on the PadSynth via LearnSessionController and are
// buffered for scoring; the pass completes automatically when the
// A/B loop wraps (AppState.onLoopWrap).

import SwiftUI
import ToneForgeEngine

struct PracticeOverlay: View {
    @ObservedObject var controller: LearnSessionController
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(spacing: 14) {
            header
            expectedChordCard
            if let prediction = controller.prediction() {
                LearnCountdownBar(
                    prediction: prediction,
                    songSeconds: appState.songSeconds
                )
            }
            chordPads
            Spacer(minLength: 0)
            statsRow
            stopButton
        }
        .padding(.vertical, 8)
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Practicing")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text(sectionName)
                    .font(.headline)
                    .foregroundStyle(TFTheme.textPrimary)
            }
            Spacer()
            if let result = controller.lastPassResult {
                passBanner(result)
            }
        }
        .padding(.horizontal, 12)
    }

    private var sectionName: String {
        (controller.activeSection?.label ?? "Section").capitalized
    }

    private func passBanner(_ result: LearnPassResult) -> some View {
        HStack(spacing: 4) {
            Image(systemName: result.isPassing
                ? "checkmark.seal.fill" : "arrow.counterclockwise")
            Text(result.isPassing
                ? "Learned!"
                : "Pass \(Int((result.accuracy * 100).rounded()))%")
                .font(.caption.weight(.semibold))
        }
        .foregroundStyle(result.isPassing
            ? Color.accentColor : TFTheme.textSecondary)
        .accessibilityLabel(result.isPassing
            ? "Section learned"
            : "Last pass accuracy \(Int((result.accuracy * 100).rounded())) percent")
    }

    // MARK: - Expected chord

    private var expectedChordCard: some View {
        VStack(spacing: 4) {
            Text("Play Now")
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
            Text(appState.currentChord?.symbol ?? "—")
                .font(.system(size: 44, weight: .bold, design: .rounded))
                .foregroundStyle(flashColor)
                .animation(.easeOut(duration: 0.25),
                           value: controller.lastPressHit)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 16)
        .tfCard()
        .padding(.horizontal, 12)
    }

    /// Brief hit/miss tint on the big chord after each press.
    private var flashColor: Color {
        switch controller.lastPressHit {
        case .some(true):  return .green
        case .some(false): return .red
        case .none:        return TFTheme.textPrimary
        }
    }

    // MARK: - Chord pads

    @ViewBuilder
    private var chordPads: some View {
        if controller.activeSection != nil {
            // Whole-song chord vocabulary, not just the section's
            // bar progression: passing chords the scorer expects must
            // always be playable, and a stable layout across sections
            // builds muscle memory.
            let chords = controller.songChords
            if chords.isEmpty {
                Text("No chords in this song — listen and follow along")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            } else {
                LazyVGrid(
                    columns: [GridItem(
                        .adaptive(minimum: 84, maximum: 160), spacing: 8
                    )],
                    spacing: 8
                ) {
                    ForEach(
                        Array(chords.enumerated()), id: \.offset
                    ) { _, symbol in
                        chordPad(symbol)
                    }
                }
                .padding(.horizontal, 12)
            }
        }
    }

    /// Amber "up next" tint, mirrored on the Launchpad pulse.
    private static let nextColor = Color(
        red: 1.00, green: 0.72, blue: 0.30)

    private func chordPad(_ symbol: String) -> some View {
        let isExpected = appState.currentChord.map {
            LearnScorer.matches(pressed: symbol, target: $0.symbol)
        } ?? false
        // Up next (distinct chord) blinks; the blink phase rides the
        // 30 Hz songSeconds publisher, no extra timer.
        let isNext = !isExpected
            && controller.prediction()?.nextSymbol == symbol
        let blinkOn = Int(appState.songSeconds / 0.35) % 2 == 0
        return Button {
            controller.recordPress(symbol: symbol)
        } label: {
            Text(symbol)
                .font(.title3.weight(.bold))
                .foregroundStyle(TFTheme.textPrimary)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 22)
                .background(
                    isExpected
                        ? Color.accentColor.opacity(0.45)
                        : isNext && blinkOn
                            ? Self.nextColor.opacity(0.35)
                            : TFTheme.chipFill,
                    in: RoundedRectangle(cornerRadius: 12)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(
                            isExpected
                                ? Color.accentColor
                                : isNext
                                    ? Self.nextColor
                                    : TFTheme.stroke,
                            lineWidth: isNext ? 2 : 1
                        )
                )
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            isNext ? "Play \(symbol), up next" : "Play \(symbol)")
    }

    // MARK: - Stats

    private var passAccuracy: Double {
        let total = controller.passHits + controller.passMisses
        guard total > 0 else { return 0 }
        return Double(controller.passHits) / Double(total)
    }

    private var statsRow: some View {
        HStack(spacing: 16) {
            accuracyRing
            VStack(alignment: .leading, spacing: 4) {
                Text("Streak \(controller.currentStreak)")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(TFTheme.textPrimary)
                Text("Hits \(controller.passHits) · Misses \(controller.passMisses)")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            Spacer()
        }
        .padding(.horizontal, 12)
    }

    private var accuracyRing: some View {
        ZStack {
            Circle()
                .stroke(TFTheme.chipFill, lineWidth: 5)
            Circle()
                .trim(from: 0, to: passAccuracy)
                .stroke(
                    Color.accentColor,
                    style: StrokeStyle(lineWidth: 5, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            Text("\(Int((passAccuracy * 100).rounded()))")
                .font(.caption.weight(.bold))
                .foregroundStyle(TFTheme.textPrimary)
        }
        .frame(width: 48, height: 48)
        .accessibilityLabel(
            "Accuracy \(Int((passAccuracy * 100).rounded())) percent")
    }

    // MARK: - Stop

    private var stopButton: some View {
        Button {
            controller.stopPractice()
        } label: {
            Label("Stop", systemImage: "stop.fill")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 12)
                .background(
                    TFTheme.chipFill,
                    in: RoundedRectangle(cornerRadius: 10)
                )
                .foregroundStyle(TFTheme.textPrimary)
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 12)
        .accessibilityLabel("Stop practicing")
    }
}
