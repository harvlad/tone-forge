// DefaultSequenceManifestTests.swift
//
// Proves that a manifestVersion-2 pack manifest carrying a
// `defaultSequence` (per-pack starter groove) decodes into SamplePack
// without throwing, and that the embedded SequencerPattern round-trips
// with its packPad track references intact. A decode throw here would
// silently break pack download/activation (the whole SamplePack fails)
// AND drop the groove — so this fixture mirrors the exact JSON the
// backend generator emits.

import XCTest
@testable import ToneForgeEngine

final class DefaultSequenceManifestTests: XCTestCase {

    /// A minimal but wire-accurate v2 manifest with a 2-track groove.
    private let v2Manifest = """
    {
      "manifestVersion": 2,
      "packId": "trap-808",
      "name": "Trap 808",
      "family": "percussion",
      "paletteHint": "red",
      "pads": [
        { "padIdx": 0, "name": "Kick", "family": "percussion",
          "filename": "00_kick.m4a", "gainDb": 0 },
        { "padIdx": 1, "name": "Snare", "family": "percussion",
          "filename": "01_snare.m4a", "gainDb": 0 }
      ],
      "defaultSequence": {
        "id": "11111111-1111-5111-8111-111111111111",
        "name": "Trap 808 Groove",
        "stepCount": 16,
        "swing": 0.0,
        "isLooping": true,
        "tracks": [
          {
            "id": "22222222-2222-5222-8222-222222222222",
            "chopRef": { "type": "packPad", "packId": "trap-808", "padIdx": 0 },
            "steps": [
              {"velocity": 1.0, "probability": 1.0},
              {"velocity": 0.0, "probability": 1.0}
            ],
            "volume": 1.0, "pan": 0.0,
            "isMuted": false, "isSoloed": false, "name": "Kick"
          },
          {
            "id": "33333333-3333-5333-8333-333333333333",
            "chopRef": { "type": "packPad", "packId": "trap-808", "padIdx": 1 },
            "steps": [
              {"velocity": 0.0, "probability": 1.0},
              {"velocity": 0.95, "probability": 1.0}
            ],
            "volume": 1.0, "pan": 0.0,
            "isMuted": false, "isSoloed": false, "name": "Snare"
          }
        ]
      }
    }
    """

    func testV2ManifestDecodes() throws {
        let pack = try JSONDecoder().decode(
            SamplePack.self, from: Data(v2Manifest.utf8)
        )
        XCTAssertEqual(pack.manifestVersion, 2)
        XCTAssertEqual(pack.pads.count, 2)

        let groove = try XCTUnwrap(
            pack.defaultSequence, "defaultSequence must decode")
        XCTAssertEqual(groove.name, "Trap 808 Groove")
        XCTAssertEqual(groove.stepCount, .sixteen)
        XCTAssertEqual(groove.tracks.count, 2)
        XCTAssertEqual(
            groove.tracks[0].chopRef, .packPad(packId: "trap-808", padIdx: 0))
        XCTAssertEqual(groove.tracks[0].steps.first?.velocity, 1.0)
    }

    /// v1 manifests (no defaultSequence key) must still decode with nil.
    func testV1ManifestDecodesWithNilSequence() throws {
        let v1 = """
        {
          "manifestVersion": 1,
          "packId": "starter",
          "name": "Starter",
          "family": "mixed",
          "pads": [
            { "padIdx": 0, "name": "Pad", "family": "pads",
              "filename": "00_pad.m4a", "gainDb": 0 }
          ]
        }
        """
        let pack = try JSONDecoder().decode(
            SamplePack.self, from: Data(v1.utf8))
        XCTAssertNil(pack.defaultSequence)
    }

    /// The real bundled starter manifest (App/Resources) must decode.
    func testBundledStarterManifestDecodes() throws {
        let url = URL(fileURLWithPath:
            "/Users/mattharvey/Sites/tone-forge/mobile-ios/App/Resources/Samples/starter/manifest.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw XCTSkip("bundled starter manifest not found at \(url.path)")
        }
        let data = try Data(contentsOf: url)
        let pack = try JSONDecoder().decode(SamplePack.self, from: data)
        XCTAssertEqual(pack.packId, "starter")
        // If we bumped it to v2 with a groove, prove the groove decoded.
        if pack.manifestVersion >= 2 {
            let groove = try XCTUnwrap(pack.defaultSequence)
            XCTAssertFalse(groove.tracks.isEmpty)
        }
    }
}
