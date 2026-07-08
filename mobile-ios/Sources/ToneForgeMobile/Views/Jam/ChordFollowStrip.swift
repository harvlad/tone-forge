// ChordFollowStrip.swift
//
// Countdown progress bar for chord-follow mode (D-022). Shows the next
// chord symbol, time remaining, and a progress bar that fills as the
// chord approaches. Mirrors the web jam-ui countdown strip.
//
// Progress is 0→1 over the duration of the current chord, reaching 1
// at the moment the next chord starts. When no next chord is available
// (end of song or no timeline), the strip hides.

import SwiftUI
import ToneForgeEngine

struct ChordFollowStrip: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        if let info = countdownInfo {
            VStack(spacing: 4) {
                // Progress bar
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        // Background track
                        RoundedRectangle(cornerRadius: 4)
                            .fill(TFTheme.chipFill)
                        // Fill
                        RoundedRectangle(cornerRadius: 4)
                            .fill(Color.accentColor.opacity(0.7))
                            .frame(width: geo.size.width * info.progress)
                            .animation(.linear(duration: 0.1), value: info.progress)
                    }
                }
                .frame(height: 6)

                // Labels
                HStack {
                    Text("Next: \(info.symbol)")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                    Spacer()
                    Text(formatTime(info.secondsRemaining))
                        .font(.subheadline.monospacedDigit())
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .tfCard()
            .padding(.horizontal, 12)
        }
    }

    // MARK: - Countdown calculation

    private struct CountdownInfo {
        let symbol: String
        let secondsRemaining: Double
        let progress: Double
    }

    private var countdownInfo: CountdownInfo? {
        guard let current = appState.currentChord,
              let next = appState.nextChord else {
            return nil
        }

        let now = appState.songSeconds
        let chordDuration = next.start - current.start
        guard chordDuration > 0 else { return nil }

        let elapsed = now - current.start
        let remaining = max(0, next.start - now)
        let progress = min(1.0, max(0.0, elapsed / chordDuration))

        return CountdownInfo(
            symbol: next.symbol,
            secondsRemaining: remaining,
            progress: progress
        )
    }

    private func formatTime(_ seconds: Double) -> String {
        if seconds >= 60 {
            let mins = Int(seconds) / 60
            let secs = seconds.truncatingRemainder(dividingBy: 60)
            return String(format: "%d:%04.1f", mins, secs)
        } else {
            return String(format: "%.1fs", seconds)
        }
    }
}

// MARK: - Launchpad countdown helpers

extension ChordFollowStrip {
    /// Calculate which LEDs (0-7) should be lit on Launchpad row 1
    /// based on countdown progress. Returns the number of pads to light
    /// (0 = none, 8 = all lit).
    static func launchpadLitCount(progress: Double) -> Int {
        // Progress 0→1 maps to 0→8 lit pads
        return Int((progress * 8).rounded(.down)).clamped(to: 0...8)
    }

    /// Progress value (0-1) for the current chord → next chord countdown.
    /// Returns nil if no countdown is active.
    @MainActor
    static func countdownProgress(appState: AppState) -> Double? {
        guard let current = appState.currentChord,
              let next = appState.nextChord else {
            return nil
        }
        let now = appState.songSeconds
        let chordDuration = next.start - current.start
        guard chordDuration > 0 else { return nil }
        let elapsed = now - current.start
        return min(1.0, max(0.0, elapsed / chordDuration))
    }
}

private extension Int {
    func clamped(to range: ClosedRange<Int>) -> Int {
        return Swift.min(Swift.max(self, range.lowerBound), range.upperBound)
    }
}
