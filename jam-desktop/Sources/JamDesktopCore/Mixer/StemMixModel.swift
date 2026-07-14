// StemMixModel.swift
//
// Gain/mute/solo matrix for the stems mixer, plus the song master
// fader. Pure state — the audio layer subscribes via `onMixChanged`
// and pulls `effectiveGain(for:)` per stem, which already folds in
// the mute/solo rules (same semantics as the mobile StemPlayer and
// the web mixer):
//
//   muted            → 0
//   any stem soloed  → 0 unless this stem is soloed
//   otherwise        → the stem's own gain
//
// Song gain multiplies on top at the submix, not per stem.

import Foundation
import Observation

public struct StemMixState: Identifiable, Equatable, Sendable {
    public let role: String
    public var gain: Double
    public var isMuted: Bool
    public var isSoloed: Bool

    public var id: String { role }

    public init(role: String, gain: Double = 1.0,
                isMuted: Bool = false, isSoloed: Bool = false) {
        self.role = role
        self.gain = gain
        self.isMuted = isMuted
        self.isSoloed = isSoloed
    }
}

@Observable
@MainActor
public final class StemMixModel {

    public private(set) var stems: [StemMixState] = []

    /// Master fader over the whole stems submix, 0…1.
    public var songGain: Double = 1.0 {
        didSet {
            if songGain != oldValue { onMixChanged?() }
        }
    }

    /// Fired after any change that alters an effective gain. The
    /// audio layer re-applies all node volumes on this signal.
    @ObservationIgnored public var onMixChanged: (() -> Void)?

    public init() {}

    /// Replace the matrix for a newly loaded song. Order preserved
    /// (bundle stem order = mixer strip order, like the web UI).
    public func load(roles: [String]) {
        stems = roles.map { StemMixState(role: $0) }
        onMixChanged?()
    }

    public func setGain(_ gain: Double, for role: String) {
        mutate(role) { $0.gain = min(max(0, gain), 1) }
    }

    public func setMuted(_ muted: Bool, for role: String) {
        mutate(role) { $0.isMuted = muted }
    }

    public func toggleMute(for role: String) {
        mutate(role) { $0.isMuted.toggle() }
    }

    public func setSoloed(_ soloed: Bool, for role: String) {
        mutate(role) { $0.isSoloed = soloed }
    }

    public func toggleSolo(for role: String) {
        mutate(role) { $0.isSoloed.toggle() }
    }

    public var anySoloed: Bool {
        stems.contains { $0.isSoloed }
    }

    /// The volume the audio layer should program on this stem's gain
    /// node — mute/solo already folded in. Unknown roles are silent.
    public func effectiveGain(for role: String) -> Double {
        guard let stem = stems.first(where: { $0.role == role }) else { return 0 }
        if stem.isMuted { return 0 }
        if anySoloed && !stem.isSoloed { return 0 }
        return stem.gain
    }

    private func mutate(_ role: String, _ change: (inout StemMixState) -> Void) {
        guard let idx = stems.firstIndex(where: { $0.role == role }) else { return }
        let before = stems[idx]
        change(&stems[idx])
        if stems[idx] != before { onMixChanged?() }
    }
}
