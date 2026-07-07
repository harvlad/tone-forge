// ContributionEvent.swift
//
// The single event type that flows through the contribution engine.
// EVERY input source — on-screen touch grid, Launchpad Pro MK3, future
// Push / MIDI-keyboard / plugin surfaces — converts its native input
// into a ContributionEvent and publishes it on the ContributionEventBus.
// Nothing else may reach the audio executors (the one documented
// exception is legacy `LayerPlayer.triggerRaw` replay, D-015).
//
// The schema is Codable from day one because SessionCapture (P6)
// serialises event arrays verbatim into session JSON. The wire shape
// is FROZEN as of P1 — see ContributionEventCodableTests' frozen-JSON
// fixtures. Additive evolution only: new Kind/Source cases get new
// `type` strings; decoders must keep accepting every v1 string.
//
// Timestamp domains:
//   `timestamp` — song-seconds from TransportClock. What SessionCapture
//     stores and SessionPlayer replays against.
//   `hostTime`  — mach_absolute_time ticks stamped by the PRODUCER
//     before any main-actor hop (CoreMIDI thread, UITouch handler).
//     This is the LatencyProbe's input: (render hostTime − event
//     hostTime) = true input→attack latency including the hop.
//
// Pad coordinates use the PadIndex convention (PadTypes.swift):
// row 1..8 with row 1 = BOTTOM, col 1..8 left→right. Adapters convert
// from their native convention at the edge (e.g. LaunchpadPad's
// row 0 = top: `row = 8 - pad.row, col = pad.col + 1`).

import Foundation

public struct ContributionEvent: Sendable, Codable, Equatable {

    /// Which surface produced the event. `future(name)` lets a not-
    /// yet-shipped adapter round-trip through session JSON without a
    /// schema bump.
    public enum Source: Sendable, Equatable {
        case touch
        case launchpad
        case push
        case midiKeyboard
        case plugin
        case future(String)
    }

    /// What happened.
    public enum Kind: Sendable, Equatable {
        /// Grid pad pressed. PadIndex convention: row/col 1..8,
        /// row 1 = bottom.
        case padDown(row: Int, col: Int)
        /// Grid pad released.
        case padUp(row: Int, col: Int)
        /// Raw MIDI note (keyboard adapter, future). `on: false`
        /// covers both Note Off and running-status velocity-0.
        case midiNote(note: Int, velocity: Int, on: Bool)
        /// Recorder-inserted silence marker (transport pause/seek
        /// during capture, P6). Never produced by input adapters;
        /// ModeRouter resolves it to `.none`.
        case gap(seconds: Double)
    }

    public let source: Source
    public let kind: Kind
    /// Song-seconds from TransportClock at the moment of input.
    public let timestamp: Double
    /// mach_absolute_time ticks, stamped by the producer BEFORE any
    /// main-actor hop. 0 when unavailable (decoded legacy sessions).
    public let hostTime: UInt64
    /// Normalised 0…1. Touch surfaces send 1.0; Launchpad maps
    /// MIDI velocity / 127.
    public let velocity: Double
    /// True when SessionPlayer re-fires a captured event. Recorders
    /// skip replay events so playback never re-records itself.
    public var isReplay: Bool

    public init(
        source: Source,
        kind: Kind,
        timestamp: Double,
        hostTime: UInt64,
        velocity: Double = 1.0,
        isReplay: Bool = false
    ) {
        self.source = source
        self.kind = kind
        self.timestamp = timestamp
        self.hostTime = hostTime
        self.velocity = velocity
        self.isReplay = isReplay
    }
}

// MARK: - Codable (frozen v1 wire shape)

extension ContributionEvent.Source: Codable {
    private enum CodingKeys: String, CodingKey { case type, name }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "touch":        self = .touch
        case "launchpad":    self = .launchpad
        case "push":         self = .push
        case "midiKeyboard": self = .midiKeyboard
        case "plugin":       self = .plugin
        case "future":
            self = .future(try c.decode(String.self, forKey: .name))
        default:
            // Forward compatibility: an unknown source string from a
            // newer app version degrades to `.future(type)` rather
            // than failing the whole session decode.
            self = .future(type)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .touch:        try c.encode("touch", forKey: .type)
        case .launchpad:    try c.encode("launchpad", forKey: .type)
        case .push:         try c.encode("push", forKey: .type)
        case .midiKeyboard: try c.encode("midiKeyboard", forKey: .type)
        case .plugin:       try c.encode("plugin", forKey: .type)
        case .future(let name):
            try c.encode("future", forKey: .type)
            try c.encode(name, forKey: .name)
        }
    }
}

extension ContributionEvent.Kind: Codable {
    private enum CodingKeys: String, CodingKey {
        case type, row, col, note, velocity, on, seconds
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "padDown":
            self = .padDown(
                row: try c.decode(Int.self, forKey: .row),
                col: try c.decode(Int.self, forKey: .col)
            )
        case "padUp":
            self = .padUp(
                row: try c.decode(Int.self, forKey: .row),
                col: try c.decode(Int.self, forKey: .col)
            )
        case "midiNote":
            self = .midiNote(
                note: try c.decode(Int.self, forKey: .note),
                velocity: try c.decode(Int.self, forKey: .velocity),
                on: try c.decode(Bool.self, forKey: .on)
            )
        case "gap":
            self = .gap(seconds: try c.decode(Double.self, forKey: .seconds))
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: c,
                debugDescription: "Unknown ContributionEvent.Kind type '\(type)'"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .padDown(let row, let col):
            try c.encode("padDown", forKey: .type)
            try c.encode(row, forKey: .row)
            try c.encode(col, forKey: .col)
        case .padUp(let row, let col):
            try c.encode("padUp", forKey: .type)
            try c.encode(row, forKey: .row)
            try c.encode(col, forKey: .col)
        case .midiNote(let note, let velocity, let on):
            try c.encode("midiNote", forKey: .type)
            try c.encode(note, forKey: .note)
            try c.encode(velocity, forKey: .velocity)
            try c.encode(on, forKey: .on)
        case .gap(let seconds):
            try c.encode("gap", forKey: .type)
            try c.encode(seconds, forKey: .seconds)
        }
    }
}

extension ContributionEvent {
    private enum CodingKeys: String, CodingKey {
        case source, kind, timestamp, hostTime, velocity, isReplay
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.source = try c.decode(Source.self, forKey: .source)
        self.kind = try c.decode(Kind.self, forKey: .kind)
        self.timestamp = try c.decode(Double.self, forKey: .timestamp)
        // hostTime is process-local mach ticks — meaningless after
        // decode, but preserved for fixture stability. Older/foreign
        // payloads may omit it.
        self.hostTime = try c.decodeIfPresent(UInt64.self, forKey: .hostTime) ?? 0
        self.velocity = try c.decodeIfPresent(Double.self, forKey: .velocity) ?? 1.0
        self.isReplay = try c.decodeIfPresent(Bool.self, forKey: .isReplay) ?? false
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(source, forKey: .source)
        try c.encode(kind, forKey: .kind)
        try c.encode(timestamp, forKey: .timestamp)
        try c.encode(hostTime, forKey: .hostTime)
        try c.encode(velocity, forKey: .velocity)
        try c.encode(isReplay, forKey: .isReplay)
    }
}
