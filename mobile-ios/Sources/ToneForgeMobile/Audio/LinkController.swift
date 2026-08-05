// LinkController.swift
//
// Ableton Link integration (PERFORM_PARITY spec 2A). Syncs tempo, beat
// phase, and start/stop with other Link apps + Ableton Live on the LAN.
//
// LinkKit dependency: Ableton's SDK (github.com/Ableton/LinkKit) ships
// the `ABLLink` C API as a static library / xcframework. It is NOT
// vendored in this repo — adding it requires accepting Ableton's Link
// license and registering the app for distribution. Until it is added,
// `canImport(ABLLink)` is false and this whole controller is an inert
// stub: `isAvailable == false`, every method a no-op, so the app builds
// and runs exactly as before.
//
// SETUP (when licensing is sorted):
//   1. Add LinkKit as a Swift package / xcframework in project.yml.
//   2. Add `#import <ABLLink/ABLLink.h>` to the bridging header (or use
//      the module map the SDK provides).
//   3. Rebuild — `canImport(ABLLink)` flips true and the real body
//      below activates. Verify the ABLLink signatures against the
//      vendored header; they are transcribed from the public API here
//      and have NOT been compiler-checked in this repo.
//
// Reconciliation model lives in ToneForgeEngine.LinkReconciler (pure,
// unit-tested). This controller only owns the ABLLink session + the
// poll loop that feeds Link's tempo/phase into the transport.

import Foundation
import ToneForgeEngine
#if canImport(ABLLink)
import ABLLink
#endif

@MainActor
public final class LinkController: ObservableObject {

    /// True once a Link session exists (LinkKit vendored + session up).
    @Published public private(set) var isAvailable = false
    /// User-facing enable state (Link menu switch).
    @Published public private(set) var isEnabled = false
    /// Current session tempo, for the UI readout.
    @Published public private(set) var sessionTempo: Double = 120
    /// True when at least one other Link peer is on the session.
    @Published public private(set) var isConnected = false

    /// The song's native tempo — the denominator of the stretch ratio.
    /// Set on song load; nil disables tempo-follow (nothing to stretch).
    public var songBpm: Double?
    /// Bars are assumed 4/4 for the Link quantum unless told otherwise.
    public var beatsPerBar: Int = 4

    // MARK: - Sinks into the transport (wired by AudioEngine/AppState)

    /// Apply a playback-rate multiplier so song content plays at the
    /// Link tempo (stems time-stretch via the existing timePitch path).
    public var applyStretch: ((Double) -> Void)?
    /// Nudge song-time by ±seconds to phase-align the downbeat to Link.
    public var nudgeSeconds: ((Double) -> Void)?
    /// Current song bar phase (0..1), read each poll to compute the nudge.
    public var songBarPhase: (() -> Double?)?
    /// Beat duration in song-seconds, for converting phase → seconds.
    public var beatDuration: (() -> Double?)?

    private var pollTimer: Timer?

    #if canImport(ABLLink)
    private var link: ABLLinkRef?

    public init(initialBpm: Double = 120) {
        link = ABLLinkNew(initialBpm)
        isAvailable = link != nil
        sessionTempo = initialBpm
    }

    deinit {
        if let link { ABLLinkDelete(link) }
    }

    /// Turn Link sync on/off. `ABLLinkSetActive` marks the app as
    /// participating; the user's own Link on/off lives in Ableton's
    /// settings view controller and is read back via `ABLLinkIsEnabled`.
    /// (There is no `ABLLinkEnable` — verified against ABLLink.h.)
    public func setActive(_ active: Bool) {
        guard let link else { return }
        ABLLinkSetActive(link, active)
        isEnabled = ABLLinkIsEnabled(link)
        if active { startPolling() } else { stopPolling(); applyStretch?(1.0) }
    }

    /// Poll Link's session state and reconcile the transport. A poll
    /// loop (not the audio render callback) is enough for tempo/phase
    /// follow at the granularity a jam needs; the sample-accurate
    /// version reads Link inside the render tap — a later refinement.
    private func startPolling() {
        guard pollTimer == nil else { return }
        let timer = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.poll() }
        }
        RunLoop.main.add(timer, forMode: .common)
        pollTimer = timer
    }

    private func stopPolling() {
        pollTimer?.invalidate()
        pollTimer = nil
    }

    private func poll() {
        guard let link else { return }
        let state = ABLLinkCaptureAppSessionState(link)
        defer { /* app session state is read-only here; nothing to commit */ }

        let tempo = ABLLinkGetTempo(state)
        sessionTempo = tempo
        isEnabled = ABLLinkIsEnabled(link)
        isConnected = ABLLinkIsConnected(link)

        // Tempo follow: stretch song content to the Link tempo.
        if let songBpm, let ratio = LinkReconciler.stretchRatio(linkBpm: tempo, songBpm: songBpm) {
            applyStretch?(ratio)
        }

        // Phase align: pull the song bar phase toward Link's.
        let hostTime = mach_absolute_time()
        let quantum = Double(beatsPerBar)
        let linkPhase = ABLLinkPhaseAtTime(state, hostTime, quantum) / quantum // 0..1
        if let songPhase = songBarPhase?(), let beat = beatDuration?(),
           !LinkReconciler.isPhaseLocked(linkBarPhase: linkPhase, songBarPhase: songPhase),
           let nudge = LinkReconciler.phaseNudgeSeconds(
               linkBarPhase: linkPhase, songBarPhase: songPhase,
               beatsPerBar: beatsPerBar, beatDuration: beat) {
            nudgeSeconds?(nudge)
        }
    }

    #else
    // MARK: - No-SDK stub (LinkKit not vendored)

    public init(initialBpm: Double = 120) {
        sessionTempo = initialBpm
    }

    /// No-op until LinkKit is added. Surfaced so callers can wire the UI
    /// now; the toggle simply does nothing while `isAvailable` is false.
    public func setActive(_ active: Bool) { /* LinkKit not available */ }
    #endif
}
