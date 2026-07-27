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

    /// Curated fingerings for common OPEN shapes where the heuristic's
    /// (fret, string) ordering deviates from the canonical lesson
    /// fingering. Keyed by the absolute shape pattern (x = muted,
    /// 0 = open, digit = fret); value = finger per string (nil for
    /// unfretted). Movable barre shapes already come out right from
    /// the heuristic, so they're not listed — same-shape transposition
    /// keeps this table tiny.
    static let curated: [String: [Int?]] = [
        // Em: canonical 2-3 anchor fingers (heuristic said 1-2).
        "022000": [nil, 2, 3, nil, nil, nil],
        // Em7 (single-finger form): middle finger, not index.
        "020000": [nil, 2, nil, nil, nil, nil],
        // A major: index-middle-ring across fret 2.
        "x02220": [nil, nil, 1, 2, 3, nil],
        // Asus2: canonical 2-3.
        "x02200": [nil, nil, 2, 3, nil, nil],
        // Cadd9: 2-1 low pair + 3-4 anchors.
        "x32033": [nil, 3, 2, nil, 4, 4],
        // Dsus4: 1-3-4 (pinky adds the sus note).
        "xx0233": [nil, nil, nil, 1, 3, 4],
        // G "rock" 4-finger form: 2-1 + 3-4 anchors.
        "320033": [2, 1, nil, nil, 3, 4],
    ]

    static func patternKey(_ shape: GuitarChordShape) -> String {
        shape.strings.map { state in
            switch state {
            case .muted: return "x"
            case .open: return "0"
            case .fretted(let f): return String(f)
            }
        }.joined()
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

        // Curated override first — the canonical lesson fingering for
        // shapes where convention differs from the heuristic.
        if let fingers = curated[patternKey(shape)] {
            var notes: [Note] = []
            for n in fretted {
                if let finger = fingers[n.string] {
                    notes.append(Note(string: n.string, fret: n.fret, finger: finger))
                }
            }
            if !notes.isEmpty {
                return Result(notes: notes, barreStrings: nil, barreFret: nil)
            }
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
        // E-shape minor barre (Gm/F#m…): the two stacked notes above
        // the barre are ring+pinky by convention, not middle+ring.
        if barreFret != nil, remaining.count == 2,
           remaining[0].fret == remaining[1].fret,
           abs(remaining[0].string - remaining[1].string) == 1 {
            nextFinger = 3
        }
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
            // Low string index = TOP row (audience view): lowerBound
            // is the top of the capsule, upperBound the bottom.
            let yTop = geo.stringY(bs.lowerBound) - geo.stringGap * 0.4
            let yBot = geo.stringY(bs.upperBound) + geo.stringGap * 0.4
            let w = geo.stringGap * 2.1   // one finger wide
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
        let restReach: [CGFloat] = [3.2, 4.0, 3.5, 2.0]  // fingers 1…4
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
        // Naturally animating VECTOR hand, styled like the mockup.
        // Layered composition (like the design's rim-lit photo hand):
        // palm/forearm silhouette behind, then each finger as its OWN
        // filled+stroked shape drawn over it, so fingers can stack,
        // converge on one fret column, or cross without any contour
        // self-intersection — the overlap just reads as one finger in
        // front of another. Fingertips are the animatable inputs; every
        // finger keeps its tip exactly on its dot. Thumb is behind the
        // neck (hidden).
        let g = max(4, plan.fingerScale)

        var lift: CGFloat = 0
        if lifted { lift = g * 2.4 }

        // Rank fingers left→right; identity (width) stays with the
        // finger number.
        let order = tips.indices.sorted { tips[$0].x < tips[$1].x }
        // Mock-measured finger widths (× string gap).
        let widthByFinger: [CGFloat] = [1.9, 2.0, 1.85, 1.5]

        // Knuckle ridge: arched, close under the board — short free
        // fingers, most mass in the palm (mock proportions). Center on
        // the SPAN (barre chords put one tip frets away from cluster).
        let xs = tips.map(\.x).sorted()
        let centerX = (xs[0] + xs[3]) / 2
        let knuckleBaseY = plan.neckBottom + g * 2.9 + lift

        struct Finger {
            var spine: [CGPoint]   // sampled base→tip
            var width: CGFloat
        }
        var fingers: [Finger] = []
        for (rank, idx) in order.enumerated() {
            // The DOT sits on the fingertip pad's center: retract the
            // spine end so the rounded cap wraps around the dot
            // instead of the pad overshooting past it.
            let dot = CGPoint(x: tips[idx].x, y: tips[idx].y + lift)
            let tip = CGPoint(x: dot.x, y: dot.y + g * 0.30)
            let spread = CGFloat(rank) - 1.5
            let arc = g * 0.55 * sin(.pi * (CGFloat(rank) + 0.5) / 4)
            // Knuckles FOLLOW their tips (with a pull toward the palm
            // center) so wide chords fan the whole hand instead of
            // stretching one finger into a tentacle.
            let idealX = centerX + spread * g * 1.6
            // Clamp the knuckle under its tip: a finger never leans
            // more than ~2 gaps sideways (stops near-horizontal planks
            // on wide chords where the span center is frets away).
            let blendX = tip.x * 0.62 + idealX * 0.38
            // Spine base is BURIED under the palm ridge (arc keeps the
            // visible emergence arched) so the fill's base cap never
            // peeks past the palm edge.
            let knuckle = CGPoint(
                x: min(max(blendX, tip.x - g * 2.0), tip.x + g * 2.0),
                y: knuckleBaseY + g * 1.0 - arc)
            // Cubic spine: launch upward off the knuckle, bow gently
            // toward the nut, then drop the pad onto the string — the
            // mock's soft C-curve rather than a straight strut.
            let bow = g * 0.7
            let c1 = CGPoint(
                x: knuckle.x + (tip.x - knuckle.x) * 0.15 + bow * 0.45,
                y: knuckle.y + (tip.y - knuckle.y) * 0.38)
            let c2 = CGPoint(
                x: knuckle.x + (tip.x - knuckle.x) * 0.72 + bow,
                y: knuckle.y + (tip.y - knuckle.y) * 0.78)
            var spine: [CGPoint] = []
            let n = 14
            for k in 0...n {
                let t = CGFloat(k) / CGFloat(n)
                let mt = 1 - t
                let x = mt*mt*mt*knuckle.x + 3*mt*mt*t*c1.x
                    + 3*mt*t*t*c2.x + t*t*t*tip.x
                let y = mt*mt*mt*knuckle.y + 3*mt*mt*t*c1.y
                    + 3*mt*t*t*c2.y + t*t*t*tip.y
                spine.append(CGPoint(x: x, y: y))
            }
            fingers.append(Finger(spine: spine, width: g * widthByFinger[idx]))
        }

        func normal(_ s: [CGPoint], _ k: Int) -> CGPoint {
            let a = s[max(0, k - 1)], b = s[min(s.count - 1, k + 1)]
            let dx = b.x - a.x, dy = b.y - a.y
            let len = max(0.001, sqrt(dx*dx + dy*dy))
            return CGPoint(x: -dy / len, y: dx / len)
        }
        // Offset point on side sgn (-1 = left, +1 = right). Width
        // profile: broad base, steady taper, tiny bulge at the middle
        // joint, slimmer rounded pad at the tip.
        func side(_ f: Finger, _ k: Int, _ sgn: CGFloat) -> CGPoint {
            let t = CGFloat(k) / CGFloat(f.spine.count - 1)
            var w = f.width * (0.55 - 0.15 * t)
            w *= 1 + 0.05 * sin(.pi * min(1, t / 0.9))   // joint fullness
            let nrm = normal(f.spine, k)
            return CGPoint(
                x: f.spine[k].x + nrm.x * w * sgn,
                y: f.spine[k].y + nrm.y * w * sgn)
        }
        // A finger as its own closed capsule: up the left side, round
        // the tip, back down the right side. The closed FILL runs from
        // the base (buried in the palm); the RIM stroke starts above
        // the palm edge so no hard base line cuts across the palm.
        func fingerPath(_ f: Finger, from start: Int) -> Path {
            var p = Path()
            let last = f.spine.count - 1
            p.move(to: side(f, start, -1))
            for k in (start + 1)...last { p.addLine(to: side(f, k, -1)) }
            let tip = f.spine[last], prev = f.spine[last - 1]
            let capOut = CGPoint(
                x: tip.x + (tip.x - prev.x) * 0.45,
                y: tip.y + (tip.y - prev.y) * 0.45)
            p.addQuadCurve(to: side(f, last, +1), control: capOut)
            for k in stride(from: last - 1, through: start, by: -1) {
                p.addLine(to: side(f, k, +1))
            }
            return p
        }

        // Palm/forearm silhouette: knuckle-arched top edge, sides down
        // to the palm bottom, tapering into a forearm that runs
        // off-canvas angled toward the pinky side (mock forearm).
        let kXs = fingers.map { $0.spine[0].x }
        let palmL = (kXs.min() ?? centerX) - g * 1.2
        let palmR = (kXs.max() ?? centerX) + g * 1.1
        let palmMid = (palmL + palmR) / 2
        let palmTopY = knuckleBaseY - g * 0.5
        let palmBottomY = knuckleBaseY + g * 2.6
        let forearmY = size.height + g * 2
        let wristX = palmMid + g * 2.2
        var palm = Path()
        palm.move(to: CGPoint(x: wristX - g * 1.4, y: forearmY))
        palm.addQuadCurve(
            to: CGPoint(x: palmL, y: palmBottomY),
            control: CGPoint(x: palmMid - g * 3.2, y: palmBottomY + g * 1.6))
        palm.addQuadCurve(
            to: CGPoint(x: palmL + g * 0.5, y: palmTopY),
            control: CGPoint(x: palmL - g * 0.5, y: knuckleBaseY + g * 0.7))
        // Knuckle ridge arch across the top.
        palm.addQuadCurve(
            to: CGPoint(x: palmR - g * 0.5, y: palmTopY),
            control: CGPoint(x: palmMid, y: palmTopY - g * 1.0))
        palm.addQuadCurve(
            to: CGPoint(x: palmR, y: palmBottomY),
            control: CGPoint(x: palmR + g * 0.6, y: knuckleBaseY + g * 0.9))
        palm.addQuadCurve(
            to: CGPoint(x: wristX + g * 1.3, y: forearmY),
            control: CGPoint(x: palmMid + g * 3.4, y: palmBottomY + g * 1.6))
        palm.closeSubpath()

        // Opaque: layered shapes must not darken where they overlap.
        let skin = Color(red: 0.085, green: 0.085, blue: 0.12)
        let rim = Color.white.opacity(0.32)
        let rimStyle = StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round)

        // Fills are PLAIN (a fill with a shadow filter would halo onto
        // shapes behind it); only rim strokes glow.
        var glow = ctx
        glow.addFilter(.shadow(color: .white.opacity(0.18), radius: 2.5))
        ctx.fill(palm, with: .color(skin))
        glow.stroke(palm, with: .color(rim), style: rimStyle)

        // Barre: finger 1 lies flat — a capsule behind the fingers.
        if let b = plan.barreRect {
            var bar = Path()
            bar.addRoundedRect(
                in: b.insetBy(dx: -g * 0.2, dy: 0).offsetBy(dx: 0, dy: lift),
                cornerSize: CGSize(width: b.width / 2, height: b.width / 2))
            ctx.fill(bar, with: .color(skin))
            glow.stroke(bar, with: .color(rim), lineWidth: 1.2)
        }

        // Fingers back-to-front: higher (top-string) tips first, so a
        // finger on a lower string draws in front — matching the real
        // player's-eye overlap. Each fill covers the rim lines of what
        // lies behind, leaving natural finger-over-finger edges.
        let palmEdgeY = palmTopY - g * 0.25
        let ordered = fingers.sorted {
            $0.spine[$0.spine.count - 1].y < $1.spine[$1.spine.count - 1].y
        }
        let fillPaths: [Path] = ordered.map { f in
            var p = fingerPath(f, from: 0)
            p.closeSubpath()
            return p
        }
        for (i, f) in ordered.enumerated() {
            ctx.fill(fillPaths[i], with: .color(skin))

            // Rim: starts where the finger clears the palm edge, and
            // skips any stretch hidden under a finger drawn in front —
            // bunched fingers keep ONE clean edge between them instead
            // of double crease lines.
            var start = 1
            for (k, p) in f.spine.enumerated() where p.y < palmEdgeY {
                start = max(1, k); break
            }
            let last = f.spine.count - 1
            guard start < last - 1 else { continue }
            let fronts = fillPaths[(i + 1)...]
            func visible(_ p: CGPoint) -> Bool {
                !fronts.contains { $0.contains(p) }
            }
            // Rim polyline: left side up, sampled tip cap, right side
            // down — then stroke only the visible runs.
            var pts: [CGPoint] = []
            for k in start...last { pts.append(side(f, k, -1)) }
            let tip = f.spine[last], prev = f.spine[last - 1]
            let capOut = CGPoint(
                x: tip.x + (tip.x - prev.x) * 0.45,
                y: tip.y + (tip.y - prev.y) * 0.45)
            let tipL = side(f, last, -1), tipR = side(f, last, +1)
            for s in 1...3 {
                let t = CGFloat(s) / 4, mt = 1 - t
                pts.append(CGPoint(
                    x: mt*mt*tipL.x + 2*mt*t*capOut.x + t*t*tipR.x,
                    y: mt*mt*tipL.y + 2*mt*t*capOut.y + t*t*tipR.y))
            }
            for k in stride(from: last, through: start, by: -1) {
                pts.append(side(f, k, +1))
            }
            var rimPath = Path()
            var run: [CGPoint] = []
            func flush() {
                if run.count > 1 {
                    rimPath.move(to: run[0])
                    for p in run.dropFirst() { rimPath.addLine(to: p) }
                }
                run.removeAll()
            }
            for p in pts {
                if visible(p) { run.append(p) } else { flush() }
            }
            flush()
            glow.stroke(rimPath, with: .color(rim), style: rimStyle)
        }
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
