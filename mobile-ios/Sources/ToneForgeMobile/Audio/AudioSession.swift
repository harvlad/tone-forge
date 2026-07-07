// AudioSession.swift
//
// Wraps AVAudioSession configuration for the perform experience:
//   - Category: `.playback` (music keeps playing when the ring/silent
//     switch is on and the screen is locked).
//   - Options: `.mixWithOthers = false` so audio from other apps is
//     ducked when they start jamming. Later we may expose a "let
//     other apps keep playing" toggle in SettingsView; for now the
//     default is exclusive playback.
//   - Mode: `.default`. `.measurement` disables system audio processing
//     which is what we want for a music app.
//
// Also owns interruption + route-change handling. When a phone call
// interrupts, the AudioEngine pauses; when the interruption ends with
// `shouldResume`, we resume. When the user unplugs headphones, we
// pause (matches iOS conventions — nobody wants their pad synth
// blasting the coffee shop).
//
// macOS build is a no-op: AVAudioSession is iOS-only.

import Foundation
#if canImport(AVFoundation)
import AVFoundation
#endif

/// Configuration + lifecycle for the shared AVAudioSession. Owns no
/// audio nodes itself — it's the layer *around* AudioEngine that lets
/// AVAudioEngine survive iOS system events.
@MainActor
public final class AudioSessionController: ObservableObject {

    /// Emitted when the OS interrupts us (phone call, alarm, Siri, …)
    /// and when the interruption ends. Consumers pause/resume audio in
    /// response.
    public enum Event: Sendable, Equatable {
        case interruptionBegan
        case interruptionEndedShouldResume
        case interruptionEndedNoResume
        case routeChanged(reason: RouteChangeReason)
    }

    /// Subset of `AVAudioSession.RouteChangeReason` we actually care
    /// about. Coalesced to a small enum so consumers don't need to
    /// import AVFoundation.
    public enum RouteChangeReason: Sendable, Equatable {
        case oldDeviceUnavailable   // headphones unplugged, BT disconnected
        case newDeviceAvailable     // headphones plugged in
        case categoryChange
        case override
        case unknown
    }

    /// Async stream of session events. The audio engine subscribes.
    public var events: AsyncStream<Event> { eventsStream }

    private var eventsStream: AsyncStream<Event>!
    private var eventsContinuation: AsyncStream<Event>.Continuation!

    #if os(iOS)
    private var interruptionObserver: NSObjectProtocol?
    private var routeObserver: NSObjectProtocol?
    #endif

    public init() {
        var continuation: AsyncStream<Event>.Continuation!
        let stream = AsyncStream<Event> { c in continuation = c }
        self.eventsStream = stream
        self.eventsContinuation = continuation
    }

    /// Configure category + mode and activate. Idempotent — safe to
    /// call multiple times.
    public func activate() {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playback,
                mode: .default,
                options: []   // .mixWithOthers deliberately omitted
            )
            // D-017: request the canonical 48 kHz rate so the engine's
            // mainMixer→output hop is (usually) SRC-free. Best-effort —
            // the OS may pin a different hardware rate (e.g. 44.1 kHz
            // Bluetooth); the contribution graph stays at 48 k either
            // way and the output boundary converts.
            try? session.setPreferredSampleRate(AudioEngine.canonicalSampleRate)
            try session.setActive(true, options: [])
        } catch {
            // Session activation failure is non-fatal here — the engine
            // will still start; the user just won't get lock-screen
            // playback. Print for developer visibility.
            print("[AudioSession] activate failed: \(error)")
            return
        }
        subscribeToNotifications()
        #endif
    }

    /// Switch to `.playAndRecord` for a mic capture (P3). Options:
    ///   - `.defaultToSpeaker`: without it, playAndRecord routes to
    ///     the earpiece — useless for a jam session.
    ///   - `.allowBluetoothA2DP`: keep AirPods usable for OUTPUT
    ///     (input stays on the built-in mic — HFP input would drop
    ///     the whole session to 16 kHz phone quality).
    /// Requests a 5 ms IO buffer for tight capture. Callers pair this
    /// with `revertToPlayback()` when the recording flow ends.
    public func activateForRecording() {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(
                .playAndRecord,
                mode: .default,
                options: [.defaultToSpeaker, .allowBluetoothA2DP]
            )
            try? session.setPreferredSampleRate(AudioEngine.canonicalSampleRate)
            try? session.setPreferredIOBufferDuration(0.005)
            try session.setActive(true, options: [])
        } catch {
            print("[AudioSession] activateForRecording failed: \(error)")
            return
        }
        subscribeToNotifications()
        #endif
    }

    /// Back to playback-only after a recording flow. `activate()` is
    /// idempotent and resets category/options, so it IS the revert.
    public func revertToPlayback() {
        activate()
    }

    /// True when the current output route includes the built-in
    /// speaker — mic capture will hear the app's own playback
    /// (feedback risk; recording UI shows a warning + no monitoring).
    public var isOutputBuiltInSpeaker: Bool {
        #if os(iOS)
        return AVAudioSession.sharedInstance().currentRoute.outputs
            .contains { $0.portType == .builtInSpeaker }
        #else
        return false
        #endif
    }

    /// True when audio is routed over Bluetooth — capture timing gets
    /// ~40 ms sloppier; the recording UI surfaces a note.
    public var isRouteBluetooth: Bool {
        #if os(iOS)
        let bt: [AVAudioSession.Port] = [.bluetoothA2DP, .bluetoothHFP, .bluetoothLE]
        let route = AVAudioSession.sharedInstance().currentRoute
        return (route.outputs + route.inputs).contains { bt.contains($0.portType) }
        #else
        return false
        #endif
    }

    /// Tear down observers. Called from the scene's disappear hook, if
    /// we ever have one; safe to call from deinit even though it's a
    /// no-op on macOS.
    public func deactivate() {
        #if os(iOS)
        if let obs = interruptionObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        if let obs = routeObserver {
            NotificationCenter.default.removeObserver(obs)
        }
        interruptionObserver = nil
        routeObserver = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
        #endif
    }

    // MARK: - Sample rate + IO buffer duration accessors

    /// The negotiated hardware sample rate. Callers wire this into
    /// `AVAudioFormat` for their engines.
    public var sampleRate: Double {
        #if os(iOS)
        return AVAudioSession.sharedInstance().sampleRate
        #else
        return 48000
        #endif
    }

    /// Requested I/O buffer duration in seconds. Matches what the OS
    /// actually chose, not our preferred value.
    public var ioBufferDuration: Double {
        #if os(iOS)
        return AVAudioSession.sharedInstance().ioBufferDuration
        #else
        return 512.0 / 48000.0
        #endif
    }

    /// Request a lower buffer size for tighter touch-to-audio latency.
    /// The system may ignore this — always read ``ioBufferDuration``
    /// after to see what actually landed.
    public func preferLowLatency(bufferFrames: Int = 256) {
        #if os(iOS)
        let session = AVAudioSession.sharedInstance()
        let duration = Double(bufferFrames) / session.sampleRate
        try? session.setPreferredIOBufferDuration(duration)
        #endif
    }

    // MARK: - Private

    #if os(iOS)
    private func subscribeToNotifications() {
        guard interruptionObserver == nil else { return }
        let center = NotificationCenter.default
        let session = AVAudioSession.sharedInstance()

        interruptionObserver = center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: session,
            queue: .main
        ) { [weak self] note in
            self?.handleInterruption(note)
        }
        routeObserver = center.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: session,
            queue: .main
        ) { [weak self] note in
            self?.handleRouteChange(note)
        }
    }

    private func handleInterruption(_ note: Notification) {
        guard
            let info = note.userInfo,
            let raw = info[AVAudioSessionInterruptionTypeKey] as? UInt,
            let type = AVAudioSession.InterruptionType(rawValue: raw)
        else { return }

        switch type {
        case .began:
            eventsContinuation.yield(.interruptionBegan)
        case .ended:
            let optionsRaw = (info[AVAudioSessionInterruptionOptionKey] as? UInt) ?? 0
            let options = AVAudioSession.InterruptionOptions(rawValue: optionsRaw)
            if options.contains(.shouldResume) {
                eventsContinuation.yield(.interruptionEndedShouldResume)
            } else {
                eventsContinuation.yield(.interruptionEndedNoResume)
            }
        @unknown default:
            break
        }
    }

    private func handleRouteChange(_ note: Notification) {
        guard
            let info = note.userInfo,
            let raw = info[AVAudioSessionRouteChangeReasonKey] as? UInt,
            let reason = AVAudioSession.RouteChangeReason(rawValue: raw)
        else { return }

        let mapped: RouteChangeReason
        switch reason {
        case .oldDeviceUnavailable: mapped = .oldDeviceUnavailable
        case .newDeviceAvailable:   mapped = .newDeviceAvailable
        case .categoryChange:       mapped = .categoryChange
        case .override:             mapped = .override
        default:                    mapped = .unknown
        }
        eventsContinuation.yield(.routeChanged(reason: mapped))
    }
    #endif
}
