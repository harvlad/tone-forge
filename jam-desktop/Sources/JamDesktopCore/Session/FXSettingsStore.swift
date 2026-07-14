// FXSettingsStore.swift
//
// UserDefaults-backed persistence for the D-022 master FX panel,
// ported from the mobile FXSettingsStore: one versioned JSON blob,
// decodeIfPresent for fields added later, corrupt blobs silently
// replaced with neutral. Load/save primitives only — the observable
// state and preset semantics live in FXPanelModel.

import Foundation
import ToneForgeEngine

public struct FXSettingsStore {

    private let defaults: UserDefaults
    private let key = "jamdesktop.fxSettings"

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public func load() -> FXSettings {
        guard let data = defaults.data(forKey: key) else { return .neutral }
        do {
            return try JSONDecoder().decode(FXSettings.self, from: data)
        } catch {
            print("[FXSettingsStore] decode failed, using neutral: \(error)")
            return .neutral
        }
    }

    public func save(_ settings: FXSettings) {
        do {
            defaults.set(try JSONEncoder().encode(settings), forKey: key)
        } catch {
            print("[FXSettingsStore] encode failed: \(error)")
        }
    }
}
