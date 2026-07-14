// AppDelegate.swift
//
// Minimal UIApplicationDelegate, attached to the SwiftUI App via
// @UIApplicationDelegateAdaptor. Its only job is the background
// URLSession relaunch handshake: when iOS wakes the app to deliver a
// finished analysis-job poll, it hands us a completion handler that must
// be stored and called once the session drains. Reinstantiating the
// background session (via `activate()`) reattaches to the pending tasks
// so their delegate callbacks fire.

import Foundation
#if canImport(UIKit)
import UIKit

public final class AppDelegate: NSObject, UIApplicationDelegate {

    public func application(
        _ application: UIApplication,
        handleEventsForBackgroundURLSession identifier: String,
        completionHandler: @escaping () -> Void
    ) {
        guard identifier == BackgroundAnalyzeSession.sessionIdentifier else {
            completionHandler()
            return
        }
        BackgroundAnalyzeSession.shared.backgroundCompletionHandler = completionHandler
        BackgroundAnalyzeSession.shared.activate()
    }
}
#endif
