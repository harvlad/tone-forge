// BeatKitPackTests.swift
//
// Beat Capture (D-024): the bundled 7-piece beatkit resolves from the
// JamDesktopAudio resource bundle with all seven percussion pads and
// on-disk one-shot files.

import XCTest
@testable import JamDesktopAudio
import ToneForgeEngine

final class BeatKitPackTests: XCTestCase {

    func testResolveBundledBeatKit() throws {
        let resolved = try BeatKitPack.resolve()
        XCTAssertEqual(resolved.pack.packId, "beatkit")
        XCTAssertEqual(resolved.pack.pads.count, 7)
        // Every pad has a resolved on-disk one-shot.
        XCTAssertEqual(resolved.padFileURLs.count, 7)
        for idx in 0..<7 {
            let url = try XCTUnwrap(resolved.padFileURLs[idx])
            XCTAssertTrue(
                FileManager.default.fileExists(atPath: url.path),
                "missing beatkit pad file for idx \(idx)"
            )
        }
    }
}
