// LiveBeatGuidedSession.swift
//
// Platform-independent driver for guided ("tap-along") Live Beat
// calibration. Runs a VISUAL metronome (no audio, so nothing bleeds into
// the mic), records the mic continuously per role via glue callbacks, and
// hands each take to `LiveBeatCalibrationEngine.ingestGuidedTake` for
// deterministic segmentation. The whole flow lives here so iOS and macOS
// share it; the platform provides only tap control (begin/end raw capture
// + sample rate).
//
// Per role: a short visual count-in, then `beatsPerRole` recorded beats.
// The user taps each beat; segmentation windows the take by those known
// beat times, so calibration never depends on the flaky live onset
// detector.

import Foundation

@MainActor
public final class LiveBeatGuidedSession: ObservableObject {

    /// The calibration engine this drives (build templates + profile).
    public let engine = LiveBeatCalibrationEngine()

    // MARK: - Published UI state

    /// Role currently being taught (nil when idle or complete).
    @Published public private(set) var currentRole: DrumRole?

    /// Current beat marker. Negative during the count-in, `0..<beatsPerRole`
    /// during recorded beats. Drives the UI pulse.
    @Published public private(set) var beat: Int = 0

    /// Total recorded beats per role (for the UI progress dots).
    @Published public private(set) var beatsPerRole: Int = 0

    /// True while a guided session is running.
    @Published public private(set) var isRunning = false

    // MARK: - Configuration

    /// Metronome tempo. 100 BPM = 0.6 s/beat: slow enough to tap cleanly,
    /// fast enough that four roles don't drag.
    public var bpm: Double = 100

    /// Silent visual count-in beats before recording starts (lets the user
    /// find the tempo). Not recorded as taps.
    public var countInBeats: Int = 2

    // MARK: - Glue callbacks (platform tap control)

    /// Arm the platform tap's continuous raw capture.
    public var onBeginCapture: (() -> Void)?
    /// Stop capture and return the accumulated mono take.
    public var onEndCapture: (() -> [Float])?
    /// Current capture sample rate.
    public var sampleRate: (() -> Double)?

    private var task: Task<Void, Never>?

    public init() {}

    // MARK: - Public API

    /// Start a guided calibration session building `profileName`.
    public func start(profileName: String) {
        cancel()
        beatsPerRole = engine.minHitsPerRole + 3
        engine.start(profileName: profileName)
        isRunning = true
        task = Task { await self.runRoles() }
    }

    /// Abort the session (no profile saved).
    public func cancel() {
        task?.cancel()
        task = nil
        isRunning = false
        currentRole = nil
        beat = 0
        _ = onEndCapture?()  // drop any in-flight take
        engine.reset()
    }

    /// The built profile (valid once `engine.step == .complete`).
    public func finalize() -> LiveBeatProfile? { engine.finalize() }

    // MARK: - Private

    private var beatInterval: Double { 60.0 / max(1, bpm) }

    private func runRoles() async {
        while !Task.isCancelled {
            // Read the role the engine is waiting on; stop when finished.
            guard case let .waitingForHits(role, _, _) = engine.step else { break }
            currentRole = role

            let take = await recordRole()
            if Task.isCancelled { return }

            let rate = sampleRate?() ?? 48_000
            let times = expectedTapTimes()
            engine.ingestGuidedTake(
                samples: take, sampleRate: rate, expectedTimes: times
            )

            // A failed role (too few taps) stops the run so the UI can show
            // the error and let the user retry.
            if case .failed = engine.step { break }
        }
        isRunning = false
        currentRole = nil
    }

    /// One role: count-in, then `beatsPerRole` recorded beats. Returns the
    /// captured take.
    private func recordRole() async -> [Float] {
        let nanos = UInt64(beatInterval * 1_000_000_000)

        // Silent count-in (negative beat markers for the UI).
        for c in stride(from: countInBeats, through: 1, by: -1) {
            beat = -c
            try? await Task.sleep(nanoseconds: nanos)
            if Task.isCancelled { return [] }
        }

        onBeginCapture?()

        // Recorded beats. `beat` drives the pulse; the tap happens ~reaction
        // time after each pulse and is windowed by `expectedTapTimes`.
        for k in 0..<beatsPerRole {
            beat = k
            try? await Task.sleep(nanoseconds: nanos)
            if Task.isCancelled { _ = onEndCapture?(); return [] }
        }

        // A final beat-length tail so the last tap's body is captured.
        try? await Task.sleep(nanoseconds: nanos)

        return onEndCapture?() ?? []
    }

    /// Beat times (seconds from capture start) the user tapped to. Capture
    /// begins at recorded beat 0, so beat k lands at `k * interval`.
    private func expectedTapTimes() -> [Double] {
        (0..<beatsPerRole).map { Double($0) * beatInterval }
    }
}
