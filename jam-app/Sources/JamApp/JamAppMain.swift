// JamApp — minimal SwiftUI shell that loads the Jam web UI in a WKWebView.
//
// Assumes the ToneForge backend is reachable at JAM_URL (defaults to
// http://localhost:8000/jam). Start the backend separately with:
//
//     cd backend && uvicorn tone_forge_api:app --port 8000
//
// then `swift run JamApp` from this directory.

import SwiftUI
import WebKit
import AppKit

// MARK: - Configuration

/// Where the embedded WebView should load the Jam UI from.
/// Override with the JAM_URL environment variable for local dev against
/// a non-default host/port (e.g. JAM_URL=http://192.168.1.10:8000/jam).
private func jamURL() -> URL {
    if let raw = ProcessInfo.processInfo.environment["JAM_URL"],
       let url = URL(string: raw) {
        return url
    }
    return URL(string: "http://localhost:8000/jam")!
}

// MARK: - WebView wrapper

/// Bridges WKWebView into SwiftUI. Kept deliberately thin — no
/// navigation hooks, no JS bridge, no cookie handling. If we ever
/// need to talk between the web UI and a native audio engine, this
/// is where the message handlers would go.
struct JamWebView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        // Allow autoplay of the stem audio without a click gesture —
        // the Jam UI is the gesture, effectively.
        config.mediaTypesRequiringUserActionForPlayback = []

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = false
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {
        // No-op: the URL is fixed for the lifetime of the window.
    }
}

// MARK: - Root view

struct ContentView: View {
    let url: URL

    var body: some View {
        JamWebView(url: url)
            .frame(minWidth: 1100, minHeight: 720)
    }
}

// MARK: - App entry

@main
struct JamApp: App {
    init() {
        // When launched via `swift run` (no .app bundle) the process
        // defaults to .accessory, which leaves the window behind other
        // apps and hides it from the Dock. Force regular activation so
        // it behaves like a normal Mac app.
        NSApplication.shared.setActivationPolicy(.regular)
        NSApplication.shared.activate(ignoringOtherApps: true)
    }

    var body: some Scene {
        WindowGroup("Jam") {
            ContentView(url: jamURL())
        }
        .windowStyle(.titleBar)
        .commands {
            // Drop the default "New Window" — there's only one Jam.
            CommandGroup(replacing: .newItem) { }
        }
    }
}
