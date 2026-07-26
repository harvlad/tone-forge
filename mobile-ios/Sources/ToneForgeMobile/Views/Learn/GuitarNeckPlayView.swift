// GuitarNeckPlayView.swift
//
// The "Show hand" mode of the Learn tab (approved sample design): ONE
// horizontal guitar neck with a silhouette hand PLAYING the song —
// not a chord chart. The current chord's fingering is shown as
// numbered dots (1 = index … 4 = pinky) with the hand fretting them;
// when the song moves to the next chord the hand animates to the new
// shape. The upcoming chord is announced as text, not a second board.

import SwiftUI
import ToneForgeEngine

struct GuitarNeckPlayView: View {
    /// Current chord symbol (nil → open hand, no dots).
    let current: String?
    /// Upcoming chord symbol (text pill only).
    let next: String?
    /// Song key for roman-numeral labels.
    let key: MusicalKey?

    var body: some View {
        VStack(alignment: .leading, spacing: TFTheme.Spacing.sm) {
            header

            GeometryReader { g in
                let shape = current.flatMap { GuitarVoicing.shape(symbol: $0) }
                let geo = NeckGeometry(size: g.size, baseFret: shape?.baseFret ?? 1)
                let fingering = shape.map { ChordFingering.assign(shape: $0) }
                    ?? ChordFingering.Result(notes: [], barreStrings: nil, barreFret: nil)
                let plan = HandPlan.plan(fingering: fingering, geo: geo)

                ZStack {
                    NeckBoardCanvas(shape: shape, geo: geo)
                    HandSilhouetteView(plan: plan)
                        .animation(.easeInOut(duration: 0.45), value: plan)
                    NeckDotsCanvas(shape: shape, geo: geo, fingering: fingering)
                }
                .clipped()
            }
        }
        .padding(TFTheme.Spacing.md)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .tfCard()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Guitar neck. Now \(current ?? "no chord")"
            + (next.map { ", next \($0)" } ?? ""))
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: TFTheme.Spacing.sm) {
            Text(current ?? "—")
                .font(.title.weight(.bold))
                .foregroundStyle(Color.accentColor)
            if let current, let numeral = RomanNumeral.label(symbol: current, key: key) {
                Text(numeral)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textSecondary)
            }
            Spacer()
            if let next {
                HStack(spacing: 4) {
                    Text("NEXT")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(TFTheme.textSecondary)
                    Text(next)
                        .font(.headline.weight(.bold))
                        .foregroundStyle(TFTheme.textPrimary)
                }
                .padding(.horizontal, TFTheme.Spacing.md)
                .padding(.vertical, 5)
                .background(TFTheme.surface2, in: Capsule())
                .overlay(Capsule().stroke(TFTheme.accent.opacity(0.5), lineWidth: 1))
            }
        }
    }
}

// MARK: - Board (wood, frets, strings, inlays, markers)

private struct NeckBoardCanvas: View {
    let shape: GuitarChordShape?
    let geo: NeckGeometry

    var body: some View {
        Canvas { ctx, _ in
            let neck = geo.neck

            // Wood board.
            ctx.fill(
                Path(roundedRect: neck, cornerRadius: 4),
                with: .linearGradient(
                    Gradient(colors: [
                        Color(red: 0.12, green: 0.082, blue: 0.055),
                        Color(red: 0.06, green: 0.042, blue: 0.032),
                    ]),
                    startPoint: neck.origin,
                    endPoint: CGPoint(x: neck.minX, y: neck.maxY)
                )
            )

            // Inlays at real fret markers.
            for col in 0..<geo.window {
                let absFret = geo.baseFret + col
                let cx = neck.minX + (CGFloat(col) + 0.5) * geo.fretW
                let r = geo.stringGap * 0.22
                let inlay = Color.white.opacity(0.10)
                if [3, 5, 7, 9, 15, 17].contains(absFret) {
                    ctx.fill(Path(ellipseIn: CGRect(
                        x: cx - r, y: neck.midY - r, width: r * 2, height: r * 2)),
                        with: .color(inlay))
                } else if absFret == 12 {
                    for dy in [-geo.stringGap, geo.stringGap] {
                        ctx.fill(Path(ellipseIn: CGRect(
                            x: cx - r, y: neck.midY + dy - r,
                            width: r * 2, height: r * 2)),
                            with: .color(inlay))
                    }
                }
            }

            // Nut (left, thick when open position) + fret wires.
            for f in 0...geo.window {
                let x = neck.minX + CGFloat(f) * geo.fretW
                var line = Path()
                line.move(to: CGPoint(x: x, y: neck.minY))
                line.addLine(to: CGPoint(x: x, y: neck.maxY))
                let isNut = f == 0 && geo.baseFret == 1
                ctx.stroke(
                    line,
                    with: .color(isNut
                        ? Color(white: 0.92)
                        : Color(white: 0.72).opacity(0.55)),
                    lineWidth: isNut ? 5 : 2
                )
            }

            // Strings: low E (bottom) thickest.
            for s in 0..<6 {
                let y = geo.stringY(s)
                var line = Path()
                line.move(to: CGPoint(x: neck.minX, y: y))
                line.addLine(to: CGPoint(x: neck.maxX, y: y))
                let gauge = 2.4 - CGFloat(s) * 0.28
                ctx.stroke(
                    line,
                    with: .color(Color(white: 0.80).opacity(0.55)),
                    lineWidth: max(0.9, gauge)
                )
            }

            // x / o markers left of the nut, per string.
            if let shape {
                for (s, state) in shape.strings.enumerated() {
                    let at = CGPoint(x: neck.minX - 13, y: geo.stringY(s))
                    switch state {
                    case .muted:
                        ctx.draw(
                            Text("×").font(.system(size: 12, weight: .semibold))
                                .foregroundColor(TFTheme.textSecondary),
                            at: at)
                    case .open:
                        let r: CGFloat = 3.6
                        ctx.stroke(
                            Path(ellipseIn: CGRect(
                                x: at.x - r, y: at.y - r, width: r * 2, height: r * 2)),
                            with: .color(TFTheme.textPrimary.opacity(0.8)),
                            lineWidth: 1.2)
                    case .fretted:
                        break
                    }
                }
                // Position label under the first fret column.
                if shape.baseFret > 1 {
                    ctx.draw(
                        Text("\(shape.baseFret)fr")
                            .font(.system(size: 10, weight: .medium))
                            .foregroundColor(TFTheme.textSecondary),
                        at: CGPoint(
                            x: neck.minX + geo.fretW * 0.5,
                            y: neck.maxY + 10))
                }
            }
        }
    }
}

// MARK: - Numbered dots (drawn above the hand)

private struct NeckDotsCanvas: View {
    let shape: GuitarChordShape?
    let geo: NeckGeometry
    let fingering: ChordFingering.Result

    var body: some View {
        Canvas { ctx, _ in
            guard shape != nil else { return }
            let r = geo.stringGap * 0.42
            for n in fingering.notes {
                let c = CGPoint(x: geo.fretX(n.fret), y: geo.stringY(n.string))
                ctx.fill(
                    Path(ellipseIn: CGRect(
                        x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)),
                    with: .color(Color.accentColor))
                ctx.draw(
                    Text("\(n.finger)")
                        .font(.system(size: r * 1.15, weight: .bold))
                        .foregroundColor(.white),
                    at: c)
            }
        }
        .allowsHitTesting(false)
    }
}
