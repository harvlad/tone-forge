// PadSampleMetadataCodableTests.swift
//
// Freezes the PadSampleMetadata v1 wire shape (frozen-JSON fixture)
// and pins the neverUpload compliance tripwire: mic/vocoded samples
// can never be marked uploadable — not via init, and not via a
// hand-edited sidecar JSON.

import XCTest
@testable import ToneForgeEngine

final class PadSampleMetadataCodableTests: XCTestCase {

    private let fixedId = UUID(uuidString: "6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB")!
    // 2026-01-02T03:04:05Z in timeIntervalSinceReferenceDate.
    private let fixedDate = Date(timeIntervalSinceReferenceDate: 789_015_845)

    private func makeMic() -> PadSampleMetadata {
        PadSampleMetadata(
            id: fixedId,
            source: .mic,
            classification: .percussion,
            confidence: 0.82,
            createdAt: fixedDate,
            durationSec: 1.75,
            sampleRate: 48_000,
            channels: 1,
            colorHint: 0xFF8C3A
        )
    }

    // MARK: - Frozen v1 wire shape

    /// The exact v1 JSON a P3 build writes. If this test breaks, the
    /// wire format changed — that requires a schemaVersion bump and a
    /// migration path, not a fixture update.
    func testFrozenV1FixtureDecodes() throws {
        let json = """
        {
          "schemaVersion": 1,
          "id": "6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB",
          "source": "mic",
          "classification": "percussion",
          "confidence": 0.82,
          "createdAt": 789015845,
          "durationSec": 1.75,
          "sampleRate": 48000,
          "channels": 1,
          "colorHint": 16747578,
          "neverUpload": true
        }
        """
        let decoded = try JSONDecoder().decode(
            PadSampleMetadata.self, from: Data(json.utf8)
        )
        XCTAssertEqual(decoded, makeMic())
    }

    func testRoundTripAllFields() throws {
        let original = PadSampleMetadata(
            id: fixedId,
            source: .vocoded,
            classification: .vocalChop,
            confidence: 0.6,
            userClassOverride: .phrase,
            createdAt: fixedDate,
            durationSec: 8.0,
            sampleRate: 48_000,
            channels: 1,
            colorHint: 0x9B4DFF,
            vocoderMode: 2,
            sourceSongId: "song-42"
        )
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(PadSampleMetadata.self, from: data)
        XCTAssertEqual(decoded, original)
    }

    func testEncodedKeysMatchFrozenNames() throws {
        let data = try JSONEncoder().encode(makeMic())
        let obj = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(
            Set(obj.keys),
            [
                "schemaVersion", "id", "source", "classification",
                "confidence", "createdAt", "durationSec", "sampleRate",
                "channels", "colorHint", "neverUpload",
            ]
        )
        XCTAssertEqual(obj["source"] as? String, "mic")
        XCTAssertEqual(obj["classification"] as? String, "percussion")
    }

    // MARK: - Forward compatibility

    func testUnknownClassificationDegradesToUnknown() throws {
        let json = """
        {
          "id": "6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB",
          "source": "songChop",
          "classification": "laser_zap_v9",
          "createdAt": 789015845,
          "durationSec": 0.5,
          "sampleRate": 48000
        }
        """
        let decoded = try JSONDecoder().decode(
            PadSampleMetadata.self, from: Data(json.utf8)
        )
        XCTAssertEqual(decoded.classification, .unknown)
        // Optional-field defaults.
        XCTAssertEqual(decoded.schemaVersion, 1)
        XCTAssertEqual(decoded.confidence, 0)
        XCTAssertEqual(decoded.channels, 1)
        XCTAssertEqual(decoded.colorHint, 0)
        XCTAssertNil(decoded.userClassOverride)
        XCTAssertNil(decoded.vocoderMode)
        XCTAssertNil(decoded.sourceSongId)
    }

    // MARK: - neverUpload tripwire

    func testMicAndVocodedAlwaysNeverUpload() {
        for source: PadSampleMetadata.Source in [.mic, .vocoded] {
            let meta = PadSampleMetadata(
                source: source,
                classification: .unknown,
                confidence: 0,
                durationSec: 1,
                sampleRate: 48_000,
                channels: 1,
                colorHint: 0,
                neverUpload: false  // caller tries to flip it
            )
            XCTAssertTrue(meta.neverUpload, "\(source) must be neverUpload")
        }
    }

    func testHandEditedSidecarCannotFlipNeverUpload() throws {
        let json = """
        {
          "id": "6F9B2C4D-1A2B-4C3D-8E5F-0123456789AB",
          "source": "mic",
          "classification": "percussion",
          "createdAt": 789015845,
          "durationSec": 1.0,
          "sampleRate": 48000,
          "neverUpload": false
        }
        """
        let decoded = try JSONDecoder().decode(
            PadSampleMetadata.self, from: Data(json.utf8)
        )
        XCTAssertTrue(decoded.neverUpload)
    }

    func testSongChopMayOptIntoUploadability() {
        let meta = PadSampleMetadata(
            source: .songChop,
            classification: .percussion,
            confidence: 1,
            durationSec: 1,
            sampleRate: 48_000,
            channels: 1,
            colorHint: 0,
            neverUpload: false
        )
        XCTAssertFalse(meta.neverUpload)
    }

    // MARK: - effectiveClass

    func testEffectiveClassPrefersUserOverride() {
        var meta = makeMic()
        XCTAssertEqual(meta.effectiveClass, .percussion)
        meta.userClassOverride = .speechWord
        XCTAssertEqual(meta.effectiveClass, .speechWord)
    }
}
