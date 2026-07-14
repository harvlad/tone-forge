// ToneForgeApp.swift (app target entry)
//
// The app target is deliberately tiny — everything lives in the
// ``ToneForgeMobile`` library so we can also run the code in tests
// and (later) previews. This file just declares @main and hands over
// to the library's ``ToneForgeScene``.

import SwiftUI
import ToneForgeMobile

@main
struct ToneForgeAppEntry: App {
    // Background URLSession relaunch handshake: iOS wakes the app to
    // deliver a finished analysis-job poll and hands us a completion
    // handler via this delegate. Needed only for that handoff.
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        ToneForgeScene()
    }
}
