//
// SessionStore.swift
//
// Audio-Ownership Pivot, post-pivot follow-up: the in-Connect
// consumer of the v2 `session_data` and `transport_state` frames.
//
// Phase 4 wired the inbound side: PresetBridge.dispatch decodes
// those frame types and fires `onSessionData([String: Any])` and
// `onTransportState(playing, positionSec)` callbacks. Those
// callbacks were no-ops at the end of Phase 4 — the plan called
// the SessionStore singleton a "future commit". This file is
// that commit.
//
// SessionStore is pure data + decoding. No AVFoundation, no UI.
// It exists so a future in-Connect chord HUD / tuner / countdown
// can subscribe to a single typed source of truth instead of
// re-parsing wire dicts at every UI tick.
//
// Threading: SessionStore protects its state with an internal
// serial queue (`stateQueue`). Reads and writes are synchronous
// against that queue. PresetBridge dispatches its inbound
// callbacks on the main queue, so the AppKit / SwiftUI side can
// safely call SessionStore methods from there; `onChange` fires
// on the main queue regardless of which thread caused the
// ingest, so UI redraw code never has to hop.
//
// Wire shapes (mirrors backend/static/jam.js v2 producer and the
// plan in docs):
//
//   session_data:
//     { "type": "session_data", "v": 2,
//       "session_id":        "<id>",
//       "song":              {"id": "<id>", "title": "<str>"},     // nullable
//       "bpm":               120.0,                                 // nullable
//       "key":               {"root": 0, "scale": "Major"},         // nullable
//       "chord_progression": [{"symbol": "E:maj", "start_s": 0.0, "end_s": 4.0}], // nullable
//       "section_markers":   [{"name": "verse", "start_s": 0.0, "end_s": 30.0}],  // nullable
//       "loop_markers":      {"start_s": 0.0, "end_s": 30.0}        // nullable
//     }
//
//   transport_state:
//     { "type": "transport_state", "v": 2,
//       "playing":    true,
//       "position_s": 12.34 }
//
// Decoding policy: missing or wrongly-typed top-level fields are
// treated as "field not present" and leave the previous value
// untouched IF this is a partial update. session_data IS a full
// snapshot from JAM's perspective — JAM rebuilds it from
// `state.beatTimes`/`songKey`/`rawChords` every push — so we
// treat a missing field as "absent now" and clear the cached
// value. transport_state is always a complete pair; we replace
// wholesale.
//

import Foundation

public final class SessionStore {

    // MARK: - Public types

    public struct SongInfo: Equatable {
        public let id: String
        public let title: String
        public init(id: String, title: String) {
            self.id = id
            self.title = title
        }
    }

    public struct KeyInfo: Equatable {
        /// Pitch class 0..11, C=0.
        public let root: Int
        /// Free-form scale label ("Major", "Minor", "Dorian", ...).
        /// Validation lives in JAM; Connect just displays.
        public let scale: String
        public init(root: Int, scale: String) {
            self.root = root
            self.scale = scale
        }
    }

    public struct ChordEvent: Equatable {
        public let symbol: String
        public let startSec: Double
        public let endSec: Double
        public init(symbol: String, startSec: Double, endSec: Double) {
            self.symbol = symbol
            self.startSec = startSec
            self.endSec = endSec
        }
        /// Half-open interval: `[startSec, endSec)`. Avoids
        /// double-counting when chord boundaries touch exactly.
        public func contains(_ sec: Double) -> Bool {
            return sec >= startSec && sec < endSec
        }
    }

    public struct SectionMarker: Equatable {
        public let name: String
        public let startSec: Double
        public let endSec: Double
        public init(name: String, startSec: Double, endSec: Double) {
            self.name = name
            self.startSec = startSec
            self.endSec = endSec
        }
        public func contains(_ sec: Double) -> Bool {
            return sec >= startSec && sec < endSec
        }
    }

    public struct LoopMarkers: Equatable {
        public let startSec: Double
        public let endSec: Double
        public init(startSec: Double, endSec: Double) {
            self.startSec = startSec
            self.endSec = endSec
        }
    }

    public struct Transport: Equatable {
        public let isPlaying: Bool
        public let positionSec: Double
        public init(isPlaying: Bool, positionSec: Double) {
            self.isPlaying = isPlaying
            self.positionSec = positionSec
        }
        public static let stopped = Transport(isPlaying: false, positionSec: 0.0)
    }

    // MARK: - Singleton

    /// Process-wide store. AppDelegate / ConnectMain wires
    /// `bridge.onSessionData` and `bridge.onTransportState` into
    /// this instance; future HUD code reads from
    /// `SessionStore.shared`.
    public static let shared = SessionStore()

    // MARK: - Public state

    // Backing storage. All access goes through `stateQueue` so
    // concurrent readers/writers from different threads stay
    // consistent. Private — public accessors below hop the queue.
    private var _sessionId: String?
    private var _song: SongInfo?
    private var _bpm: Double?
    private var _key: KeyInfo?
    private var _chordProgression: [ChordEvent] = []
    private var _sectionMarkers: [SectionMarker] = []
    private var _loopMarkers: LoopMarkers?
    private var _transport: Transport = .stopped
    private let stateQueue = DispatchQueue(label: "com.toneforge.connect.session-store")

    public var sessionId: String?              { stateQueue.sync { _sessionId } }
    public var song: SongInfo?                 { stateQueue.sync { _song } }
    public var bpm: Double?                    { stateQueue.sync { _bpm } }
    public var key: KeyInfo?                   { stateQueue.sync { _key } }
    public var chordProgression: [ChordEvent]  { stateQueue.sync { _chordProgression } }
    public var sectionMarkers: [SectionMarker] { stateQueue.sync { _sectionMarkers } }
    public var loopMarkers: LoopMarkers?       { stateQueue.sync { _loopMarkers } }
    public var transport: Transport            { stateQueue.sync { _transport } }

    /// Fired on the main queue after every successful ingest. UI
    /// code should treat this as a "redraw" signal; the store does
    /// not diff. Default is nil — no observer until a HUD wires
    /// itself in.
    public var onChange: (() -> Void)?

    public init() {}

    // MARK: - Ingest

    /// Replace the cached snapshot from a v2 `session_data` wire
    /// dict. The dict is the full message body as decoded by
    /// `JSONSerialization` (the outer envelope already stripped by
    /// PresetBridge before the callback fires — but we tolerate the
    /// envelope being present too, since callers may pass the raw
    /// frame). Missing fields clear their cached values; that
    /// matches JAM's producer semantics where every push rebuilds
    /// the snapshot from scratch.
    public func ingestSessionData(_ dict: [String: Any]) {
        stateQueue.sync {
            _sessionId        = SessionStore.decodeString(dict["session_id"])
            _song             = SessionStore.decodeSong(dict["song"])
            _bpm              = SessionStore.decodeOptionalDouble(dict["bpm"])
            _key              = SessionStore.decodeKey(dict["key"])
            _chordProgression = SessionStore.decodeChordList(dict["chord_progression"])
            _sectionMarkers   = SessionStore.decodeSectionList(dict["section_markers"])
            _loopMarkers      = SessionStore.decodeLoop(dict["loop_markers"])
        }
        fireOnChange()
    }

    /// Replace transport state from a v2 `transport_state` frame.
    /// Position is in seconds since the start of the stem (matches
    /// JAM's `audio.currentTime`).
    public func ingestTransport(playing: Bool, positionSec: Double) {
        // Floor at zero so a stray negative from a serializer
        // glitch doesn't propagate into UI math. -inf / NaN guard.
        let safePos = positionSec.isFinite ? max(0.0, positionSec) : 0.0
        stateQueue.sync {
            _transport = Transport(isPlaying: playing, positionSec: safePos)
        }
        fireOnChange()
    }

    /// Clear cached state. Used when the bridge disconnects or the
    /// user reassigns to a different session.
    public func reset() {
        stateQueue.sync {
            _sessionId        = nil
            _song             = nil
            _bpm              = nil
            _key              = nil
            _chordProgression = []
            _sectionMarkers   = []
            _loopMarkers      = nil
            _transport        = .stopped
        }
        fireOnChange()
    }

    /// Invoke the change callback on the main queue. Always
    /// dispatched (never sync) so reentrant onChange handlers can
    /// safely re-read the store without deadlocking on
    /// `stateQueue.sync`.
    private func fireOnChange() {
        guard let cb = onChange else { return }
        DispatchQueue.main.async {
            cb()
        }
    }

    // MARK: - Queries

    /// Active chord at a given absolute time (seconds from stem
    /// start), or nil if no chord covers that instant. O(log n)
    /// would be possible with a sorted-array binary search; n is
    /// dozens of chords per song so a linear scan is faster in
    /// practice and easier to reason about.
    public func chord(at sec: Double) -> ChordEvent? {
        return stateQueue.sync { _chordProgression.first(where: { $0.contains(sec) }) }
    }

    /// Active section at a given absolute time.
    public func section(at sec: Double) -> SectionMarker? {
        return stateQueue.sync { _sectionMarkers.first(where: { $0.contains(sec) }) }
    }

    // MARK: - Decoding helpers
    //
    // Static so they're reusable from tests without going through
    // a SessionStore instance, and so the @MainActor isolation
    // doesn't leak into the helpers (decoding is pure).

    private static func decodeString(_ any: Any?) -> String? {
        return any as? String
    }

    private static func decodeOptionalDouble(_ any: Any?) -> Double? {
        if let d = any as? Double { return d.isFinite ? d : nil }
        if let i = any as? Int    { return Double(i) }
        return nil
    }

    private static func decodeDouble(_ any: Any?, default fallback: Double = 0.0) -> Double {
        return decodeOptionalDouble(any) ?? fallback
    }

    private static func decodeInt(_ any: Any?, default fallback: Int = 0) -> Int {
        if let i = any as? Int    { return i }
        if let d = any as? Double { return Int(d) }
        return fallback
    }

    private static func decodeSong(_ any: Any?) -> SongInfo? {
        guard let dict = any as? [String: Any] else { return nil }
        guard
            let id    = dict["id"]    as? String,
            let title = dict["title"] as? String
        else { return nil }
        return SongInfo(id: id, title: title)
    }

    private static func decodeKey(_ any: Any?) -> KeyInfo? {
        guard let dict = any as? [String: Any] else { return nil }
        guard let scale = dict["scale"] as? String else { return nil }
        let root = decodeInt(dict["root"], default: 0)
        // Reject obviously-wrong root values rather than wrap; the
        // UI only cares about the 0..11 pitch class.
        guard (0...11).contains(root) else { return nil }
        return KeyInfo(root: root, scale: scale)
    }

    private static func decodeChordList(_ any: Any?) -> [ChordEvent] {
        guard let arr = any as? [[String: Any]] else { return [] }
        return arr.compactMap { item in
            guard
                let symbol = item["symbol"] as? String,
                let start  = decodeOptionalDouble(item["start_s"]),
                let end    = decodeOptionalDouble(item["end_s"])
            else { return nil }
            // Skip nonsense intervals so the UI doesn't have to.
            guard end > start else { return nil }
            return ChordEvent(symbol: symbol, startSec: start, endSec: end)
        }
    }

    private static func decodeSectionList(_ any: Any?) -> [SectionMarker] {
        guard let arr = any as? [[String: Any]] else { return [] }
        return arr.compactMap { item in
            guard
                let name  = item["name"] as? String,
                let start = decodeOptionalDouble(item["start_s"]),
                let end   = decodeOptionalDouble(item["end_s"])
            else { return nil }
            guard end > start else { return nil }
            return SectionMarker(name: name, startSec: start, endSec: end)
        }
    }

    private static func decodeLoop(_ any: Any?) -> LoopMarkers? {
        guard let dict = any as? [String: Any] else { return nil }
        guard
            let start = decodeOptionalDouble(dict["start_s"]),
            let end   = decodeOptionalDouble(dict["end_s"])
        else { return nil }
        guard end > start else { return nil }
        return LoopMarkers(startSec: start, endSec: end)
    }
}
