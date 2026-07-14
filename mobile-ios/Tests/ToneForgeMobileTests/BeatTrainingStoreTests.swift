// BeatTrainingStoreTests.swift
//
// Correction logging + CSV training-export. Uses a temp directory so
// the on-device Application Support store is never touched.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class BeatTrainingStoreTests: XCTestCase {

    private func tempDir() -> URL {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        try? FileManager.default.createDirectory(
            at: dir, withIntermediateDirectories: true
        )
        return dir
    }

    private func features(centroid: Float) -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: centroid, zcr: 0.05, attackSec: 0.002,
            durationSec: 0.08, pitchedness: 0.6, lowBandRatio: 0.5,
            peakRMS: 0.4
        )
    }

    func testLogAppendsAndPersists() {
        let dir = tempDir()
        let store = BeatTrainingStore(directory: dir)
        store.log(features: features(centroid: 120), original: .perc, corrected: .kick)
        XCTAssertEqual(store.corrections.count, 1)
        // A fresh store over the same dir reloads the persisted record.
        let reloaded = BeatTrainingStore(directory: dir)
        XCTAssertEqual(reloaded.corrections.count, 1)
        XCTAssertEqual(reloaded.corrections.first?.corrected, .kick)
    }

    func testLogIgnoresNoOpCorrection() {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(centroid: 120), original: .kick, corrected: .kick)
        XCTAssertTrue(store.corrections.isEmpty)
    }

    func testExportCSVHeaderMatchesFeatureOrder() {
        let store = BeatTrainingStore(directory: tempDir())
        let header = store.exportCSV().split(separator: "\n").first.map(String.init)
        XCTAssertEqual(
            header,
            (OnsetFeatures.featureNames + ["original", "corrected", "timestamp"])
                .joined(separator: ",")
        )
    }

    func testExportCSVRowCarriesRolesAndFeatures() {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(centroid: 120), original: .perc, corrected: .snare)
        let lines = store.exportCSV().split(separator: "\n").map(String.init)
        XCTAssertEqual(lines.count, 2)  // header + 1 row
        let cols = lines[1].split(separator: ",").map(String.init)
        XCTAssertEqual(cols.count, OnsetFeatures.featureNames.count + 3)
        XCTAssertEqual(cols[0], "120.000000")            // centroidHz
        XCTAssertEqual(cols[OnsetFeatures.featureNames.count], "perc")
        XCTAssertEqual(cols[OnsetFeatures.featureNames.count + 1], "snare")
    }

    func testExportCSVFileWritesReadableCSV() throws {
        let store = BeatTrainingStore(directory: tempDir())
        store.log(features: features(centroid: 120), original: .perc, corrected: .kick)
        let url = try store.exportCSVFile()
        let contents = try String(contentsOf: url, encoding: .utf8)
        XCTAssertEqual(contents, store.exportCSV())
    }
}
