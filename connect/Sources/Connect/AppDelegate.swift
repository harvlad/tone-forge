//
// AppDelegate — menu-bar entry point for double-click / .app launches.
//
// Connect retains its CLI subcommands for developers and for the
// bridge mode invoked by the web app (see ONBOARDING_AUDIT §F2.1).
// When launched from Finder with no subcommand, main.swift routes
// here, which installs an NSStatusItem and starts an AppKit run loop.
//
// Wiring status:
//   * "Pair with browser…"  → opens the web app; pairing completes
//                              when the browser fires toneforge://pair
//   * "Microphone…"         → F3.1 (permission-denial recovery)
//   * "Input / Output…"     → later (device picker UI)
//   * "Check for Updates…"  → P2e (Sparkle, shipped)
//   * Engine status text    → live state from AudioEngine.onStateChange
//
// Pairing handoff (P2h):
//   The browser advertises an "Open in Connect" button that fires
//   `toneforge://pair?session=<id>&ws=<encoded-url>` (token= reserved
//   for a future signed handoff). macOS routes the URL to
//   handleURLEvent below, which builds an AudioEngine + PresetBridge
//   pair against the supplied session and surfaces live status
//   through the menu-bar item — no Terminal required.
//

import AppKit
import AVFoundation
import ConnectCore
import Foundation
import Sparkle

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var statusItem: NSStatusItem?

    /// Sparkle updater. Configured at launch with the appcast URL from
    /// Info.plist (SUFeedURL) and an EdDSA public key (SUPublicEDKey).
    /// Sparkle handles the entire flow: background check, download,
    /// signature verify, install-on-quit. We just own the menu item
    /// that triggers `checkForUpdates(_:)`.
    private var updaterController: SPUStandardUpdaterController?

    // MARK: - Bridge / engine state (P2h)
    //
    // The GUI delegate owns the AudioEngine + PresetBridge once a
    // deep-link pairs us with the browser. Stays nil until the user
    // either picks "Pair with browser…" and completes the handoff, or
    // arrives via `toneforge://pair?…` directly.
    private var engine: AudioEngine?
    private var bridge: PresetBridge?
    private var currentSessionId: String?
    private var currentServerURL: URL?

    /// Header menu item that doubles as the live status line. We
    /// rewrite its title from "idle" → "waiting" → "paired" → audio
    /// state so the user can confirm pairing without opening Terminal.
    private var statusMenuItem: NSMenuItem?

    func applicationWillFinishLaunching(_ notification: Notification) {
        // URL events fire BEFORE applicationDidFinishLaunching when the
        // app is cold-launched via a toneforge:// click — register the
        // AppleEvent handler here or the very first deep link is
        // silently dropped. (Apple Technical Note TN2106.)
        registerURLSchemeHandler()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installStatusItem()
        installUpdater()
    }

    private func installUpdater() {
        // Dev builds ship with the literal "__SPARKLE_PUBLIC_KEY__"
        // placeholder still in Info.plist (build_release.sh stamps the
        // real key only when CONNECT_SPARKLE_PUBLIC_KEY is set). In
        // that case Sparkle's XPC updater can't validate appcast
        // signatures, fires an "Unable to Check For Updates" alert on
        // every cold launch, and blocks the menu-bar app from being
        // usable. Detect the placeholder and start the controller with
        // automatic checks disabled — the user can still force a check
        // from the menu, but the dev session isn't interrupted.
        let key = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String ?? ""
        let isDevBuild = key.isEmpty || key.contains("__")
        if isDevBuild {
            NSLog("[Connect] Sparkle: dev build (no SUPublicEDKey stamped) — auto-check disabled")
        }
        updaterController = SPUStandardUpdaterController(
            startingUpdater: !isDevBuild,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // No windows at MVP — the menu-bar item is the persistent UI.
        // Returning false keeps the app alive until the user explicitly
        // quits from the status menu or Dock.
        return false
    }

    func applicationWillTerminate(_ notification: Notification) {
        // Belt-and-braces: tear down audio nodes before AppKit unwinds
        // so AVAudioEngine doesn't log a "stopped without stop()" warning.
        stopPairedBridge()
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

        let status = NSMenuItem(
            title: "Connect — idle",
            action: nil,
            keyEquivalent: ""
        )
        status.isEnabled = false
        menu.addItem(status)
        statusMenuItem = status

        menu.addItem(.separator())

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

    /// Push a one-liner into the status menu so the user sees
    /// connection / audio state without opening Terminal.
    private func setStatus(_ text: String) {
        statusMenuItem?.title = "Connect — \(text)"
    }

    // MARK: - Menu actions

    @objc private func pairWithBrowser(_ sender: Any?) {
        // The pairing flow runs from the browser: it generates the
        // toneforge:// deep link and macOS routes it back to
        // handleURLEvent below. Until that link fires we just open
        // the web app and surface a waiting hint in the menu so the
        // user knows we're alive and watching for the handoff.
        setStatus("waiting to pair…")
        NSWorkspace.shared.open(URL(string: "http://127.0.0.1:8000/")!)
    }

    @objc private func openMicrophoneSettings(_ sender: Any?) {
        // Deep-link to System Settings → Privacy & Security → Microphone.
        // This URL is documented and stable across macOS 12+.
        let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!
        NSWorkspace.shared.open(url)
    }

    @objc private func checkForUpdates(_ sender: Any?) {
        // Sparkle owns the UI from here — shows a sheet, downloads, and
        // installs on quit. If installUpdater() somehow didn't run
        // (shouldn't happen post-launch) we fall back to a transparent
        // error so the user isn't left clicking into the void.
        guard let updater = updaterController else {
            let alert = NSAlert()
            alert.messageText = "Updater not initialized"
            alert.informativeText = "Restart ToneForge Connect and try again."
            alert.runModal()
            return
        }
        updater.checkForUpdates(sender)
    }

    // MARK: - URL scheme (toneforge://) — P2h

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

        // toneforge://pair?session=…&ws=…&token=…
        // token= is reserved for a future signed-handoff phase; we
        // accept and ignore it today so the browser can roll the
        // signed flow out without breaking older Connect builds.
        guard url.scheme == "toneforge", url.host == "pair" else {
            NSLog("[Connect] ignoring deep link scheme=\(url.scheme ?? "?") host=\(url.host ?? "?")")
            return
        }
        guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false)
        else {
            NSLog("[Connect] deep link parse failed: \(urlString)")
            return
        }
        var query: [String: String] = [:]
        for item in components.queryItems ?? [] {
            if let value = item.value {
                query[item.name] = value
            }
        }
        let session = query["session"] ?? "default"
        let wsString = query["ws"] ?? "ws://127.0.0.1:8000/ws/connect-bridge"
        guard let wsURL = URL(string: wsString) else {
            NSLog("[Connect] deep link has invalid ws URL: \(wsString)")
            setStatus("pair failed: bad ws URL")
            return
        }
        NSLog("[Connect] deep-link pair session=\(session) ws=\(wsString)")
        startPairedBridge(sessionId: session, serverURL: wsURL)
    }

    // MARK: - Bridge lifecycle (P2h)
    //
    // Mirrors ConnectMain.startBridge() but lives inside the GUI
    // delegate so the AppKit run loop drives the engine instead of
    // RunLoop.current.run(). Status messages route to the menu bar.

    private func startPairedBridge(sessionId: String, serverURL: URL) {
        // Idempotent: don't tear down a healthy session if the user
        // re-fires the same deep link (clicking "Open in Connect"
        // twice is the most likely cause).
        if currentSessionId == sessionId,
           currentServerURL == serverURL,
           bridge != nil {
            setStatus("already paired: \(sessionId)")
            return
        }

        // Different params → stop the prior session before starting
        // fresh. Otherwise we'd run two engines competing for the
        // microphone.
        stopPairedBridge()

        // Microphone gate. We can't start the engine without the
        // user's blessing, and starting silently after a "Don't
        // Allow" leaves the user wondering why nothing works.
        let auth = AVCaptureDevice.authorizationStatus(for: .audio)
        switch auth {
        case .authorized:
            launchEngineAndBridge(sessionId: sessionId, serverURL: serverURL)
        case .notDetermined:
            setStatus("requesting microphone access…")
            AVCaptureDevice.requestAccess(for: .audio) { [weak self] granted in
                DispatchQueue.main.async { [weak self] in
                    if granted {
                        self?.launchEngineAndBridge(sessionId: sessionId, serverURL: serverURL)
                    } else {
                        self?.surfaceMicrophoneDenied()
                    }
                }
            }
        case .denied, .restricted:
            surfaceMicrophoneDenied()
        @unknown default:
            surfaceMicrophoneDenied()
        }
    }

    private func surfaceMicrophoneDenied() {
        setStatus("microphone denied")
        let alert = NSAlert()
        alert.messageText = "ToneForge Connect needs microphone access"
        alert.informativeText = "Connect listens to your guitar to apply matched tone. Grant access in System Settings → Privacy & Security → Microphone, then try pairing again."
        alert.addButton(withTitle: "Open System Settings")
        alert.addButton(withTitle: "Cancel")
        if alert.runModal() == .alertFirstButtonReturn {
            openMicrophoneSettings(nil)
        }
    }

    private func launchEngineAndBridge(sessionId: String, serverURL: URL) {
        let engine = AudioEngine()
        // Default monitor gain matches the CLI bridge subcommand —
        // muted is safest; the browser's slider drives it up.
        engine.inputMonitorGain = 0.0
        engine.ampSimEnabled = true
        engine.onStateChange = { [weak self] state in
            // Translate the state to a status string at the call site
            // so we don't have to capture `self` into a switch that
            // straddles the actor boundary; the main-queue dispatch
            // below just calls setStatus with the prepared string.
            let title: String
            switch state {
            case .stopped:
                title = "audio stopped"
            case .starting:
                title = "audio starting…"
            case .running:
                title = "paired: \(sessionId)"
            case .reconfiguring(let reason):
                title = "audio reconfiguring (\(reason))"
            case .failed(let error):
                title = "audio failed: \(error)"
            }
            DispatchQueue.main.async { [weak self] in
                self?.setStatus(title)
            }
        }

        do {
            try engine.start()
        } catch {
            setStatus("audio failed: \(error.localizedDescription)")
            NSLog("[Connect] engine failed to start: \(error)")
            return
        }

        let bridge = PresetBridge(serverURL: serverURL, sessionId: sessionId)
        bridge.onStatus = { msg in
            NSLog("[Connect] [bridge] \(msg)")
        }
        bridge.onPresetPush = { [weak engine] preset in
            engine?.applyTonePreset(preset)
        }
        bridge.onGainChange = { [weak engine] gain in
            engine?.inputMonitorGain = gain
        }
        bridge.onChainApply = { [weak engine] spec in
            engine?.applyChain(spec)
        }
        bridge.onSetAutoUpdate = { [weak self] enabled in
            self?.setAutoUpdateEnabled(enabled)
        }
        bridge.onVersionMismatch = { [weak self] required in
            NSLog("[Connect] version mismatch: server requires v\(required)")
            DispatchQueue.main.async { [weak self] in
                self?.stopPairedBridge()
                self?.surfaceVersionMismatch(required: required)
            }
        }
        bridge.start()

        self.engine = engine
        self.bridge = bridge
        self.currentSessionId = sessionId
        self.currentServerURL = serverURL
        setStatus("paired: \(sessionId)")
    }

    private func surfaceVersionMismatch(required: Int) {
        let alert = NSAlert()
        alert.messageText = "ToneForge Connect is out of date"
        alert.informativeText = "The ToneForge server requires protocol v\(required). Update Connect to continue."
        alert.addButton(withTitle: "Check for Updates")
        alert.addButton(withTitle: "Quit")
        let response = alert.runModal()
        if response == .alertFirstButtonReturn {
            updaterController?.checkForUpdates(nil)
        } else {
            NSApplication.shared.terminate(nil)
        }
    }

    /// Write the user's Sparkle auto-update preference into our app's
    /// ``UserDefaults`` under the canonical ``SUEnableAutomaticChecks``
    /// key. Sparkle picks the value up on its next scheduled-check
    /// tick — no restart required and no need to poke
    /// ``SPUStandardUpdaterController`` directly. We keep
    /// ``UserDefaults`` as the single source of truth and let the
    /// Sparkle framework decide what to do with it.
    ///
    /// Logged to NSLog so the menu-bar status tail shows the change
    /// during dev / smoke-testing; the menu itself doesn't surface
    /// a checkbox in this commit (browser is the sole UI).
    func setAutoUpdateEnabled(_ enabled: Bool) {
        UserDefaults.standard.set(enabled, forKey: "SUEnableAutomaticChecks")
        NSLog("[Connect] Sparkle auto-update \(enabled ? "enabled" : "disabled")")
    }

    private func stopPairedBridge() {
        bridge?.stop()
        engine?.stop()
        bridge = nil
        engine = nil
        currentSessionId = nil
        currentServerURL = nil
    }
}
