// Haptics.swift
//
// Intentional, musical haptics for the JamN UI. Rate-limited so rapid
// pattern playing stays comfortable (a burst of pad taps must not
// machine-gun the Taptic Engine). Audio triggering never waits on this —
// haptics are fire-and-forget, off the audio path.

import Foundation
#if canImport(UIKit)
import UIKit
#endif

@MainActor
enum Haptics {
    #if canImport(UIKit)
    private static let light = UIImpactFeedbackGenerator(style: .light)
    private static let medium = UIImpactFeedbackGenerator(style: .medium)
    private static let rigid = UIImpactFeedbackGenerator(style: .rigid)
    private static let selection = UISelectionFeedbackGenerator()
    private static let notify = UINotificationFeedbackGenerator()

    /// Minimum gap between pad-trigger haptics (seconds). Fast runs above
    /// this rate feel like one continuous texture instead of a rattle.
    private static let padMinInterval: TimeInterval = 0.045
    private static var lastPadFire: TimeInterval = 0
    #endif

    /// Warm up the generators — call when a haptic surface appears so the
    /// first tap isn't late.
    static func prepare() {
        #if canImport(UIKit)
        light.prepare(); medium.prepare(); selection.prepare()
        #endif
    }

    /// Pad trigger — light, rate-limited.
    static func padTrigger() {
        #if canImport(UIKit)
        let now = ProcessInfo.processInfo.systemUptime
        guard now - lastPadFire >= padMinInterval else { return }
        lastPadFire = now
        light.impactOccurred(intensity: 0.7)
        #endif
    }

    /// Radial menu opened.
    static func radialOpen() {
        #if canImport(UIKit)
        medium.impactOccurred()
        #endif
    }

    /// Selection moved (radial segment change, section pick).
    static func selectionChanged() {
        #if canImport(UIKit)
        selection.selectionChanged()
        #endif
    }

    /// Loop / latch toggle — crisp state change.
    static func toggle() {
        #if canImport(UIKit)
        rigid.impactOccurred()
        #endif
    }

    /// Record start/stop.
    static func record() {
        #if canImport(UIKit)
        notify.notificationOccurred(.success)
        #endif
    }
}
