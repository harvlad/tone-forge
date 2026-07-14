// NotificationManager.swift
//
// Local completion notifications for background analyses. Uses only the
// runtime UNUserNotificationCenter authorization — no push entitlement,
// no APNs. When a job finishes while the app is backgrounded or killed,
// this posts a local notification; tapping it routes the history id back
// so the finished song opens.
//
// APNs (remote push) is a future swap-in: the server already stores a
// device token via /api/register-device and calls a completion hook.
// This class stays the on-device presenter regardless of transport.

import Foundation
import os
#if canImport(UserNotifications)
import UserNotifications
#endif

public final class NotificationManager: NSObject {

    private static let log = Logger(
        subsystem: "com.harvlad.toneforge.mobile", category: "notifications"
    )

    public static let shared = NotificationManager()

    /// Routes a notification tap to open the finished song. Set by
    /// JobCompletionCenter at boot.
    public var onOpenHistory: ((String) -> Void)?

    /// Wire the notification-center delegate so taps route back into
    /// the app. Safe at launch — shows no permission prompt.
    public func activate() {
        #if canImport(UserNotifications)
        UNUserNotificationCenter.current().delegate = self
        #endif
    }

    /// Ask for notification permission. Deferred until the first
    /// analysis job is submitted so the prompt appears in context
    /// ("get notified when your song is ready") instead of at first
    /// launch. Calling repeatedly is harmless — iOS prompts once.
    public func requestAuthorization() {
        #if canImport(UserNotifications)
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        center.requestAuthorization(options: [.alert, .sound]) { granted, error in
            if let error {
                Self.log.error(
                    "notification authorization failed: \(error.localizedDescription, privacy: .public)"
                )
            } else if !granted {
                // Not an error — completion alerts stay off, and the
                // queued-open path still surfaces finished songs on
                // next foreground.
                Self.log.notice("notification authorization denied by user")
            }
        }
        #endif
    }

    public func postCompleted(historyId: String, title: String) {
        #if canImport(UserNotifications)
        let content = UNMutableNotificationContent()
        content.title = "Analysis complete"
        content.body = "\(title) is ready to jam."
        content.sound = .default
        content.userInfo = ["historyId": historyId]
        let request = UNNotificationRequest(
            identifier: "job-done-\(historyId)", content: content, trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
        #endif
    }

    public func postFailed(title: String) {
        #if canImport(UserNotifications)
        let content = UNMutableNotificationContent()
        content.title = "Analysis failed"
        content.body = "\(title) couldn't be analysed. Open the app to retry."
        content.sound = .default
        let request = UNNotificationRequest(
            identifier: "job-fail-\(UUID().uuidString)", content: content, trigger: nil
        )
        UNUserNotificationCenter.current().add(request)
        #endif
    }
}

#if canImport(UserNotifications)
extension NotificationManager: UNUserNotificationCenterDelegate {

    /// Present the banner even when the app is foreground (rare — the
    /// completion path suppresses foreground notifications — but a
    /// racing background poll shouldn't be swallowed silently).
    public func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler:
            @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound])
    }

    public func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        if let historyId = response.notification.request.content
            .userInfo["historyId"] as? String {
            DispatchQueue.main.async { self.onOpenHistory?(historyId) }
        }
        completionHandler()
    }
}
#endif
