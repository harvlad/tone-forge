// HandSilhouette.swift
//
// The fretting-hand silhouette for the Learn tab's guitar-neck play
// view (the "Show hand" mode): a HORIZONTAL neck with a hand playing
// the song, per the approved sample design — dark wood board, fingers
// arching up from below the neck, numbered fingertips landing on the
// current chord, animating naturally to the next chord.
//
// No image assets: the hand is parametric (fingertips are the
// Animatable data), so it fits any chord shape at any fret window.

import SwiftUI
import ToneForgeEngine

// MARK: - Fingering (pure, geometry-free)

/// Standard chord-chart finger assignment: barre = finger 1 across the
/// window's lowest fret; remaining notes get fingers in (fret, string)
/// order. 1 = index … 4 = pinky.
enum ChordFingering {
    struct Note: Equatable {
        let string: Int   // 0 = low E … 5 = high e
        let fret: Int     // absolute fret
        let finger: Int   // 1…4
    }

    struct Result: Equatable {
        var notes: [Note]
        /// Strings covered by a finger-1 barre (at the window's lowest
        /// fret). Nil = no barre.
        var barreStrings: ClosedRange<Int>?
        var barreFret: Int?
    }

    static func assign(shape: GuitarChordShape, window: Int = 4) -> Result {
        var fretted: [(string: Int, fret: Int)] = []
        for (s, state) in shape.strings.enumerated() {
            if case .fretted(let f) = state,
               f >= shape.baseFret, f < shape.baseFret + window {
                fretted.append((s, f))
            }
        }
        guard !fretted.isEmpty else {
            return Result(notes: [], barreStrings: nil, barreFret: nil)
        }
        let minFret = fretted.map(\.fret).min()!
        let atMin = fretted.filter { $0.fret == minFret }
        let others = fretted.filter { $0.fret != minFret }

        var notes: [Note] = []
        var nextFinger = 1
        var barreStrings: ClosedRange<Int>? = nil
        var barreFret: Int? = nil
        var remaining: [(string: Int, fret: Int)]

        let atMinLo = atMin.map(\.string).min() ?? 0
        let atMinHi = atMin.map(\.string).max() ?? 0
        // A real barre SPANS the neck (>=3 strings apart). Two stray
        // notes on the same fret (D major's G+e at fret 2) are separate
        // fingers, not a barre.
        if atMin.count >= 2 && (atMinHi - atMinLo) >= 3 && !others.isEmpty {
            barreStrings = atMinLo...atMinHi
            barreFret = minFret
            for n in atMin {
                notes.append(Note(string: n.string, fret: n.fret, finger: 1))
            }
            nextFinger = 2
            remaining = others
        } else {
            remaining = fretted
        }
        remaining.sort { ($0.fret, $0.string) < ($1.fret, $1.string) }
        for n in remaining where nextFinger <= 4 {
            notes.append(Note(string: n.string, fret: n.fret, finger: nextFinger))
            nextFinger += 1
        }
        return Result(notes: notes, barreStrings: barreStrings, barreFret: barreFret)
    }
}

// MARK: - Horizontal neck geometry

/// Pixel geometry of the horizontal neck: nut at the LEFT, frets
/// increasing right, strings horizontal with low E at the BOTTOM
/// (player-mirror view — the hand reaches up from below the neck).
struct NeckGeometry: Equatable {
    let neck: CGRect
    let stringGap: CGFloat
    let fretW: CGFloat
    let baseFret: Int
    let window: Int

    init(size: CGSize, baseFret: Int, window: Int = 4) {
        // Right gutter for the x/o markers beside the nut (+ "3fr"
        // label); room below the neck for the hand's palm.
        let left: CGFloat = 10
        let right: CGFloat = 30
        let top: CGFloat = 8
        // Real-neck proportions: wide, not square — cap the height so
        // a tall host card yields a slim board with hand room below.
        let neckH = min(size.height * 0.60, size.width * 0.44)
        self.neck = CGRect(
            x: left, y: top,
            width: max(1, size.width - left - right), height: max(1, neckH))
        self.stringGap = neck.height / 6
        self.fretW = neck.width / CGFloat(window)
        self.baseFret = baseFret
        self.window = window
    }

    /// String row center. 0 = low E → BOTTOM row; 5 = high e → top.
    func stringY(_ s: Int) -> CGFloat {
        neck.minY + (CGFloat(5 - s) + 0.5) * stringGap
    }

    /// Dot center x for an absolute fret. Nut is at the RIGHT (sample
    /// design: headstock right), frets increase leftward.
    func fretX(_ fret: Int) -> CGFloat {
        neck.maxX - (CGFloat(fret - baseFret) + 0.5) * fretW
    }

    /// X of fret wire f (0 = nut at the right edge).
    func wireX(_ f: Int) -> CGFloat {
        neck.maxX - CGFloat(f) * fretW
    }
}

// MARK: - Hand plan (tips in neck coordinates)

struct HandPlan: Equatable {
    /// Fingertips, index 0…3 = fingers 1…4 (unused fingers rest curled
    /// below the neck).
    var tips: [CGPoint]
    /// Barre: finger 1 lies flat across strings — a vertical bar.
    var barreRect: CGRect?
    /// Bottom edge of the neck: the hand's anatomy anchors here.
    var neckBottom: CGFloat
    /// String spacing — the anatomical unit (finger width ≈ one string
    /// gap, like a real hand on a real neck).
    var fingerScale: CGFloat = 12

    static func plan(
        fingering: ChordFingering.Result, geo: NeckGeometry
    ) -> HandPlan {
        var tipByFinger: [Int: CGPoint] = [:]
        var barreRect: CGRect? = nil

        if let bs = fingering.barreStrings, let bf = fingering.barreFret {
            let x = geo.fretX(bf)
            let yTop = geo.stringY(bs.upperBound) - geo.stringGap * 0.4
            let yBot = geo.stringY(bs.lowerBound) + geo.stringGap * 0.4
            let w = geo.fretW * 0.30
            barreRect = CGRect(x: x - w / 2, y: yTop, width: w, height: yBot - yTop)
            // Finger 1's tip = top of the barre (it lies flat downward).
            tipByFinger[1] = CGPoint(x: x, y: yTop + geo.stringGap * 0.3)
        }
        for n in fingering.notes where tipByFinger[n.finger] == nil {
            tipByFinger[n.finger] = CGPoint(
                x: geo.fretX(n.fret), y: geo.stringY(n.string))
        }

        // Rest positions: curled just under the neck, spread rightward
        // from the used fingers.
        var tips: [CGPoint] = []
        let usedXs = tipByFinger.values.map(\.x)
        var restX = (usedXs.max() ?? geo.neck.midX)
        for finger in 1...4 {
            if let t = tipByFinger[finger] {
                tips.append(t)
            } else {
                restX += geo.fretW * 0.4
                tips.append(CGPoint(
                    x: min(restX, geo.neck.maxX - 4),
                    y: geo.neck.maxY - geo.stringGap * 0.4))
            }
        }
        return HandPlan(tips: tips, barreRect: barreRect,
                        neckBottom: geo.neck.maxY, fingerScale: geo.stringGap)
    }
}

// MARK: - Animatable silhouette

/// The hand. `Animatable` over the four fingertips: a chord change
/// animates each finger to its new spot like a hand moving.
struct HandSilhouetteView: View, Animatable {
    var plan: HandPlan

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
        let fill = Color(red: 0.13, green: 0.13, blue: 0.17).opacity(0.95)
        let fingerFill = Color(red: 0.19, green: 0.19, blue: 0.25).opacity(0.96)
        let rim = Color.white.opacity(0.20)

        // Anatomical unit: one string gap ≈ one finger width, exactly
        // like a real hand on a real neck. Everything scales off it.
        let g = max(6, plan.fingerScale)

        // Knuckles sit about two string-gaps below the board.
        let knuckleY = plan.neckBottom + g * 2.0
        let xs = tips.map(\.x).sorted()
        let handCenterX = (xs[1] + xs[2]) / 2 + g * 0.3

        var hand = Path()
        var fingers = Path()

        // Knuckles rank-matched to tip order: parallel fingers, no
        // crossings (finger identity lives on the numbered dots).
        let order = tips.indices.sorted { tips[$0].x < tips[$1].x }
        var rankOf: [Int: Int] = [:]
        for (rank, idx) in order.enumerated() { rankOf[idx] = rank }

        for (i, tip) in tips.enumerated() {
            let spread = CGFloat(rankOf[i] ?? i) - 1.5
            let knuckle = CGPoint(
                x: handCenterX + spread * g * 1.15,
                y: knuckleY + abs(spread) * g * 0.25
            )
            // Three phalanges along a gentle bow — near-constant width
            // (real fingers barely taper), round fingertip.
            let bow = g * 0.35
            let j1 = CGPoint(
                x: knuckle.x + (tip.x - knuckle.x) * 0.38 + bow * 0.6,
                y: knuckle.y + (tip.y - knuckle.y) * 0.38 + bow * 0.3)
            let j2 = CGPoint(
                x: knuckle.x + (tip.x - knuckle.x) * 0.72 + bow * 0.35,
                y: knuckle.y + (tip.y - knuckle.y) * 0.72)
            let wA = g * 0.92
            let wB = g * 0.84
            let wC = g * 0.76
            fingers.addPath(segment(from: knuckle, to: j1, width: wA))
            fingers.addPath(segment(from: j1, to: j2, width: wB))
            fingers.addPath(segment(from: j2, to: tip, width: wC))
        }

        // Barre: finger 1 flat across the strings (vertical bar).
        if let b = plan.barreRect {
            fingers.addRoundedRect(
                in: b.insetBy(dx: -g * 0.05, dy: 0),
                cornerSize: CGSize(width: b.width / 2, height: b.width / 2))
        }

        // Palm: hand-sized (≈ 3.4 fingers wide), mostly below the neck.
        let palmW = g * 3.9
        let palmH = g * 3.4
        hand.addEllipse(in: CGRect(
            x: handCenterX - palmW / 2, y: knuckleY - g * 0.5,
            width: palmW, height: palmH))
        // Wrist: angled column off the bottom-right (forearm).
        var wrist = Path()
        let wTop = knuckleY + palmH * 0.55
        wrist.move(to: CGPoint(x: handCenterX - palmW * 0.30, y: wTop))
        wrist.addLine(to: CGPoint(x: handCenterX + palmW * 0.38, y: wTop - g * 0.3))
        wrist.addLine(to: CGPoint(x: handCenterX + palmW * 0.70, y: size.height + g))
        wrist.addLine(to: CGPoint(x: handCenterX + palmW * 0.02, y: size.height + g))
        wrist.closeSubpath()
        hand.addPath(wrist)

        var glow = ctx
        glow.addFilter(.shadow(color: rim, radius: 2))
        glow.fill(hand, with: .color(fill))
        glow.fill(fingers, with: .color(fingerFill))
    }

    private func segment(from a: CGPoint, to b: CGPoint, width: CGFloat) -> Path {
        var line = Path()
        line.move(to: a)
        line.addLine(to: b)
        return line.strokedPath(StrokeStyle(lineWidth: width, lineCap: .round))
    }
}
