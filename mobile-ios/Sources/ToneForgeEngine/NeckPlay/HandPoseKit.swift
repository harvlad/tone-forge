// HandPoseKit.swift  (ToneForgeEngine)
//
// Articulated fretting-hand pose pipeline (see docs/FRETTING_HAND_SPIKE.md):
//
//   fingertip targets (guitar space) → constrained analytic IK →
//   3D hand pose → oblique orthographic projection → 2D silhouette.
//
// We POSE a hand and derive the silhouette from the pose — we never
// draw the silhouette directly. Pure Swift, µs-cheap, solved every
// frame so SwiftUI's fingertip interpolation animates the whole
// articulated hand through valid poses.

import SwiftUI

// MARK: - Physical guitar space (millimetres)

/// Canonical physical guitar geometry. The UI scales this into the
/// available rect via one px/mm factor; hand anatomy uses the same
/// factor, so hand-to-neck proportions are physically correct by
/// construction.
public enum GuitarPhysical {
    public static let scaleLength: CGFloat = 648      // mm, standard
    public static let stringSpanNut: CGFloat = 35     // E→e at the nut
    public static let stringSpanSaddle: CGFloat = 52  // E→e at the saddle
    public static let neckThickness: CGFloat = 21     // thumb sits behind

    /// Fret-wire distance from the nut, equal temperament:
    /// d(n) = scale · (1 − 2^(−n/12)).
    public static func wire(_ n: Int) -> CGFloat {
        n <= 0 ? 0 : scaleLength * (1 - pow(2, -CGFloat(n) / 12))
    }

    /// Finger contact point for a fret: 30% of the slot behind the wire.
    public static func fingerX(_ fret: Int) -> CGFloat {
        let lo = wire(fret - 1), hi = wire(fret)
        return hi - (hi - lo) * 0.30
    }

    /// String-to-string gap at a distance from the nut (linear taper).
    public static func stringGapMM(atX x: CGFloat) -> CGFloat {
        (stringSpanNut + (stringSpanSaddle - stringSpanNut)
            * max(0, min(1, x / scaleLength))) / 5
    }
}

// MARK: - Hand skeleton spec (millimetres, anthropometric means)

/// Segment lengths / joint limits for the solved skeleton. Order is
/// always index, middle, ring, pinky (fingers 1…4).
public enum HandSkeleton {
    public static let proximal: [CGFloat] = [45, 50, 46, 37]
    public static let middleSeg: [CGFloat] = [25, 29, 27, 20]
    public static let distal: [CGFloat] = [22, 24, 24, 19]
    public static let fingerWidth: [CGFloat] = [17, 18, 16.5, 14]

    /// MCP offsets along the knuckle line from the hand centre.
    /// Index is toward the nut (RIGHT in the UI).
    public static let mcpOffset: [CGFloat] = [28, 9, -11, -30]
    /// Knuckle arch: middle knuckle proudest (closest to the board).
    public static let mcpArch: [CGFloat] = [2, 5, 3, -2]

    public static let wristToMCP: CGFloat = 95
    /// Knuckle-row height over the board plane.
    public static let mcpHeight: CGFloat = 32
    /// Knuckle row sits this far beyond the high-e edge of the board.
    public static let mcpBelowBoard: CGFloat = 16

    public static let pipMaxFlex: CGFloat = 110 * .pi / 180
    /// Natural tendon coupling.
    public static let dipToPip: CGFloat = 0.67

    public static func totalLength(_ i: Int) -> CGFloat {
        proximal[i] + middleSeg[i] + distal[i]
    }
}

// MARK: - Pose types

struct Joint3 {
    var x: CGFloat, y: CGFloat, z: CGFloat   // px; z toward the viewer
}

/// One solved finger: projected joint chain MCP → PIP → DIP → tip.
public struct FingerChain {
    public var joints: [CGPoint]     // 4 projected joints
    public var widthPx: CGFloat
    public var isBarre: Bool
    public var pressing: Bool
}

/// Full projected pose (plus debug data).
public struct HandPose {
    public var fingers: [FingerChain]        // index…pinky
    public var wrist: CGPoint
    public var thumb: CGPoint                // behind the neck (occluded)
    public var targets: [CGPoint?]           // debug: fingertip targets
}

// MARK: - Solver

public enum HandPoseSolver {

    public struct Target {
        public var point: CGPoint   // px, board plane
        public var zMM: CGFloat     // height over the board (0 = pressing)
        public var press: Bool
        public var barre: Bool
        public init(point: CGPoint, zMM: CGFloat, press: Bool, barre: Bool) {
            self.point = point; self.zMM = zMM
            self.press = press; self.barre = barre
        }
    }

    /// Oblique orthographic projection: things closer to the viewer
    /// shift slightly down — front view with a subtle depth cue.
    static func project(_ j: Joint3) -> CGPoint {
        CGPoint(x: j.x, y: j.y + j.z * 0.30)
    }

    /// Solve the articulated pose for four fingertip targets.
    /// `targets` is per finger 1…4 (never nil here — rest fingers get
    /// hover targets from the plan). All px; `s` = px per mm.
    public static func solve(
        targets: [Target], neckBottom: CGFloat, s: CGFloat, lifted: Bool
    ) -> HandPose {
        let liftZ: CGFloat = lifted ? 18 : 0     // mm off the strings
        let liftY: CGFloat = lifted ? 5 : 0

        // Hand centre from the PRESSING cluster (rest fingers follow).
        // When targets bunch on one fret column the knuckle spread
        // compresses (real fingers converge from the MCPs without
        // crossing); wide chords use the full anatomical spread.
        let press = targets.enumerated().filter { $0.element.press }
        let pressXs = press.map { $0.element.point.x }
        let spanX = (pressXs.max() ?? 0) - (pressXs.min() ?? 0)
        let spread = max(0.4, min(1, spanX / (55 * s)))
        let centered = press.map {
            $0.element.point.x - HandSkeleton.mcpOffset[$0.offset] * s * spread * 0.7
        }
        let centerX = centered.isEmpty
            ? targets.map(\.point.x).reduce(0, +) / 4
            : centered.reduce(0, +) / CGFloat(centered.count)

        var mcpRowY = neckBottom + HandSkeleton.mcpBelowBoard * s + liftY * s

        func mcp3(_ i: Int, rowY: CGFloat) -> Joint3 {
            Joint3(
                x: centerX + HandSkeleton.mcpOffset[i] * s * spread,
                y: rowY - HandSkeleton.mcpArch[i] * s,
                z: (HandSkeleton.mcpHeight + liftZ) * s)
        }

        // Reach pre-pass: if a pressing finger can't reach its target,
        // pull the knuckle row toward the board.
        for _ in 0..<2 {
            var worst: CGFloat = 0
            for i in 0..<4 where targets[i].press {
                let m = mcp3(i, rowY: mcpRowY)
                let t = targets[i]
                let dz = (t.zMM + liftZ) * s - m.z
                let d = sqrt(
                    pow(t.point.x - m.x, 2) + pow(t.point.y - m.y, 2) + dz * dz)
                let reach = HandSkeleton.totalLength(i) * s * 0.96
                if d > reach { worst = max(worst, d - reach) }
            }
            if worst <= 0.5 { break }
            mcpRowY -= worst * 0.9
        }

        var fingers: [FingerChain] = []
        var debugTargets: [CGPoint?] = []
        for i in 0..<4 {
            let t = targets[i]
            let m = mcp3(i, rowY: mcpRowY)
            let tip: Joint3
            if t.press {
                tip = Joint3(x: t.point.x, y: t.point.y, z: (t.zMM + liftZ) * s)
            } else {
                // Rest fingers hover in a natural curl ABOVE THEIR OWN
                // knuckle, just off the strings — never solved to a
                // faraway point (the plan's legacy rest spots produced
                // absurd cross-hand diagonals).
                tip = Joint3(
                    x: m.x + 6 * s,
                    y: neckBottom - 9 * s,
                    z: (14 + liftZ) * s)
            }
            debugTargets.append(t.press ? t.point : nil)
            let chain = solveFinger(i: i, mcp: m, tip: tip, s: s)
            fingers.append(FingerChain(
                joints: chain.map(project),
                widthPx: HandSkeleton.fingerWidth[i] * s,
                isBarre: t.barre,
                pressing: t.press))
        }

        let wrist = project(Joint3(
            x: centerX + 10 * s,
            y: mcpRowY + HandSkeleton.wristToMCP * 0.58 * s,
            z: (HandSkeleton.mcpHeight - 6 + liftZ) * s))
        let thumb = project(Joint3(
            x: centerX + 12 * s,
            y: neckBottom - 3.2 * GuitarPhysical.stringGapMM(atX: 100) * s,
            z: -GuitarPhysical.neckThickness * s))

        return HandPose(
            fingers: fingers, wrist: wrist, thumb: thumb,
            targets: debugTargets)
    }

    /// Analytic 3-segment finger solve, planar in the finger's flexion
    /// plane. Curl parameter c ∈ [0,1] drives PIP (0…110°) with
    /// DIP = 0.67·PIP tendon coupling; MCP orientation aims the curled
    /// chain at the target. Bisection on c matches the reach — closed,
    /// deterministic, joint-limit safe.
    private static func solveFinger(
        i: Int, mcp: Joint3, tip: Joint3, s: CGFloat
    ) -> [Joint3] {
        let L1 = HandSkeleton.proximal[i] * s
        let L2 = HandSkeleton.middleSeg[i] * s
        let L3 = HandSkeleton.distal[i] * s

        // Flexion plane: horizontal axis toward the target in XY,
        // vertical axis = z. The finger curls "down-plane" (toward the
        // board) exactly like a fretting finger.
        let dx = tip.x - mcp.x, dy = tip.y - mcp.y
        let r = max(0.001, sqrt(dx * dx + dy * dy))
        let ux = dx / r, uy = dy / r
        let dv = tip.z - mcp.z
        let dist = min(sqrt(r * r + dv * dv), (L1 + L2 + L3) * 0.995)

        // Exact two-link IK in the flexion plane (u toward the target
        // in XY, v = +z toward the viewer): link A = proximal, link
        // B = middle+distal welded at a fixed DIP pre-bend. The PIP
        // "elbow" is constrained to the viewer side of the MCP→tip
        // chord — the finger arches over the string and drops onto it,
        // never overshooting past the target.
        // DIP pre-bend is ADAPTIVE: relaxed (22°) for normal reaches,
        // deepening toward 72° for close targets — a real finger curls
        // its distal joint to reach near its own palm. PIP flexion is
        // capped at ~115°; the chord is clamped only if even the
        // deepest DIP can't close the distance.
        let A = L1
        let interiorMin = CGFloat.pi - 115 * .pi / 180
        func bLink(_ d: CGFloat) -> CGFloat {
            sqrt(L2 * L2 + L3 * L3 + 2 * L2 * L3 * cos(d))
        }
        func dMin(_ d: CGFloat) -> CGFloat {
            let b = bLink(d)
            return sqrt(max(1, A * A + b * b - 2 * A * b * cos(interiorMin)))
        }
        var delta: CGFloat = 22 * .pi / 180
        let deltaMax: CGFloat = 72 * .pi / 180
        if dist < dMin(delta) {
            var lo = delta, hi = deltaMax
            for _ in 0..<12 {
                let mid = (lo + hi) / 2
                if dMin(mid) > dist { lo = mid } else { hi = mid }
            }
            delta = min(deltaMax, hi)
        }
        let B = bLink(delta)
        // Angle of L2 within the welded B link.
        let beta = atan2(L3 * sin(delta), L2 + L3 * cos(delta))
        let D = min(max(dist, dMin(delta)), (A + B) * 0.995)
        let chord = atan2(dv, r)
        // The planar tip sits at distance D along the chord — when the
        // reach clamps, the finger lands SHORT along the target ray
        // instead of stretching its segments.
        let tipU = D * cos(chord), tipV = D * sin(chord)
        let cosElbow = (A * A + D * D - B * B) / (2 * A * D)
        let elbowOff = acos(min(1, max(-1, cosElbow)))
        let a1 = chord + elbowOff                        // elbow on +v side

        let pip = (u: A * cos(a1), v: A * sin(a1))
        let phiB = atan2(tipV - pip.v, tipU - pip.u)
        let dip = (u: pip.u + L2 * cos(phiB + beta),
                   v: pip.v + L2 * sin(phiB + beta))
        let planar: [(CGFloat, CGFloat)] = [
            (0, 0), (pip.u, pip.v), (dip.u, dip.v), (tipU, tipV),
        ]

        return planar.map { p in
            Joint3(x: mcp.x + ux * p.0, y: mcp.y + uy * p.0, z: mcp.z + p.1)
        }
    }
}

// MARK: - Silhouette + debug rendering helpers

public enum HandPoseRender {

    /// Smooth resample of a joint chain: subdivide the polyline, then
    /// Chaikin corner-cutting. Unlike Catmull-Rom this NEVER overshoots
    /// a sharp elbow — the spine stays inside the joint polygon.
    static func resample(_ joints: [CGPoint], samples n: Int) -> [CGPoint] {
        guard joints.count >= 2 else { return joints }
        // Subdivide each bone so corner cutting has material to round.
        var pts: [CGPoint] = []
        for k in 0..<(joints.count - 1) {
            let a = joints[k], b = joints[k + 1]
            for step in 0..<3 {
                let t = CGFloat(step) / 3
                pts.append(CGPoint(x: a.x + (b.x - a.x) * t,
                                   y: a.y + (b.y - a.y) * t))
            }
        }
        pts.append(joints[joints.count - 1])
        for _ in 0..<2 {   // Chaikin passes
            var next: [CGPoint] = [pts[0]]
            for k in 0..<(pts.count - 1) {
                let a = pts[k], b = pts[k + 1]
                next.append(CGPoint(x: a.x * 0.75 + b.x * 0.25,
                                    y: a.y * 0.75 + b.y * 0.25))
                next.append(CGPoint(x: a.x * 0.25 + b.x * 0.75,
                                    y: a.y * 0.25 + b.y * 0.75))
            }
            next.append(pts[pts.count - 1])
            pts = next
        }
        _ = n
        return pts
    }

    /// Closed capsule path around a finger chain with tapered radii and
    /// a rounded fingertip pad. `from` trims the base end (buried in
    /// the palm) for rim strokes.
    public static func fingerPath(
        _ chain: FingerChain, from startFrac: CGFloat = 0, close: Bool = true
    ) -> Path {
        let spine = resample(chain.joints, samples: 24)
        let n = spine.count - 1
        let start = max(0, min(n - 2, Int(CGFloat(n) * startFrac)))
        // Radii taper knuckle → tip (slight style slimming of the
        // anatomical width so the silhouette matches the mock).
        func radius(_ k: Int) -> CGFloat {
            let t = CGFloat(k) / CGFloat(n)
            let w = chain.widthPx * (chain.isBarre ? 0.50 : 0.44)
            return w * (1.02 - 0.22 * t)
        }
        func normal(_ k: Int) -> CGPoint {
            let a = spine[max(0, k - 1)], b = spine[min(n, k + 1)]
            let dx = b.x - a.x, dy = b.y - a.y
            let len = max(0.001, sqrt(dx * dx + dy * dy))
            return CGPoint(x: -dy / len, y: dx / len)
        }
        func side(_ k: Int, _ sgn: CGFloat) -> CGPoint {
            let nr = normal(k), r = radius(k)
            return CGPoint(x: spine[k].x + nr.x * r * sgn,
                           y: spine[k].y + nr.y * r * sgn)
        }
        var p = Path()
        p.move(to: side(start, -1))
        for k in (start + 1)...n { p.addLine(to: side(k, -1)) }
        // Rounded fingertip pad: sampled semicircle around the tip,
        // swept through the outward tangent so it always bulges past
        // the tip (no arc-API direction surprises).
        let tip = spine[n]
        let prevPt = spine[n - 1]
        let nrmT = normal(n)
        let r = radius(n)
        let aL = atan2(-nrmT.y, -nrmT.x)
        let tangent = atan2(tip.y - prevPt.y, tip.x - prevPt.x)
        // Sweep direction: the semicircle's midpoint must equal the
        // tangent heading. Try both; pick the closer.
        func angDiff(_ a: CGFloat, _ b: CGFloat) -> CGFloat {
            var d = a - b
            while d > .pi { d -= 2 * .pi }
            while d < -.pi { d += 2 * .pi }
            return abs(d)
        }
        let sweep: CGFloat =
            angDiff(aL + .pi / 2, tangent) < angDiff(aL - .pi / 2, tangent)
            ? .pi : -.pi
        for k in 1...8 {
            let a = aL + sweep * CGFloat(k) / 8
            p.addLine(to: CGPoint(x: tip.x + cos(a) * r, y: tip.y + sin(a) * r))
        }
        for k in stride(from: n - 1, through: start, by: -1) {
            p.addLine(to: side(k, +1))
        }
        if close { p.closeSubpath() }
        return p
    }

    /// Palm/forearm silhouette anchored to the solved skeleton: knuckle
    /// arch through the MCPs, sides to the wrist, forearm off-canvas.
    public static func palmPath(_ pose: HandPose, canvasHeight: CGFloat, s: CGFloat) -> Path {
        let mcps = pose.fingers.map { $0.joints[0] }
        guard mcps.count == 4 else { return Path() }
        let idx = mcps[0], pky = mcps[3]
        let wrist = pose.wrist
        let forearmY = canvasHeight + 10
        let halfW = 17 * s   // wrist half-width mm
        var p = Path()
        p.move(to: CGPoint(x: wrist.x - halfW, y: forearmY))
        p.addQuadCurve(
            to: CGPoint(x: pky.x - 10 * s, y: pky.y + 26 * s),
            control: CGPoint(x: wrist.x - halfW - 6 * s, y: wrist.y + 8 * s))
        p.addQuadCurve(
            to: CGPoint(x: pky.x - 7 * s, y: pky.y - 2 * s),
            control: CGPoint(x: pky.x - 12 * s, y: pky.y + 8 * s))
        // Knuckle arch across the MCP row.
        p.addQuadCurve(
            to: CGPoint(x: idx.x + 8 * s, y: idx.y - 1 * s),
            control: CGPoint(
                x: (idx.x + pky.x) / 2, y: min(mcps[1].y, mcps[2].y) - 7 * s))
        p.addQuadCurve(
            to: CGPoint(x: idx.x + 11 * s, y: idx.y + 28 * s),
            control: CGPoint(x: idx.x + 13 * s, y: idx.y + 10 * s))
        p.addQuadCurve(
            to: CGPoint(x: wrist.x + halfW * 0.9, y: forearmY),
            control: CGPoint(x: wrist.x + halfW + 8 * s, y: wrist.y + 10 * s))
        p.closeSubpath()
        return p
    }

    /// Debug skeleton overlay: bones, joints, targets, wrist, thumb.
    public static func drawDebug(
        _ ctx: GraphicsContext, pose: HandPose, s: CGFloat
    ) {
        for f in pose.fingers {
            var bones = Path()
            bones.move(to: f.joints[0])
            for j in f.joints.dropFirst() { bones.addLine(to: j) }
            ctx.stroke(bones, with: .color(.white.opacity(0.9)), lineWidth: 1.5)
            let colors: [Color] = [.green, .yellow, .orange, .red]
            for (k, j) in f.joints.enumerated() {
                let r: CGFloat = k == 0 ? 4 : 3
                ctx.fill(Path(ellipseIn: CGRect(
                    x: j.x - r, y: j.y - r, width: r * 2, height: r * 2)),
                    with: .color(colors[k]))
            }
        }
        for t in pose.targets.compactMap({ $0 }) {
            var cross = Path()
            cross.move(to: CGPoint(x: t.x - 5, y: t.y))
            cross.addLine(to: CGPoint(x: t.x + 5, y: t.y))
            cross.move(to: CGPoint(x: t.x, y: t.y - 5))
            cross.addLine(to: CGPoint(x: t.x, y: t.y + 5))
            ctx.stroke(cross, with: .color(.cyan), lineWidth: 1.4)
        }
        let w = pose.wrist
        ctx.fill(Path(ellipseIn: CGRect(x: w.x - 5, y: w.y - 5, width: 10, height: 10)),
                 with: .color(.purple))
        // Thumb (behind the neck — dashed ghost).
        let th = pose.thumb
        ctx.stroke(
            Path(ellipseIn: CGRect(x: th.x - 7, y: th.y - 7, width: 14, height: 14)),
            with: .color(.mint.opacity(0.8)),
            style: StrokeStyle(lineWidth: 1.2, dash: [3, 3]))
    }
}
