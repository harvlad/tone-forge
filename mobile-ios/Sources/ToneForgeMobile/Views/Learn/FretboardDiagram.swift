// FretboardDiagram.swift
//
// Canvas-drawn guitar chord diagram (D-022 Learn redesign): six
// strings, a four-fret window, finger dots, open/muted markers, and
// a "5fr" position label when the shape lives above the nut.
// Renders a GuitarChordShape from GuitarVoicing; sized for the Learn
// chord cards (~110 pt tall).

import SwiftUI
import ToneForgeEngine

struct FretboardDiagram: View {
    let shape: GuitarChordShape

    /// Frets drawn in the window (matches GuitarVoicing's search
    /// window).
    private let fretRows = 4

    var body: some View {
        Canvas { context, size in
            draw(in: context, size: size)
        }
        .accessibilityLabel("Chord diagram")
    }

    // MARK: - Drawing

    private func draw(in context: GraphicsContext, size: CGSize) {
        let markerHeight: CGFloat = 14      // x/o strip above the nut
        let sideInset: CGFloat = shape.baseFret > 1 ? 20 : 6
        let gridRect = CGRect(
            x: sideInset,
            y: markerHeight,
            width: size.width - sideInset - 6,
            height: size.height - markerHeight - 4
        )
        guard gridRect.width > 0, gridRect.height > 0 else { return }

        let stringCount = shape.strings.count
        let stringGap = gridRect.width / CGFloat(stringCount - 1)
        let fretGap = gridRect.height / CGFloat(fretRows)

        func stringX(_ s: Int) -> CGFloat {
            gridRect.minX + CGFloat(s) * stringGap
        }

        // Nut (thick when open position) + fret wires.
        for f in 0...fretRows {
            let y = gridRect.minY + CGFloat(f) * fretGap
            var line = Path()
            line.move(to: CGPoint(x: gridRect.minX, y: y))
            line.addLine(to: CGPoint(x: gridRect.maxX, y: y))
            let isNut = f == 0 && shape.baseFret == 1
            context.stroke(
                line,
                with: .color(TFTheme.textPrimary.opacity(isNut ? 0.9 : 0.35)),
                lineWidth: isNut ? 3 : 1
            )
        }

        // Strings.
        for s in 0..<stringCount {
            var line = Path()
            line.move(to: CGPoint(x: stringX(s), y: gridRect.minY))
            line.addLine(to: CGPoint(x: stringX(s), y: gridRect.maxY))
            context.stroke(
                line,
                with: .color(TFTheme.textPrimary.opacity(0.35)),
                lineWidth: 1
            )
        }

        // Position label ("3fr") beside the first fret row.
        if shape.baseFret > 1 {
            context.draw(
                Text("\(shape.baseFret)fr")
                    .font(.system(size: 9, weight: .medium))
                    .foregroundColor(TFTheme.textSecondary),
                at: CGPoint(
                    x: gridRect.minX - 11,
                    y: gridRect.minY + fretGap / 2
                )
            )
        }

        // Markers + dots.
        let dotRadius = min(stringGap, fretGap) * 0.32
        for (s, state) in shape.strings.enumerated() {
            let markerCenter = CGPoint(
                x: stringX(s), y: markerHeight / 2)
            switch state {
            case .muted:
                context.draw(
                    Text("×")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundColor(TFTheme.textSecondary),
                    at: markerCenter
                )
            case .open:
                let r: CGFloat = 3.5
                context.stroke(
                    Path(ellipseIn: CGRect(
                        x: markerCenter.x - r, y: markerCenter.y - r,
                        width: r * 2, height: r * 2)),
                    with: .color(TFTheme.textPrimary.opacity(0.8)),
                    lineWidth: 1.2
                )
            case .fretted(let fret):
                let row = fret - shape.baseFret     // 0-based window row
                guard row >= 0, row < fretRows else { continue }
                let center = CGPoint(
                    x: stringX(s),
                    y: gridRect.minY + (CGFloat(row) + 0.5) * fretGap
                )
                context.fill(
                    Path(ellipseIn: CGRect(
                        x: center.x - dotRadius, y: center.y - dotRadius,
                        width: dotRadius * 2, height: dotRadius * 2)),
                    with: .color(Color.accentColor)
                )
            }
        }
    }
}
