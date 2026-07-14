// DeviceIdentity.swift
//
// Persistent per-install device id. Sent as `X-Device-Id` on backend
// requests so anonymous analyses are stamped with this device and can
// be claimed when the user signs in later.
//
// Stored in UserDefaults (not Keychain): a reinstall generating a new
// id is acceptable — server-side uploads are retained for days, not
// months, and claimed history is already bound to the account.

import Foundation

public enum DeviceIdentity {
    public static let defaultsKey = "toneforge.deviceId"

    /// Returns the stable device id, creating and persisting one on
    /// first use.
    public static func id(defaults: UserDefaults = .standard) -> String {
        if let existing = defaults.string(forKey: defaultsKey), !existing.isEmpty {
            return existing
        }
        let fresh = UUID().uuidString.lowercased()
        defaults.set(fresh, forKey: defaultsKey)
        return fresh
    }
}
