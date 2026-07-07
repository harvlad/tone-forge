// PadTransformCodableTests.swift
//
// Frozen-JSON fixtures for PadTransform's v1 wire shape. These exact
// strings must decode to the expected values, and encoding must
// reproduce them (with sortedKeys). Unknown types throw. PadSlot
// back-compat: P3-era blobs (only `ref`) decode with empty transforms.

import XCTest
@testable import ToneForgeEngine

final class PadTransformCodableTests: XCTestCase {

    private let fixedId = UUID(
        uuidString: "A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
    )!

    private func encode(_ value: some Encodable) throws -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = .sortedKeys
        let data = try enc.encode(value)
        return String(data: data, encoding: .utf8)!
    }

    private func decode<T: Decodable>(
        _ type: T.Type, from json: String
    ) throws -> T {
        return try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    // MARK: - Frozen fixtures

    func testReverseFixture() throws {
        let fixture = """
        {"type":"reverse"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .reverse)
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testStutterFixture() throws {
        let fixture = """
        {"rate":"r1_16","type":"stutter"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .stutter(.r1_16))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testGranularFixture() throws {
        let fixture = """
        {"params":{"densityHz":25,"grainMs":80,"pitchSpreadSemis":0,"positionJitter":0.05,"seed":7},"type":"granular"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(
            decoded,
            .granular(GranularParams(seed: 7))
        )
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testStretchFixture() throws {
        let fixture = """
        {"factor":2,"type":"stretch"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .stretch(2.0))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testOctaveFixture() throws {
        let fixture = """
        {"octaves":-1,"type":"octave"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .octave(-1))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testHarmonyFixture() throws {
        let fixture = """
        {"type":"harmony"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .harmony)
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testChoirFixture() throws {
        let fixture = """
        {"type":"choir"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .choir)
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testGateFixture() throws {
        let steps = [
            true, false, true, false,
            true, false, true, false,
            true, false, true, false,
            true, false, true, false,
        ]
        let fixture = """
        {"steps":[true,false,true,false,true,false,true,false,true,false,true,false,true,false,true,false],"type":"gate"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .gate(steps: steps))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testLoopFixture() throws {
        let fixture = """
        {"type":"loop"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .loop)
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testSpectralFreezeFixture() throws {
        let fixture = """
        {"atSec":0.5,"seed":42,"type":"spectralFreeze"}
        """
        let decoded = try decode(PadTransform.self, from: fixture)
        XCTAssertEqual(decoded, .spectralFreeze(atSec: 0.5, seed: 42))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    // MARK: - Unknown type

    func testUnknownTypeThrows() {
        let json = """
        {"type":"laserZap"}
        """
        XCTAssertThrowsError(
            try decode(PadTransform.self, from: json)
        ) { error in
            guard
                case DecodingError.dataCorrupted(let context) = error
            else {
                XCTFail("expected dataCorrupted, got \(error)")
                return
            }
            XCTAssertTrue(
                context.debugDescription.contains("Unknown PadTransform")
            )
        }
    }

    // MARK: - StutterRate.beats

    func testStutterRateBeats() {
        XCTAssertEqual(StutterRate.r1_4.beats, 1.0)
        XCTAssertEqual(StutterRate.r1_8.beats, 0.5)
        XCTAssertEqual(StutterRate.r1_16.beats, 0.25)
        XCTAssertEqual(StutterRate.r1_32.beats, 0.125)
    }

    // MARK: - PadSlot back-compat

    func testPadSlotP3BlobDecodesWithEmptyTransforms() throws {
        // P3-era blob: only the ref field.
        let json = """
        {"ref":{"id":"A1B2C3D4-E5F6-7890-ABCD-EF1234567890","type":"localSample"}}
        """
        let decoded = try decode(PadSlot.self, from: json)
        XCTAssertEqual(decoded.ref, .localSample(id: fixedId))
        XCTAssertEqual(decoded.transforms, [])
        XCTAssertEqual(decoded.timing, TransformTiming())
    }

    func testPadSlotFullRoundTrip() throws {
        let original = PadSlot(
            ref: .localSample(id: fixedId),
            transforms: [
                .reverse,
                .stutter(.r1_8),
                .gate(steps: [true, false, true, false]),
            ],
            timing: TransformTiming(fixedBpm: 138)
        )
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(PadSlot.self, from: data)
        XCTAssertEqual(decoded, original)
    }

    func testTransformTimingCodable() throws {
        let nilBpm = TransformTiming(fixedBpm: nil)
        let data1 = try JSONEncoder().encode(nilBpm)
        let decoded1 = try JSONDecoder().decode(
            TransformTiming.self, from: data1
        )
        XCTAssertEqual(decoded1, nilBpm)

        let fixed = TransformTiming(fixedBpm: 120)
        let data2 = try JSONEncoder().encode(fixed)
        let decoded2 = try JSONDecoder().decode(
            TransformTiming.self, from: data2
        )
        XCTAssertEqual(decoded2, fixed)
    }
}
