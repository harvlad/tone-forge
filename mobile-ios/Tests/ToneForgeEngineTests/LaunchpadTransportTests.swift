// LaunchpadTransportTests.swift
//
// Contract tests for the LaunchpadTransport seam using an in-test
// fake. Verifies the shape a hardware transport must satisfy: press
// routing through the closures, single-light writes, batch frames,
// and clear-all semantics.

import XCTest
@testable import ToneForgeEngine

/// Minimal conforming transport that records everything.
private final class FakeLaunchpadTransport: LaunchpadTransport {
    var connectionState: LaunchpadConnectionState = .connected(deviceName: "Fake LP")

    var onPadDown: ((LaunchpadPad) -> Void)?
    var onPadUp: ((LaunchpadPad) -> Void)?

    private(set) var lights: [LaunchpadPad: LaunchpadLight] = [:]

    func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
        if case .off = light {
            lights.removeValue(forKey: pad)
        } else {
            lights[pad] = light
        }
    }

    func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
        for (pad, light) in frame { setLight(light, at: pad) }
    }

    func clearLights() {
        lights.removeAll()
    }

    // Device-side event injection, as a real transport would do from
    // its MIDI callback.
    func simulatePress(_ pad: LaunchpadPad) { onPadDown?(pad) }
    func simulateRelease(_ pad: LaunchpadPad) { onPadUp?(pad) }
}

final class LaunchpadTransportTests: XCTestCase {

    func testPressRoutingReachesClosures() {
        let transport = FakeLaunchpadTransport()
        var downs: [LaunchpadPad] = []
        var ups: [LaunchpadPad] = []
        transport.onPadDown = { downs.append($0) }
        transport.onPadUp = { ups.append($0) }

        let pad = LaunchpadPad(row: 2, col: 5)
        transport.simulatePress(pad)
        transport.simulatePress(LaunchpadPad(row: 0, col: 0))
        transport.simulateRelease(pad)

        XCTAssertEqual(downs, [pad, LaunchpadPad(row: 0, col: 0)])
        XCTAssertEqual(ups, [pad])
    }

    func testBatchLightFrameMergesAndOffRemoves() {
        let transport = FakeLaunchpadTransport()
        let a = LaunchpadPad(row: 0, col: 0)
        let b = LaunchpadPad(row: 1, col: 1)
        let c = LaunchpadPad(row: 2, col: 2)

        transport.setLight(.solid(colorHint: 0xFF0000), at: a)
        transport.setLights([
            b: .pulse(colorHint: 0x00FF00),
            c: .solid(colorHint: 0x0000FF),
        ])
        XCTAssertEqual(transport.lights.count, 3)
        XCTAssertEqual(transport.lights[b], .pulse(colorHint: 0x00FF00))

        // Frames merge — pads absent from the frame are untouched,
        // .off entries remove.
        transport.setLights([a: .off, b: .solid(colorHint: 0xFFFFFF)])
        XCTAssertNil(transport.lights[a])
        XCTAssertEqual(transport.lights[b], .solid(colorHint: 0xFFFFFF))
        XCTAssertEqual(transport.lights[c], .solid(colorHint: 0x0000FF))
    }

    func testClearLightsEmptiesEverything() {
        let transport = FakeLaunchpadTransport()
        transport.setLights([
            LaunchpadPad(row: 0, col: 1): .solid(colorHint: 0x123456),
            LaunchpadPad(row: 3, col: 3): .pulse(colorHint: 0x654321),
        ])
        transport.clearLights()
        XCTAssertTrue(transport.lights.isEmpty)
    }

    func testPadEqualityAndHashing() {
        XCTAssertEqual(LaunchpadPad(row: 4, col: 7), LaunchpadPad(row: 4, col: 7))
        XCTAssertNotEqual(LaunchpadPad(row: 4, col: 7), LaunchpadPad(row: 7, col: 4))
        let set: Set<LaunchpadPad> = [
            LaunchpadPad(row: 1, col: 1),
            LaunchpadPad(row: 1, col: 1),
            LaunchpadPad(row: 1, col: 2),
        ]
        XCTAssertEqual(set.count, 2)
    }
}
