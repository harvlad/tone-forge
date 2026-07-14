// AdminCredentials.swift
//
// Operator admin token for /api/admin/* and /api/debug/* endpoints.
// The backend guards those behind TONEFORGE_ADMIN_TOKEN (X-Admin-Token
// header) and answers 404 — not 401 — when the token is missing or
// wrong, so callers should surface "set the admin token in Settings"
// on a 404 from a guarded path. Loopback backends pass tokenless.

import Foundation

public enum AdminCredentials {
    static let defaultsKey = "jamdesktop.adminToken"

    public static func token(
        defaults: UserDefaults = .standard
    ) -> String? {
        let stored = defaults.string(forKey: defaultsKey)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return (stored?.isEmpty ?? true) ? nil : stored
    }

    public static func setToken(
        _ token: String?, defaults: UserDefaults = .standard
    ) {
        let trimmed = token?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let trimmed, !trimmed.isEmpty {
            defaults.set(trimmed, forKey: defaultsKey)
        } else {
            defaults.removeObject(forKey: defaultsKey)
        }
    }

    /// Attach the token when present; harmless no-op otherwise (local
    /// backends accept direct loopback without one).
    public static func apply(
        to request: inout URLRequest, defaults: UserDefaults = .standard
    ) {
        if let token = token(defaults: defaults) {
            request.setValue(token, forHTTPHeaderField: "X-Admin-Token")
        }
    }
}
