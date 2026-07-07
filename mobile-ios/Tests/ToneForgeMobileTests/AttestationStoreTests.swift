// AttestationStoreTests.swift
//
// Persistence semantics of the one-time ownership attestation:
// fresh state, accept round trip, version bump re-prompt, UI-test
// reset.

import XCTest
@testable import ToneForgeMobile

@MainActor
final class AttestationStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.attestation"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testFreshStoreIsNotAccepted() {
        let store = AttestationStore(defaults: defaults)
        XCTAssertFalse(store.isAccepted)
        XCTAssertNil(store.acceptedAt)
    }

    func testAcceptPersistsAcrossInitWithTimestampAndVersion() {
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        AttestationStore(defaults: defaults).accept(now: now)

        let reloaded = AttestationStore(defaults: defaults)

        XCTAssertTrue(reloaded.isAccepted)
        XCTAssertEqual(reloaded.acceptedAt, now)
        XCTAssertEqual(
            defaults.integer(forKey: "toneforge.attestation.version"),
            AttestationStore.currentVersion
        )
    }

    func testStaleVersionRequiresReacceptance() {
        AttestationStore(defaults: defaults).accept()
        // Simulate an old acceptance recorded before a version bump.
        defaults.set(AttestationStore.currentVersion - 1, forKey: "toneforge.attestation.version")

        let store = AttestationStore(defaults: defaults)

        XCTAssertFalse(store.isAccepted)
    }

    func testResetForUITestsClearsEverything() {
        let store = AttestationStore(defaults: defaults)
        store.accept()

        store.resetForUITests()

        XCTAssertFalse(store.isAccepted)
        XCTAssertNil(store.acceptedAt)
        XCTAssertNil(defaults.object(forKey: "toneforge.attestation.accepted"))
        XCTAssertFalse(AttestationStore(defaults: defaults).isAccepted)
    }
}
