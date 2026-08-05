// NeckHandRenderHarness.swift
//
// Dev-only render harness for iterating the vector hand: writes
// chord-posed PNGs of GuitarNeckPlaySurface to
// TONEFORGE_HANDCHECK_DIR. Skipped unless that env var is set, so CI
// never runs it.

#if canImport(UIKit)
import XCTest
import SwiftUI
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class NeckHandRenderHarness: XCTestCase {
    func testRenderChordPoses() throws {
        guard let dir = ProcessInfo.processInfo.environment["TONEFORGE_HANDCHECK_DIR"],
              !dir.isEmpty else {
            throw XCTSkip("TONEFORGE_HANDCHECK_DIR not set")
        }
        let out = URL(fileURLWithPath: dir, isDirectory: true)
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

        // Spike progression (G → D → Em → C) + stress poses, rendered
        // in BOTH modes: silhouette and skeleton-debug. The debug view
        // is the only place to judge the IK — never the silhouette.
        for debug in [false, true] {
            if debug { setenv("TONEFORGE_HAND_DEBUG", "1", 1) }
            else { unsetenv("TONEFORGE_HAND_DEBUG") }
            for (name, current, next) in [
                ("G", "G", Optional("D")),
                ("D", "D", Optional("Em")),
                ("Em", "Em", Optional("C")),
                ("C", "C", Optional("G")),
                ("Fsharp", "F#", Optional("Bm")),
                ("Gm", "Gm", nil),
                ("Am", "Am", nil),
            ] {
                let view = GuitarNeckPlaySurface(current: current, transitionTo: next)
                    .frame(width: 720, height: 420)
                    .background(Color(red: 0.05, green: 0.05, blue: 0.07))
                    .environment(\.colorScheme, .dark)
                let renderer = ImageRenderer(content: view)
                renderer.scale = 2
                guard let img = renderer.uiImage, let png = img.pngData() else {
                    XCTFail("render failed for \(name)"); continue
                }
                let suffix = debug ? "-debug" : ""
                try png.write(to: out.appendingPathComponent("hand-\(name)\(suffix).png"))
            }
        }
        unsetenv("TONEFORGE_HAND_DEBUG")

        // Mid-transition frame: targets halfway D → Em, hand lifted —
        // proves the solver produces a valid articulated pose at any
        // interpolated target set (what SwiftUI animates through).
        if let a = GuitarVoicing.shape(symbol: "D"),
           let b = GuitarVoicing.shape(symbol: "Em") {
            let size = CGSize(width: 720, height: 420)
            let geo = NeckGeometry(size: size, baseFret: 1)
            let pa = HandPlan.plan(fingering: ChordFingering.assign(shape: a), geo: geo)
            let pb = HandPlan.plan(fingering: ChordFingering.assign(shape: b), geo: geo)
            var mid = pa
            mid.tips = zip(pa.tips, pb.tips).map {
                CGPoint(x: ($0.x + $1.x) / 2, y: ($0.y + $1.y) / 2)
            }
            let view = ZStack {
                HandSilhouetteView(plan: mid, lifted: true)
            }
            .frame(width: size.width, height: size.height)
            .background(Color(red: 0.05, green: 0.05, blue: 0.07))
            let renderer = ImageRenderer(content: view)
            renderer.scale = 2
            if let img = renderer.uiImage, let png = img.pngData() {
                try png.write(to: out.appendingPathComponent("hand-mid-D-Em.png"))
            }
        }
    }
}
#endif
