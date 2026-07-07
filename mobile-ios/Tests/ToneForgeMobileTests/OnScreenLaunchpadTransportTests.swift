// OnScreenLaunchpadTransportTests.swift
//
// The on-screen transport is what the SwiftUI grids feed; verify the
// grid → closure forwarding and the published light-state semantics.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class OnScreenLaunchpadTransportTests: XCTestCase {

    func testAlwaysOnScreenConnectionState() {
        XCTAssertEqual(OnScreenLaunchpadTransport().connectionState, .onScreen)
    }

    func testGridEventsForwardToClosures() {
        let transport = OnScreenLaunchpadTransport()
        var downs: [LaunchpadPad] = []
        var ups: [LaunchpadPad] = []
        transport.onPadDown = { downs.append($0) }
        transport.onPadUp = { ups.append($0) }

        let pad = LaunchpadPad(row: 3, col: 2)
        transport.padDown(pad)
        transport.padUp(pad)

        XCTAssertEqual(downs, [pad])
        XCTAssertEqual(ups, [pad])
    }

    func testLightsPublishAndOffRemoves() {
        let transport = OnScreenLaunchpadTransport()
        let a = LaunchpadPad(row: 0, col: 0)
        let b = LaunchpadPad(row: 1, col: 0)

        transport.setLights([
            a: .solid(colorHint: 0xFF8800),
            b: .pulse(colorHint: 0x88FF00),
        ])
        XCTAssertEqual(transport.lights[a], .solid(colorHint: 0xFF8800))
        XCTAssertEqual(transport.lights[b], .pulse(colorHint: 0x88FF00))

        transport.setLight(.off, at: a)
        XCTAssertNil(transport.lights[a])
        XCTAssertEqual(transport.lights.count, 1)

        transport.clearLights()
        XCTAssertTrue(transport.lights.isEmpty)
    }
}
