// EngineControllerTests.swift
//
// Construction-only coverage: we never start() here — CoreAudio
// device IO is hostile to CI sandboxes (same discipline as
// ConnectCoreTests). Verifies the controller wires onGraphRebuilt.

import XCTest
@testable import JamDesktopAudio

final class EngineControllerTests: XCTestCase {

    @MainActor
    func testInitWiresGraphRebuiltHook() {
        let controller = EngineController()
        XCTAssertNotNil(controller.engine.onGraphRebuilt)
    }
}
