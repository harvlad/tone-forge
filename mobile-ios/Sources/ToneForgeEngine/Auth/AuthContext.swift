// AuthContext.swift
//
// Process-wide auth state applied to backend requests.
//
// Why a singleton and not per-client injection: the Engine clients
// (HistoryClient, JobClient, PackClient, ...) are ad-hoc Sendable
// structs created all over the app. Injecting a token into each would
// go stale the moment the user signs in or out. Instead AccountStore
// (Mobile) writes the session token here once, and every client calls
// `apply(to:)` at request-build time — reads always see the current
// token.
//
// NSLock + @unchecked Sendable keeps this correct under strict
// concurrency without making every call site async.

import Foundation

public final class AuthContext: @unchecked Sendable {
    public static let shared = AuthContext()

    private let lock = NSLock()
    private var _sessionToken: String?
    private var _deviceId: String?

    init() {}

    /// Current opaque session token, or nil when signed out.
    public var sessionToken: String? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return _sessionToken
        }
        set {
            lock.lock()
            defer { lock.unlock() }
            _sessionToken = newValue
        }
    }

    /// Persistent device id (set once at app boot from DeviceIdentity).
    public var deviceId: String? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return _deviceId
        }
        set {
            lock.lock()
            defer { lock.unlock() }
            _deviceId = newValue
        }
    }

    /// Stamp a backend request with the bearer token (when signed in)
    /// and the device id (always, once set).
    public func apply(to request: inout URLRequest) {
        lock.lock()
        let token = _sessionToken
        let device = _deviceId
        lock.unlock()
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let device, !device.isEmpty {
            request.setValue(device, forHTTPHeaderField: "X-Device-Id")
        }
    }
}
