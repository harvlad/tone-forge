// ChordTransition.swift  (ToneForgeEngine)
//
// Phase 2 of the neck-play design: chord TRANSITIONS. Given two
// fingerings, classify what each finger does — Stay (keep it planted),
// Move (slide/hop to a new position), Lift (comes off), Place (goes
// down fresh) — and draw the movement overlay (dashed arrows to the
// new spots, dashed rings where fingers land) on the shared neck
// surface. Colors follow the approved sample design's legend:
// Stay purple · Move blue · Lift/Place green.

import SwiftUI

public enum FingerRole: Equatable, Sendable {
    case stay
    case move
    case lift
    case place

    public var color: Color {
        switch self {
        case .stay:  return Color(red: 0.55, green: 0.36, blue: 0.96)  // purple
        case .move:  return Color(red: 0.23, green: 0.51, blue: 0.96)  // blue
        case .lift, .place: return Color(red: 0.13, green: 0.77, blue: 0.37) // green
        }
    }
}

public struct ChordTransition: Equatable {
    public struct FingerChange: Equatable {
        public let finger: Int                       // 1…4
        public let from: ChordFingering.Note?
        public let to: ChordFingering.Note?
        public let role: FingerRole
    }

    public let changes: [FingerChange]

    /// Role per finger for the CURRENT chord's dots.
    public var rolesByFinger: [Int: FingerRole] {
        Dictionary(uniqueKeysWithValues: changes.map { ($0.finger, $0.role) })
    }

    /// Classify each finger's job between two fingerings. Barres
    /// compare by their anchor note (finger 1's first entry).
    public static func analyze(
        from: ChordFingering.Result, to: ChordFingering.Result
    ) -> ChordTransition {
        func note(_ r: ChordFingering.Result, _ finger: Int) -> ChordFingering.Note? {
            r.notes.first { $0.finger == finger }
        }
        var changes: [FingerChange] = []
        for finger in 1...4 {
            let a = note(from, finger)
            let b = note(to, finger)
            switch (a, b) {
            case (nil, nil):
                continue
            case (let a?, nil):
                changes.append(FingerChange(finger: finger, from: a, to: nil, role: .lift))
            case (nil, let b?):
                changes.append(FingerChange(finger: finger, from: nil, to: b, role: .place))
            case (let a?, let b?):
                let same = a.string == b.string && a.fret == b.fret
                changes.append(FingerChange(
                    finger: finger, from: a, to: b, role: same ? .stay : .move))
            }
        }
        return ChordTransition(changes: changes)
    }
}

// MARK: - Overlay (arrows + landing rings)

/// Movement overlay for the transition view: dashed arrows from each
/// moving finger's current spot to its target, dashed rings where
/// lifted-in fingers land. Draw ABOVE the dots.
public struct TransitionOverlayCanvas: View {
    let transition: ChordTransition
    let geo: NeckGeometry

    public init(transition: ChordTransition, geo: NeckGeometry) {
        self.transition = transition
        self.geo = geo
    }

    public var body: some View {
        Canvas { ctx, _ in
            let r = geo.stringGap * 0.42
            for c in transition.changes {
                switch c.role {
                case .move:
                    guard let a = c.from, let b = c.to else { continue }
                    let p0 = CGPoint(x: geo.fretX(a.fret), y: geo.stringY(a.string))
                    let p1 = CGPoint(x: geo.fretX(b.fret), y: geo.stringY(b.string))
                    drawArrow(ctx, from: p0, to: p1, color: FingerRole.move.color,
                              clearance: r)
                case .place:
                    guard let b = c.to else { continue }
                    let p = CGPoint(x: geo.fretX(b.fret), y: geo.stringY(b.string))
                    var ring = Path(ellipseIn: CGRect(
                        x: p.x - r, y: p.y - r, width: r * 2, height: r * 2))
                    ctx.stroke(
                        ring,
                        with: .color(FingerRole.place.color),
                        style: StrokeStyle(lineWidth: 1.6, dash: [4, 3]))
                    ring = Path()
                case .lift:
                    guard let a = c.from else { continue }
                    // Small downward arrow off the lifted finger.
                    let p = CGPoint(x: geo.fretX(a.fret), y: geo.stringY(a.string))
                    let q = CGPoint(x: p.x, y: p.y + geo.stringGap * 1.1)
                    drawArrow(ctx, from: p, to: q, color: FingerRole.lift.color,
                              clearance: r)
                case .stay:
                    break
                }
            }
        }
        .allowsHitTesting(false)
    }

    private func drawArrow(
        _ ctx: GraphicsContext, from p0: CGPoint, to p1: CGPoint,
        color: Color, clearance: CGFloat
    ) {
        let dx = p1.x - p0.x, dy = p1.y - p0.y
        let len = max(1, sqrt(dx * dx + dy * dy))
        guard len > clearance * 1.6 else { return }
        // Trim ends so arrows start/stop at the dot edges; bow the path.
        let ux = dx / len, uy = dy / len
        let a = CGPoint(x: p0.x + ux * clearance, y: p0.y + uy * clearance)
        let b = CGPoint(x: p1.x - ux * (clearance + 5), y: p1.y - uy * (clearance + 5))
        let midBow = CGPoint(
            x: (a.x + b.x) / 2 - uy * len * 0.18,
            y: (a.y + b.y) / 2 + ux * len * 0.18)
        var path = Path()
        path.move(to: a)
        path.addQuadCurve(to: b, control: midBow)
        ctx.stroke(path, with: .color(color),
                   style: StrokeStyle(lineWidth: 1.8, lineCap: .round, dash: [5, 4]))
        // Arrow head at b, oriented along the curve end.
        let hx = b.x - midBow.x, hy = b.y - midBow.y
        let hlen = max(1, sqrt(hx * hx + hy * hy))
        let hux = hx / hlen, huy = hy / hlen
        var head = Path()
        let s: CGFloat = 6
        head.move(to: CGPoint(x: b.x + hux * s, y: b.y + huy * s))
        head.addLine(to: CGPoint(x: b.x - huy * s * 0.6, y: b.y + hux * s * 0.6))
        head.addLine(to: CGPoint(x: b.x + huy * s * 0.6, y: b.y - hux * s * 0.6))
        head.closeSubpath()
        ctx.fill(head, with: .color(color))
    }
}
