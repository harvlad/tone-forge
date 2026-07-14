// BeatTrainingStoreTests.swift
//
// Beat Capture (D-024): the device-local correction log records, dedups
// same-role no-ops, persists across instances, and exports canonical CSV.

import XCTest
@testable import JamDesktopCore
import ToneForgeEngine

@MainActor
final class BeatTrainingStoreTests: XCTestCase {

    private func tempDir() -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent("beat-train-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
        return dir
    }

    private func features(_ centroid: Float = 200) -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: centroid, zcr: 0.1, attackSec: 0.005,
            durationSec: 0.08, pitchedness: 0.2, lowBandRatio: 0.7,
            peakRMS: 0.5
        )
    }

    func testLogAppendsCorrection() {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(), original: .kick, corrected: .snare)
        XCTAssertEqual(store.corrections.count, 1)
        XCTAssertEqual(store.corrections.first?.original, .kick)
        XCTAssertEqual(store.corrections.first?.corrected, .snare)
    }

    func testSameRoleCorrectionIsNoOp() {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(), original: .kick, corrected: .kick)
        XCTAssertTrue(store.corrections.isEmpty)
    }

    func testPersistenceAcrossInstances() {
        let dir = tempDir()
        let a = BeatTrainingStore(directory: dir)
        a.log(features: features(), original: .perc, corrected: .rim)
        let b = BeatTrainingStore(directory: dir)
        XCTAssertEqual(b.corrections.count, 1)
        XCTAssertEqual(b.corrections.first?.corrected, .rim)
    }

    func testExportCSVHeaderAndRows() {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(), original: .kick, corrected: .snare)
        let csv = store.exportCSV()
        let lines = csv.split(separator: "\n")
        XCTAssertEqual(lines.count, 2)  // header + one row
        let header = String(lines[0])
        for name in OnsetFeatures.featureNames {
            XCTAssertTrue(header.contains(name))
        }
        XCTAssertTrue(header.contains("original"))
        XCTAssertTrue(header.contains("corrected"))
        XCTAssertTrue(String(lines[1]).contains("kick,snare"))
    }
}
