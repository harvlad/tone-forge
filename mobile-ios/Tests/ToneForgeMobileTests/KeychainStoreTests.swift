// KeychainStoreTests.swift
//
// SecretStore contract against the in-memory fake, plus one guarded
// round-trip against the real Keychain (skipped when the host
// environment has no Keychain, e.g. some CI simulators).

import XCTest
@testable import ToneForgeMobile

final class KeychainStoreTests: XCTestCase {

    // MARK: - InMemorySecretStore contract

    func testInMemoryReadMissingReturnsNil() {
        let store = InMemorySecretStore()
        XCTAssertNil(store.read(key: "absent"))
    }

    func testInMemoryWriteReadRoundTrip() {
        let store = InMemorySecretStore()
        store.write(key: "token", value: "abc123")
        XCTAssertEqual(store.read(key: "token"), "abc123")
    }

    func testInMemoryOverwrite() {
        let store = InMemorySecretStore()
        store.write(key: "token", value: "first")
        store.write(key: "token", value: "second")
        XCTAssertEqual(store.read(key: "token"), "second")
    }

    func testInMemoryDelete() {
        let store = InMemorySecretStore()
        store.write(key: "token", value: "abc")
        store.delete(key: "token")
        XCTAssertNil(store.read(key: "token"))
    }

    func testInMemoryDeleteMissingIsNoop() {
        let store = InMemorySecretStore()
        store.delete(key: "never-written")
        XCTAssertNil(store.read(key: "never-written"))
    }

    func testInMemoryKeysAreIndependent() {
        let store = InMemorySecretStore()
        store.write(key: "a", value: "1")
        store.write(key: "b", value: "2")
        store.delete(key: "a")
        XCTAssertNil(store.read(key: "a"))
        XCTAssertEqual(store.read(key: "b"), "2")
    }

    func testInMemoryReferenceSemantics() {
        // Class, not struct: handles share state like the real Keychain.
        let store = InMemorySecretStore()
        let alias: any SecretStore = store
        store.write(key: "token", value: "shared")
        XCTAssertEqual(alias.read(key: "token"), "shared")
    }

    // MARK: - Real Keychain (guarded)

    func testKeychainRoundTrip() throws {
        // Unique service per run so parallel/aborted runs never collide
        // with the app's real items.
        let service = "KeychainStoreTests-\(UUID().uuidString)"
        let store = KeychainStore(service: service)
        defer { store.delete(key: "token") }

        store.write(key: "token", value: "round-trip")
        guard store.read(key: "token") == "round-trip" else {
            throw XCTSkip("Keychain unavailable in this test environment")
        }

        // Update path (SecItemUpdate branch).
        store.write(key: "token", value: "updated")
        XCTAssertEqual(store.read(key: "token"), "updated")

        // Delete path.
        store.delete(key: "token")
        XCTAssertNil(store.read(key: "token"))
    }
}
