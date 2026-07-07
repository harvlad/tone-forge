// AttestationStore.swift
//
// One-time ownership attestation. Before the first import (Music
// library or Files), the user must confirm they own the audio or have
// rights to use it for personal practice. The acceptance is persisted
// in UserDefaults with a timestamp and a version — bumping
// `currentVersion` re-prompts everyone if the attestation copy ever
// changes materially.

import Foundation
import Combine

@MainActor
public final class AttestationStore: ObservableObject {

    public nonisolated static let currentVersion = 1

    enum Keys {
        static let accepted = "toneforge.attestation.accepted"
        static let acceptedAt = "toneforge.attestation.acceptedAt"
        static let version = "toneforge.attestation.version"
    }

    /// True once the user accepted the current attestation version.
    @Published public private(set) var isAccepted: Bool

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        isAccepted = defaults.bool(forKey: Keys.accepted)
            && defaults.integer(forKey: Keys.version) >= Self.currentVersion
    }

    /// When the user accepted, if they have.
    public var acceptedAt: Date? {
        guard
            let raw = defaults.string(forKey: Keys.acceptedAt),
            let date = ISO8601DateFormatter().date(from: raw)
        else {
            return nil
        }
        return date
    }

    public func accept(now: Date = Date()) {
        defaults.set(true, forKey: Keys.accepted)
        defaults.set(ISO8601DateFormatter().string(from: now), forKey: Keys.acceptedAt)
        defaults.set(Self.currentVersion, forKey: Keys.version)
        isAccepted = true
    }

    /// Clears the persisted attestation. Used by the `-uitest-reset-
    /// attestation` launch argument so UI tests always start from the
    /// un-attested state.
    public func resetForUITests() {
        Self.resetPersisted(defaults: defaults)
        isAccepted = false
    }

    /// Nonisolated variant for the launch-argument hook, which runs
    /// before any store instance exists (ToneForgeScene.init).
    public nonisolated static func resetPersisted(defaults: UserDefaults = .standard) {
        defaults.removeObject(forKey: Keys.accepted)
        defaults.removeObject(forKey: Keys.acceptedAt)
        defaults.removeObject(forKey: Keys.version)
    }
}
