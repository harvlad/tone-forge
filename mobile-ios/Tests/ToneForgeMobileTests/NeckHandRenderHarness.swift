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

        for (name, current, next) in [
            ("D", "D", Optional("F#")),
            ("Fsharp", "F#", Optional("Bm")),
            ("C", "C", Optional("G")),
            ("Am", "Am", nil),
            ("G", "G", Optional("Gm")),
            ("Gm", "Gm", Optional("A#")),
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
            try png.write(to: out.appendingPathComponent("hand-\(name).png"))
        }
    }
}
#endif
