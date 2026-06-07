# Connect onboarding friction audit

**Persona:** non-technical guitarist on a 5-year-old MacBook (likely Intel,
likely on macOS Monterey / Big Sur depending on their update cadence).
**Test:** install and use Connect without opening Terminal.

This audit enumerates every concrete blocker between that user and a
working session. Each item lists *what stops them today* and *the fix*
(file path + change). Items are ordered by where they first hit the
user in the install → first-jam flow.

The audit is the work plan for Priority 2 (Connect Hardening). Items
flagged **[P2a]**…**[P2f]** map to TodoWrite entries.

---

## Stage 0 — Acquiring the app

### F0.1 No distributable artifact exists [P2b]
**Blocker.** The repo ships a Swift Package. The user has no `.app`,
no `.dmg`, no `.zip`. Running it requires `swift run Connect`, which
requires the Xcode command-line tools — installable only via Terminal.
**Fix.** Add `connect/build_release.sh` that produces a signed,
notarized `.app` bundle and a `Connect.dmg`. Two-target build:
- `swift build -c release` produces the executable.
- A new `Resources/` dir contributes `Info.plist`, icon, entitlements.
- `codesign --options runtime --entitlements …` against Developer ID.
- `xcrun notarytool submit … --wait` against the Apple notary service.
- `xcrun stapler staple Connect.app`.
- `hdiutil create -volname "ToneForge Connect" -srcfolder Connect.app …`.
**Output of this stage:** a `Connect.dmg` we can host on GitHub Releases.

### F0.2 No release hosting [P2e]
**Blocker.** User has nowhere to download the DMG from.
**Fix.** Publish releases to GitHub Releases under
`anthropics/tone-forge` (or the actual repo). `build_release.sh` ends
with `gh release create` so we never ship by hand.

### F0.3 No appcast for updates [P2e]
**Blocker.** Even if the user installs once, they have no path to
receive updates short of redownloading manually.
**Fix.** Integrate Sparkle (SPM). Add `appcast.xml` generation to
`build_release.sh` and host it on GitHub Releases. The released `.app`
ships with `SUFeedURL` in `Info.plist`.

---

## Stage 1 — Opening the app for the first time

### F1.1 Gatekeeper will block an unsigned binary [P2b]
**Blocker.** macOS refuses to launch an app that isn't notarized and
signed with a Developer ID. The user sees "Connect.app cannot be
opened because the developer cannot be verified." Right-click → Open
is a workaround we will not document; a non-technical user reads that
message as broken software.
**Fix.** Same as F0.1 — Developer ID Application signature + Apple
notary stapling. Verification step in `build_release.sh`:
`spctl --assess --verbose=4 Connect.app` must report
`source=Notarized Developer ID`.

### F1.2 No Info.plist → no permission prompts [P2a]
**Blocker.** A bare CLI binary has no Info.plist. macOS treats a
missing `NSMicrophoneUsageDescription` as a hard deny — the input
node's tap will return silence forever with no prompt and no error
the user can act on.
**Fix.** Create `connect/Resources/Info.plist` with:
- `CFBundleIdentifier` = `com.toneforge.connect`
- `CFBundleName`, `CFBundleDisplayName`, `CFBundleVersion`,
  `CFBundleShortVersionString`
- `LSMinimumSystemVersion` = `13.0` (see F1.5)
- `NSMicrophoneUsageDescription` = "ToneForge Connect listens to your
  guitar so it can be mixed alongside the song you're jamming with."
- `LSApplicationCategoryType` = `public.app-category.music`
- `LSUIElement` = `NO` (we want a Dock entry for quitting)
- `SUFeedURL` (set in F0.3)

### F1.3 No hardened-runtime entitlements [P2a]
**Blocker.** Notarization requires the hardened runtime. The hardened
runtime denies microphone access unless the entitlement is granted.
**Fix.** Create `connect/Resources/Connect.entitlements`:
- `com.apple.security.device.audio-input` = `true`
- `com.apple.security.network.client` = `true` (WebSocket to backend)
- `com.apple.security.files.user-selected.read-only` = `true`
  (future: drag-and-drop stems)
- `com.apple.security.app-sandbox` = `false` (we need CoreAudio HAL
  device enumeration which sandbox forbids; this is allowed for
  Developer-ID distribution outside the App Store)

### F1.4 No app icon
**Blocker.** Dock shows a generic gear. Looks like malware to a
cautious user.
**Fix.** Ship `connect/Resources/AppIcon.icns`. Even a simple
mark-only icon is far better than the default. Tracked under [P2a]
because it lives next to Info.plist.

### F1.5 macOS 13 minimum cuts off ~3 years of Macs [P2a]
**Blocker.** Package.swift requires macOS 13 (Ventura, Oct 2022). A
5-year-old MacBook on default updates is likely on macOS 11–12. The
user gets "This app requires macOS 13 or later" with no recourse.
**Fix.** Drop the floor to **macOS 12 (Monterey)**. AVAudioEngine
behavior we depend on is stable since macOS 11; the only macOS 13+
API we'd lose is `AsyncSequence` in `URLSession.WebSocketTask`,
which has a backport pattern. Update both `Package.swift`
(`.macOS(.v12)`) and `Info.plist` (`LSMinimumSystemVersion 12.0`).
If a stretch goal demands macOS 13 later, do it then, not preemptively.

---

## Stage 2 — Running Connect without a Terminal

### F2.1 No GUI [P2a]
**Blocker.** `Connect.app` today is a CLI binary. Double-clicking it
launches Terminal, which is the failure mode we're explicitly
preventing.
**Fix.** Add a minimal AppKit entry point: `NSApplicationMain` + a
status-bar menu (the same `NSStatusItem` Connect would need anyway
once we add a UI). The CLI commands stay reachable via Terminal for
developers, but the default double-click path launches the menu-bar
app. This lives in a new `Sources/Connect/AppDelegate.swift`. Menu
items for MVP:
- "Pair with browser…" (opens system browser to the session URL)
- "Microphone…" (re-prompt if denied earlier, or open System Settings)
- "Input device → submenu"
- "Output device → submenu"
- "Quit"

### F2.2 No automatic backend pairing [P2c]
**Blocker.** `connect bridge` requires the user to know
`ws://127.0.0.1:8000/ws/connect-bridge` and the session id, and to
copy them between web and CLI.
**Fix.** Pair via deep-link. The web app generates a one-time
session token and opens `toneforge://pair?token=…&ws=…`. Connect
registers as the handler for `toneforge://` (LSHandlerRank in
Info.plist). The deep-link handler stores the token, opens the WS,
sends `{"type":"hello","token":…,"protocol_version":1}` (see F3.1),
and the server validates.

### F2.3 Default monitor gain is muted [P2a]
**Blocker.** Bridge mode defaults `monitorGain = 0.0`, requiring a
relaunch with a positional argument to hear yourself. A non-technical
user cannot relaunch with a positional argument.
**Fix.** Keep the muted default in code, but expose a Dock/menu
toggle (or, better, a slider in the web UI that the bridge already
listens to via `onGainChange`). Document in the menu-bar item:
"Monitor: muted — enable from the browser."

---

## Stage 3 — Mic permission prompt

### F3.1 Permission denial is a dead end
**Blocker.** If the user accidentally clicks "Don't Allow" the first
time, today there is no UX path back. The engine will silently
deliver silence forever and the user concludes the app is broken.
**Fix.** On engine start, call `AVCaptureDevice.authorizationStatus`.
On `.denied`, the menu-bar UI surfaces "Microphone blocked — click
to open System Settings" which launches
`x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone`.
This lives in `AudioEngine.swift` + `AppDelegate.swift`.

---

## Stage 4 — Reliability during a session

### F4.1 Engine has no recovery from device disconnect [P2d]
**Blocker.** Five-year-old MacBooks frequently have flaky USB. If
the user's interface disconnects mid-jam, AVAudioEngine throws and
the process must be killed. Today there is no Terminal-free way to
kill and relaunch.
**Fix.** Observe
`AVAudioEngineConfigurationChangeNotification`. On notification,
stop and restart the engine with the new default device, reattach
all nodes, resume playback at the current transport position. The
menu-bar UI displays "Audio interface changed — reconnecting…" then
"Connected to <new device>." This is the bulk of [P2d].

### F4.2 No structured logging
**Blocker.** When something goes wrong, the user cannot supply us a
diagnostic. We end up debugging blindly.
**Fix.** Route all `print` calls in `Sources/Connect/main.swift` and
`ConnectCore/*.swift` through `os.Logger` with a `com.toneforge.connect`
subsystem. The menu-bar app has a "Reveal Log…" item that opens
Console.app filtered to that subsystem. Tracked under [P2f].

### F4.3 WebSocket has no protocol versioning [P2c]
**Blocker.** When the server schema changes, an out-of-date Connect
silently misbehaves (drops preset payloads, applies the wrong gain
field, etc.) The user blames the app, not the version mismatch.
**Fix.** Define WS protocol v1:
- Client sends `{"type":"hello","protocol_version":1,…}` on connect.
- Server replies `{"type":"hello_ack","protocol_version":1}` or
  `{"type":"version_mismatch","required":N}` and closes.
- Connect surfaces "ToneForge has been updated. Please update Connect."
  with a "Check for Updates" button (Sparkle).
- Both sides version their message envelope: `{"v":1,"type":…}`.

### F4.4 No tests in ConnectCore [P2f]
**Blocker.** Refactoring AudioEngine is risky because we have no
regression coverage. We will be tempted to ship without verification.
**Fix.** Add `connect/Tests/ConnectCoreTests/` with unit tests for:
- `LatencyProbe` impulse detection (use a synthetic loopback).
- `PresetBridge` message routing (mock URLSessionWebSocketTask).
- `AudioEngine` lifecycle (start/stop/restart, preset apply).
- Configuration-change notification recovery.
Add a GitHub Actions workflow `.github/workflows/connect.yml` that
runs `swift test` on macOS-14 runners on every PR.

---

## Stage 5 — Updating later

### F5.1 No "Check for Updates" path [P2e]
**Blocker.** Even if we ship Sparkle, the user needs a menu item.
**Fix.** Standard Sparkle menu items: "Check for Updates…" calls
`SPUStandardUpdaterController.checkForUpdates(_:)`. Auto-check
weekly by default; opt-out in app settings (later phase).

---

## Out-of-scope for this audit

- **Web-app onboarding** (account creation, payment, etc.) — the user's
  guitarist persona arrives at Connect *after* signing into ToneForge.
- **Asio-style ultra-low-latency tuning** — covered by Priority 3 work
  on the monitor chain bank, not onboarding.
- **Drag-and-drop file imports** — gated behind file-picker
  entitlement (F1.3 reserves it) but the UX work is later.

---

## Summary of new artifacts this priority will produce

| Path                                                | Owner |
|-----------------------------------------------------|-------|
| `connect/Resources/Info.plist`                      | [P2a] |
| `connect/Resources/Connect.entitlements`            | [P2a] |
| `connect/Resources/AppIcon.icns`                    | [P2a] |
| `connect/Sources/Connect/AppDelegate.swift`         | [P2a] |
| `connect/build_release.sh`                          | [P2b] |
| `connect/Sources/ConnectCore/Protocol.swift`        | [P2c] |
| `connect/Sources/ConnectCore/AudioEngine.swift` *   | [P2d] |
| `connect/Sources/ConnectCore/Updater.swift`         | [P2e] |
| `connect/Tests/ConnectCoreTests/*`                  | [P2f] |
| `.github/workflows/connect.yml`                     | [P2f] |

\* existing file; gains config-change handling and `os.Logger` routing.
