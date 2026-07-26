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
    /// Layer selection so a host can sandwich the hand silhouette
    /// between the board and the dots: grid → hand → dots.
    var layer: Layer = .full
    /// Visual style: .chart = the classic thin-line chord chart;
    /// .neck = realistic dark fretboard (used by the hand-overlay
    /// mode). The chart is NEVER restyled — the neck is a separate,
    /// toggleable view.
    var style: Style = .chart

    enum Layer { case full, board, dots }
    enum Style { case chart, neck }

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

        if layer != .dots {
        switch style {
        case .chart:
            // Classic chord chart: thin lines, no board.
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

        case .neck:
        // Neck: a dark wood-black board behind the grid (the mock's
        // realistic fretboard), extending a little past the outer
        // strings.
        let boardInset = stringGap * 0.45
        let board = CGRect(
            x: gridRect.minX - boardInset, y: gridRect.minY,
            width: gridRect.width + boardInset * 2,
            height: gridRect.height
        )
        context.fill(
            Path(roundedRect: board, cornerRadius: 3),
            with: .linearGradient(
                Gradient(colors: [
                    Color(red: 0.11, green: 0.075, blue: 0.05),
                    Color(red: 0.055, green: 0.04, blue: 0.03),
                ]),
                startPoint: board.origin,
                endPoint: CGPoint(x: board.minX, y: board.maxY)
            )
        )

        // Inlay dots (3/5/7/9/15/17 single, 12 double) at real fret
        // positions inside the window.
        for row in 0..<fretRows {
            let absFret = shape.baseFret + row
            let cy = gridRect.minY + (CGFloat(row) + 0.5) * fretGap
            let r = min(stringGap, fretGap) * 0.13
            let inlay = Color.white.opacity(0.10)
            if [3, 5, 7, 9, 15, 17].contains(absFret) {
                context.fill(Path(ellipseIn: CGRect(
                    x: gridRect.midX - r, y: cy - r, width: r * 2, height: r * 2)),
                    with: .color(inlay))
            } else if absFret == 12 {
                for dx in [-stringGap, stringGap] {
                    context.fill(Path(ellipseIn: CGRect(
                        x: gridRect.midX + dx - r, y: cy - r,
                        width: r * 2, height: r * 2)),
                        with: .color(inlay))
                }
            }
        }

        // Nut (thick when open position) + metallic fret wires.
        for f in 0...fretRows {
            let y = gridRect.minY + CGFloat(f) * fretGap
            var line = Path()
            line.move(to: CGPoint(x: board.minX, y: y))
            line.addLine(to: CGPoint(x: board.maxX, y: y))
            let isNut = f == 0 && shape.baseFret == 1
            context.stroke(
                line,
                with: .color(isNut
                    ? TFTheme.textPrimary.opacity(0.95)
                    : Color(white: 0.72).opacity(0.55)),
                lineWidth: isNut ? 4 : 2
            )
        }

        // Strings: gauged — wound low strings thicker than the plain
        // high ones, with a metallic sheen.
        for s in 0..<stringCount {
            var line = Path()
            line.move(to: CGPoint(x: stringX(s), y: gridRect.minY))
            line.addLine(to: CGPoint(x: stringX(s), y: gridRect.maxY))
            let gauge = 2.2 - CGFloat(s) * 0.25   // low E → high e
            context.stroke(
                line,
                with: .color(Color(white: 0.80).opacity(0.55)),
                lineWidth: max(0.9, gauge)
            )
        }
        }  // switch style

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

        }  // layer != .dots

        guard layer != .board else { return }

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
