// SessionCaptureRecorder.swift
//
// Captures every ContributionEvent that crosses the bus while armed
// (P6). This is the recording half of the D-015 changeover: the
// record UI arms THIS recorder; the legacy LayerRecorder stack is
// frozen read-only (old layers stay listable/replayable/exportable).
//
// Semantics, mirroring LayerRecorder where they overlap:
//   idle → arm() → armed → first event → recording → stop()/cancel()
//   - Subscribes to the bus once at init; the state gate decides
//     whether an event is kept, so arming never races subscription.
//   - SKIPS isReplay events — SessionPlayer re-fires captured events
//     through the same bus, and playback must never re-record itself.
//   - SKIPS negative-timestamp events — the sketch count-in lead bar
//     runs the transport through negative song time with all input
//     suppressed at the executor; capture matches what sounded.
//   - `noteTransportPause()` / `noteTransportSeek(from:to:)` insert
//     `.gap` markers (bookkeeping only — ModeRouter resolves .gap to
//     .none, and the bounce ignores them). Gap `seconds` is the
//     signed song-time jump (0 for a pause).
//   - Autosaves the take every `autosaveInterval` (10 s) and exposes
//     `snapshot()` so lifecycle code (P7 willResignActive) can force
//     one. All saves — autosaves and the final stop() — carry the
//     SAME sessionId fixed at arm time, so they land in one file.
//   - padMapping is snapshotted by the CALLER at arm time (the
//     recorder is engine-side and cannot see PadAssignmentStore).

import Foundation

@MainActor
public final class SessionCaptureRecorder: ObservableObject {

    public enum State: Equatable {
        case idle
        case armed
        case recording
    }

    @Published public private(set) var state: State = .idle
    @Published public private(set) var eventCount: Int = 0

    /// Called with the current take on every autosave tick. Wired by
    /// AppState to SessionStore.save. Also invoked once by stop().
    public var onAutosave: ((SessionCapture) -> Void)?

    /// Seconds between autosaves while armed/recording.
    public nonisolated static let autosaveInterval: TimeInterval = 10

    private let bus: ContributionEventBus
    private let clockNow: () -> Double
    private var busToken: ContributionEventBus.Token?
    private var autosaveTimer: Timer?

    // Snapshot taken at arm time.
    private var sessionId = UUID()
    private var songBackendId: String?
    private var appMode: AppMode = .sample
    private var capturedAt = Date()
    private var tempoBpm: Double?
    private var padMapping: [PadAddress: PadSampleReference] = [:]
    private var buffer: [ContributionEvent] = []

    public init(
        bus: ContributionEventBus,
        clockNow: @escaping () -> Double
    ) {
        self.bus = bus
        self.clockNow = clockNow
        self.busToken = bus.subscribe { [weak self] event in
            self?.ingest(event)
        }
    }

    deinit {
        if let token = busToken {
            // Bus and recorder are both main-actor owned; deinit of a
            // main-actor object runs on the main actor in practice,
            // but assumeIsolated keeps the compiler honest.
            MainActor.assumeIsolated {
                bus.unsubscribe(token)
            }
        }
    }

    // MARK: - Arm / stop / cancel

    /// Begin waiting for the first event. The caller snapshots song
    /// context and the current pad assignments. No-op when not idle.
    public func arm(
        songBackendId: String?,
        appMode: AppMode,
        tempoBpm: Double?,
        padMapping: [PadAddress: PadSampleReference]
    ) {
        guard state == .idle else { return }
        sessionId = UUID()
        self.songBackendId = songBackendId
        self.appMode = appMode
        self.capturedAt = Date()
        self.tempoBpm = tempoBpm
        self.padMapping = padMapping
        buffer.removeAll()
        eventCount = 0
        state = .armed
        startAutosaveTimer()
    }

    /// Freeze the buffer into a SessionCapture (events sorted
    /// ascending by timestamp, stable). Returns nil if nothing was
    /// captured — an armed-but-untouched take produces no session.
    /// Fires one final autosave callback with the finished take.
    public func stop() -> SessionCapture? {
        guard state != .idle else { return nil }
        stopAutosaveTimer()
        defer {
            buffer.removeAll()
            eventCount = 0
            state = .idle
        }
        guard buffer.contains(where: { !$0.kind.isGap }) else { return nil }
        let session = snapshot()
        onAutosave?(session)
        return session
    }

    /// Abandon the take without producing a session.
    public func cancel() {
        stopAutosaveTimer()
        buffer.removeAll()
        eventCount = 0
        state = .idle
    }

    /// The take as it stands right now (events sorted). Used by
    /// autosave and by lifecycle interruption saves.
    public func snapshot() -> SessionCapture {
        SessionCapture(
            sessionId: sessionId,
            songBackendId: songBackendId,
            appMode: appMode,
            capturedAt: capturedAt,
            tempoBpm: tempoBpm,
            events: sortedStable(buffer),
            padMapping: padMapping
        )
    }

    // MARK: - Transport discontinuities

    /// The transport paused mid-take. Song time does not advance
    /// while paused, so timestamps stay consistent — the marker just
    /// records that a real-time break happened here.
    public func noteTransportPause() {
        insertGap(seconds: 0)
    }

    /// The transport jumped (seek/scrub) mid-take. `seconds` is the
    /// signed song-time delta; the marker is stamped at the PRE-seek
    /// position so a backward seek is visible in the stream.
    public func noteTransportSeek(from: Double, to: Double) {
        insertGap(seconds: to - from, at: from)
    }

    private func insertGap(seconds: Double, at time: Double? = nil) {
        guard state == .recording else { return }
        buffer.append(ContributionEvent(
            source: .future("transport"),
            kind: .gap(seconds: seconds),
            timestamp: time ?? clockNow(),
            hostTime: 0
        ))
        eventCount = buffer.count
    }

    // MARK: - Ingest

    private func ingest(_ event: ContributionEvent) {
        guard state != .idle else { return }
        guard !event.isReplay else { return }
        if case .gap = event.kind { return }  // gaps are recorder-made
        // Negative song time exists only in the sketch count-in lead
        // bar, where the executor suppresses the sound. A hit the
        // performer never heard must not enter the take (and must not
        // trip armed → recording).
        guard event.timestamp >= 0 else { return }
        if state == .armed { state = .recording }
        buffer.append(event)
        eventCount = buffer.count
    }

    // MARK: - Autosave

    private func startAutosaveTimer() {
        autosaveTimer?.invalidate()
        let timer = Timer.scheduledTimer(
            withTimeInterval: Self.autosaveInterval, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.autosaveTick()
            }
        }
        timer.tolerance = 1
        autosaveTimer = timer
    }

    private func stopAutosaveTimer() {
        autosaveTimer?.invalidate()
        autosaveTimer = nil
    }

    private func autosaveTick() {
        guard state == .recording, !buffer.isEmpty else { return }
        onAutosave?(snapshot())
    }

    /// Stable ascending sort by timestamp — equal timestamps keep
    /// arrival order so a padDown/padUp pair at the same tick never
    /// swaps.
    private func sortedStable(
        _ events: [ContributionEvent]
    ) -> [ContributionEvent] {
        events.enumerated()
            .sorted {
                ($0.element.timestamp, $0.offset)
                    < ($1.element.timestamp, $1.offset)
            }
            .map(\.element)
    }
}

private extension ContributionEvent.Kind {
    var isGap: Bool {
        if case .gap = self { return true }
        return false
    }
}
