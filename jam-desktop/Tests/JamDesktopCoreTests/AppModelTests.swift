// AppModelTests.swift
//
// Pins the top-level view inventory (first four stay 1:1 with jam.js
// showView ids; studio ports studio.html) and AppModel defaults.

import XCTest
@testable import JamDesktopCore

final class AppModelTests: XCTestCase {

    func testViewInventoryMatchesWebApp() {
        // .guitar is its own nav item (web parity): Perform = the Launchpad
        // pads, Guitar = the fretboard/tone surface (SURFACE_TO_VIEW
        // perform='kit', guitar='stage'). .songs is the full-screen library
        // table that superseded the Band Room card stack (still enumerated as
        // a retained fallback surface, no longer a switcher destination).
        XCTAssertEqual(
            JamView.allCases,
            [.intake, .songs, .bandRoom, .rehearsal, .perform, .guitar, .studio]
        )
    }

    @MainActor
    func testDefaultsStartAtIntakeAgainstHostedBackend() {
        let model = AppModel()
        XCTAssertEqual(model.view, .intake)
        XCTAssertEqual(model.backendBaseURL.absoluteString, "https://jamn.app")
    }
}
