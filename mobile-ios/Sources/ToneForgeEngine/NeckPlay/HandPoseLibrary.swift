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
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

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
        /// Projected 2D silhouette contour(s) of the posed MPFB mesh,
        /// rings of [x, z] in metres — the exact rendered outline.
        public let outline: [[[Double]]]?
        public struct Sprite: Decodable {
            public let camX: Double, camZ: Double
            public let ortho: Double
            public let w: Double, h: Double
        }
        /// Baked transparent render of the posed hand (Freestyle lines
        /// + fill, thumb clipped by the neck) with camera metadata for
        /// re-projection.
        public let sprite: Sprite?
    }

    /// Sprite image cache (per chord).
    private static var spriteCache: [String: Image] = [:]

    /// "#" is illegal in a bundled resource lookup — sprite files use
    /// "-sharp" (see the export pipeline). Keep this in sync.
    private static func spriteSlug(_ symbol: String) -> String {
        symbol.replacingOccurrences(of: "#", with: "-sharp")
    }

    public static func spriteImage(for symbol: String) -> Image? {
        if let img = spriteCache[symbol] { return img }
        guard let url = Bundle.module.url(
            forResource: "sprite-\(spriteSlug(symbol))", withExtension: "png",
            subdirectory: "HandSprites") else { return nil }
        #if canImport(UIKit)
        guard let ui = UIImage(contentsOfFile: url.path) else { return nil }
        let img = Image(uiImage: ui)
        #else
        guard let ns = NSImage(contentsOf: url) else { return nil }
        let img = Image(nsImage: ns)
        #endif
        spriteCache[symbol] = img
        return img
    }

    /// UI rect covering the sprite's full camera frame, re-anchored
    /// into the fret window.
    public static func spriteRect(
        entry: Entry, anchorX: CGFloat, boardBottomY: CGFloat,
        pxPerMM: CGFloat, lifted: Bool
    ) -> CGRect? {
        guard let sp = entry.sprite else { return nil }
        let s = pxPerMM * 1000
        let liftY: CGFloat = lifted ? -10 * pxPerMM : 0
        let orthoV = sp.ortho * sp.h / sp.w
        let leftM = sp.camX - sp.ortho / 2
        let topM = sp.camZ + orthoV / 2
        let x = anchorX + (leftM - entry.clusterX) * s
        let y = boardBottomY + (entry.boardBottomZ - topM) * s + liftY
        return CGRect(x: x, y: y, width: sp.ortho * s, height: orthoV * s)
    }

    /// Build a filled silhouette Path from a pose's mesh contour,
    /// re-anchored into the UI fret window.
    public static func outlinePath(
        entry: Entry, anchorX: CGFloat, boardBottomY: CGFloat,
        pxPerMM: CGFloat, lifted: Bool
    ) -> Path? {
        guard let rings = entry.outline, !rings.isEmpty else { return nil }
        let s = pxPerMM * 1000
        let liftY: CGFloat = lifted ? -10 * pxPerMM : 0
        var p = Path()
        for ring in rings where ring.count > 2 {
            let pts = ring.map { pt in
                CGPoint(
                    x: anchorX + (pt[0] - entry.clusterX) * s,
                    y: boardBottomY + (entry.boardBottomZ - pt[1]) * s + liftY)
            }
            p.move(to: pts[0])
            for q in pts.dropFirst() { p.addLine(to: q) }
            p.closeSubpath()
        }
        return p
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
