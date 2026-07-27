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
    public let fretW: CGFloat          // mean fret-column width (sizing legacy)
    public let baseFret: Int
    public let window: Int
    /// One px/mm factor: the board is a scaled PHYSICAL neck
    /// (GuitarPhysical), and the hand skeleton uses the same factor,
    /// so hand-to-neck proportions are physically correct.
    public let pxPerMM: CGFloat
    /// mm from the nut of the window's first wire (window left edge is
    /// the LAST wire — nut is on the right).
    private let windowLoMM: CGFloat

    public init(size: CGSize, baseFret: Int, window: Int = 4) {
        let gutter: CGFloat = 40   // x/o markers beside the nut
        let top: CGFloat = 8
        let loMM = GuitarPhysical.wire(baseFret - 1)
        let hiMM = GuitarPhysical.wire(baseFret - 1 + window)
        let spanMM = hiMM - loMM
        let gapMM = GuitarPhysical.stringGapMM(atX: (loMM + hiMM) / 2)
        // px/mm from whichever axis constrains (board ≤ 55% height).
        let s = max(0.4, min(
            (size.width - gutter - 10) / spanMM,
            size.height * 0.55 / (6 * gapMM)))
        let neckW = spanMM * s
        let gap = gapMM * s
        let x = max(10, (size.width - gutter - neckW) / 2)
        self.neck = CGRect(x: x, y: top, width: neckW, height: gap * 6)
        self.stringGap = gap
        self.fretW = neckW / CGFloat(window)
        self.baseFret = baseFret
        self.window = window
        self.pxPerMM = s
        self.windowLoMM = loMM
    }

    /// String row center. 0 = low E → TOP row; 5 = high e → bottom
    /// (audience view of a right-handed guitar, matching the nut-right
    /// orientation).
    public func stringY(_ s: Int) -> CGFloat {
        neck.minY + (CGFloat(s) + 0.5) * stringGap
    }

    /// Dot center x for an absolute fret: the physical finger contact
    /// point (30% behind the wire). Nut is at the RIGHT, frets narrow
    /// toward the left per equal temperament.
    public func fretX(_ fret: Int) -> CGFloat {
        neck.maxX - (GuitarPhysical.fingerX(fret) - windowLoMM) * pxPerMM
    }

    /// X of the window's f-th wire (0 = the wire at the right edge —
    /// the nut in open position).
    public func wireX(_ f: Int) -> CGFloat {
        neck.maxX - (GuitarPhysical.wire(baseFret - 1 + f) - windowLoMM) * pxPerMM
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
    /// px per mm — scales the hand skeleton to the physical neck.
    public var pxPerMM: CGFloat = 1

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
                        fretWidth: geo.fretW, pxPerMM: geo.pxPerMM)
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
        // POSE pipeline (docs/FRETTING_HAND_SPIKE.md): fingertip
        // targets → articulated IK solve → oblique projection →
        // silhouette. The Animatable tips interpolate through TARGET
        // space and the pose re-solves every frame, so the whole hand
        // moves as one articulated unit through valid poses. Thumb is
        // posed behind the neck (occluded).
        let s = max(0.4, plan.pxPerMM)

        var targets: [HandPoseSolver.Target] = []
        for i in 0..<4 {
            let press = plan.activeFingers.contains(i + 1)
            let barre = i == 0 && plan.barreRect != nil
            targets.append(HandPoseSolver.Target(
                point: tips[i], zMM: press ? 0 : 12,
                press: press, barre: barre))
        }
        let pose = HandPoseSolver.solve(
            targets: targets, neckBottom: plan.neckBottom, s: s,
            lifted: lifted)

        let skin = Color(red: 0.085, green: 0.085, blue: 0.12)
        let rim = Color.white.opacity(0.32)
        let rimStyle = StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round)
        var glow = ctx
        glow.addFilter(.shadow(color: .white.opacity(0.18), radius: 2.5))

        let palm = HandPoseRender.palmPath(pose, canvasHeight: size.height, s: s)
        ctx.fill(palm, with: .color(skin))
        glow.stroke(palm, with: .color(rim), style: rimStyle)

        // Fingers back-to-front (lower-string tips draw in front). A
        // barre index lies across the strings — its tip is on the top
        // string, so it lands behind the stacked fingers automatically.
        let ordered = pose.fingers.sorted {
            ($0.joints.last?.y ?? 0) < ($1.joints.last?.y ?? 0)
        }
        let fills = ordered.map { HandPoseRender.fingerPath($0) }
        for (i, chain) in ordered.enumerated() {
            ctx.fill(fills[i], with: .color(skin))
            var rimPath = HandPoseRender.fingerPath(chain, from: 0.18, close: false)
            if i + 1 < ordered.count {
                rimPath = trimmed(rimPath, hiddenBy: Array(fills[(i + 1)...]))
            }
            glow.stroke(rimPath, with: .color(rim), style: rimStyle)
        }

        if ProcessInfo.processInfo.environment["TONEFORGE_HAND_DEBUG"] == "1" {
            HandPoseRender.drawDebug(ctx, pose: pose, s: s)
        }
    }

    /// Rebuild an open path keeping only the runs whose points are not
    /// covered by any of the given front fills (kills double crease
    /// lines where fingers overlap).
    private func trimmed(_ path: Path, hiddenBy fronts: [Path]) -> Path {
        var out = Path()
        var run: [CGPoint] = []
        func flush() {
            if run.count > 1 {
                out.move(to: run[0])
                for p in run.dropFirst() { out.addLine(to: p) }
            }
            run.removeAll()
        }
        path.forEach { el in
            let p: CGPoint?
            switch el {
            case .move(let to), .line(let to): p = to
            case .quadCurve(let to, _): p = to
            case .curve(let to, _, _): p = to
            case .closeSubpath: p = nil
            }
            guard let pt = p else { return }
            if fronts.contains(where: { $0.contains(pt) }) { flush() }
            else { run.append(pt) }
        }
        flush()
        return out
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
