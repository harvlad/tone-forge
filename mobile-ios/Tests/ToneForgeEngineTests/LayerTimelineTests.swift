// LayerTimelineTests.swift
//
// Round-trip tests for the persisted `LayerTimeline` schema. Because
// this is a LOCK-IN Codable schema (see LayerTimeline.swift header),
// the tests assert:
//
//   1. A representative event stream survives encode → decode → equal.
//   2. Optional per-event params are preserved (present + absent).
//   3. `manifestVersion` / `timelineVersion` = 1 is emitted.
//   4. `empty(analysisId:)` seeds a UUID layerId, current timestamp,
//      empty events, zero duration.
//   5. Interleaved sampleOn + noteOn events preserve their order.

import XCTest
@testable import ToneForgeEngine

final class LayerTimelineTests: XCTestCase {

    // MARK: - Round-trip

    func testRoundTripEncodesAndDecodesEqual() throws {
        let timeline = LayerTimeline(
            timelineVersion: 1,
            layerId: "layer-abc",
            analysisId: "song-xyz",
            name: "Test Layer",
            createdAtEpoch: 1_700_000_000.0,
            durationSec: 120.5,
            events: [
                LayerEvent(
                    kind: .sampleOn,
                    songTimeSec: 0.25,
                    params: LayerEvent.Params(padIdx: 3, velocity: 0.9)
                ),
                LayerEvent(
                    kind: .noteOn,
                    songTimeSec: 1.5,
                    params: LayerEvent.Params(midiNote: 60, velocity: 0.7)
                ),
                LayerEvent(
                    kind: .sampleOff,
                    songTimeSec: 2.0,
                    params: LayerEvent.Params(padIdx: 3)
                ),
                LayerEvent(
                    kind: .noteOff,
                    songTimeSec: 2.5,
                    params: LayerEvent.Params(midiNote: 60)
                ),
            ],
            activePackId: "starter"
        )

        let data = try JSONEncoder().encode(timeline)
        let decoded = try JSONDecoder().decode(LayerTimeline.self, from: data)
        XCTAssertEqual(decoded, timeline)
    }

    func testRoundTripPreservesEventOrder() throws {
        // Events at the same songTimeSec — order must be preserved
        // because the replay iterator walks the array in order.
        let events = (0..<20).map { i in
            LayerEvent(
                kind: i % 2 == 0 ? .sampleOn : .sampleOff,
                songTimeSec: Double(i) * 0.1,
                params: LayerEvent.Params(padIdx: i % 16)
            )
        }
        let timeline = LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: "song-1",
            name: "Ordering",
            createdAtEpoch: 1_700_000_000.0,
            durationSec: 2.0,
            events: events,
            activePackId: nil
        )

        let data = try JSONEncoder().encode(timeline)
        let decoded = try JSONDecoder().decode(LayerTimeline.self, from: data)
        XCTAssertEqual(decoded.events.map { $0.songTimeSec },
                       events.map { $0.songTimeSec })
        XCTAssertEqual(decoded.events.map { $0.kind },
                       events.map { $0.kind })
    }

    func testRoundTripPreservesNilPackId() throws {
        let timeline = LayerTimeline(
            layerId: "l1",
            analysisId: "a1",
            name: "No pack",
            createdAtEpoch: 1.0,
            durationSec: 1.0,
            events: [],
            activePackId: nil
        )

        let data = try JSONEncoder().encode(timeline)
        let decoded = try JSONDecoder().decode(LayerTimeline.self, from: data)
        XCTAssertNil(decoded.activePackId)
    }

    func testRoundTripPreservesPackIdOverride() throws {
        let ev = LayerEvent(
            kind: .sampleOn,
            songTimeSec: 0,
            params: LayerEvent.Params(padIdx: 5, packIdOverride: "shoegaze")
        )
        let data = try JSONEncoder().encode(ev)
        let decoded = try JSONDecoder().decode(LayerEvent.self, from: data)
        XCTAssertEqual(decoded.params.packIdOverride, "shoegaze")
    }

    // MARK: - Sketch metadata (additive, optional)

    func testRoundTripPreservesSketchMetadata() throws {
        let timeline = LayerTimeline(
            layerId: "sketch-1",
            analysisId: "__sketch__",
            name: "Sketch take",
            createdAtEpoch: 1_700_000_000.0,
            durationSec: 8.0,
            events: [
                LayerEvent(
                    kind: .sampleOn,
                    songTimeSec: 0.5,
                    params: LayerEvent.Params(padIdx: 0)
                ),
            ],
            activePackId: "starter",
            sketchTempoBpm: 120,
            sketchTimeSigNumerator: 3,
            packName: "Starter"
        )

        let data = try JSONEncoder().encode(timeline)
        let decoded = try JSONDecoder().decode(LayerTimeline.self, from: data)
        XCTAssertEqual(decoded, timeline)
        XCTAssertEqual(decoded.sketchTempoBpm, 120)
        XCTAssertEqual(decoded.sketchTimeSigNumerator, 3)
        XCTAssertEqual(decoded.packName, "Starter")
    }

    func testDecodesPreMetadataJSONWithNilSketchFields() throws {
        // A v1 layer written before the sketch-metadata fields existed.
        // LOCK-IN: this JSON must keep decoding forever, with the new
        // optionals nil.
        let json = """
        {
          "timelineVersion": 1,
          "layerId": "old-layer",
          "analysisId": "song-old",
          "name": "Old Layer",
          "createdAtEpoch": 1700000000.0,
          "durationSec": 4.0,
          "events": [
            {
              "kind": "sampleOn",
              "songTimeSec": 1.0,
              "params": { "padIdx": 2, "velocity": 0.8 }
            }
          ],
          "activePackId": "starter"
        }
        """
        let decoded = try JSONDecoder().decode(
            LayerTimeline.self,
            from: Data(json.utf8)
        )
        XCTAssertEqual(decoded.layerId, "old-layer")
        XCTAssertEqual(decoded.events.count, 1)
        XCTAssertNil(decoded.sketchTempoBpm)
        XCTAssertNil(decoded.sketchTimeSigNumerator)
        XCTAssertNil(decoded.packName)
    }

    // MARK: - Schema

    func testEncodedJSONContainsTimelineVersion() throws {
        let timeline = LayerTimeline.empty(analysisId: "song-1", activePackId: nil)
        let data = try JSONEncoder().encode(timeline)
        let json = String(data: data, encoding: .utf8) ?? ""
        XCTAssertTrue(json.contains("\"timelineVersion\":1"),
                      "Expected timelineVersion:1 in encoded JSON, got: \(json)")
    }

    // MARK: - empty(analysisId:)

    func testEmptyHasEmptyEventsAndZeroDuration() {
        let t = LayerTimeline.empty(analysisId: "s1", activePackId: "starter")
        XCTAssertEqual(t.analysisId, "s1")
        XCTAssertEqual(t.activePackId, "starter")
        XCTAssertEqual(t.durationSec, 0)
        XCTAssertTrue(t.events.isEmpty)
    }

    func testEmptySeedsUniqueLayerId() {
        let a = LayerTimeline.empty(analysisId: "s", activePackId: nil)
        let b = LayerTimeline.empty(analysisId: "s", activePackId: nil)
        XCTAssertNotEqual(a.layerId, b.layerId)
        // Should be a valid UUID string.
        XCTAssertNotNil(UUID(uuidString: a.layerId))
    }

    // MARK: - LayerEvent.Params defaults

    func testLayerEventParamsAllOptionalsDefaultNil() {
        let params = LayerEvent.Params()
        XCTAssertNil(params.padIdx)
        XCTAssertNil(params.midiNote)
        XCTAssertNil(params.velocity)
        XCTAssertNil(params.packIdOverride)
    }
}
