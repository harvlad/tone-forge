// SequencerPatternStoreTests.swift
//
// Persistence coverage for the desktop SequencerPatternStore:
// save/load round-trips through UserDefaults, deletes persist,
// name-sorted listing, the iOS wire format decodes verbatim
// (patterns move between platforms), and corrupt/hostile blobs
// degrade to an empty store instead of crashing.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class SequencerPatternStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suite = "SequencerPatternStoreTests"
    private let key = "jamdesktop.sequencerPatterns"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suite)
        defaults.removePersistentDomain(forName: suite)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    @MainActor
    private func makeStore() -> SequencerPatternStore {
        SequencerPatternStore(defaults: defaults)
    }

    private func makePattern(name: String = "Beat") -> SequencerPattern {
        var pattern = SequencerPattern(name: name)
        pattern.addTrack(
            for: .bundleChop(presetKey: "harmonic", chopIndex: 2, resolvedId: nil),
            name: "Am"
        )
        pattern.tracks[0].toggleStep(at: 0)
        pattern.tracks[0].toggleStep(at: 4)
        pattern.swing = 0.25
        return pattern
    }

    // MARK: - Round-trip

    @MainActor
    func testSaveRoundTripsThroughFreshStore() {
        let store = makeStore()
        let pattern = makePattern()
        store.save(pattern)

        let reloaded = makeStore()
        XCTAssertEqual(reloaded.pattern(id: pattern.id), pattern)
        XCTAssertEqual(reloaded.all().count, 1)
    }

    @MainActor
    func testSaveUpdatesInPlaceByPatternId() {
        let store = makeStore()
        var pattern = makePattern()
        store.save(pattern)

        pattern.name = "Beat v2"
        store.save(pattern)

        XCTAssertEqual(store.all().count, 1)
        XCTAssertEqual(store.pattern(id: pattern.id)?.name, "Beat v2")
    }

    @MainActor
    func testDeleteRemovesAndPersists() {
        let store = makeStore()
        let pattern = makePattern()
        store.save(pattern)
        store.delete(id: pattern.id)

        XCTAssertNil(store.pattern(id: pattern.id))
        XCTAssertTrue(makeStore().all().isEmpty, "delete persists")
    }

    @MainActor
    func testAllSortsByNameCaseInsensitive() {
        let store = makeStore()
        store.save(SequencerPattern(name: "zulu"))
        store.save(SequencerPattern(name: "Alpha"))
        store.save(SequencerPattern(name: "beta"))

        XCTAssertEqual(store.all().map(\.name), ["Alpha", "beta", "zulu"])
    }

    // MARK: - iOS wire format

    @MainActor
    func testDecodesIOSWireFormat() throws {
        let patternId = "11111111-1111-1111-1111-111111111111"
        let trackId = "22222222-2222-2222-2222-222222222222"
        let json = """
        {
          "storeVersion": 1,
          "patterns": {
            "\(patternId)": {
              "id": "\(patternId)",
              "name": "From iOS",
              "stepCount": 8,
              "swing": 0.2,
              "isLooping": true,
              "tracks": [
                {
                  "id": "\(trackId)",
                  "chopRef": {
                    "type": "bundleChop",
                    "presetKey": "harmonic",
                    "chopIndex": 3
                  },
                  "steps": [
                    {"velocity": 1, "probability": 1},
                    {"velocity": 0, "probability": 1},
                    {"velocity": 0.5, "probability": 1},
                    {"velocity": 0, "probability": 1},
                    {"velocity": 1, "probability": 1},
                    {"velocity": 0, "probability": 1},
                    {"velocity": 0, "probability": 1},
                    {"velocity": 0, "probability": 1}
                  ],
                  "volume": 0.8,
                  "pan": -0.5,
                  "isMuted": false,
                  "isSoloed": false,
                  "name": "Am"
                }
              ]
            }
          }
        }
        """
        defaults.set(Data(json.utf8), forKey: key)

        let store = makeStore()
        let pattern = try XCTUnwrap(
            store.pattern(id: UUID(uuidString: patternId)!)
        )
        XCTAssertEqual(pattern.name, "From iOS")
        XCTAssertEqual(pattern.stepCount, .eight)
        XCTAssertNil(pattern.bpmOverride)
        XCTAssertEqual(pattern.tracks.count, 1)
        let track = pattern.tracks[0]
        XCTAssertEqual(
            track.chopRef,
            .bundleChop(presetKey: "harmonic", chopIndex: 3, resolvedId: nil)
        )
        XCTAssertEqual(track.steps.count, 8)
        XCTAssertEqual(track.steps[2].velocity, 0.5)
        XCTAssertEqual(track.volume, 0.8)
        XCTAssertEqual(track.pan, -0.5)
        XCTAssertEqual(track.name, "Am")
    }

    @MainActor
    func testCorruptBlobFallsBackToEmpty() {
        defaults.set(Data("not json".utf8), forKey: key)
        XCTAssertTrue(makeStore().all().isEmpty)
    }

    @MainActor
    func testInvalidUUIDKeysAreSkipped() {
        let good = makePattern()
        let json = """
        {
          "storeVersion": 1,
          "patterns": {
            "not-a-uuid": {"id": "\(good.id.uuidString)", "name": "Bad key",
              "stepCount": 16, "swing": 0, "isLooping": true, "tracks": []},
            "\(good.id.uuidString)": {"id": "\(good.id.uuidString)",
              "name": "Good", "stepCount": 16, "swing": 0,
              "isLooping": true, "tracks": []}
          }
        }
        """
        defaults.set(Data(json.utf8), forKey: key)

        let store = makeStore()
        XCTAssertEqual(store.all().count, 1)
        XCTAssertEqual(store.pattern(id: good.id)?.name, "Good")
    }
}
