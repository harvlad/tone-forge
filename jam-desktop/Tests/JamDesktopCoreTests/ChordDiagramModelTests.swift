// ChordDiagramModelTests.swift
//
// Diagram construction from GuitarVoicing shapes, plus the web
// parity spot-check against a sample of chord_shapes.json.

import XCTest
@testable import JamDesktopCore

final class ChordDiagramModelTests: XCTestCase {

    // MARK: Construction

    func testAmDiagram() throws {
        let diagram = try XCTUnwrap(ChordDiagram.make(symbol: "Am"))
        XCTAssertEqual(diagram.baseFret, 1)
        XCTAssertEqual(diagram.fretsArray, [-1, 0, 2, 2, 1, 0]) // x02210
        XCTAssertEqual(diagram.mutedStrings, [0])
        XCTAssertEqual(diagram.openStrings, [1, 5])
        XCTAssertNil(diagram.barre)
    }

    func testUnparsableSymbolReturnsNil() {
        XCTAssertNil(ChordDiagram.make(symbol: "not-a-chord"))
    }

    // MARK: Web parity spot-check (chord_shapes.json sample)

    private struct ShapeFile: Decodable {
        struct Shape: Decodable { let frets: [Int] }
        let shapes: [String: Shape]
    }

    /// "C:maj" -> "C", "A:min" -> "Am", "G:7" -> "G7".
    private func symbol(forKey key: String) -> String {
        let parts = key.split(separator: ":")
        let root = String(parts[0])
        switch parts[1] {
        case "maj": return root
        case "min": return root + "m"
        default: return root + parts[1]
        }
    }

    func testOpenPositionShapesMatchWebChartData() throws {
        let url = try XCTUnwrap(Bundle.module.url(
            forResource: "chord_shapes_sample", withExtension: "json",
            subdirectory: "Fixtures"))
        let file = try JSONDecoder().decode(ShapeFile.self, from: Data(contentsOf: url))
        XCTAssertFalse(file.shapes.isEmpty)

        for (key, expected) in file.shapes {
            let sym = symbol(forKey: key)
            let diagram = try XCTUnwrap(
                ChordDiagram.make(symbol: sym), "no diagram for \(sym)")
            XCTAssertEqual(
                diagram.fretsArray, expected.frets,
                "\(sym): native \(diagram.fretsArray) != web \(expected.frets)")
        }
    }

    // MARK: Barre inference

    func testBarreInferredForThreePlusStringsAtMinFret() {
        // Synthetic 133211-style shape (native F avoids this; the
        // inference still must handle chart-style data).
        let dots = [
            ChordDiagram.Dot(string: 0, fret: 1),
            ChordDiagram.Dot(string: 1, fret: 3),
            ChordDiagram.Dot(string: 2, fret: 3),
            ChordDiagram.Dot(string: 3, fret: 2),
            ChordDiagram.Dot(string: 4, fret: 1),
            ChordDiagram.Dot(string: 5, fret: 1),
        ]
        let barre = ChordDiagram.inferBarre(dots: dots, open: [], muted: [])
        XCTAssertEqual(barre, ChordDiagram.Barre(fret: 1, fromString: 0, toString: 5))
    }

    func testNoBarreWhenOpenStringInsideSpan() {
        // Native F: 1,0,3,2,1,1 — open A string breaks the bar.
        let dots = [
            ChordDiagram.Dot(string: 0, fret: 1),
            ChordDiagram.Dot(string: 2, fret: 3),
            ChordDiagram.Dot(string: 3, fret: 2),
            ChordDiagram.Dot(string: 4, fret: 1),
            ChordDiagram.Dot(string: 5, fret: 1),
        ]
        let barre = ChordDiagram.inferBarre(dots: dots, open: [1], muted: [])
        XCTAssertNil(barre)
    }

    func testNoBarreWithFewerThanThreeStrings() {
        let dots = [
            ChordDiagram.Dot(string: 4, fret: 2),
            ChordDiagram.Dot(string: 5, fret: 2),
        ]
        XCTAssertNil(ChordDiagram.inferBarre(dots: dots, open: [], muted: [0, 1, 2, 3]))
    }
}
