// ChordRibbonView.swift
//
// Time-proportional chord ribbon on a Canvas, mirroring the web jam
// ribbon: a window of song time scrolls right-to-left under a fixed
// playhead; each chord draws as a block sized by its duration with
// the sounding chord highlighted. The big current/next labels sit
// above the ribbon.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct ChordRibbonView: View {
    let ribbon: ChordRibbonModel
    let positionSeconds: Double

    /// Song-seconds shown behind and ahead of the playhead.
    private let lookBehind: Double = 2
    private let lookAhead: Double = 10

    var body: some View {
        VStack(spacing: 12) {
            currentNextLabels
            ribbonCanvas
        }
    }

    // MARK: - Big labels

    private var currentNextLabels: some View {
        let window = ribbon.window(at: positionSeconds, count: 2)
        let current = ribbon.currentChord(at: positionSeconds)
        let next: ChordEvent? = {
            guard let first = window.first else { return nil }
            if current != nil {
                return window.count > 1 ? window[1] : nil
            }
            return first   // in a gap: show what's coming
        }()

        return HStack(alignment: .firstTextBaseline, spacing: 24) {
            Text(current?.symbol ?? "—")
                .font(.system(size: 64, weight: .bold, design: .rounded))
                .monospacedDigit()
            if let next {
                Text("→ \(next.symbol)")
                    .font(.system(size: 28, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .animation(nil, value: positionSeconds)
    }

    // MARK: - Canvas ribbon

    private var ribbonCanvas: some View {
        ChordRibbonCanvas(
            ribbon: ribbon,
            positionSeconds: positionSeconds,
            lookBehind: lookBehind,
            lookAhead: lookAhead
        )
        .frame(minHeight: 64)
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Strip-only version (no labels) for secondary placement.
struct ChordRibbonStripView: View {
    let ribbon: ChordRibbonModel
    let positionSeconds: Double

    var body: some View {
        ChordRibbonCanvas(
            ribbon: ribbon,
            positionSeconds: positionSeconds,
            lookBehind: 2,
            lookAhead: 10
        )
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
    }
}

/// Shared canvas for chord ribbon rendering.
private struct ChordRibbonCanvas: View {
    let ribbon: ChordRibbonModel
    let positionSeconds: Double
    let lookBehind: Double
    let lookAhead: Double

    var body: some View {
        Canvas { context, size in
            let windowStart = positionSeconds - lookBehind
            let windowSpan = lookBehind + lookAhead
            let pxPerSecond = size.width / windowSpan

            func x(at t: Double) -> CGFloat {
                CGFloat(t - windowStart) * pxPerSecond
            }

            // Chord blocks intersecting the window.
            for (idx, chord) in ribbon.chords.enumerated() {
                guard chord.end > windowStart,
                      chord.start < windowStart + windowSpan else { continue }
                let x0 = max(0, x(at: chord.start))
                let x1 = min(size.width, x(at: chord.end))
                guard x1 > x0 + 1 else { continue }

                let isCurrent = ribbon.chordIndex(at: positionSeconds) == idx
                let rect = CGRect(
                    x: x0 + 1, y: 4, width: x1 - x0 - 2, height: size.height - 8
                )
                let shape = Path(roundedRect: rect, cornerRadius: 6)
                context.fill(
                    shape,
                    with: .color(isCurrent
                        ? Color.accentColor.opacity(0.85)
                        : Color.gray.opacity(0.25))
                )

                let label = Text(chord.symbol)
                    .font(.system(size: 13, weight: isCurrent ? .bold : .medium))
                    .foregroundStyle(isCurrent ? Color.white : Color.primary)
                context.draw(
                    context.resolve(label),
                    at: CGPoint(x: rect.midX, y: rect.midY)
                )
            }

            // Fixed playhead.
            let playheadX = x(at: positionSeconds)
            var line = Path()
            line.move(to: CGPoint(x: playheadX, y: 0))
            line.addLine(to: CGPoint(x: playheadX, y: size.height))
            context.stroke(line, with: .color(.red.opacity(0.8)), lineWidth: 2)
        }
    }
}
