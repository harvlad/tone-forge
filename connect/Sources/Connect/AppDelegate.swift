//
// AppDelegate — menu-bar entry point for double-click / .app launches.
//
// Connect retains its CLI subcommands for developers and for the
// bridge mode invoked by the web app (see ONBOARDING_AUDIT §F2.1).
// When launched from Finder with no subcommand, main.swift routes
// here, which installs an NSStatusItem and starts an AppKit run loop.
//
// This file is the *skeleton*. The menu items below are stubs; each
// becomes wired up in subsequent priority steps:
//   * "Pair with browser…"  → P2c (WS v1 + toneforge:// deep-link)
//   * "Microphone…"          → F3.1 (permission-denial recovery)
//   * "Input / Output…"      → later (device picker UI)
//   * "Check for Updates…"   → P2e (Sparkle)
//   * Engine status text     → P2d (config-change recovery)
//
// The skeleton's job today: make the .app bundle launch cleanly,
// show a Dock + menu-bar presence, and quit cleanly. Nothing more.
//

import AppKit
import ConnectCore
import Foundation

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        installStatusItem()
        registerURLSchemeHandler()
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // No windows at MVP — the menu-bar item is the persistent UI.
        // Returning false keeps the app alive until the user explicitly
        // quits from the status menu or Dock.
        return false
    }

    // MARK: - Status bar

    private func installStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            // Plain text title until we ship an icon (.icns asset is
            // assembled by build_release.sh). Single character keeps
            // the bar uncluttered.
            button.title = "TF"
            button.toolTip = "ToneForge Connect"
        }
        item.menu = buildMenu()
        statusItem = item
    }

    private func buildMenu() -> NSMenu {
        let menu = NSMenu()

        let statusItem = NSMenuItem(
            title: "Connect — idle",
            action: nil,
            keyEquivalent: ""
        )
        statusItem.isEnabled = false
        menu.addItem(statusItem)

        menu.addItem(.separator())

        // P2c will replace this with a real pairing flow.
        let pair = NSMenuItem(
            title: "Pair with browser…",
            action: #selector(pairWithBrowser(_:)),
            keyEquivalent: "p"
        )
        pair.target = self
        menu.addItem(pair)

        // F3.1 — recovery path for accidental "Don't Allow".
        let mic = NSMenuItem(
            title: "Microphone…",
            action: #selector(openMicrophoneSettings(_:)),
            keyEquivalent: ""
        )
        mic.target = self
        menu.addItem(mic)

        menu.addItem(.separator())

        // P2e will replace this with a Sparkle-backed check.
        let update = NSMenuItem(
            title: "Check for Updates…",
            action: #selector(checkForUpdates(_:)),
            keyEquivalent: ""
        )
        update.target = self
        menu.addItem(update)

        menu.addItem(.separator())

        let quit = NSMenuItem(
            title: "Quit ToneForge Connect",
            action: #selector(NSApplication.terminate(_:)),
            keyEquivalent: "q"
        )
        menu.addItem(quit)

        return menu
    }

    // MARK: - Menu actions (stubs)

    @objc private func pairWithBrowser(_ sender: Any?) {
        // P2c lands the real flow. Until then, surface a transparent
        // message so an internal tester knows where the work is going.
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8000/")!)
    }

    @objc private func openMicrophoneSettings(_ sender: Any?) {
        // Deep-link to System Settings → Privacy & Security → Microphone.
        // This URL is documented and stable across macOS 12+.
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!
        NSWorkspace.shared.open(url)
    }

    @objc private func checkForUpdates(_ sender: Any?) {
        // P2e wires this into Sparkle's SPUStandardUpdaterController.
        let alert = NSAlert()
        alert.messageText = "Updates not yet wired in this build."
        alert.informativeText = "Auto-update will arrive in a near-term Connect release."
        alert.runModal()
    }

    // MARK: - URL scheme (toneforge://)

    private func registerURLSchemeHandler() {
        NSAppleEventManager.shared().setEventHandler(
            self,
            andSelector: #selector(handleURLEvent(_:withReplyEvent:)),
            forEventClass: AEEventClass(kInternetEventClass),
            andEventID: AEEventID(kAEGetURL)
        )
    }

    @objc func handleURLEvent(
        _ event: NSAppleEventDescriptor,
        withReplyEvent reply: NSAppleEventDescriptor
    ) {
        guard
            let urlString = event.paramDescriptor(forKeyword: keyDirectObject)?.stringValue,
            let url = URL(string: urlString)
        else { return }

        // toneforge://pair?token=…&ws=…
        // Real handling lands in P2c. Today we just log it so the
        // path through deep-link → app is verifiable end-to-end.
        let scheme = url.scheme ?? "(no-scheme)"
        let host = url.host ?? "(no-host)"
        NSLog("[Connect] received deep link scheme=\(scheme) host=\(host)")
    }
}
