// SessionCapture.swift
//
// The persisted shape of one recorded contribution session (P6):
// every ContributionEvent that reached the bus while armed, plus a
// snapshot of the pad → sample mapping taken at arm time so replay
// and bounce reproduce what the performer actually heard even after
// pads are reassigned.
//
// Wire shape is FROZEN at schemaVersion 1 (frozen-JSON fixture in
// SessionCaptureCodableTests):
//   - `capturedAt` encodes as `capturedAtEpoch` (Unix seconds Double,
//     same convention as LayerTimeline.createdAtEpoch).
//   - `padMapping` encodes as a pair ARRAY sorted by (mode, pad) —
//     PadAddress is not a String, and dictionary order would make
//     encoded sessions non-deterministic.
//   - Optionals decode with decodeIfPresent so newer readers accept
//     older files.
//
// Sessions hold NO audio — only event timings and references
// (pack pads by id, local samples by UUID). Keeping a session after
// its song's analysis is deleted is therefore compliant; the bounce
// simply skips references it can no longer resolve.

import Foundation

/// One pad slot in one mode — the key of the session's pad-mapping
/// snapshot. `mode` matters because the same PadIndex means different
/// things per mode (hybrid rows 1–4 are synth, not samples).
public struct PadAddress: Hashable, Sendable {
    public var mode: AppMode
    public var pad: PadIndex

    public init(mode: AppMode, pad: PadIndex) {
        self.mode = mode
        self.pad = pad
    }
}

extension PadAddress: Codable {
    private enum CodingKeys: String, CodingKey { case mode, pad }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.mode = try c.decode(AppMode.self, forKey: .mode)
        self.pad = PadIndex(try c.decode(Int.self, forKey: .pad))
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(mode, forKey: .mode)
        try c.encode(pad.rawValue, forKey: .pad)
    }
}

/// A complete captured session. Value type — the recorder assembles
/// one on `stop()`, SessionStore persists it, SessionPlayer replays
/// it, SessionBounceRenderer renders it offline.
public struct SessionCapture: Sendable, Equatable {
    /// Wire version. 1 = initial P6 shape.
    public var schemaVersion: Int
    /// Stable identity; fixed at arm time so autosaves and the final
    /// save land under the same file.
    public var sessionId: UUID
    /// Backend analysisId of the song playing under the take, or nil
    /// for a song-less take on the synthetic tempo grid.
    public var songBackendId: String?
    /// The AppMode that was active at arm time (drives ModeRouter
    /// resolution on replay/bounce).
    public var appMode: AppMode
    /// Wall-clock arm time.
    public var capturedAt: Date
    /// Synthetic-grid tempo for song-less takes (stutter/gate/bounce
    /// timing); nil when a song's own timeline provides tempo.
    public var tempoBpm: Double?
    /// Everything captured, sorted ascending by `timestamp` once the
    /// recorder stops. Includes recorder-inserted `.gap` markers.
    public var events: [ContributionEvent]
    /// Pad → sample snapshot at arm time.
    public var padMapping: [PadAddress: PadSampleReference]
    /// Layer A/B slot label (D-022 Phase 7). "A" or "B" for takes
    /// assigned to a slot; nil for older takes captured before this
    /// field existed. decodeIfPresent + encodeIfPresent for back-compat.
    public var slotLabel: String?

    public init(
        schemaVersion: Int = 1,
        sessionId: UUID,
        songBackendId: String?,
        appMode: AppMode,
        capturedAt: Date,
        tempoBpm: Double?,
        events: [ContributionEvent],
        padMapping: [PadAddress: PadSampleReference],
        slotLabel: String? = nil
    ) {
        self.schemaVersion = schemaVersion
        self.sessionId = sessionId
        self.songBackendId = songBackendId
        self.appMode = appMode
        self.capturedAt = capturedAt
        self.tempoBpm = tempoBpm
        self.events = events
        self.padMapping = padMapping
        self.slotLabel = slotLabel
    }

    /// Song-seconds of the last captured event (0 for an empty take).
    /// The bounce adds its own reverb tail beyond this.
    public var durationSec: Double {
        events.map(\.timestamp).max() ?? 0
    }
}

// MARK: - Codable (frozen v1 wire shape)

extension SessionCapture: Codable {
    private enum CodingKeys: String, CodingKey {
        case schemaVersion, sessionId, songBackendId, appMode
        case capturedAtEpoch, tempoBpm, events, padMapping, slotLabel
    }

    /// One padMapping pair on the wire.
    private struct MappingEntry: Codable {
        let address: PadAddress
        let ref: PadSampleReference
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        self.sessionId = try c.decode(UUID.self, forKey: .sessionId)
        self.songBackendId = try c.decodeIfPresent(
            String.self, forKey: .songBackendId)
        self.appMode = try c.decode(AppMode.self, forKey: .appMode)
        let epoch = try c.decode(Double.self, forKey: .capturedAtEpoch)
        self.capturedAt = Date(timeIntervalSince1970: epoch)
        self.tempoBpm = try c.decodeIfPresent(Double.self, forKey: .tempoBpm)
        self.events = try c.decode([ContributionEvent].self, forKey: .events)
        let entries = try c.decodeIfPresent(
            [MappingEntry].self, forKey: .padMapping) ?? []
        var mapping: [PadAddress: PadSampleReference] = [:]
        for entry in entries { mapping[entry.address] = entry.ref }
        self.padMapping = mapping
        self.slotLabel = try c.decodeIfPresent(String.self, forKey: .slotLabel)
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(sessionId, forKey: .sessionId)
        try c.encodeIfPresent(songBackendId, forKey: .songBackendId)
        try c.encode(appMode, forKey: .appMode)
        try c.encode(
            capturedAt.timeIntervalSince1970, forKey: .capturedAtEpoch)
        try c.encodeIfPresent(tempoBpm, forKey: .tempoBpm)
        try c.encode(events, forKey: .events)
        let entries = padMapping
            .map { MappingEntry(address: $0.key, ref: $0.value) }
            .sorted {
                ($0.address.mode.rawValue, $0.address.pad.rawValue)
                    < ($1.address.mode.rawValue, $1.address.pad.rawValue)
            }
        try c.encode(entries, forKey: .padMapping)
        try c.encodeIfPresent(slotLabel, forKey: .slotLabel)
    }
}
