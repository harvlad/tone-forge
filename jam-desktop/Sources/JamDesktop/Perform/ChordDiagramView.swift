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
        VStack(spacing: 4) {
            Text(diagram.symbol)
                .font(.system(.callout, design: .rounded).weight(.semibold))
                .lineLimit(1)
            Canvas { context, size in
                draw(in: context, size: size)
            }
            .aspectRatio(0.9, contentMode: .fit)
        }
    }

    private static let fretRows = 4

    private func draw(in context: GraphicsContext, size: CGSize) {
        // Layout: marker row on top, grid below.
        let markerHeight: CGFloat = size.height * 0.14
        let inset: CGFloat = size.width * 0.08
        let gridLeft = inset
        let gridRight = size.width - inset
        let gridTop = markerHeight
        let gridBottom = size.height - 2
        let stringSpacing = (gridRight - gridLeft) / 5
        let fretSpacing = (gridBottom - gridTop) / CGFloat(Self.fretRows)

        func stringX(_ string: Int) -> CGFloat {
            gridLeft + CGFloat(string) * stringSpacing
        }

        // Strings.
        for string in 0..<6 {
            var path = Path()
            path.move(to: CGPoint(x: stringX(string), y: gridTop))
            path.addLine(to: CGPoint(x: stringX(string), y: gridBottom))
            context.stroke(path, with: .color(.secondary), lineWidth: 1)
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
                lineWidth: isNut ? 3 : 1
            )
        }

        // Base-fret label when up the neck.
        if diagram.baseFret > 1 {
            context.draw(
                Text("\(diagram.baseFret)fr").font(.system(size: 9)),
                at: CGPoint(x: gridLeft - inset * 0.5, y: gridTop + fretSpacing / 2),
                anchor: .trailing
            )
        }

        // Open / muted markers above the nut.
        let markerY = markerHeight / 2
        for string in diagram.openStrings {
            let rect = CGRect(
                x: stringX(string) - 4, y: markerY - 4, width: 8, height: 8)
            context.stroke(
                Path(ellipseIn: rect), with: .color(.secondary), lineWidth: 1)
        }
        for string in diagram.mutedStrings {
            let x = stringX(string)
            var cross = Path()
            cross.move(to: CGPoint(x: x - 4, y: markerY - 4))
            cross.addLine(to: CGPoint(x: x + 4, y: markerY + 4))
            cross.move(to: CGPoint(x: x + 4, y: markerY - 4))
            cross.addLine(to: CGPoint(x: x - 4, y: markerY + 4))
            context.stroke(cross, with: .color(.secondary), lineWidth: 1.2)
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
                x: from.x - 5, y: from.y - 5,
                width: to.x - from.x + 10, height: 10)
            context.fill(
                Path(roundedRect: rect, cornerRadius: 5),
                with: .color(.primary.opacity(0.75))
            )
        }

        // Fretted dots.
        for dot in diagram.dots {
            let center = dotCenter(string: dot.string, fret: dot.fret)
            let rect = CGRect(
                x: center.x - 5, y: center.y - 5, width: 10, height: 10)
            context.fill(Path(ellipseIn: rect), with: .color(.primary))
        }
    }
}
