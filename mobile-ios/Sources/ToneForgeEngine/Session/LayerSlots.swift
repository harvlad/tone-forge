// LayerSlots.swift
//
// Layer A/B slot management (D-022 Phase 7). Two recording slots —
// takes can be assigned to A or B; both replay together when the
// transport runs. Re-recording replaces the active slot's pointer;
// old takes stay on the shelf.
//
// The slots are keyed per analysisId (per-song) or the sketch
// sentinel. UI toggles between A and B; stopping a take assigns
// the capture to the active slot. Both slot players pump the bus
// with `isReplay = true` so they never re-record themselves.

import Foundation

/// The two layer slots. RawRepresentable for Codable storage.
public enum RecordingSlot: String, Codable, Sendable, CaseIterable {
    case a = "A"
    case b = "B"

    /// The other slot.
    public var toggled: RecordingSlot {
        switch self {
        case .a: return .b
        case .b: return .a
        }
    }
}

/// Pure value tracking which slot is active and which takes are
/// assigned per slot. The takes dictionary maps slot → sessionId.
/// Keyed per analysisId in AppState (each song has its own A/B
/// state); the sketch grid uses the sentinel analysisId.
public struct LayerSlots: Sendable, Equatable, Codable {
    /// The currently active slot for recording. New takes land here.
    public var active: RecordingSlot
    /// Session IDs assigned to each slot. nil = empty slot.
    public var takes: [RecordingSlot: UUID]

    public init(
        active: RecordingSlot = .a,
        takes: [RecordingSlot: UUID] = [:]
    ) {
        self.active = active
        self.takes = takes
    }

    /// Toggle the active slot. Returns the new active slot.
    public mutating func toggleActive() -> RecordingSlot {
        active = active.toggled
        return active
    }

    /// Assign a take to a slot. Re-assigning replaces the old pointer.
    public mutating func assign(sessionId: UUID, to slot: RecordingSlot) {
        takes[slot] = sessionId
    }

    /// Clear a slot (e.g. when its take is deleted).
    public mutating func clear(slot: RecordingSlot) {
        takes.removeValue(forKey: slot)
    }

    /// True if the slot has a take assigned.
    public func hasTake(_ slot: RecordingSlot) -> Bool {
        takes[slot] != nil
    }

    /// True if at least one slot has a take.
    public var hasAnyTake: Bool {
        !takes.isEmpty
    }

    /// The take for a given slot, if any.
    public func take(for slot: RecordingSlot) -> UUID? {
        takes[slot]
    }
}
