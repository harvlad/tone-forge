// AttestationUITests.swift
//
// End-to-end checks of the ownership-attestation gate using the app's
// UI-test launch-argument contract (see UITestSupport.swift):
//
//   -uitest-reset-attestation  clear persisted attestation at launch
//   -uitest-stub-import        show a stub import row (baked WAV +
//                              stubbed analyze transport, no network)
//
// Flow under test:
//   1. Fresh state → import → attestation sheet appears → Accept →
//      Analysing sheet appears.
//   2. Relaunch without reset → import → no attestation sheet,
//      straight to Analysing (acceptance persisted).

import XCTest

final class AttestationUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testAttestationGateShownOnceThenPersisted() throws {
        // --- Launch 1: un-attested ---------------------------------
        let app = XCUIApplication()
        app.launchArguments = ["-uitest-reset-attestation", "-uitest-stub-import"]
        app.launch()

        let importRow = app.buttons["uitest-import-row"]
        XCTAssertTrue(
            importRow.waitForExistence(timeout: 15),
            "Stub import row should be visible under -uitest-stub-import"
        )
        importRow.tap()

        let accept = app.buttons["attestation-accept"]
        XCTAssertTrue(
            accept.waitForExistence(timeout: 10),
            "Attestation sheet should appear before the first import"
        )
        accept.tap()

        XCTAssertTrue(
            app.navigationBars["Analysing"].waitForExistence(timeout: 15),
            "Accepting the attestation should start the import"
        )

        // --- Launch 2: attestation persisted -----------------------
        app.terminate()
        app.launchArguments = ["-uitest-stub-import"]
        app.launch()

        XCTAssertTrue(importRow.waitForExistence(timeout: 15))
        importRow.tap()

        XCTAssertTrue(
            app.navigationBars["Analysing"].waitForExistence(timeout: 15),
            "Second import should skip straight to Analysing"
        )
        XCTAssertFalse(
            app.buttons["attestation-accept"].exists,
            "Attestation sheet must not re-appear once accepted"
        )
    }

    func testAttestationCancelAbandonsImport() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-uitest-reset-attestation", "-uitest-stub-import"]
        app.launch()

        let importRow = app.buttons["uitest-import-row"]
        XCTAssertTrue(importRow.waitForExistence(timeout: 15))
        importRow.tap()

        let cancel = app.buttons["attestation-cancel"]
        XCTAssertTrue(cancel.waitForExistence(timeout: 10))
        cancel.tap()

        // No import starts; the attestation sheet is gone and the
        // Analysing sheet never shows.
        XCTAssertFalse(app.navigationBars["Analysing"].waitForExistence(timeout: 3))
        XCTAssertFalse(app.buttons["attestation-accept"].exists)
    }
}
