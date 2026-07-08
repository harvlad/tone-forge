// ProgressRing.swift
//
// Circular progress indicator for the Learn tab (D-022): a trimmed
// ring with a headline value in the middle and a caption below.
// Same trim-circle recipe as PracticeOverlay's accuracy ring, pulled
// out so the overview screen and future surfaces share one.

import SwiftUI

struct ProgressRing: View {
    /// 0…1 fraction of the ring to fill.
    let value: Double
    /// Headline inside the ring ("67%").
    let centerText: String
    /// Caption under the headline ("learned").
    let caption: String
    var diameter: CGFloat = 84
    var lineWidth: CGFloat = 7

    var body: some View {
        ZStack {
            Circle()
                .stroke(TFTheme.chipFill, lineWidth: lineWidth)
            Circle()
                .trim(from: 0, to: max(0, min(1, value)))
                .stroke(
                    Color.accentColor,
                    style: StrokeStyle(lineWidth: lineWidth, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            VStack(spacing: 0) {
                Text(centerText)
                    .font(.headline.weight(.bold))
                    .foregroundStyle(TFTheme.textPrimary)
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
            }
        }
        .frame(width: diameter, height: diameter)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(centerText) \(caption)")
    }
}
