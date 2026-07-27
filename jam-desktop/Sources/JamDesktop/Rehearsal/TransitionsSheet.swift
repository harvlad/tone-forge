// TransitionsSheet.swift
//
// Desktop host for the Phase 2 chord-transition practice view (shared
// ToneForgeEngine neck surface): chord-pair picker from the song's
// progression, Stay/Move/Lift-colored dots + movement arrows, and a
// tempo looper that animates the hand between the two shapes.

import SwiftUI
import ToneForgeEngine

struct TransitionsSheet: View {
    let pairs: [(from: String, to: String)]
    @Environment(\.dismiss) private var dismiss

    @State private var selected = 0
    @State private var showingNext = false
    @State private var handNext = false
    @State private var handLifted = false
    @State private var isLooping = false
    @State private var bpm: Double = 90
    @State private var loopTask: Task<Void, Never>? = nil

    private var pair: (from: String, to: String)? {
        pairs.indices.contains(selected) ? pairs[selected] : nil
    }

    var body: some View {
        VStack(spacing: 14) {
            HStack {
                if let pair {
                    Text(pair.from)
                        .font(.title.weight(.bold))
                        .foregroundStyle(showingNext ? .secondary : .primary)
                    Image(systemName: "arrow.right").foregroundStyle(.secondary)
                    Text(pair.to)
                        .font(.title.weight(.bold))
                        .foregroundStyle(showingNext ? Color.accentColor : .secondary)
                }
                Spacer()
                Button("Close") { stop(); dismiss() }
            }

            if let pair {
                GuitarNeckPlaySurface(
                    current: showingNext ? pair.to : pair.from,
                    transitionTo: showingNext ? nil : pair.to,
                    handTarget: handNext ? pair.to : pair.from,
                    handLifted: handLifted
                )
                .frame(minHeight: 260)

                HStack(spacing: 18) {
                    legendItem(.stay, "Stay")
                    legendItem(.move, "Move")
                    legendItem(.place, "Lift")
                    Spacer()
                    Button {
                        isLooping ? stop() : start(pair)
                    } label: {
                        Label(isLooping ? "Stop" : "Play transition",
                              systemImage: isLooping ? "stop.fill" : "play.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(isLooping ? .red : .accentColor)
                    Slider(value: $bpm, in: 40...160, step: 5) {
                        Text("Tempo")
                    }
                    .frame(width: 180)
                    Text("\(Int(bpm)) BPM").monospacedDigit()
                        .foregroundStyle(.secondary)
                }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(pairs.enumerated()), id: \.offset) { i, p in
                            Button("\(p.from) → \(p.to)") {
                                stop(); selected = i
                            }
                            .buttonStyle(.bordered)
                            .tint(i == selected ? .accentColor : .secondary)
                        }
                    }
                }
            } else {
                Text("No chord changes in this song yet.")
                    .foregroundStyle(.secondary)
                    .frame(maxHeight: .infinity)
            }
        }
        .padding(20)
        .frame(minWidth: 640, minHeight: 460)
        .onDisappear { stop() }
    }

    private func legendItem(_ role: FingerRole, _ name: String) -> some View {
        HStack(spacing: 5) {
            Circle().fill(role.color).frame(width: 9, height: 9)
            Text(name).font(.caption)
        }
    }

    private func start(_ pair: (from: String, to: String)) {
        stop()
        isLooping = true
        loopTask = Task { @MainActor in
            while !Task.isCancelled {
                let interval = UInt64((60.0 / bpm) * 2 * 1_000_000_000)
                // Lift → move → drop; dots follow when it lands.
                handLifted = true
                withAnimation(.easeInOut(duration: 0.32)) { handNext.toggle() }
                try? await Task.sleep(nanoseconds: 260_000_000)
                handLifted = false
                try? await Task.sleep(nanoseconds: 120_000_000)
                showingNext = handNext
                try? await Task.sleep(nanoseconds:
                    interval > 380_000_000 ? interval - 380_000_000 : interval)
            }
        }
    }

    private func stop() {
        loopTask?.cancel()
        loopTask = nil
        isLooping = false
        handLifted = false
        withAnimation(.easeInOut(duration: 0.3)) {
            handNext = false
            showingNext = false
        }
    }
}

extension TransitionsSheet {
    /// Unique consecutive chord pairs, first-appearance order.
    static func pairs(from symbols: [String]) -> [(from: String, to: String)] {
        var seen = Set<String>()
        var out: [(String, String)] = []
        for (a, b) in zip(symbols, symbols.dropFirst()) where a != b {
            if seen.insert("\(a)→\(b)").inserted { out.append((a, b)) }
        }
        return out
    }
}
