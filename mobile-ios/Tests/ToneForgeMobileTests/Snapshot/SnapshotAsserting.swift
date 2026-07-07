// SnapshotAsserting.swift
//
// Hand-rolled golden-PNG snapshot assertion. No third-party deps.
//
// How it works
// ------------
//   1. The view is rendered with SwiftUI's ImageRenderer at scale 2,
//      dark color scheme, inside a fixed frame.
//   2. The CGImage is redrawn into a fixed-sRGB RGBA8 CGContext so
//      the pixel layout is deterministic regardless of the source
//      image's native format.
//   3. The golden PNG is decoded through the SAME context path (never
//      compare PNG bytes — encoders differ across OS releases).
//   4. Pixels compare with a small per-channel tolerance plus an
//      allowed fraction of differing pixels, absorbing minor AA/text
//      raster drift between simulator runtimes.
//
// Recording goldens
// -----------------
//   TEST_RUNNER_TONEFORGE_SNAPSHOT_RECORD=1 \
//   TEST_RUNNER_TONEFORGE_SNAPSHOT_DIR=$PWD/Tests/ToneForgeMobileTests/Fixtures/Goldens \
//   xcodebuild test … -only-testing:ToneForgeMobileTests/JamScreenSnapshotTests
//
// (xcodebuild forwards TEST_RUNNER_-prefixed variables into the test
// process.) Record mode writes the PNG then fails the test so a
// recording run can't silently pass in CI.
//
// UIKit-only: on macOS `swift test` these helpers don't exist and the
// snapshot tests XCTSkip.

#if canImport(UIKit)

import XCTest
import SwiftUI
import UIKit

/// Raw RGBA8 pixels in a fixed sRGB layout.
struct SnapshotPixels {
    let width: Int
    let height: Int
    let rgba: [UInt8]
}

enum SnapshotError: Error, CustomStringConvertible {
    case renderFailed
    case decodeFailed(URL)
    case contextFailed

    var description: String {
        switch self {
        case .renderFailed: return "ImageRenderer produced no image"
        case .decodeFailed(let url): return "Could not decode PNG at \(url.path)"
        case .contextFailed: return "Could not create sRGB bitmap context"
        }
    }
}

/// Render `view` at `size` (points, scale 2) and compare against the
/// bundled golden `Fixtures/Goldens/{named}.png`.
///
/// - Parameters:
///   - maxPixelDelta: per-channel tolerance (0–255) below which two
///     pixels count as equal.
///   - maxDifferingFraction: fraction of pixels allowed to exceed the
///     tolerance before the assertion fails.
@MainActor
func assertSnapshot<V: View>(
    of view: V,
    size: CGSize,
    named name: String,
    maxPixelDelta: Int = 2,
    maxDifferingFraction: Double = 0.005,
    file: StaticString = #filePath,
    line: UInt = #line
) throws {
    let content = view
        .frame(width: size.width, height: size.height)
        .environment(\.colorScheme, .dark)

    let renderer = ImageRenderer(content: content)
    renderer.scale = 2
    renderer.proposedSize = ProposedViewSize(size)
    guard let cgImage = renderer.cgImage else {
        throw SnapshotError.renderFailed
    }

    let env = ProcessInfo.processInfo.environment
    if env["TONEFORGE_SNAPSHOT_RECORD"] == "1" {
        guard let dirPath = env["TONEFORGE_SNAPSHOT_DIR"], !dirPath.isEmpty else {
            XCTFail(
                "TONEFORGE_SNAPSHOT_RECORD=1 requires TONEFORGE_SNAPSHOT_DIR",
                file: file, line: line
            )
            return
        }
        let dir = URL(fileURLWithPath: dirPath, isDirectory: true)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("\(name).png")
        guard let png = UIImage(cgImage: cgImage).pngData() else {
            throw SnapshotError.renderFailed
        }
        try png.write(to: url, options: [.atomic])
        XCTFail(
            "Recorded golden \(url.path) — re-run without record mode",
            file: file, line: line
        )
        return
    }

    guard let goldenURL = Bundle.module.url(
        forResource: name, withExtension: "png", subdirectory: "Fixtures/Goldens"
    ) else {
        XCTFail(
            """
            Missing golden '\(name).png'. Record it with \
            TEST_RUNNER_TONEFORGE_SNAPSHOT_RECORD=1 \
            TEST_RUNNER_TONEFORGE_SNAPSHOT_DIR=<repo>/Tests/ToneForgeMobileTests/Fixtures/Goldens
            """,
            file: file, line: line
        )
        return
    }

    let actual = try rgbaPixels(from: cgImage)
    let golden = try rgbaPixels(fromPNGAt: goldenURL)

    guard actual.width == golden.width, actual.height == golden.height else {
        XCTFail(
            "Snapshot '\(name)' size mismatch: actual \(actual.width)×\(actual.height), " +
            "golden \(golden.width)×\(golden.height)",
            file: file, line: line
        )
        return
    }

    let pixelCount = actual.width * actual.height
    var differing = 0
    for p in 0..<pixelCount {
        let i = p * 4
        for c in 0..<4 where abs(Int(actual.rgba[i + c]) - Int(golden.rgba[i + c])) > maxPixelDelta {
            differing += 1
            break
        }
    }
    let fraction = Double(differing) / Double(pixelCount)
    if fraction > maxDifferingFraction {
        // Dump the actual render next to tmp for diffing.
        let dumpURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("snapshot-failed-\(name).png")
        if let png = UIImage(cgImage: cgImage).pngData() {
            try? png.write(to: dumpURL)
        }
        XCTFail(
            String(
                format: "Snapshot '%@' differs: %.3f%% of pixels beyond ±%d (allowed %.3f%%). Actual: %@",
                name, fraction * 100, maxPixelDelta, maxDifferingFraction * 100, dumpURL.path
            ),
            file: file, line: line
        )
    }
}

// MARK: - Pixel extraction

/// Redraw `image` into a deterministic sRGB RGBA8 context and return
/// the raw bytes. Both the fresh render and the decoded golden pass
/// through here so format/premultiplication quirks cancel out.
func rgbaPixels(from image: CGImage) throws -> SnapshotPixels {
    let width = image.width
    let height = image.height
    var rgba = [UInt8](repeating: 0, count: width * height * 4)

    guard
        let colorSpace = CGColorSpace(name: CGColorSpace.sRGB),
        let context = CGContext(
            data: &rgba,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width * 4,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                | CGBitmapInfo.byteOrder32Big.rawValue
        )
    else {
        throw SnapshotError.contextFailed
    }
    context.interpolationQuality = .none
    context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    return SnapshotPixels(width: width, height: height, rgba: rgba)
}

func rgbaPixels(fromPNGAt url: URL) throws -> SnapshotPixels {
    guard
        let data = try? Data(contentsOf: url),
        let image = UIImage(data: data)?.cgImage
    else {
        throw SnapshotError.decodeFailed(url)
    }
    return try rgbaPixels(from: image)
}

#endif
