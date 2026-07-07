// TransportLoopTests.swift
//
// AppState side of the A/B loop (redesign Phase 5): the published
// region round-trips through setLoop and is cleared when the song
// is ejected. The wrap decision itself is pure engine logic
// (LoopRegionTests); the tick merely applies it through seek(to:).

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class TransportLoopTests: XCTestCase {

    func testSetLoopPublishesRegion() {
        let app = AppState()
        XCTAssertNil(app.loopRegion)

        let region = LoopRegion(startSec: 10, endSec: 20)
        app.setLoop(region)
        XCTAssertEqual(app.loopRegion, region)

        app.setLoop(nil)
        XCTAssertNil(app.loopRegion)
    }

    func testEjectClearsLoop() {
        let app = AppState()
        app.setLoop(LoopRegion(startSec: 0, endSec: 8))
        XCTAssertNotNil(app.loopRegion)

        app.ejectSong()
        XCTAssertNil(app.loopRegion)
    }
}
