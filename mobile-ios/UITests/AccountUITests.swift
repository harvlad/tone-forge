// AccountUITests.swift
//
// End-to-end account flow through the Settings sheet, using the
// UI-test launch-argument contract (see UITestSupport.swift):
//
//   -uitest-reset-account  clear persisted profile + token at launch
//   -uitest-stub-account   plain sign-in button + StubAuthClient
//                          (instant sign-in, claim reports 2)
//
// Flow under test:
//   1. Fresh state → Settings shows the sign-in button.
//   2. Tap it → identity row + "2 analyses" synced count appear.
//   3. Sign out → back to the sign-in button.
//   4. Relaunch without reset → still signed in (persistence).

import XCTest

final class AccountUITests: XCTestCase {

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    /// Settings lives behind the gear in the Library tab header.
    private func openSettings(_ app: XCUIApplication) {
        let library = app.tabBars.buttons["Library"]
        XCTAssertTrue(
            library.waitForExistence(timeout: 15),
            "Library tab should be reachable from the tab bar"
        )
        library.tap()

        let gear = app.buttons["library-settings-button"]
        XCTAssertTrue(
            gear.waitForExistence(timeout: 10),
            "Settings gear should be in the Library header"
        )
        gear.tap()
    }

    func testSignInClaimSignOut() throws {
        let app = XCUIApplication()
        app.launchArguments = ["-uitest-reset-account", "-uitest-stub-account"]
        app.launch()
        openSettings(app)

        // --- Signed out: sign-in button visible -----------------------
        let signIn = app.buttons["settings-signin-apple"]
        XCTAssertTrue(
            signIn.waitForExistence(timeout: 10),
            "Sign-in button should show when signed out"
        )
        signIn.tap()

        // --- Signed in: identity + synced count -----------------------
        // LabeledContent rows combine into single accessibility
        // elements (label + value), so match by identifier, any type.
        let status = app.descendants(matching: .any)["settings-account-status"]
            .firstMatch
        XCTAssertTrue(
            status.waitForExistence(timeout: 10),
            "Identity row should appear after stub sign-in"
        )
        let synced = app.descendants(matching: .any)["settings-claim-status"]
            .firstMatch
        XCTAssertTrue(
            synced.waitForExistence(timeout: 10),
            "Auto-claim should report the stub's synced analyses"
        )

        // --- Sign out -------------------------------------------------
        let signOut = app.buttons["settings-signout"]
        XCTAssertTrue(signOut.waitForExistence(timeout: 5))
        signOut.tap()

        XCTAssertTrue(
            signIn.waitForExistence(timeout: 10),
            "Sign-in button should return after sign-out"
        )
    }

    func testSessionPersistsAcrossLaunch() throws {
        // --- Launch 1: reset, sign in ---------------------------------
        let app = XCUIApplication()
        app.launchArguments = ["-uitest-reset-account", "-uitest-stub-account"]
        app.launch()
        openSettings(app)

        let signIn = app.buttons["settings-signin-apple"]
        XCTAssertTrue(signIn.waitForExistence(timeout: 10))
        signIn.tap()
        XCTAssertTrue(
            app.descendants(matching: .any)["settings-account-status"]
                .firstMatch.waitForExistence(timeout: 10)
        )

        // --- Launch 2: no reset → still signed in ---------------------
        app.terminate()
        app.launchArguments = ["-uitest-stub-account"]
        app.launch()
        openSettings(app)

        XCTAssertTrue(
            app.descendants(matching: .any)["settings-account-status"]
                .firstMatch.waitForExistence(timeout: 10),
            "Cached profile should survive relaunch"
        )
        XCTAssertFalse(
            app.buttons["settings-signin-apple"].exists,
            "Sign-in button must not show while signed in"
        )
    }
}
