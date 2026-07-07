// IdleTimerPolicy.swift
//
// When should the screen be kept awake? Pure predicate (P7) so the
// rule is unit-testable off-device; AppState.syncIdleTimer applies
// the result to `UIApplication.shared.isIdleTimerDisabled`.
//
// Rationale: a performer jamming from the Launchpad (or recording a
// take) may not touch the screen for minutes — auto-lock would kill
// the audio session mid-performance. Outside those states the normal
// system idle behavior is preserved (battery, App Review).

import Foundation

enum IdleTimerPolicy {

    /// True when auto-lock must be held off. The transport must be
    /// running AND at least one hands-off surface is engaged:
    /// hardware grid connected, a take armed/recording, or a mic /
    /// vocoder capture in flight.
    static func shouldDisableIdleTimer(
        isPlaying: Bool,
        launchpadConnected: Bool,
        recorderActive: Bool,
        captureActive: Bool
    ) -> Bool {
        isPlaying && (launchpadConnected || recorderActive || captureActive)
    }
}
