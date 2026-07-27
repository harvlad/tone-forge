// ChordTransitionSheet.swift
//
// Phase 2 of the neck-play design: the chord-TRANSITION practice
// sheet. Pick any chord pair from the song's progression, see the
// current fingering with Stay/Move/Lift-colored dots and dashed
// movement arrows toward the next chord, and run "Play transition" —
// the hand alternates between the two shapes at an adjustable tempo,
// optionally voicing each chord (metronome-style practice loop).

import SwiftUI
import ToneForgeEngine

struct ChordTransitionSheet: View {
    /// Consecutive chord pairs from the song (deduped, in order).
    let pairs: [(from: String, to: String)]
    /// Voice a chord audibly (wired to the PadSynth). Nil = silent.
    var onPlayChord: ((String) -> Void)? = nil
    @Environment(\.dismiss) private var dismiss

    @State private var selected: Int = 0
    @State private var showingNext = false     // looper phase (dots)
    @State private var handNext = false        // hand leads the dots
    @State private var isLooping = false
    @State private var bpm: Double = 90
    @State private var loopTask: Task<Void, Never>? = nil

    private var pair: (from: String, to: String)? {
        pairs.indices.contains(selected) ? pairs[selected] : nil
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: TFTheme.Spacing.md) {
                if let pair {
                    header(pair)

                    GuitarNeckPlaySurface(
                        current: showingNext ? pair.to : pair.from,
                        transitionTo: showingNext ? nil : pair.to,
                        handTarget: handNext ? pair.to : pair.from
                    )
                    .frame(maxHeight: .infinity)

                    legend

                    looperControls(pair)

                    pairPicker
                } else {
                    Text("No chord changes in this song yet.")
                        .foregroundStyle(TFTheme.textSecondary)
                        .frame(maxHeight: .infinity)
                }
            }
            .padding(TFTheme.Spacing.lg)
            .background(TFTheme.background.ignoresSafeArea())
            .navigationTitle("Transitions")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        stopLoop()
                        dismiss()
                    }
                }
            }
        }
        .preferredColorScheme(.dark)
        .onDisappear { stopLoop() }
    }

    // MARK: - Header (D → Asus4)

    private func header(_ pair: (from: String, to: String)) -> some View {
        HStack(spacing: TFTheme.Spacing.sm) {
            Text(pair.from)
                .font(.title.weight(.bold))
                .foregroundStyle(showingNext ? TFTheme.textSecondary : TFTheme.textPrimary)
            Image(systemName: "arrow.right")
                .font(.headline)
                .foregroundStyle(TFTheme.textSecondary)
            Text(pair.to)
                .font(.title.weight(.bold))
                .foregroundStyle(showingNext ? Color.accentColor : TFTheme.textSecondary)
            Spacer()
        }
    }

    // MARK: - Legend (sample design)

    private var legend: some View {
        HStack(spacing: TFTheme.Spacing.lg) {
            legendItem(role: .stay, name: "Stay", detail: "Keep finger in place")
            legendItem(role: .move, name: "Move", detail: "New position")
            legendItem(role: .place, name: "Lift", detail: "On / off the string")
            Spacer()
        }
    }

    private func legendItem(role: FingerRole, name: String, detail: String) -> some View {
        HStack(spacing: 6) {
            Circle().fill(role.color).frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 0) {
                Text(name).font(.caption.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                Text(detail).font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
    }

    // MARK: - Looper

    private func looperControls(_ pair: (from: String, to: String)) -> some View {
        HStack(spacing: TFTheme.Spacing.md) {
            Button {
                isLooping ? stopLoop() : startLoop(pair)
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: isLooping ? "stop.fill" : "play.fill")
                    Text(isLooping ? "Stop" : "Play transition")
                        .font(.subheadline.weight(.semibold))
                }
                .padding(.horizontal, TFTheme.Spacing.lg)
                .padding(.vertical, 10)
                .background(
                    isLooping ? TFTheme.danger.opacity(0.85) : TFTheme.accent,
                    in: Capsule())
                .foregroundStyle(TFTheme.textPrimary)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                Text("Tempo \(Int(bpm)) BPM")
                    .font(TFTheme.controlLabel)
                    .foregroundStyle(TFTheme.textSecondary)
                Slider(value: $bpm, in: 40...160, step: 5)
                    .tint(TFTheme.accent)
            }
        }
    }

    private func startLoop(_ pair: (from: String, to: String)) {
        stopLoop()
        isLooping = true
        loopTask = Task { @MainActor in
            while !Task.isCancelled {
                // Two beats per chord.
                let interval = UInt64((60.0 / bpm) * 2 * 1_000_000_000)
                // Hand moves first; dots follow once it lands.
                withAnimation(.easeInOut(duration: 0.32)) {
                    handNext.toggle()
                }
                Haptics.padTrigger()
                onPlayChord?(handNext ? pair.to : pair.from)
                try? await Task.sleep(nanoseconds: 340_000_000)
                showingNext = handNext
                try? await Task.sleep(nanoseconds:
                    interval > 340_000_000 ? interval - 340_000_000 : interval)
            }
        }
    }

    private func stopLoop() {
        loopTask?.cancel()
        loopTask = nil
        isLooping = false
        withAnimation(.easeInOut(duration: 0.3)) {
            handNext = false
            showingNext = false
        }
    }

    // MARK: - Pair picker

    private var pairPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: TFTheme.Spacing.sm) {
                ForEach(Array(pairs.enumerated()), id: \.offset) { i, p in
                    Button {
                        stopLoop()
                        selected = i
                    } label: {
                        Text("\(p.from) → \(p.to)")
                            .font(.subheadline.weight(.semibold))
                            .tfChip(active: i == selected)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }
}

// MARK: - Pair extraction

extension ChordTransitionSheet {
    /// Unique consecutive chord pairs from a song's chord timeline,
    /// in first-appearance order.
    static func pairs(from symbols: [String]) -> [(from: String, to: String)] {
        var seen = Set<String>()
        var out: [(String, String)] = []
        for (a, b) in zip(symbols, symbols.dropFirst()) where a != b {
            let key = "\(a)→\(b)"
            if seen.insert(key).inserted {
                out.append((a, b))
            }
        }
        return out
    }
}
