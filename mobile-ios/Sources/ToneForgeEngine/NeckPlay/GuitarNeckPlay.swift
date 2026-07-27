// GuitarNeckPlay.swift  (ToneForgeEngine)
//
// Shared guitar-neck play surface: a horizontal neck (nut RIGHT, per
// the sample design) with an anatomical silhouette hand playing the
// current chord — used by the iOS Learn tab and the desktop Rehearsal
// screen. Pure SwiftUI + Canvas, no assets, dark-theme palette.

import SwiftUI
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

/// Dark-mode palette for the neck surface (both apps are dark-only).
enum NeckPalette {
    static let textPrimary = Color.white
    static let textSecondary = Color(red: 0.63, green: 0.63, blue: 0.67)
}



// MARK: - Fingering (pure, geometry-free)

/// Standard chord-chart finger assignment: barre = finger 1 across the
/// window's lowest fret; remaining notes get fingers in (fret, string)
/// order. 1 = index … 4 = pinky.
public enum ChordFingering {
    public struct Note: Equatable {
        public let string: Int   // 0 = low E … 5 = high e
        public let fret: Int     // absolute fret
        public let finger: Int   // 1…4
    }

    public struct Result: Equatable {
        public var notes: [Note]
        /// Strings covered by a finger-1 barre (at the window's lowest
        /// fret). Nil = no barre.
        public var barreStrings: ClosedRange<Int>?
        public var barreFret: Int?

        public init(notes: [Note], barreStrings: ClosedRange<Int>?, barreFret: Int?) {
            self.notes = notes
            self.barreStrings = barreStrings
            self.barreFret = barreFret
        }
    }

    public static func assign(shape: GuitarChordShape, window: Int = 4) -> Result {
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
public struct NeckGeometry: Equatable {
    public let neck: CGRect
    public let stringGap: CGFloat
    public let fretW: CGFloat
    public let baseFret: Int
    public let window: Int

    public init(size: CGSize, baseFret: Int, window: Int = 4) {
        // MOCK proportions, measured from the approved design: one
        // fret column ≈ 10 string gaps, hand ≈ 2.2 frets wide. Solve
        // the string gap from whichever axis constrains it and CENTER
        // the board instead of stretching it across the surface.
        let gutter: CGFloat = 40   // x/o markers beside the nut
        let top: CGFloat = 8
        let fretPerGap: CGFloat = 10
        let gap = max(4, min(
            (size.width - gutter - 10) / (CGFloat(window) * fretPerGap),
            size.height * 0.55 / 6))
        let neckW = CGFloat(window) * fretPerGap * gap
        let neckH = gap * 6
        let x = max(10, (size.width - gutter - neckW) / 2)
        self.neck = CGRect(x: x, y: top, width: neckW, height: neckH)
        self.stringGap = gap
        self.fretW = fretPerGap * gap
        self.baseFret = baseFret
        self.window = window
    }

    /// String row center. 0 = low E → TOP row; 5 = high e → bottom
    /// (audience view of a right-handed guitar, matching the nut-right
    /// orientation).
    public func stringY(_ s: Int) -> CGFloat {
        neck.minY + (CGFloat(s) + 0.5) * stringGap
    }

    /// Dot center x for an absolute fret. Nut is at the RIGHT (sample
    /// design: headstock right), frets increase leftward.
    public func fretX(_ fret: Int) -> CGFloat {
        neck.maxX - (CGFloat(fret - baseFret) + 0.5) * fretW
    }

    /// X of fret wire f (0 = nut at the right edge).
    public func wireX(_ f: Int) -> CGFloat {
        neck.maxX - CGFloat(f) * fretW
    }
}

// MARK: - Hand plan (tips in neck coordinates)

public struct HandPlan: Equatable {
    /// Fingertips, index 0…3 = fingers 1…4 (unused fingers rest curled
    /// below the neck).
    public var tips: [CGPoint]
    /// Barre: finger 1 lies flat across strings — a vertical bar.
    public var barreRect: CGRect?
    /// Bottom edge of the neck: the hand's anatomy anchors here.
    public var neckBottom: CGFloat
    /// String spacing — the anatomical unit (finger width ≈ one string
    /// gap, like a real hand on a real neck).
    public var fingerScale: CGFloat = 12
    /// Fingers (1…4) that actually fret a note.
    public var activeFingers: Set<Int> = []
    /// Fret-window column width — sizes the hand like the mock (a hand
    /// spans about three frets).
    public var fretWidth: CGFloat = 40

    public static func plan(
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
        // from the used fingers. Reach differs per finger — the pinky
        // barely clears the board edge, the middle finger hovers
        // deepest (real relative finger lengths).
        let restReach: [CGFloat] = [4.0, 4.8, 4.3, 2.6]  // fingers 1…4
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
                    y: geo.neck.maxY - geo.stringGap * restReach[finger - 1]))
            }
        }
        return HandPlan(tips: tips, barreRect: barreRect,
                        neckBottom: geo.neck.maxY, fingerScale: geo.stringGap,
                        activeFingers: Set(tipByFinger.keys),
                        fretWidth: geo.fretW)
    }
}

// MARK: - Animatable silhouette

/// The hand. `Animatable` over the four fingertips: a chord change
/// animates each finger to its new spot like a hand moving.
public struct HandSilhouetteView: View, Animatable {
    public var plan: HandPlan
    /// Looper choreography: true while the hand is lifted off the
    /// strings mid-transition (drops back down on landing).
    public var lifted: Bool = false

    public init(plan: HandPlan, lifted: Bool = false) {
        self.plan = plan
        self.lifted = lifted
    }

    public typealias TipPair = AnimatablePair<CGFloat, CGFloat>
    public typealias Tips4 = AnimatablePair<
        AnimatablePair<TipPair, TipPair>, AnimatablePair<TipPair, TipPair>
    >
    public var animatableData: Tips4 {
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

    public var body: some View {
        Canvas { ctx, size in
            let tips = paddedTips
            guard tips.contains(where: { $0 != .zero }) else { return }
            draw(ctx: ctx, size: size, tips: tips)
        }
        .allowsHitTesting(false)
    }

    private func draw(ctx: GraphicsContext, size: CGSize, tips: [CGPoint]) {
        // Naturally animating VECTOR hand, styled like the mockup:
        // dark silhouette with a bright rim, one smooth contour (palm,
        // webbing, four fingers with rounded tips). Fingertips are the
        // animatable inputs, so each finger independently reaches its
        // dot and the whole hand re-solves every frame. Thumb is
        // behind the neck (hidden).
        let g = max(4, plan.fingerScale)

        var lift: CGFloat = 0
        if lifted { lift = g * 2.4 }

        // Rank fingers left→right for the contour walk; identity (for
        // width) stays with the finger number.
        let order = tips.indices.sorted { tips[$0].x < tips[$1].x }
        // Mock-measured finger widths (× string gap).
        let widthByFinger: [CGFloat] = [3.0, 3.2, 2.9, 2.4]

        // Knuckle ridge: arched, below the board.
        let xs = tips.map(\.x).sorted()
        let centerX = (xs[1] + xs[2]) / 2
        let knuckleBaseY = plan.neckBottom + g * 3.5 + lift

        struct Finger {
            var spine: [CGPoint]   // sampled base→tip
            var width: CGFloat
        }
        var fingers: [Finger] = []
        for (rank, idx) in order.enumerated() {
            let tip = CGPoint(x: tips[idx].x, y: tips[idx].y + lift)
            let spread = CGFloat(rank) - 1.5
            let arc = g * 0.55 * sin(.pi * (CGFloat(rank) + 0.5) / 4)
            let knuckle = CGPoint(
                x: centerX + spread * g * 3.4,
                y: knuckleBaseY - arc)
            // Gentle bow toward the nut side.
            let ctrl = CGPoint(
                x: (knuckle.x + tip.x) / 2 + (tip.x - knuckle.x) * 0.10 + g * 0.35,
                y: (knuckle.y + tip.y) / 2 + g * 0.25)
            var spine: [CGPoint] = []
            let n = 12
            for k in 0...n {
                let t = CGFloat(k) / CGFloat(n)
                let mt = 1 - t
                spine.append(CGPoint(
                    x: mt*mt*knuckle.x + 2*mt*t*ctrl.x + t*t*tip.x,
                    y: mt*mt*knuckle.y + 2*mt*t*ctrl.y + t*t*tip.y))
            }
            fingers.append(Finger(spine: spine, width: g * widthByFinger[idx]))
        }

        func normal(_ s: [CGPoint], _ k: Int) -> CGPoint {
            let a = s[max(0, k - 1)], b = s[min(s.count - 1, k + 1)]
            let dx = b.x - a.x, dy = b.y - a.y
            let len = max(0.001, sqrt(dx*dx + dy*dy))
            return CGPoint(x: -dy / len, y: dx / len)
        }
        // Offset point on side sgn (-1 = left, +1 = right), tapering
        // toward a slightly slimmer tip.
        func side(_ f: Finger, _ k: Int, _ sgn: CGFloat) -> CGPoint {
            let t = CGFloat(k) / CGFloat(f.spine.count - 1)
            let w = f.width * (0.52 - 0.10 * t)
            let nrm = normal(f.spine, k)
            return CGPoint(
                x: f.spine[k].x + nrm.x * w * sgn,
                y: f.spine[k].y + nrm.y * w * sgn)
        }

        let emerge = 2        // spine index where fingers separate
        var hand = Path()

        // Wrist left, up the palm's index-side edge.
        // Palm ends ~4.5 gaps under the knuckles, tapering into a
        // narrower forearm that runs off-canvas.
        let palmBottomY = knuckleBaseY + g * 4.4
        let forearmY = size.height + g * 2
        let firstL = side(fingers[0], emerge, -1)
        hand.move(to: CGPoint(x: centerX - g * 3.1, y: forearmY))
        hand.addQuadCurve(
            to: CGPoint(x: centerX - g * 5.9, y: palmBottomY),
            control: CGPoint(x: centerX - g * 3.6, y: palmBottomY + g * 1.6))
        hand.addQuadCurve(
            to: firstL,
            control: CGPoint(x: centerX - g * 7.0, y: knuckleBaseY + g * 0.4))

        for (i, f) in fingers.enumerated() {
            // Left edge up.
            if i > 0 { }
            for k in stride(from: emerge, through: f.spine.count - 1, by: 1)
            where k > emerge {
                hand.addLine(to: side(f, k, -1))
            }
            // Rounded tip.
            let tipL = side(f, f.spine.count - 1, -1)
            let tipR = side(f, f.spine.count - 1, +1)
            let tip = f.spine[f.spine.count - 1]
            let nrm = normal(f.spine, f.spine.count - 1)
            let capOut = CGPoint(
                x: tip.x + (tip.x - f.spine[f.spine.count - 2].x) * 0.55,
                y: tip.y + (tip.y - f.spine[f.spine.count - 2].y) * 0.55)
            _ = nrm; _ = tipL
            hand.addQuadCurve(to: tipR, control: capOut)
            // Right edge down.
            for k in stride(from: f.spine.count - 2, through: emerge, by: -1) {
                hand.addLine(to: side(f, k, +1))
            }
            // Webbing valley to the next finger.
            if i < fingers.count - 1 {
                let nextL = side(fingers[i + 1], emerge, -1)
                let hereR = side(f, emerge, +1)
                let valley = CGPoint(
                    x: (hereR.x + nextL.x) / 2,
                    y: max(hereR.y, nextL.y) + g * 0.45)
                hand.addQuadCurve(to: nextL, control: valley)
            }
        }

        // Pinky-side palm edge, then taper into the forearm.
        hand.addQuadCurve(
            to: CGPoint(x: centerX + g * 5.7, y: palmBottomY),
            control: CGPoint(x: centerX + g * 7.0, y: knuckleBaseY + g * 1.0))
        hand.addQuadCurve(
            to: CGPoint(x: centerX + g * 2.9, y: forearmY),
            control: CGPoint(x: centerX + g * 3.4, y: palmBottomY + g * 1.6))
        hand.addLine(to: CGPoint(x: centerX - g * 3.1, y: forearmY))
        hand.closeSubpath()

        // Barre: finger 1 lies flat — a capsule under the main contour.
        if let b = plan.barreRect {
            var bar = Path()
            bar.addRoundedRect(
                in: b.insetBy(dx: -g * 0.2, dy: 0).offsetBy(dx: 0, dy: lift),
                cornerSize: CGSize(width: b.width / 2, height: b.width / 2))
            var glowB = ctx
            glowB.addFilter(.shadow(color: .white.opacity(0.20), radius: 1.5))
            glowB.fill(bar, with: .color(Color(red: 0.10, green: 0.10, blue: 0.14).opacity(0.96)))
            glowB.stroke(bar, with: .color(.white.opacity(0.30)), lineWidth: 1.2)
        }

        var glow = ctx
        glow.addFilter(.shadow(color: .white.opacity(0.18), radius: 2.5))
        glow.fill(hand, with: .color(Color(red: 0.085, green: 0.085, blue: 0.12).opacity(0.96)))
        glow.stroke(hand, with: .color(.white.opacity(0.32)), lineWidth: 1.3)
    }

    private func segment(from a: CGPoint, to b: CGPoint, width: CGFloat) -> Path {
        var line = Path()
        line.move(to: a)
        line.addLine(to: b)
        return line.strokedPath(StrokeStyle(lineWidth: width, lineCap: .round))
    }
}


// MARK: - Composite surface (board + hand + dots)

/// The complete neck-play surface: wood board, animated hand, numbered
/// dots. Hosts add their own chord header/chrome around it.
public struct GuitarNeckPlaySurface: View {
    let current: String?
    /// Optional transition target (Phase 2): colors the dots by the
    /// Stay/Move/Lift role and draws the movement overlay toward this
    /// chord. Nil = plain play surface.
    let transitionTo: String?
    /// Optional separate target for the HAND (looper choreography:
    /// the hand animates to this chord first, the host then updates
    /// `current` so the dots follow once the hand lands). Nil = hand
    /// plays `current`.
    let handTarget: String?
    /// True while the hand is lifted off the strings (mid-transition).
    let handLifted: Bool

    public init(
        current: String?, transitionTo: String? = nil,
        handTarget: String? = nil, handLifted: Bool = false
    ) {
        self.current = current
        self.transitionTo = transitionTo
        self.handTarget = handTarget
        self.handLifted = handLifted
    }

    public var body: some View {
        GeometryReader { g in
            let shape = current.flatMap { GuitarVoicing.shape(symbol: $0) }
            let geo = NeckGeometry(size: g.size, baseFret: shape?.baseFret ?? 1)
            let fingering = shape.map { ChordFingering.assign(shape: $0) }
                ?? ChordFingering.Result(notes: [], barreStrings: nil, barreFret: nil)
            let handShape = (handTarget ?? current)
                .flatMap { GuitarVoicing.shape(symbol: $0) } ?? shape
            let handFingering = handShape.map { ChordFingering.assign(shape: $0) }
                ?? fingering
            let plan = HandPlan.plan(fingering: handFingering, geo: geo)
            // Transition analysis only when both shapes live in the
            // same fret window (open-position pairs — the common case).
            let nextFingering: ChordFingering.Result? = transitionTo
                .flatMap { GuitarVoicing.shape(symbol: $0) }
                .flatMap { ns in
                    ns.baseFret == (shape?.baseFret ?? 1)
                        ? ChordFingering.assign(shape: ns) : nil
                }
            let transition = nextFingering.map {
                ChordTransition.analyze(from: fingering, to: $0)
            }

            ZStack {
                // Real fretting-hand layering: wood, then the hand,
                // then strings/wires OVER the fingers (a hand sits
                // behind the strings), then dots + arrows on top.
                NeckBoardCanvas(shape: shape, geo: geo, layer: .wood)
                HandSilhouetteView(plan: plan, lifted: handLifted)
                    .animation(.easeInOut(duration: 0.32), value: plan)
                    .animation(.easeInOut(duration: 0.16), value: handLifted)
                NeckBoardCanvas(shape: shape, geo: geo, layer: .hardware)
                NeckDotsCanvas(
                    shape: shape, geo: geo, fingering: fingering,
                    roles: transition?.rolesByFinger ?? [:])
                if let transition {
                    TransitionOverlayCanvas(transition: transition, geo: geo)
                }
            }
            .clipped()
        }
    }
}

// MARK: - Board (wood, frets, strings, inlays, markers)


private struct NeckBoardCanvas: View {
    let shape: GuitarChordShape?
    let geo: NeckGeometry
    var layer: Layer = .wood

    enum Layer { case wood, hardware }

    var body: some View {
        Canvas { ctx, _ in
            let neck = geo.neck

            if layer == .hardware {
                drawHardware(ctx: ctx, neck: neck)
                return
            }

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
                let cx = geo.fretX(geo.baseFret + col)
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

        }
    }

    /// Strings, fret wires, nut, markers — drawn OVER the hand so the
    /// fingers sit behind the strings like a real fretting hand.
    private func drawHardware(ctx: GraphicsContext, neck: CGRect) {
            // Nut (RIGHT, thick when open position) + fret wires —
            // headstock-right orientation per the sample design.
            for f in 0...geo.window {
                let x = geo.wireX(f)
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

            // Strings: low E (top) thickest.
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

            // x / o markers beside the nut, per string.
            if let shape {
                for (s, state) in shape.strings.enumerated() {
                    let at = CGPoint(x: neck.maxX + 13, y: geo.stringY(s))
                    switch state {
                    case .muted:
                        ctx.draw(
                            Text("×").font(.system(size: 12, weight: .semibold))
                                .foregroundColor(NeckPalette.textSecondary),
                            at: at)
                    case .open:
                        let r: CGFloat = 3.6
                        ctx.stroke(
                            Path(ellipseIn: CGRect(
                                x: at.x - r, y: at.y - r, width: r * 2, height: r * 2)),
                            with: .color(NeckPalette.textPrimary.opacity(0.8)),
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
                            .foregroundColor(NeckPalette.textSecondary),
                        at: CGPoint(
                            x: geo.fretX(shape.baseFret),
                            y: neck.maxY + 10))
                }
            }
    }
}


// MARK: - Numbered dots (drawn above the hand)


private struct NeckDotsCanvas: View {
    let shape: GuitarChordShape?
    let geo: NeckGeometry
    let fingering: ChordFingering.Result
    /// Transition roles per finger — colors the dots (Stay purple,
    /// Move blue, Lift green). Empty = plain accent dots.
    var roles: [Int: FingerRole] = [:]

    var body: some View {
        Canvas { ctx, _ in
            guard shape != nil else { return }
            let r = geo.stringGap * 0.42
            for n in fingering.notes {
                let c = CGPoint(x: geo.fretX(n.fret), y: geo.stringY(n.string))
                ctx.fill(
                    Path(ellipseIn: CGRect(
                        x: c.x - r, y: c.y - r, width: r * 2, height: r * 2)),
                    with: .color(roles[n.finger]?.color ?? Color.accentColor))
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
