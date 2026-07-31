// HandSceneLibrary.swift  (ToneForgeEngine)
//
// The 3D "hero" neck+hand scene: a baked MPFB render of the whole fretboard
// WITH the hand naturally holding it (tools/handrig/render_scene.py), one per
// chord under a single fixed camera. `projection` maps (string, fret) -> pixel
// in that render, so the app overlays the interactive dots / playhead onto the
// angled neck. Companion to HandPoseLibrary (which is the flat-board overlay).

import SwiftUI
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

public struct HandSceneLibrary {
    /// (string, fret) -> pixel grid in the render, plus its resolution.
    public struct Projection: Decodable {
        public let res: [Double]          // [width, height] px
        public let frets: Int             // grid covers fret 0...frets
        public let grid: [[[Double]]]     // grid[string 0..5][fret 0..frets] = [px, py]

        /// Pixel for a (possibly fractional) string/fret, bilinearly interpolated.
        public func pixel(string: Double, fret: Double) -> CGPoint? {
            guard !grid.isEmpty, let cols = grid.first?.count, cols > 0 else { return nil }
            let s = max(0, min(Double(grid.count - 1), string))
            let f = max(0, min(Double(cols - 1), fret))
            let s0 = Int(s.rounded(.down)), f0 = Int(f.rounded(.down))
            let s1 = min(grid.count - 1, s0 + 1), f1 = min(cols - 1, f0 + 1)
            let ds = s - Double(s0), df = f - Double(f0)
            func pt(_ si: Int, _ fi: Int) -> CGPoint { CGPoint(x: grid[si][fi][0], y: grid[si][fi][1]) }
            let a = pt(s0, f0), b = pt(s0, f1), c = pt(s1, f0), d = pt(s1, f1)
            let top = CGPoint(x: a.x + (b.x - a.x) * df, y: a.y + (b.y - a.y) * df)
            let bot = CGPoint(x: c.x + (d.x - c.x) * df, y: c.y + (d.y - c.y) * df)
            return CGPoint(x: top.x + (bot.x - top.x) * ds, y: top.y + (bot.y - top.y) * ds)
        }
    }

    private static var cache: [String: Image] = [:]
    private static func slug(_ symbol: String) -> String {
        symbol.replacingOccurrences(of: "#", with: "-sharp")
    }

    public static func sceneImage(for symbol: String) -> Image? {
        if let img = cache[symbol] { return img }
        guard let url = Bundle.module.url(
            forResource: "scene-\(slug(symbol))", withExtension: "png",
            subdirectory: "HandScene") else { return nil }
        #if canImport(UIKit)
        guard let ui = UIImage(contentsOfFile: url.path) else { return nil }
        let img = Image(uiImage: ui)
        #else
        guard let ns = NSImage(contentsOf: url) else { return nil }
        let img = Image(nsImage: ns)
        #endif
        cache[symbol] = img
        return img
    }

    public static let projection: Projection? = {
        guard let url = Bundle.module.url(
            forResource: "neck_projection", withExtension: "json", subdirectory: "HandScene"),
              let data = try? Data(contentsOf: url),
              let p = try? JSONDecoder().decode(Projection.self, from: data)
        else { return nil }
        return p
    }()
}
