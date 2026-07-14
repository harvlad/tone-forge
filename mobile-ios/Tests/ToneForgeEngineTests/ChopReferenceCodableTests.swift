// ChopReferenceCodableTests.swift
//
// Frozen-JSON wire shape for ChopReference. The synthChord case (added
// for song-less key-chord loops) must round-trip: encode emits
// {"chordSymbol":...,"type":"synthChord"} and that exact string decodes
// back to the symbol. Unknown types throw.

import XCTest
@testable import ToneForgeEngine

final class ChopReferenceCodableTests: XCTestCase {

    private func encode(_ value: some Encodable) throws -> String {
        let enc = JSONEncoder()
        enc.outputFormatting = .sortedKeys
        return String(data: try enc.encode(value), encoding: .utf8)!
    }

    private func decode<T: Decodable>(
        _ type: T.Type, from json: String
    ) throws -> T {
        try JSONDecoder().decode(type, from: Data(json.utf8))
    }

    func testSynthChordFixture() throws {
        let fixture = """
        {"chordSymbol":"Gmaj7","type":"synthChord"}
        """
        let decoded = try decode(ChopReference.self, from: fixture)
        // Octave defaults to 0 and the octave-0 wire shape omits chordOctave.
        XCTAssertEqual(decoded, .synthChord(symbol: "Gmaj7", octaveShift: 0))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testSynthChordOctaveFixture() throws {
        let fixture = """
        {"chordOctave":-2,"chordSymbol":"Dm","type":"synthChord"}
        """
        let decoded = try decode(ChopReference.self, from: fixture)
        XCTAssertEqual(decoded, .synthChord(symbol: "Dm", octaveShift: -2))
        XCTAssertEqual(try encode(decoded), fixture)
    }

    func testSynthChordRoundTrip() throws {
        for symbol in ["C", "Dm", "Bdim", "F#m7b5"] {
            for octave in [-3, 0, 2] {
                let ref = ChopReference.synthChord(symbol: symbol, octaveShift: octave)
                let decoded = try decode(ChopReference.self, from: encode(ref))
                XCTAssertEqual(decoded, ref)
                XCTAssertEqual(decoded.displayLabel, symbol)
            }
        }
    }

    func testSynthChordDistinctFromOtherCases() throws {
        XCTAssertNotEqual(
            ChopReference.synthChord(symbol: "C", octaveShift: 0),
            .bundleChop(presetKey: "harmonic", chopIndex: 0, resolvedId: nil)
        )
    }

    func testUnknownTypeThrows() {
        let bad = """
        {"type":"bogus"}
        """
        XCTAssertThrowsError(try decode(ChopReference.self, from: bad))
    }
}
