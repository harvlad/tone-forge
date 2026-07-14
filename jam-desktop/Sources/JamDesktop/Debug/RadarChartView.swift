// RadarChartView.swift
//
// Six-axis radar of per-stem features, ported from debug.js
// renderRadar: fixed 440×240 canvas, per-axis max normalization
// (across stems, floor 1e-9), dashed rings at 0.5/1.0, stem palette,
// dominant stem starred in the legend.

import SwiftUI
import JamDesktopCore

struct RadarChartView: View {
    let features: [StemDebugFeatures]
    let dominantStem: String?

    private static let axes: [(label: String, value: (StemDebugFeatures) -> Double)] = [
        ("density/s", { $0.chordDensityPerS ?? 0 }),
        ("mono", { $0.monophonicRatio ?? 0 }),
        ("repetition", { $0.repetitionScore ?? 0 }),
        ("polyphony", { $0.polyphonyScore ?? 0 }),
        ("lead", { $0.leadActivityScore ?? 0 }),
        ("pitch div", { $0.pitchClassDiversity ?? 0 }),
    ]

    private static let palette: [Color] = [
        Color(hex: 0x60A5FA), Color(hex: 0xF97316), Color(hex: 0xA78BFA),
        Color(hex: 0xF43F5E), Color(hex: 0x34D399), Color(hex: 0xFBBF24),
    ]

    private let width: CGFloat = 440
    private let height: CGFloat = 240

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Canvas { context, size in
                draw(context: context, size: size)
            }
            .frame(width: width, height: height)
            legend
        }
        .padding(8)
        .jamTile()
    }

    private var legend: some View {
        HStack(spacing: 12) {
            ForEach(Array(features.enumerated()), id: \.offset) { i, stem in
                let name = stem.stemName ?? "stem \(i)"
                let dominant = stem.stemName != nil && stem.stemName == dominantStem
                HStack(spacing: 4) {
                    Circle()
                        .fill(Self.palette[i % Self.palette.count])
                        .frame(width: 8, height: 8)
                    Text(dominant ? "\(name) ★" : name)
                        .font(.caption)
                        .foregroundStyle(
                            dominant ? JamTheme.accent : JamTheme.textSecondary)
                }
            }
        }
    }

    private func draw(context: GraphicsContext, size: CGSize) {
        let center = CGPoint(x: size.width / 2, y: size.height / 2)
        let radius = min(size.width / 2 - 70, size.height / 2 - 30)
        let count = Self.axes.count

        func point(axis i: Int, fraction: Double) -> CGPoint {
            let angle = Double(i) / Double(count) * 2 * .pi - .pi / 2
            return CGPoint(
                x: center.x + CGFloat(cos(angle) * fraction) * radius,
                y: center.y + CGFloat(sin(angle) * fraction) * radius)
        }

        // Per-axis max across stems, floored to avoid divide-by-zero.
        let maxima: [Double] = Self.axes.map { axis in
            max(features.map { axis.value($0) }.max() ?? 0, 1e-9)
        }

        // Rings at 0.5 and 1.0, dashed.
        for ring in [0.5, 1.0] {
            var path = Path()
            for i in 0...count {
                let p = point(axis: i % count, fraction: ring)
                if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
            }
            context.stroke(
                path, with: .color(.white.opacity(0.12)),
                style: StrokeStyle(lineWidth: 1, dash: [3, 3]))
        }

        // Spokes + labels.
        for (i, axis) in Self.axes.enumerated() {
            var spoke = Path()
            spoke.move(to: center)
            spoke.addLine(to: point(axis: i, fraction: 1.0))
            context.stroke(spoke, with: .color(.white.opacity(0.08)))

            let labelPoint = point(axis: i, fraction: 1.22)
            context.draw(
                Text(axis.label)
                    .font(.system(size: 10))
                    .foregroundStyle(.white.opacity(0.6)),
                at: labelPoint)
        }

        // Stem polygons.
        for (s, stem) in features.enumerated() {
            var path = Path()
            for i in 0...count {
                let axisIndex = i % count
                let fraction = Self.axes[axisIndex].value(stem) / maxima[axisIndex]
                let p = point(axis: axisIndex, fraction: min(1, max(0, fraction)))
                if i == 0 { path.move(to: p) } else { path.addLine(to: p) }
            }
            let color = Self.palette[s % Self.palette.count]
            let dominant = stem.stemName != nil && stem.stemName == dominantStem
            context.fill(path, with: .color(color.opacity(dominant ? 0.22 : 0.10)))
            context.stroke(
                path, with: .color(color.opacity(dominant ? 1.0 : 0.7)),
                lineWidth: dominant ? 2 : 1)
        }
    }
}
