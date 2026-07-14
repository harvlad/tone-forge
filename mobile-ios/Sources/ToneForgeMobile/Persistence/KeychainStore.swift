// KeychainStore.swift
//
// Minimal secret persistence for the account session token. Protocol
// seam (`SecretStore`) so AccountStore tests run against an in-memory
// fake — the real Keychain is exercised by one guarded round-trip
// test on device/simulator.
//
// Generic-password items, service-scoped, kSecAttrAccessibleAfterFirstUnlock
// (background URLSession work may need the token before first unlock
// completes is NOT a requirement here; AfterFirstUnlock is the
// standard choice for tokens refreshed at app launch). No access
// group — keeps the store simulator-safe without entitlement setup.

import Foundation
import Security

public protocol SecretStore: Sendable {
    func read(key: String) -> String?
    func write(key: String, value: String)
    func delete(key: String)
}

public struct KeychainStore: SecretStore {
    public let service: String

    public init(service: String = "com.harvlad.toneforge.mobile") {
        self.service = service
    }

    private func baseQuery(key: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
    }

    public func read(key: String) -> String? {
        var query = baseQuery(key: key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    public func write(key: String, value: String) {
        let data = Data(value.utf8)
        var query = baseQuery(key: key)
        // Try update first; add on not-found.
        let update: [String: Any] = [kSecValueData as String: data]
        let status = SecItemUpdate(query as CFDictionary, update as CFDictionary)
        if status == errSecItemNotFound {
            query[kSecValueData as String] = data
            query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
            SecItemAdd(query as CFDictionary, nil)
        }
    }

    public func delete(key: String) {
        SecItemDelete(baseQuery(key: key) as CFDictionary)
    }
}

/// Test double. Class + lock (not struct) so all copies share state,
/// matching Keychain semantics.
public final class InMemorySecretStore: SecretStore, @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String: String] = [:]

    public init() {}

    public func read(key: String) -> String? {
        lock.lock()
        defer { lock.unlock() }
        return storage[key]
    }

    public func write(key: String, value: String) {
        lock.lock()
        defer { lock.unlock() }
        storage[key] = value
    }

    public func delete(key: String) {
        lock.lock()
        defer { lock.unlock() }
        storage[key] = nil
    }
}
