// HandPoseLibrary.swift  (ToneForgeEngine)
//
// Canonical anatomical fretting-hand poses, generated OFFLINE by the
// Blender/MPFB reference pipeline (tools/handrig/blender_mpfb_rig.py)
// from the same GuitarVoicing fingerings the app renders. Each entry
// stores world joint chains in guitar space (metres: x along the neck
// from the nut, z up from the board centreline, y depth away from the
// viewer).
//
// At runtime the pose is re-anchored into the UI fret window via the
// px/mm factor, and each finger chain gets a TIP CORRECTION toward the
// live animatable fingertips — anatomy comes from the rigged hand,
// exact dot landing and chord-transition motion come from the app.

import SwiftUI

public struct HandPoseLibrary {
    public struct Entry: Decodable {
        /// Musical finger "1"…"4" → 4 joints (MCP, PIP, DIP, tip),
        /// each [x, y, z] in metres.
        public let fingers: [String: [[Double]]]
        public let wrist: [Double]
        public let thumbTip: [Double]
        /// Pressed-cluster centre the pose was authored around.
        public let clusterX: Double
        /// z of the board's low (high-e) edge in the authoring scene.
        public let boardBottomZ: Double
    }

    public let entries: [String: Entry]

    public static let shared: HandPoseLibrary = {
        guard let url = Bundle.module.url(forResource: "HandPoses", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let entries = try? JSONDecoder().decode([String: Entry].self, from: data)
        else {
            return HandPoseLibrary(entries: [:])
        }
        return HandPoseLibrary(entries: entries)
    }()

    public func entry(for symbol: String?) -> Entry? {
        guard let symbol else { return nil }
        return entries[symbol]
    }

    /// Project a library pose into UI space and build finger chains.
    ///
    /// - anchorX: UI x of the chord's pressed-cluster centre.
    /// - boardBottomY: UI y of the board's bottom edge.
    /// - pxPerMM: the shared physical scale factor.
    /// - liveTips: animatable fingertip positions (UI px) — each chain
    ///   is warped so its tip lands exactly there (weights ramp from
    ///   the knuckle), which both corrects authoring drift and carries
    ///   chord-transition animation through the anatomical pose.
    public static func chains(
        entry: Entry, anchorX: CGFloat, boardBottomY: CGFloat,
        pxPerMM: CGFloat, liveTips: [CGPoint], lifted: Bool
    ) -> (fingers: [FingerChain], wrist: CGPoint) {
        let s = pxPerMM * 1000            // px per metre
        let liftY: CGFloat = lifted ? -10 * pxPerMM : 0
        func project(_ p: [Double]) -> CGPoint {
            CGPoint(
                x: anchorX + (p[0] - entry.clusterX) * s,
                y: boardBottomY + (entry.boardBottomZ - p[2]) * s + liftY)
        }
        var fingers: [FingerChain] = []
        let corrWeights: [CGFloat] = [0, 0.35, 0.8, 1]
        for fi in 1...4 {
            guard let raw = entry.fingers[String(fi)], raw.count == 4 else { continue }
            var joints = raw.map(project)
            if fi - 1 < liveTips.count, liveTips[fi - 1] != .zero {
                let delta = CGPoint(
                    x: liveTips[fi - 1].x - joints[3].x,
                    y: liveTips[fi - 1].y + liftY - joints[3].y)
                for k in 0..<4 {
                    joints[k].x += delta.x * corrWeights[k]
                    joints[k].y += delta.y * corrWeights[k]
                }
            }
            fingers.append(FingerChain(
                joints: joints,
                widthPx: HandSkeleton.fingerWidth[fi - 1] * pxPerMM,
                isBarre: false,
                pressing: true))
        }
        return (fingers, project(entry.wrist))
    }
}
