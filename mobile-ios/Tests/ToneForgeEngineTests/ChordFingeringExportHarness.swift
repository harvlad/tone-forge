// ChordFingeringExportHarness.swift
//
// Dev-only export of canonical chord fingerings for the Blender
// hand-rig reference pipeline (tools/handrig). The Blender scene must
// consume EXACTLY what the app renders — never a hand-typed copy.
// Skipped unless TONEFORGE_CHORD_EXPORT_DIR is set.
//
// Output: chords.json
//   { "G": { "baseFret": 1,
//            "notes": [ {"string": 0, "fret": 3, "finger": 2}, ... ],
//            "barre": {"fret": 2, "loString": 0, "hiString": 5}? } }
// String indices 0 = low E … 5 = high e (matches NeckGeometry rows).

import XCTest
@testable import ToneForgeEngine

final class ChordFingeringExportHarness: XCTestCase {
    func testExportChordFingerings() throws {
        guard let dir = ProcessInfo.processInfo.environment["TONEFORGE_CHORD_EXPORT_DIR"],
              !dir.isEmpty else {
            throw XCTSkip("TONEFORGE_CHORD_EXPORT_DIR not set")
        }
        let symbols = ["G", "D", "Em", "C", "A", "Am", "E", "Dm"]
        var out: [String: Any] = [:]
        for symbol in symbols {
            guard let shape = GuitarVoicing.shape(symbol: symbol) else {
                XCTFail("no shape for \(symbol)"); continue
            }
            let fingering = ChordFingering.assign(shape: shape)
            var entry: [String: Any] = [
                "baseFret": shape.baseFret,
                "notes": fingering.notes.map {
                    ["string": $0.string, "fret": $0.fret, "finger": $0.finger]
                },
            ]
            if let bs = fingering.barreStrings, let bf = fingering.barreFret {
                entry["barre"] = [
                    "fret": bf,
                    "loString": bs.lowerBound,
                    "hiString": bs.upperBound,
                ]
            }
            out[symbol] = entry
        }
        let data = try JSONSerialization.data(
            withJSONObject: out, options: [.prettyPrinted, .sortedKeys])
        let url = URL(fileURLWithPath: dir, isDirectory: true)
            .appendingPathComponent("chords.json")
        try FileManager.default.createDirectory(
            at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url)
    }
}
