// HandNeckView.swift
//
// Hand View — the Perform performance visualization. A calm horizontal neck
// (nut on the RIGHT, app convention) with coloured numbered fingertips that
// perform the song's chords through time: fingers plant, lift, and shift
// between chords. Ported from the approved prototype's visual language:
// sticky states, motion only on real events, one subtle arrival pulse,
// minimal wrist origin, light finger stems.
//
// Independent of Planner V1 / V2 / the Blender pipeline. Driven entirely by
// the existing Perform timeline (ChordRibbonModel + transport position). The
// finger numbering is the SIMPLEST deterministic mapping (HandFingering) — the
// point is validating the visualization, not solving pedagogy.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

// MARK: - simplest deterministic fingering

struct FingerContact: Equatable { let finger: Int; let string: Int; let fret: Int }
struct BarreSpan: Equatable { let fret: Int; let lo: Int; let hi: Int }   // index barre: lo..hi strings at `fret`
/// One chord's fingering: an optional index BARRE + the remaining fingers.
struct HandShape: Equatable { let barre: BarreSpan?; let fingers: [FingerContact] }

enum HandFingering {
    /// Bounded, deterministic fingering — NOT a general engine. Barre chords are
    /// core guitar vocabulary, so we detect them explicitly:
    ///   • if >= 2 fretted notes share the LOWEST fret AND notes exist above it,
    ///     infer an index BARRE across those notes (finger 1); higher notes get
    ///     fingers 2,3,4 by ascending fret then string.
    ///   • otherwise assign distinct fingers 1..4 ascending (open chords: A, D…).
    /// Covers the common barre set (Cm, F, B, F#m) — no alternate voicings, no
    /// advanced/jazz fingerings.
    static func shape(for d: ChordDiagram) -> HandShape {
        let dots = d.dots.sorted { $0.fret != $1.fret ? $0.fret < $1.fret : $0.string < $1.string }
        guard let minFret = dots.map(\.fret).min() else { return HandShape(barre: nil, fingers: []) }
        let atMin = dots.filter { $0.fret == minFret }
        let higher = dots.filter { $0.fret > minFret }
        if atMin.count >= 2 && !higher.isEmpty {          // index barre
            let strings = atMin.map(\.string)
            let barre = BarreSpan(fret: minFret, lo: strings.min()!, hi: strings.max()!)
            var out: [FingerContact] = []; var f = 2
            for dot in higher { out.append(FingerContact(finger: min(4, f), string: dot.string, fret: dot.fret)); f += 1 }
            return HandShape(barre: barre, fingers: out)
        }
        var out: [FingerContact] = []; var f = 1          // distinct fingers (A, D, open)
        for dot in dots { out.append(FingerContact(finger: min(4, f), string: dot.string, fret: dot.fret)); f += 1 }
        return HandShape(barre: nil, fingers: out)
    }
}

// MARK: - view

struct HandNeckView: View {
    let chords: [ChordEvent]
    let positionSeconds: Double
    // Three INDEPENDENT overlay layers on the neck. None knows about the others.
    var showDots: Bool = true       // Finger Dots — exact contacts + finger identity (primary)
    var showMotion: Bool = true     // Motion — trajectories, ghosts, arrival pulses, movement emphasis
    var showHand: Bool = true       // Animated Hand — realistic pose drawn over the neck (low opacity)

    // fingering per chord index, computed once per chord list
    private let shapes: [HandShape]
    private let maxFret: Int

    init(chords: [ChordEvent], positionSeconds: Double,
         showDots: Bool = true, showMotion: Bool = true, showHand: Bool = true) {
        self.chords = chords
        self.positionSeconds = positionSeconds
        self.showDots = showDots
        self.showMotion = showMotion
        self.showHand = showHand
        let sh = chords.map { ev -> HandShape in
            guard let d = ChordDiagram.make(symbol: ev.symbol) else { return HandShape(barre: nil, fingers: []) }
            return HandFingering.shape(for: d)
        }
        self.shapes = sh
        let frets = sh.flatMap { $0.fingers.map(\.fret) + ($0.barre.map { [$0.fret] } ?? []) }
        self.maxFret = max(5, (frets.max() ?? 4) + 1)
    }

    private let col: [Int: Color] = [
        1: Color(red: 0.36, green: 0.62, blue: 1.0),
        2: Color(red: 0.31, green: 0.82, blue: 0.63),
        3: Color(red: 0.95, green: 0.71, blue: 0.36),
        4: Color(red: 0.85, green: 0.55, blue: 1.0)]

    var body: some View {
        Canvas { ctx, size in draw(ctx, size) }
            .background(Color(red: 0.075, green: 0.07, blue: 0.09))
            .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    /// Keep only path runs NOT covered by a front fill — kills double crease lines
    /// where fingers overlap (port of the mobile renderer's `trimmed`).
    private func trimmedPath(_ path: Path, hiddenBy fronts: [Path]) -> Path {
        var out = Path(); var run: [CGPoint] = []
        func flush() {
            if run.count > 1 { out.move(to: run[0]); for p in run.dropFirst() { out.addLine(to: p) } }
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
            if fronts.contains(where: { $0.contains(pt) }) { flush() } else { run.append(pt) }
        }
        flush()
        return out
    }

    // MARK: sample a finger's state at time t
    // sLo..sHi is the string SPAN a finger covers: equal for a normal fingertip,
    // a range for the index barre (finger 1). f is the (possibly fractional) fret.
    private struct FState { var f: Double; var sLo: Double; var sHi: Double; var press: Double; var moving: Bool; var arrive: Double }

    private func activeIndex(_ t: Double) -> Int {
        guard !chords.isEmpty else { return 0 }
        var lo = 0, hi = chords.count - 1, cand = 0
        while lo <= hi { let m = (lo + hi) / 2
            if chords[m].start <= t { cand = m; lo = m + 1 } else { hi = m - 1 } }
        return cand
    }
    private func currentSymbol(_ t: Double) -> String? {
        guard !chords.isEmpty else { return nil }
        return chords[activeIndex(t)].symbol
    }
    // smootherstep: zero 1st AND 2nd derivative at both ends — no jerk.
    private func easeIO(_ x: Double) -> Double { let c = min(1, max(0, x)); return c*c*c*(c*(c*6-15)+10) }
    private func restString(_ fi: Int) -> Double { 1.4 + Double(fi - 1) * 0.7 }
    private func anchorFret(_ idx: Int) -> Double {
        guard idx >= 0, idx < shapes.count else { return 2 }
        var fs = shapes[idx].fingers.map { Double($0.fret) }
        if let b = shapes[idx].barre { fs.append(Double(b.fret)) }
        return fs.isEmpty ? 2 : max(1, fs.reduce(0, +) / Double(fs.count))
    }

    /// A finger's target in chord `idx`: fret + string SPAN + whether pressed.
    /// Finger 1 may be an index barre (sLo != sHi); everyone else is a point.
    private func target(_ fi: Int, _ idx: Int) -> (f: Double, sLo: Double, sHi: Double, pressed: Bool) {
        guard idx >= 0, idx < shapes.count else { return (2, restString(fi), restString(fi), false) }
        let sh = shapes[idx]
        if fi == 1, let b = sh.barre {
            return (Double(b.fret), Double(b.lo), Double(b.hi), true)
        }
        if let c = sh.fingers.first(where: { $0.finger == fi }) {
            return (Double(c.fret), Double(c.string), Double(c.string), true)
        }
        return (anchorFret(idx), restString(fi), restString(fi), false)   // parked on-board
    }

    private func sample(_ fi: Int, _ t: Double) -> FState {
        guard !chords.isEmpty else { return FState(f: 2, sLo: restString(fi), sHi: restString(fi), press: 0, moving: false, arrive: 0) }
        let i = activeIndex(t)
        let j = min(i + 1, chords.count - 1)
        let boundary = chords[i].end
        let dur = chords[i].end - chords[i].start
        let trans = min(0.34, max(0.12, dur * 0.5))
        let a = target(fi, i)
        // arrival pulse: newly pressing (or changed placement) at chord i's start
        var arrive = 0.0
        if a.pressed {
            let p = target(fi, i - 1)
            if !p.pressed || p.f != a.f || p.sLo != a.sLo || p.sHi != a.sHi {
                let dt = t - chords[i].start
                if dt >= 0 && dt < 0.4 { arrive = 1 - dt / 0.4 }
            }
        }
        if j == i || t < boundary - trans {           // holding
            return FState(f: a.f, sLo: a.sLo, sHi: a.sHi, press: a.pressed ? 1 : 0, moving: false, arrive: arrive)
        }
        // transition into the next chord
        let b = target(fi, j)
        let k = easeIO((t - (boundary - trans)) / trans)
        func lp(_ x: Double, _ y: Double) -> Double { x + (y - x) * k }
        let press = (a.pressed ? 1.0 : 0.0) * (1 - k) + (b.pressed ? 1.0 : 0.0) * k
        return FState(f: lp(a.f, b.f), sLo: lp(a.sLo, b.sLo), sHi: lp(a.sHi, b.sHi), press: press, moving: true, arrive: 0)
    }

    // MARK: draw
    private func draw(_ ctx: GraphicsContext, _ size: CGSize) {
        let W = size.width, H = size.height
        let top: CGFloat = 34
        let padR: CGFloat = 46, padL: CGFloat = 18
        // PHYSICAL neck (GuitarPhysical, mm) scaled by ONE px/mm factor, so the
        // board AND the anatomical hand share proportions by construction — the
        // same contract the mobile renderer uses. Window = nut..maxFret, nut RIGHT.
        let loMM = GuitarPhysical.wire(0)
        let hiMM = GuitarPhysical.wire(maxFret)
        let spanMM = max(1, hiMM - loMM)
        let gapMM = GuitarPhysical.stringGapMM(atX: (loMM + hiMM) / 2)
        let pxPerMM = max(0.3, min((W - padR - padL) / spanMM, (H * 0.42) / (6 * gapMM)))
        let gap = gapMM * pxPerMM
        let boardH = 6 * gap
        let bot = top + boardH
        let right = W - padR
        let left = right - spanMM * pxPerMM
        func cxMM(_ fret: Double) -> CGFloat {      // finger-contact mm for a fractional fret
            let f0 = max(0, Int(floor(fret))), f1 = f0 + 1
            let a = GuitarPhysical.fingerX(f0), b = GuitarPhysical.fingerX(f1)
            return a + (b - a) * CGFloat(fret - Double(f0))
        }
        func cx(_ fret: Double) -> CGFloat { right - (cxMM(fret) - loMM) * pxPerMM }
        func sy(_ s: Double) -> CGFloat { top + (CGFloat(s) + 0.5) * gap }
        func wireX(_ n: Int) -> CGFloat { right - (GuitarPhysical.wire(n) - loMM) * pxPerMM }

        // fretboard
        let slab = Path(roundedRect: CGRect(x: left-4, y: top-14, width: (right-left)+16, height: boardH+28), cornerRadius: 8)
        ctx.fill(slab, with: .linearGradient(Gradient(colors: [
            Color(red:0.235,green:0.168,blue:0.128), Color(red:0.16,green:0.108,blue:0.078),
            Color(red:0.12,green:0.075,blue:0.055)]), startPoint: CGPoint(x:0,y:top-14), endPoint: CGPoint(x:0,y:bot+14)))
        // inlays
        for f in [3,5,7,9,12] where f <= maxFret {
            let x = (wireX(f) + wireX(f-1)) / 2
            ctx.fill(Path(ellipseIn: CGRect(x: x-4, y: (top+bot)/2-4, width: 8, height: 8)),
                     with: .color(.white.opacity(0.16)))
        }
        // frets
        for f in 1...maxFret {
            let x = wireX(f)
            ctx.stroke(Path { $0.move(to: CGPoint(x:x,y:top-13)); $0.addLine(to: CGPoint(x:x,y:bot+13)) },
                       with: .color(Color(white:0.5)), lineWidth: 2)
            ctx.draw(Text("\(f)").font(.system(size:11, design:.monospaced)).foregroundColor(Color(white:0.45)),
                     at: CGPoint(x: (wireX(f)+wireX(f-1))/2, y: bot+26))
        }
        // nut (right)
        ctx.fill(Path(roundedRect: CGRect(x: right+2, y: top-15, width: 6, height: boardH+30), cornerRadius: 2),
                 with: .color(Color(white:0.85)))
        // strings
        let wds: [CGFloat] = [3.0,2.6,2.2,1.8,1.4,1.0]
        for s in 0..<6 {
            let y = sy(Double(s))
            ctx.stroke(Path { $0.move(to: CGPoint(x:left-4,y:y)); $0.addLine(to: CGPoint(x:right+8,y:y)) },
                       with: .linearGradient(Gradient(colors:[Color(white:0.5),Color(white:0.92),Color(white:0.5)]),
                       startPoint: CGPoint(x:0,y:y-2), endPoint: CGPoint(x:0,y:y+2)),
                       lineWidth: wds[s])
        }

        // All on-neck layers read the SAME contact states, so the Hand
        // silhouette, the Dots and the Motion effects are always in register.
        guard showDots || showMotion || showHand else { return }

        // states
        var st: [Int: FState] = [:]
        for fi in 1...4 { st[fi] = sample(fi, positionSeconds) }
        // hand base (glides with the shift)
        var sx: CGFloat = 0, sw: CGFloat = 0
        for fi in 1...4 { let w = 0.2 + 0.8*CGFloat(st[fi]!.press); sx += cx(st[fi]!.f)*w; sw += w }
        let baseX = sw > 0 ? sx/sw : (left+right)/2
        let kY = bot + 30            // knuckle row, just below the board (visible)
        func kX(_ fi: Int) -> CGFloat { baseX + (CGFloat(fi) - 2.5) * 22 }

        // per-finger geometry shared by every on-neck layer (finger-tracked)
        func geom(_ fi: Int) -> (x: CGFloat, yc: CGFloat, yLo: CGFloat, yHi: CGFloat, press: CGFloat, barre: Bool) {
            let s = st[fi]!
            let lift = CGFloat(1 - s.press) * 16
            let yLo = sy(s.sLo) - lift, yHi = sy(s.sHi) - lift
            return (cx(s.f), (yLo+yHi)/2, yLo, yHi, CGFloat(s.press), (s.sHi - s.sLo) > 0.5)
        }

        // ANIMATED HAND (priority 3): a real posed hand for ANY chord. Feed the dot
        // positions to the analytic IK solver (HandPoseSolver — the same articulated
        // method as the Blender rig, run live) as fingertip targets, so the fingers
        // land on the dots whatever the chord — no per-chord baked asset. Rendered as
        // palm + back-to-front finger capsules; ghosted so the neck stays hero and
        // the dots read on top.
        if showHand {
            var targets: [HandPoseSolver.Target] = []
            for fi in 1...4 {
                let g = geom(fi)
                let pressing = g.press > 0.5 || g.barre
                let pt = CGPoint(x: g.x, y: g.barre ? g.yLo : g.yc)   // barre tip = top string
                targets.append(HandPoseSolver.Target(
                    point: pt, zMM: pressing ? 0 : 12, press: pressing, barre: g.barre))
            }
            let pose = HandPoseSolver.solve(targets: targets, neckBottom: bot, s: pxPerMM, lifted: false)
            var hand = ctx; hand.opacity = 0.8
            let skin = Color(red: 0.42, green: 0.44, blue: 0.52)
            let rim = Color.white.opacity(0.5)
            let rimStyle = StrokeStyle(lineWidth: 1.3, lineCap: .round, lineJoin: .round)
            var glow = hand; glow.addFilter(.shadow(color: .white.opacity(0.16), radius: 2.5))
            let palm = HandPoseRender.palmPath(pose, canvasHeight: size.height, s: pxPerMM)
            hand.fill(palm, with: .color(skin))
            glow.stroke(palm, with: .color(rim), style: rimStyle)
            // fingers back-to-front (lower-string tips draw in front)
            let ordered = pose.fingers.sorted { a, b in
                let ay = a.joints.last?.y ?? 0
                let by = b.joints.last?.y ?? 0
                return ay < by
            }
            let fills = ordered.map { HandPoseRender.fingerPath($0) }
            for (i, chain) in ordered.enumerated() {
                hand.fill(fills[i], with: .color(skin))
                var rimPath = HandPoseRender.fingerPath(chain, from: 0.18, close: false)
                if i + 1 < ordered.count { rimPath = trimmedPath(rimPath, hiddenBy: Array(fills[(i+1)...])) }
                glow.stroke(rimPath, with: .color(rim), style: rimStyle)
            }
        }

        // FINGER DOTS (priority 2, primary) + MOTION (technique). Draw the block if
        // EITHER is on. Motion adds trajectories / ghosts / arrival pulses / movement
        // emphasis; Dots owns the coloured markers + finger-number identity.
        guard showDots || showMotion else { return }

        // draw non-moving first, moving finger last (on top)
        let order = (1...4).sorted { (st[$0]!.moving ? 1:0) < (st[$1]!.moving ? 1:0) }
        for fi in order {
            let s = st[fi]!, c = col[fi]!
            // "move" emphasis is a Motion effect; without Motion a transitioning
            // finger renders as a plain plant/lift dot.
            let state = (s.moving && showMotion) ? "move" : (s.press < 0.5 ? "lift" : "plant")
            let g = geom(fi)
            let x = g.x, yLo = g.yLo, yHi = g.yHi, yc = g.yc, isBarre = g.barre

            if !isBarre && s.press < 0.04 && state != "move" { continue }

            // MOTION: ghost trail behind a moving fingertip
            if state == "move" && !isBarre {
                for g in 1...4 {
                    let gp = sample(fi, positionSeconds - Double(g)*0.05)
                    let gy = sy((gp.sLo+gp.sHi)/2)
                    ctx.fill(Path(ellipseIn: CGRect(x: cx(gp.f)-CGFloat(9-g), y: gy-CGFloat(9-g), width: CGFloat(2*(9-g)), height: CGFloat(2*(9-g)))),
                             with: .color(c.opacity(0.10*(1-Double(g)/5))))
                }
            }

            // DOTS: coloured stem to the contact — only when the Hand silhouette
            // isn't already drawing finger shapes (avoid doubling).
            if showDots && !showHand {
                let emph: CGFloat = state == "move" ? 1 : state == "plant" ? 0.62 : 0.28
                let mx = (kX(fi)+x)/2, my = (kY+yc)/2 - 20*CGFloat(s.press) - abs(x-kX(fi))*0.06
                var stem = Path(); stem.move(to: CGPoint(x: kX(fi), y: kY)); stem.addQuadCurve(to: CGPoint(x: x, y: yc), control: CGPoint(x: mx, y: my))
                ctx.stroke(stem, with: .color(c.opacity((0.12+0.30*Double(s.press))*(0.5+0.5*Double(emph)))),
                           style: StrokeStyle(lineWidth: 3+3*CGFloat(s.press), lineCap: .round))
                ctx.stroke(stem, with: .color(c.opacity(0.5*Double(emph))), style: StrokeStyle(lineWidth: 1.7, lineCap: .round))
            }

            if isBarre {
                // one finger, many strings: a rounded bar across yLo..yHi at fret x
                let rB: CGFloat = 12.5
                let cap = Path(roundedRect: CGRect(x: x-rB, y: yLo-rB, width: 2*rB, height: (yHi-yLo)+2*rB), cornerRadius: rB)
                var bar = ctx
                bar.opacity = state == "lift" ? 0.34 : 1
                if state != "lift" { bar.addFilter(.shadow(color: c.opacity(0.45), radius: 8)) }
                bar.fill(cap, with: .color(c))
                ctx.stroke(cap, with: .color(Color(white:0.06)), lineWidth: 1.5)
                if showDots {
                    ctx.draw(Text("\(fi)").font(.system(size: 15, weight: .bold, design: .monospaced)).foregroundColor(Color(white:0.05)),
                             at: CGPoint(x: x, y: yc))
                }
                continue
            }

            // MOTION: one subtle arrival pulse (fingertips)
            if s.arrive > 0 && showMotion {
                let k = 1 - s.arrive
                ctx.stroke(Path(ellipseIn: CGRect(x: x-(13+CGFloat(k)*12), y: yc-(13+CGFloat(k)*12), width: 2*(13+CGFloat(k)*12), height: 2*(13+CGFloat(k)*12))),
                           with: .color(c.opacity(s.arrive*0.45)), lineWidth: 2)
            }
            // fingertip — always a circle
            let r: CGFloat = state == "move" ? 19 : state == "plant" ? 17 : 12
            var tip = ctx
            if state == "move" { tip.addFilter(.shadow(color: c.opacity(0.7), radius: 9)) }
            let rect = CGRect(x: x - r, y: yc - r, width: 2*r, height: 2*r)
            tip.opacity = state == "lift" ? 0.34 : 1
            tip.fill(Path(ellipseIn: rect), with: .color(c))
            if state == "move" {   // MOTION: white ring on the moving finger
                ctx.stroke(Path(ellipseIn: CGRect(x: x-r-3.5, y: yc-r-3.5, width: 2*(r+3.5), height: 2*(r+3.5))),
                           with: .color(.white.opacity(0.85)), lineWidth: 2)
            }
            ctx.stroke(Path(ellipseIn: rect), with: .color(Color(white:0.06)), lineWidth: 1.5)
            if showDots {   // DOTS: finger-number identity
                ctx.draw(Text("\(fi)").font(.system(size: state=="move" ? 17:15, weight: .bold, design: .monospaced))
                            .foregroundColor(Color(white:0.05).opacity(state=="lift" ? 0.6:1)),
                         at: CGPoint(x: x, y: yc))
            }
        }
    }
}
