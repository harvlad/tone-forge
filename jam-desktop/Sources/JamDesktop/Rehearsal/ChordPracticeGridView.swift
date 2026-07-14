// ChordPracticeGridView.swift
//
// Interactive chord pad grid for Learn mode practice. Shows all unique
// chords in the song; pressing a pad voices the chord through the synth
// and records the press for scoring.
//
// The grid highlights:
//   - the current timeline chord (playing along)
//   - the next chord when imminent (countdown under 600ms)
//   - hit/miss flash on each press

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

struct ChordPracticeGridView: View {
    @EnvironmentObject private var session: SessionController
    let chords: [String]
    let currentChordSymbol: String?
    let prediction: LearnSessionModel.ChordPrediction?
    let lastPressHit: Bool?

    @State private var flashSymbol: String?
    @State private var flashIsHit: Bool = false

    var body: some View {
        let columns = [GridItem(.adaptive(minimum: 80), spacing: 10)]
        LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            ForEach(chords, id: \.self) { symbol in
                padButton(symbol: symbol)
            }
        }
    }

    @ViewBuilder
    private func padButton(symbol: String) -> some View {
        let isCurrent = symbol == currentChordSymbol
        let isNext = symbol == prediction?.nextSymbol && prediction?.imminent == true
        let isFlashHit = flashSymbol == symbol && flashIsHit
        let isFlashMiss = flashSymbol == symbol && !flashIsHit

        Button {
            pressChord(symbol)
        } label: {
            Text(symbol)
                .font(.title2.weight(.semibold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 18)
                .background(backgroundColor(
                    isCurrent: isCurrent,
                    isNext: isNext,
                    isFlashHit: isFlashHit,
                    isFlashMiss: isFlashMiss
                ))
                .clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(
                            isNext ? Color.yellow : Color.clear,
                            lineWidth: 3
                        )
                )
                .animation(.easeOut(duration: 0.15), value: isCurrent)
                .animation(.easeOut(duration: 0.1), value: flashSymbol)
        }
        .buttonStyle(.plain)
    }

    private func backgroundColor(
        isCurrent: Bool,
        isNext: Bool,
        isFlashHit: Bool,
        isFlashMiss: Bool
    ) -> Color {
        if isFlashHit {
            return Color.green.opacity(0.7)
        }
        if isFlashMiss {
            return Color.red.opacity(0.7)
        }
        if isCurrent {
            return Color.accentColor.opacity(0.5)
        }
        return Color(nsColor: .controlBackgroundColor)
    }

    private func pressChord(_ symbol: String) {
        let songTime = session.transport.positionSeconds
        session.learn.recordPress(symbol: symbol, atTime: songTime)
        // Flash feedback
        let hit = session.learn.lastPressHit ?? false
        flashSymbol = symbol
        flashIsHit = hit
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 150_000_000)
            if flashSymbol == symbol { flashSymbol = nil }
        }
    }
}

/// Overlay showing hit/miss counts and streak during practice.
struct PracticeScoreOverlay: View {
    let passHits: Int
    let passMisses: Int
    let currentStreak: Int
    let lastPassResult: LearnPassResult?

    var body: some View {
        HStack(spacing: 20) {
            statPill(label: "Hits", value: passHits, color: .green)
            statPill(label: "Miss", value: passMisses, color: .red)
            statPill(label: "Streak", value: currentStreak, color: .orange)
            if let result = lastPassResult {
                Divider().frame(height: 20)
                Text(result.isPassing ? "PASS" : "Try again")
                    .font(.headline)
                    .foregroundStyle(result.isPassing ? Color.green : Color.secondary)
                Text("\(Int(result.accuracy * 100))%")
                    .font(.headline.monospacedDigit())
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: Capsule())
    }

    private func statPill(label: String, value: Int, color: Color) -> some View {
        HStack(spacing: 4) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("\(value)")
                .font(.headline.monospacedDigit())
                .foregroundStyle(color)
        }
    }
}

/// Countdown bar showing progress toward next chord change.
struct ChordCountdownBar: View {
    let prediction: LearnSessionModel.ChordPrediction?

    var body: some View {
        if let pred = prediction {
            VStack(spacing: 4) {
                HStack {
                    Text(pred.currentSymbol)
                        .font(.caption.weight(.medium))
                    Spacer()
                    Text("next: \(pred.nextSymbol)")
                        .font(.caption)
                        .foregroundStyle(pred.imminent ? .yellow : .secondary)
                }
                GeometryReader { geo in
                    Capsule()
                        .fill(Color.secondary.opacity(0.3))
                        .overlay(alignment: .leading) {
                            Capsule()
                                .fill(pred.imminent ? Color.yellow : Color.accentColor)
                                .frame(width: geo.size.width * pred.progress)
                        }
                }
                .frame(height: 6)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 8))
        }
    }
}
