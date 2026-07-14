// LearnCountdownBar.swift
//
// Horizontal anticipation bar for the practice overlay: fills as the
// next distinct chord approaches (cyan → amber → red gradient) and
// blinks in the final 600 ms. Port of the web jam launchpad-mode
// countdown strip; the same prediction drives the Launchpad top row
// (AppState.learnPracticeFrame).

import SwiftUI
import ToneForgeEngine

struct LearnCountdownBar: View {
    let prediction: LearnSessionController.ChordPrediction
    /// Song-clock seconds — drives the imminent blink phase so the
    /// bar animates without its own timer (songSeconds publishes at
    /// 30 Hz while playing).
    let songSeconds: Double

    var body: some View {
        VStack(spacing: 4) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(TFTheme.chipFill)
                    RoundedRectangle(cornerRadius: 4)
                        .fill(
                            LinearGradient(
                                colors: [
                                    Color(red: 0.30, green: 0.82, blue: 0.88), // cyan
                                    Color(red: 1.00, green: 0.72, blue: 0.30), // amber
                                    Color(red: 1.00, green: 0.32, blue: 0.32), // red
                                ],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: geo.size.width * prediction.progress)
                        .opacity(fillOpacity)
                        .animation(.linear(duration: 0.1),
                                   value: prediction.progress)
                }
            }
            .frame(height: 8)

            HStack {
                Text("Up next: \(prediction.nextSymbol)")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(
                        prediction.imminent
                            ? Color(red: 1.00, green: 0.32, blue: 0.32)
                            : TFTheme.textPrimary)
                Spacer()
                Text(String(format: "%.1fs", prediction.remainingSec))
                    .font(.subheadline.monospacedDigit())
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .tfCard()
        .padding(.horizontal, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "Next chord \(prediction.nextSymbol) in "
            + String(format: "%.0f", prediction.remainingSec.rounded())
            + " seconds")
    }

    /// Hard blink in the last 600 ms (web parity), solid otherwise.
    private var fillOpacity: Double {
        guard prediction.imminent else { return 1 }
        return Int(songSeconds / 0.2) % 2 == 0 ? 1 : 0.35
    }
}
