// VerticalFader.swift
//
// Vertical drag fader for the restyled Mixer (Phase 11) — track,
// filled portion from the bottom, and a thumb capsule. Value maps
// linearly over `range`; dragging jumps to the touch point so a tap
// anywhere on the track sets the level directly.
//
// Reused by the Chord Pads layer fader (Phase 12).

import SwiftUI

struct VerticalFader: View {
    @Binding var value: Double
    var range: ClosedRange<Double> = 0...1

    private let trackWidth: CGFloat = 6
    private let thumbSize = CGSize(width: 30, height: 12)

    var body: some View {
        GeometryReader { geo in
            let height = geo.size.height
            let frac = normalizedValue
            ZStack(alignment: .bottom) {
                // Track
                Capsule()
                    .fill(TFTheme.chipFill)
                    .frame(width: trackWidth)
                // Fill from the bottom up to the value
                Capsule()
                    .fill(Color.accentColor.opacity(0.6))
                    .frame(width: trackWidth, height: max(trackWidth, frac * height))
                // Thumb
                Capsule()
                    .fill(TFTheme.textPrimary)
                    .frame(width: thumbSize.width, height: thumbSize.height)
                    .offset(y: -(frac * (height - thumbSize.height)))
                    .shadow(radius: 2)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        let usable = height - thumbSize.height
                        guard usable > 0 else { return }
                        let y = g.location.y - thumbSize.height / 2
                        let frac = 1 - min(max(y / usable, 0), 1)
                        value = range.lowerBound
                            + frac * (range.upperBound - range.lowerBound)
                    }
            )
        }
        .accessibilityElement()
        .accessibilityValue(Text("\(Int((normalizedValue * 100).rounded())) percent"))
        .accessibilityAdjustableAction { direction in
            let step = (range.upperBound - range.lowerBound) / 20
            switch direction {
            case .increment:
                value = min(range.upperBound, value + step)
            case .decrement:
                value = max(range.lowerBound, value - step)
            @unknown default:
                break
            }
        }
    }

    private var normalizedValue: Double {
        let span = range.upperBound - range.lowerBound
        guard span > 0 else { return 0 }
        return min(max((value - range.lowerBound) / span, 0), 1)
    }
}
