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

enum HandFingering {
    /// Fretted dots -> finger numbers. Barre fret -> finger 1 across its span;
    /// remaining fretted notes get 2,3,4 by ascending fret then string. Open /
    /// muted strings carry no finger. Deterministic; not claimed optimal.
    static func contacts(for d: ChordDiagram) -> [FingerContact] {
        let dots = d.dots.sorted { $0.fret != $1.fret ? $0.fret < $1.fret : $0.string < $1.string }
        var out: [FingerContact] = []
        if let barre = d.barre {
            var next = 2
            for dot in dots {
                if dot.fret == barre.fret {
                    out.append(FingerContact(finger: 1, string: dot.string, fret: dot.fret))
                } else {
                    out.append(FingerContact(finger: min(4, next), string: dot.string, fret: dot.fret)); next += 1
                }
            }
        } else {
            var f = 1
            for dot in dots { out.append(FingerContact(finger: min(4, f), string: dot.string, fret: dot.fret)); f += 1 }
        }
        return out
    }
}

// MARK: - view

struct HandNeckView: View {
    let chords: [ChordEvent]
    let positionSeconds: Double

    // fingering per chord index, computed once per chord list
    private let fings: [[FingerContact]]
    private let maxFret: Int

    init(chords: [ChordEvent], positionSeconds: Double) {
        self.chords = chords
        self.positionSeconds = positionSeconds
        let f = chords.map { ev -> [FingerContact] in
            guard let d = ChordDiagram.make(symbol: ev.symbol) else { return [] }
            return HandFingering.contacts(for: d)
        }
        self.fings = f
        self.maxFret = max(5, (f.flatMap { $0 }.map(\.fret).max() ?? 4) + 1)
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

    // MARK: geometry (nut on the right)
    private func ntpos(_ fret: Double) -> Double {
        let p = { (f: Double) in 1 - pow(2, -f / 12) }
        return (p(fret) - p(0)) / (p(Double(maxFret)) - p(0))
    }

    // MARK: sample a finger's state at time t
    private struct FState { var s: Double; var f: Double; var press: Double; var moving: Bool; var arrive: Double }

    private func contact(_ fi: Int, _ idx: Int) -> FingerContact? {
        guard idx >= 0, idx < fings.count else { return nil }
        return fings[idx].first { $0.finger == fi }
    }
    private func activeIndex(_ t: Double) -> Int {
        guard !chords.isEmpty else { return 0 }
        var lo = 0, hi = chords.count - 1, cand = 0
        while lo <= hi { let m = (lo + hi) / 2
            if chords[m].start <= t { cand = m; lo = m + 1 } else { hi = m - 1 } }
        return cand
    }
    private func easeIO(_ x: Double) -> Double { x < 0.5 ? 4*x*x*x : 1 - pow(-2*x+2, 3)/2 }

    private func sample(_ fi: Int, _ t: Double) -> FState {
        guard !chords.isEmpty else { return FState(s: 2.5, f: 0, press: 0, moving: false, arrive: 0) }
        let i = activeIndex(t)
        let cur = contact(fi, i)
        let trans = 0.22
        let j = min(i + 1, chords.count - 1)
        let boundary = chords[i].end
        // arrival pulse: this finger newly pressing at chord i's start
        var arrive = 0.0
        if let c = cur {
            let prev = contact(fi, i - 1)
            if prev == nil || prev != c {
                let dt = t - chords[i].start
                if dt >= 0 && dt < 0.4 { arrive = 1 - dt / 0.4 }
            }
        }
        if j == i || t < boundary - trans {           // holding
            if let c = cur { return FState(s: Double(c.string), f: Double(c.fret), press: 1, moving: false, arrive: arrive) }
            return FState(s: 2.5, f: 0, press: 0, moving: false, arrive: 0)
        }
        // transition into the next chord
        let nxt = contact(fi, j)
        let k = easeIO(min(1, max(0, (t - (boundary - trans)) / trans)))
        let fromS = Double(cur?.string ?? nxt?.string ?? 2), fromF = Double(cur?.fret ?? nxt?.fret ?? 0)
        let toS = Double(nxt?.string ?? cur?.string ?? 2), toF = Double(nxt?.fret ?? cur?.fret ?? 0)
        let press = (cur != nil ? 1.0 : 0.0) * (1 - k) + (nxt != nil ? 1.0 : 0.0) * k
        return FState(s: fromS + (toS-fromS)*k, f: fromF + (toF-fromF)*k, press: press, moving: true, arrive: 0)
    }

    // MARK: draw
    private func draw(_ ctx: GraphicsContext, _ size: CGSize) {
        let W = size.width, H = size.height
        let padX: CGFloat = 46, top: CGFloat = 34
        let boardH = H * 0.5, bot = top + boardH
        let left = padX, right = W - padX
        func fx(_ fret: Double) -> CGFloat { right - (right - left) * CGFloat(ntpos(fret)) }
        func cx(_ fret: Double) -> CGFloat { (fx(fret) + fx(fret - 1)) / 2 }
        func sy(_ s: Double) -> CGFloat { top + boardH * CGFloat(s / 5.0) }

        // fretboard
        var slab = Path(roundedRect: CGRect(x: left-4, y: top-14, width: (right-left)+16, height: boardH+28), cornerRadius: 8)
        ctx.fill(slab, with: .linearGradient(Gradient(colors: [
            Color(red:0.235,green:0.168,blue:0.128), Color(red:0.16,green:0.108,blue:0.078),
            Color(red:0.12,green:0.075,blue:0.055)]), startPoint: CGPoint(x:0,y:top-14), endPoint: CGPoint(x:0,y:bot+14)))
        _ = slab
        // inlays
        for f in [3,5,7,9,12] where f <= maxFret {
            let x = (fx(Double(f)) + fx(Double(f-1))) / 2
            ctx.fill(Path(ellipseIn: CGRect(x: x-4, y: (top+bot)/2-4, width: 8, height: 8)),
                     with: .color(.white.opacity(0.16)))
        }
        // frets
        for f in 1...maxFret {
            let x = fx(Double(f))
            ctx.stroke(Path { $0.move(to: CGPoint(x:x,y:top-13)); $0.addLine(to: CGPoint(x:x,y:bot+13)) },
                       with: .color(Color(white:0.5)), lineWidth: 2)
            ctx.draw(Text("\(f)").font(.system(size:11, design:.monospaced)).foregroundColor(Color(white:0.45)),
                     at: CGPoint(x: (fx(Double(f))+fx(Double(f-1)))/2, y: bot+26))
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

        // states
        var st: [Int: FState] = [:]
        for fi in 1...4 { st[fi] = sample(fi, positionSeconds) }
        // hand base (glides with the shift)
        var sx: CGFloat = 0, sw: CGFloat = 0
        for fi in 1...4 { let w = 0.2 + 0.8*CGFloat(st[fi]!.press); sx += cx(st[fi]!.f)*w; sw += w }
        let baseX = sw > 0 ? sx/sw : (left+right)/2, baseY = bot + boardH*0.7
        func kX(_ fi: Int) -> CGFloat { baseX + CGFloat(fi-2)*15 - 7 }
        let kY = baseY - 10

        // wrist / back-of-hand (subtle)
        var wrist = Path()
        wrist.move(to: CGPoint(x: kX(1)-12, y: kY+2))
        wrist.addQuadCurve(to: CGPoint(x: kX(4)+12, y: kY+2), control: CGPoint(x: baseX, y: kY-9))
        wrist.addQuadCurve(to: CGPoint(x: kX(1)-12, y: kY+2), control: CGPoint(x: baseX, y: baseY+28))
        ctx.fill(wrist, with: .color(.white.opacity(0.05)))
        ctx.stroke(wrist, with: .color(.white.opacity(0.12)), lineWidth: 1.3)

        // draw non-moving first, moving finger last (on top)
        let order = (1...4).sorted { (st[$0]!.moving ? 1:0) < (st[$1]!.moving ? 1:0) }
        for fi in order {
            let s = st[fi]!, c = col[fi]!
            let tx = cx(s.f), ty = sy(s.s) - CGFloat(1 - s.press)*16
            let state = s.moving ? "move" : (s.press < 0.5 ? "lift" : "plant")
            let emph: CGFloat = state == "move" ? 1 : state == "plant" ? 0.62 : 0.28

            // motion trail + future ring (moving only)
            if state == "move" {
                for g in 1...4 {
                    let gp = sample(fi, positionSeconds - Double(g)*0.05)
                    ctx.fill(Path(ellipseIn: CGRect(x: cx(gp.f)-CGFloat(9-g), y: sy(gp.s)-CGFloat(9-g), width: CGFloat(2*(9-g)), height: CGFloat(2*(9-g)))),
                             with: .color(c.opacity(0.10*(1-Double(g)/5))))
                }
            }
            if s.press < 0.04 && state != "move" { continue }

            // curved tapered stem terminating at the tip
            let mx = (kX(fi)+tx)/2, my = (kY+ty)/2 - 20*CGFloat(s.press) - abs(tx-kX(fi))*0.06
            var stem = Path(); stem.move(to: CGPoint(x: kX(fi), y: kY)); stem.addQuadCurve(to: CGPoint(x: tx, y: ty), control: CGPoint(x: mx, y: my))
            ctx.stroke(stem, with: .color(c.opacity((0.12+0.30*Double(s.press))*(0.5+0.5*Double(emph)))),
                       style: StrokeStyle(lineWidth: 3+3*CGFloat(s.press), lineCap: .round))
            ctx.stroke(stem, with: .color(c.opacity(0.5*Double(emph))), style: StrokeStyle(lineWidth: 1.7, lineCap: .round))

            // one subtle arrival pulse
            if s.arrive > 0 {
                let k = 1 - s.arrive
                ctx.stroke(Path(ellipseIn: CGRect(x: tx-(13+CGFloat(k)*12), y: ty-(13+CGFloat(k)*12), width: 2*(13+CGFloat(k)*12), height: 2*(13+CGFloat(k)*12))),
                           with: .color(c.opacity(s.arrive*0.45)), lineWidth: 2)
            }
            // plant squash
            var rx: CGFloat = 1, ry: CGFloat = 1
            if s.arrive > 0 { rx = 1 + 0.22*CGFloat(s.arrive); ry = 1 - 0.18*CGFloat(s.arrive) }

            // fingertip
            let r: CGFloat = state == "move" ? 19 : state == "plant" ? 17 : 12
            var tip = ctx
            if state == "move" { tip.addFilter(.shadow(color: c.opacity(0.7), radius: 9)) }
            else if state == "plant" { }
            let rect = CGRect(x: tx - r*rx, y: ty - r*ry, width: 2*r*rx, height: 2*r*ry)
            tip.opacity = state == "lift" ? 0.34 : 1
            tip.fill(Path(ellipseIn: rect), with: .color(c))
            if state == "move" {
                ctx.stroke(Path(ellipseIn: CGRect(x: tx-r-3.5, y: ty-r-3.5, width: 2*(r+3.5), height: 2*(r+3.5))),
                           with: .color(.white.opacity(0.85)), lineWidth: 2)
            }
            ctx.stroke(Path(ellipseIn: rect), with: .color(Color(white:0.06)), lineWidth: 1.5)
            ctx.draw(Text("\(fi)").font(.system(size: state=="move" ? 17:15, weight: .bold, design: .monospaced))
                        .foregroundColor(Color(white:0.05).opacity(state=="lift" ? 0.6:1)),
                     at: CGPoint(x: tx, y: ty))
        }
    }
}
