// SessionPlayer.swift
//
// Replays a SessionCapture by re-firing its events through the
// ContributionEventBus with `isReplay: true` (P6). Because replay
// goes through the SAME bus → ModeRouter → executor path as live
// input, playback sounds exactly like the take did — and recorders
// on the bus skip isReplay events so playback never re-records
// itself.
//
// The pad-mapping overlay (restoring the take's pad → sample
// bindings for the duration of playback) is the mobile layer's job —
// ModeCoordinator applies `session.padMapping` as transient local
// assignments around start/stop. The player itself only re-fires
// events.
//
// Timing mirrors LayerPlayer: a 60 Hz Timer pump fires every event
// whose timestamp is ≤ the transport's current song-seconds. Seek
// recomputes the cursor without retro-firing skipped events. `.gap`
// markers are bookkeeping and are never re-published.

import Foundation

@MainActor
public final class SessionPlayer: ObservableObject {

    @Published public private(set) var isPlaying: Bool = false
    /// The session currently loaded for replay (nil when cleared).
    @Published public private(set) var session: SessionCapture?

    private let bus: ContributionEventBus
    private let clockNow: () -> Double
    private var events: [ContributionEvent] = []
    private var cursor: Int = 0
    private var timer: Timer?

    public init(
        bus: ContributionEventBus,
        clockNow: @escaping () -> Double
    ) {
        self.bus = bus
        self.clockNow = clockNow
    }

    deinit {
        timer?.invalidate()
    }

    // MARK: - Load / clear

    /// Load a session for replay. Events are assumed sorted (the
    /// recorder sorts on stop); the cursor starts at the first event
    /// at or after the current transport position.
    public func load(_ session: SessionCapture) {
        self.session = session
        self.events = session.events
        seek(to: clockNow())
    }

    /// Drop the loaded session and stop the pump.
    public func clear() {
        stop()
        session = nil
        events = []
        cursor = 0
    }

    // MARK: - Transport

    public func start() {
        guard session != nil, !isPlaying else { return }
        isPlaying = true
        let t = Timer.scheduledTimer(
            withTimeInterval: 1.0 / 60.0, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.tick()
            }
        }
        timer = t
    }

    public func stop() {
        timer?.invalidate()
        timer = nil
        isPlaying = false
    }

    /// Move the cursor to the first event at or after `songTime`.
    /// Never retro-fires events that were skipped over.
    public func seek(to songTime: Double) {
        cursor = events.firstIndex { $0.timestamp >= songTime }
            ?? events.count
    }

    // MARK: - Pump

    /// Manual tick for tests (no Timer in unit tests).
    public func tickForTests() { tick() }

    private func tick() {
        let now = clockNow()
        while cursor < events.count, events[cursor].timestamp <= now {
            var event = events[cursor]
            cursor += 1
            if case .gap = event.kind { continue }
            event.isReplay = true
            bus.publish(event)
        }
    }
}
