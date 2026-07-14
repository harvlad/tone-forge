// ReplayExecutor.swift
//
// Turns replayed ContributionEvents back into pad sounds. Subscribes
// to the bus and handles ONLY isReplay pad events — live input never
// reaches here (live pads go LaunchpadController → ChopPlayer, and
// SessionController publishes the corresponding events itself).
// Handling only replays also guarantees no feedback loop: this class
// never re-publishes, and the recorder skips isReplay anyway.
//
// Pad resolution happens at fire time against the CURRENT grid
// (desktop captures store an empty padMapping — the grid is
// deterministic from the bundle + chop edits for a songBackendId).
// Unassigned pads no-op, mirroring the bounce's skip semantics.

import Foundation
import ToneForgeEngine

@MainActor
public final class ReplayExecutor {

    /// Current grid lookup (LaunchpadController.assignments).
    public var assignmentProvider: ((LaunchpadPad) -> PadAssignment?)?
    /// Sound a pad (ChopPlayer.trigger). Velocity is the captured
    /// normalized value.
    public var onTrigger: ((PadAssignment, _ velocity: Float) -> Void)?
    /// Stop a pad (ChopPlayer.release).
    public var onRelease: ((PadAssignment) -> Void)?

    private let bus: ContributionEventBus
    private var token: ContributionEventBus.Token?

    public init(bus: ContributionEventBus) {
        self.bus = bus
        self.token = bus.subscribe { [weak self] event in
            self?.handle(event)
        }
    }

    deinit {
        if let token {
            MainActor.assumeIsolated {
                bus.unsubscribe(token)
            }
        }
    }

    private func handle(_ event: ContributionEvent) {
        guard event.isReplay else { return }
        switch event.kind {
        case .padDown(let row, let col):
            guard let assignment = resolve(row: row, col: col) else { return }
            onTrigger?(assignment, Float(min(max(event.velocity, 0), 1)))
        case .padUp(let row, let col):
            guard let assignment = resolve(row: row, col: col) else { return }
            onRelease?(assignment)
        case .midiNote, .gap:
            break
        }
    }

    private func resolve(row: Int, col: Int) -> PadAssignment? {
        guard let pad = PadEventMapping.launchpadPad(row: row, col: col)
        else { return nil }
        return assignmentProvider?(pad)
    }
}
