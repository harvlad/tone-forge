// TabLaneView.swift
//
// Scrolling guitar-tab lane over TabLaneModel (picking-tab-lane.js
// port): six horizontal strings (high E on top), a fixed playhead
// ~30% in, note glyphs placed at absolute x and shifted per frame by
// model.translation(at:). Redraws with the Perform display timer via
// `positionSeconds`.

import SwiftUI
import JamDesktopCore

struct TabLaneView: View {
    /// Configured model (notes + glyph). Geometry fields are
    /// overwritten with the actual canvas size each frame.
    var model: TabLaneModel
    let positionSeconds: Double

    var body: some View {
        Canvas { context, size in
            var lane = model
            lane.width = size.width
            lane.height = size.height
            draw(lane, in: context, size: size)
        }
        .frame(height: model.height)
        .clipShape(RoundedRectangle(cornerRadius: 6))
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(.quaternary.opacity(0.3))
        )
    }

    private func draw(
        _ lane: TabLaneModel, in context: GraphicsContext, size: CGSize
    ) {
        // Strings + gutter labels (top-to-bottom E B G D A E).
        for (row, label) in TabLaneModel.stringLabelsTopToBottom.enumerated() {
            let y = lane.stringTop + Double(row) * lane.stringSpacing
            var path = Path()
            path.move(to: CGPoint(x: lane.padLeft, y: y))
            path.addLine(to: CGPoint(x: size.width - lane.padRight, y: y))
            context.stroke(
                path, with: .color(.secondary.opacity(0.5)), lineWidth: 1)
            context.draw(
                Text(label).font(.system(size: 9, design: .monospaced)),
                at: CGPoint(x: lane.padLeft - 10, y: y),
                anchor: .center
            )
        }

        // Playhead.
        var playhead = Path()
        playhead.move(to: CGPoint(x: lane.playheadX, y: lane.stringTop - 6))
        playhead.addLine(to: CGPoint(x: lane.playheadX, y: lane.stringBottom + 6))
        context.stroke(playhead, with: .color(.accentColor), lineWidth: 2)

        // Notes, shifted so "now" lands on the playhead; only draw
        // what's inside the lane.
        let tx = lane.translation(at: positionSeconds)
        for placement in lane.placements() {
            let x = placement.x + tx
            guard x >= lane.padLeft - 8,
                  x <= size.width - lane.padRight + 8 else { continue }
            let passed = x < lane.playheadX
            let color: Color = passed ? .secondary.opacity(0.4) : .primary

            switch lane.glyph {
            case .dot:
                let rect = CGRect(
                    x: x - 3.5, y: placement.y - 3.5, width: 7, height: 7)
                context.fill(Path(ellipseIn: rect), with: .color(color))
            case .fret:
                context.draw(
                    Text("\(placement.fret)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(color),
                    at: CGPoint(x: x, y: placement.y),
                    anchor: .center
                )
            case .note:
                context.draw(
                    Text(placement.noteName)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(color),
                    at: CGPoint(x: x, y: placement.y),
                    anchor: .center
                )
            }
        }
    }
}
