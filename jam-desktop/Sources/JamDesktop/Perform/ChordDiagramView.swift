// ChordDiagramView.swift
//
// Canvas-rendered fretboard diagram for a ChordDiagram (native
// GuitarVoicing shapes — chord_diagrams.js parity). Vertical
// strings (low E left), four-fret window, open/muted markers above
// the nut, optional barre capsule, "Nfr" label when the window sits
// up the neck.

import SwiftUI
import JamDesktopCore

struct ChordDiagramView: View {
    let diagram: ChordDiagram

    var body: some View {
        VStack(spacing: 8) {
            Text(diagram.symbol)
                .font(.system(size: 24, weight: .bold, design: .rounded))
                .lineLimit(1)
            Canvas { context, size in
                draw(in: context, size: size)
            }
            .aspectRatio(0.9, contentMode: .fit)
        }
    }

    private static let fretRows = 4

    private func draw(in context: GraphicsContext, size: CGSize) {
        // Scale factor for line widths and dot sizes.
        let scale = min(size.width, size.height) / 100

        // Layout: marker row on top, grid below.
        let markerHeight: CGFloat = size.height * 0.14
        let inset: CGFloat = size.width * 0.08
        let gridLeft = inset
        let gridRight = size.width - inset
        let gridTop = markerHeight
        let gridBottom = size.height - 2
        let stringSpacing = (gridRight - gridLeft) / 5
        let fretSpacing = (gridBottom - gridTop) / CGFloat(Self.fretRows)

        let stringLine = max(1.5, 2 * scale)
        let fretLine = max(1.5, 2 * scale)
        let nutLine = max(4, 5 * scale)
        let dotRadius = max(6, 5.5 * scale)
        let markerRadius = max(5, 4.5 * scale)

        func stringX(_ string: Int) -> CGFloat {
            gridLeft + CGFloat(string) * stringSpacing
        }

        // Strings.
        for string in 0..<6 {
            var path = Path()
            path.move(to: CGPoint(x: stringX(string), y: gridTop))
            path.addLine(to: CGPoint(x: stringX(string), y: gridBottom))
            context.stroke(path, with: .color(.secondary), lineWidth: stringLine)
        }

        // Frets; thick nut when the window starts at fret 1.
        for row in 0...Self.fretRows {
            let y = gridTop + CGFloat(row) * fretSpacing
            var path = Path()
            path.move(to: CGPoint(x: gridLeft, y: y))
            path.addLine(to: CGPoint(x: gridRight, y: y))
            let isNut = row == 0 && diagram.baseFret == 1
            context.stroke(
                path,
                with: .color(isNut ? .primary : .secondary),
                lineWidth: isNut ? nutLine : fretLine
            )
        }

        // Base-fret label when up the neck.
        if diagram.baseFret > 1 {
            context.draw(
                Text("\(diagram.baseFret)fr").font(.system(size: max(10, 12 * scale))),
                at: CGPoint(x: gridLeft - inset * 0.5, y: gridTop + fretSpacing / 2),
                anchor: .trailing
            )
        }

        // Open / muted markers above the nut.
        let markerY = markerHeight / 2
        for string in diagram.openStrings {
            let rect = CGRect(
                x: stringX(string) - markerRadius,
                y: markerY - markerRadius,
                width: markerRadius * 2,
                height: markerRadius * 2)
            context.stroke(
                Path(ellipseIn: rect), with: .color(.secondary), lineWidth: max(1.5, 2 * scale))
        }
        for string in diagram.mutedStrings {
            let x = stringX(string)
            var cross = Path()
            cross.move(to: CGPoint(x: x - markerRadius, y: markerY - markerRadius))
            cross.addLine(to: CGPoint(x: x + markerRadius, y: markerY + markerRadius))
            cross.move(to: CGPoint(x: x + markerRadius, y: markerY - markerRadius))
            cross.addLine(to: CGPoint(x: x - markerRadius, y: markerY + markerRadius))
            context.stroke(cross, with: .color(.secondary), lineWidth: max(1.5, 2 * scale))
        }

        func dotCenter(string: Int, fret: Int) -> CGPoint {
            let row = fret - diagram.baseFret  // 0-based window row
            let y = gridTop + (CGFloat(row) + 0.5) * fretSpacing
            return CGPoint(x: stringX(string), y: y)
        }

        // Barre (cosmetic) under the dots.
        if let barre = diagram.barre {
            let from = dotCenter(string: barre.fromString, fret: barre.fret)
            let to = dotCenter(string: barre.toString, fret: barre.fret)
            let rect = CGRect(
                x: from.x - dotRadius, y: from.y - dotRadius,
                width: to.x - from.x + dotRadius * 2, height: dotRadius * 2)
            context.fill(
                Path(roundedRect: rect, cornerRadius: dotRadius),
                with: .color(.primary.opacity(0.75))
            )
        }

        // Fretted dots.
        for dot in diagram.dots {
            let center = dotCenter(string: dot.string, fret: dot.fret)
            let rect = CGRect(
                x: center.x - dotRadius, y: center.y - dotRadius,
                width: dotRadius * 2, height: dotRadius * 2)
            context.fill(Path(ellipseIn: rect), with: .color(.primary))
        }
    }
}
