// HandSilhouette.swift
//
// Optional fretting-hand silhouette over the Learn chord diagrams:
// the photoreal pose (hand reaching up from below the neck, fingers
// arched over the strings, wrist dropping off the card) rendered as a
// dark parametric silhouette — no image assets, so it fits ANY chord
// shape at any fret window and animates naturally from chord to chord
// (fingertips are the animatable data; the hand re-solves each frame).
//
// Geometry mirrors FretboardDiagram exactly so fingertips land on the
// finger dots. Layering: grid → hand → dots (host composes).

import SwiftUI
import ToneForgeEngine

// MARK: - Finger assignment

/// Which fingertip goes where for a chord shape. Heuristic matching
/// standard chord-chart fingering: barre = finger 1 across the lowest
/// fret; otherwise fingers 1…4 in (fret, string) order.
struct HandPlan: Equatable {
    /// Fingertip targets in diagram coordinates, index 0…3 = fingers
    /// 1…4. Fingers without a job rest curled below the grid.
    var tips: [CGPoint]
    /// X-span of a barre at tips[0]'s y (finger 1 laid flat). Nil = no
    /// barre.
    var barre: ClosedRange<CGFloat>?
    /// Bottom edge of the fret grid — the hand's anatomy anchors here
    /// (knuckles just below the board), not to the deepest fingertip.
    var gridBottom: CGFloat = 0

    /// Diagram geometry duplicated from FretboardDiagram (markerHeight
    /// 14, side insets, 4 fret rows) — keep in sync.
    static func plan(shape: GuitarChordShape, size: CGSize) -> HandPlan {
        let fretRows = 4
        let markerHeight: CGFloat = 14
        let sideInset: CGFloat = shape.baseFret > 1 ? 20 : 6
        let gridRect = CGRect(
            x: sideInset, y: markerHeight,
            width: size.width - sideInset - 6,
            height: size.height - markerHeight - 4
        )
        guard gridRect.width > 0, gridRect.height > 0 else {
            return HandPlan(tips: [], barre: nil, gridBottom: 0)
        }
        let stringCount = shape.strings.count
        let stringGap = gridRect.width / CGFloat(max(1, stringCount - 1))
        let fretGap = gridRect.height / CGFloat(fretRows)
        func stringX(_ s: Int) -> CGFloat { gridRect.minX + CGFloat(s) * stringGap }
        func dotY(_ fret: Int) -> CGFloat {
            gridRect.minY + (CGFloat(fret - shape.baseFret) + 0.5) * fretGap
        }

        // Fretted notes inside the window.
        var notes: [(string: Int, fret: Int)] = []
        for (s, state) in shape.strings.enumerated() {
            if case .fretted(let f) = state,
               f >= shape.baseFret, f < shape.baseFret + fretRows {
                notes.append((s, f))
            }
        }
        guard !notes.isEmpty else {
            return HandPlan(tips: [], barre: nil, gridBottom: gridRect.maxY)
        }

        // Barre: ≥2 strings on the window's lowest fret spanning to the
        // highest played string → finger 1 lies flat across them.
        let minFret = notes.map(\.fret).min()!
        let atMin = notes.filter { $0.fret == minFret }
        let others = notes.filter { $0.fret != minFret }
        var tips: [CGPoint] = []
        var barre: ClosedRange<CGFloat>? = nil
        var remaining: [(string: Int, fret: Int)]

        if atMin.count >= 2 && !others.isEmpty {
            let lo = stringX(atMin.map(\.string).min()!)
            let hi = stringX(atMin.map(\.string).max()!)
            barre = lo...hi
            tips.append(CGPoint(x: (lo + hi) / 2, y: dotY(minFret)))
            remaining = others
        } else {
            remaining = notes
        }
        remaining.sort { ($0.fret, $0.string) < ($1.fret, $1.string) }
        for n in remaining.prefix(4 - tips.count) {
            tips.append(CGPoint(x: stringX(n.string), y: dotY(n.fret)))
        }
        // Unused fingers rest curled just above the knuckle line
        // (short stubs beside the last active finger).
        let restY = gridRect.maxY - size.width * 0.06
        var restX = (tips.last?.x ?? gridRect.midX)
        while tips.count < 4 {
            restX += stringGap * 0.9
            tips.append(CGPoint(x: min(restX, gridRect.maxX + stringGap * 0.4),
                                y: restY))
        }
        return HandPlan(tips: tips, barre: barre, gridBottom: gridRect.maxY)
    }
}

// MARK: - Animatable silhouette

/// The silhouette itself. `Animatable` over the four fingertips, so a
/// chord change animates each finger sliding/curling to its new spot.
struct HandSilhouetteView: View, Animatable {
    var plan: HandPlan

    // Animatable data = 4 fingertips (8 scalars) as nested pairs.
    typealias TipPair = AnimatablePair<CGFloat, CGFloat>
    typealias Tips4 = AnimatablePair<
        AnimatablePair<TipPair, TipPair>, AnimatablePair<TipPair, TipPair>
    >
    var animatableData: Tips4 {
        get {
            let t = paddedTips
            return AnimatablePair(
                AnimatablePair(TipPair(t[0].x, t[0].y), TipPair(t[1].x, t[1].y)),
                AnimatablePair(TipPair(t[2].x, t[2].y), TipPair(t[3].x, t[3].y))
            )
        }
        set {
            plan.tips = [
                CGPoint(x: newValue.first.first.first, y: newValue.first.first.second),
                CGPoint(x: newValue.first.second.first, y: newValue.first.second.second),
                CGPoint(x: newValue.second.first.first, y: newValue.second.first.second),
                CGPoint(x: newValue.second.second.first, y: newValue.second.second.second),
            ]
        }
    }

    private var paddedTips: [CGPoint] {
        var t = plan.tips
        while t.count < 4 { t.append(.zero) }
        return t
    }

    var body: some View {
        Canvas { ctx, size in
            let tips = paddedTips
            guard tips.contains(where: { $0 != .zero }) else { return }
            draw(ctx: ctx, size: size, tips: tips)
        }
        .allowsHitTesting(false)
    }

    private func draw(ctx: GraphicsContext, size: CGSize, tips: [CGPoint]) {
        // Dark gray with a visible rim — pure black vanished into the
        // card background.
        let fill = Color(red: 0.13, green: 0.13, blue: 0.17).opacity(0.95)
        let rim = Color.white.opacity(0.25)

        // Hand metrics scale with WIDTH (the board's musical geometry),
        // never card height — the Learn cards flex tall and a
        // height-based hand stretched into spaghetti.
        let unit = size.width

        // Knuckle baseline pinned just below the BOARD — real fretting
        // anatomy: the palm never floats up into the strings; fingers
        // are long and reach from under the neck to their frets.
        let gridBottom = plan.gridBottom > 0 ? plan.gridBottom : size.height * 0.9
        let knuckleY = gridBottom + unit * 0.09
        let xs = tips.map(\.x).sorted()
        let handCenterX = (xs[1] + xs[2]) / 2 + unit * 0.05

        var hand = Path()
        var fingers = Path()

        // Fingers: knuckle → mid → tip, tapered round-cap strokes with
        // a slight outward bow (curl).
        for (i, tip) in tips.enumerated() {
            let spread = CGFloat(i) - 1.5
            let knuckle = CGPoint(
                x: handCenterX + spread * unit * 0.16,
                y: knuckleY + abs(spread) * unit * 0.03
            )
            let mid = CGPoint(
                x: (knuckle.x + tip.x) / 2 + (tip.x - knuckle.x) * 0.12 + unit * 0.02,
                y: (knuckle.y + tip.y) / 2 + unit * 0.03
            )
            let wProx = unit * 0.095
            let wDist = unit * 0.072
            fingers.addPath(strokeSegment(from: knuckle, to: mid, width: wProx))
            fingers.addPath(strokeSegment(from: mid, to: tip, width: wDist))
            fingers.addEllipse(in: CGRect(
                x: tip.x - wDist / 2, y: tip.y - wDist / 2,
                width: wDist, height: wDist))
        }

        // Barre: finger 1 flattens into a bar across the strings.
        if let barre = plan.barre, let tip1 = tips.first {
            let w = unit * 0.10
            fingers.addRoundedRect(
                in: CGRect(
                    x: barre.lowerBound - w / 2, y: tip1.y - w / 2,
                    width: barre.upperBound - barre.lowerBound + w, height: w),
                cornerSize: CGSize(width: w / 2, height: w / 2))
        }

        // Palm: rounded mass at the knuckles; a straight wrist column
        // drops off the bottom, slightly right of palm center.
        let palmW = unit * 0.60
        let palmH = unit * 0.55
        hand.addEllipse(in: CGRect(
            x: handCenterX - palmW / 2, y: knuckleY - palmH * 0.22,
            width: palmW, height: palmH))
        let wristW = palmW * 0.72
        hand.addRoundedRect(
            in: CGRect(
                x: handCenterX - wristW / 2 + unit * 0.05,
                y: knuckleY + palmH * 0.10,
                width: wristW,
                height: max(unit * 0.6, size.height - knuckleY)),
            cornerSize: CGSize(width: wristW * 0.25, height: wristW * 0.25))

        // Palm dark, fingers a shade lighter — the fingers must read
        // against the dark board while the palm recedes.
        var glow = ctx
        glow.addFilter(.shadow(color: rim, radius: 2))
        glow.fill(hand, with: .color(fill))
        glow.fill(fingers, with: .color(
            Color(red: 0.20, green: 0.20, blue: 0.26).opacity(0.96)))
    }

    /// A finger segment as a filled round-capped stroke path.
    private func strokeSegment(from a: CGPoint, to b: CGPoint, width: CGFloat) -> Path {
        var line = Path()
        line.move(to: a)
        line.addLine(to: b)
        return line.strokedPath(StrokeStyle(lineWidth: width, lineCap: .round))
    }
}
