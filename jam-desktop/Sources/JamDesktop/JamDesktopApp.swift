// JamDesktopApp.swift
//
// Entry point. One window, one AppModel. The audio engine and
// bridge live in controllers owned by RootView's environment so
// SwiftUI previews of leaf views never spin up CoreAudio.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

@main
struct JamDesktopApp: App {
    @StateObject private var model = AppModel()
    @StateObject private var session = SessionController()

    init() {
        // Line-buffer stdout so `print()` diagnostics reach a redirected
        // log file immediately (stdout is block-buffered when piped).
        setvbuf(stdout, nil, _IOLBF, 0)

        // Stamp a persistent device id onto every backend request so
        // analysis jobs are scoped to this machine (GET /api/jobs).
        AuthContext.shared.deviceId = DeviceIdentity.id()
    }

    var body: some Scene {
        WindowGroup("Jamn") {
            RootView()
                .environmentObject(model)
                .environmentObject(session)
                .frame(minWidth: 960, minHeight: 640)
                .preferredColorScheme(.dark)
                .tint(JamTheme.accent)
        }
        .windowResizability(.contentMinSize)

        Settings {
            SettingsView()
                .environmentObject(model)
                .environmentObject(session)
                .preferredColorScheme(.dark)
                .tint(JamTheme.accent)
        }

        // Dev tool (port of backend/static/debug.js) — separate window
        // so it stays out of the product nav. Open with ⌘⇧D.
        Window("Debug", id: "debug") {
            DebugWindowView()
                .environmentObject(model)
                .frame(minWidth: 900, minHeight: 620)
                .preferredColorScheme(.dark)
                .tint(JamTheme.accent)
        }
        .keyboardShortcut("d", modifiers: [.command, .shift])
        .windowResizability(.contentMinSize)
    }
}
