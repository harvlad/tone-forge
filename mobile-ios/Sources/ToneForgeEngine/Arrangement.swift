// Arrangement.swift — live-capture arrangement engine (pure, DOM/UI-free).
//
// Swift port of the arrangement helpers in backend/static/kit.js
// (collapseSections / blockIndexAtTime / arrangementDiff / parse+serialize).
// Port-parity rule: this file and kit.js's arrangement helpers move in
// lockstep — a change to one lands in the same commit as the other so drift
// stays visible (same rule as launchpad.js ↔ ToneForgeEngine).
//
// The math is deliberately identical to the web so a song captured on one
// surface replays bit-for-bit on another:
//   • collapseSections merges consecutive same-`type` analyzer sections into
//     a handful of readable blocks [firstStart, lastEnd].
//   • blockIndexAtTime maps song time → block index (final block owns its end).
//   • arrangementDiff is the replay set-difference (toArm / toRelease).
//   • Captured shape is {blockIndex: [padIndex…]}, sorted + de-duped, persisted
//     as JSON under jamn.arrangement.<songId>.

import Foundation

/// One collapsed section block on the arrangement strip.
public struct ArrangementBlock: Equatable {
    public let type: String   // raw analyzer type, e.g. "chorus"
    public let label: String  // Title-cased for the strip, e.g. "Chorus"
    public let start: Double   // seconds
    public let end: Double     // seconds
    public init(type: String, label: String, start: Double, end: Double) {
        self.type = type; self.label = label; self.start = start; self.end = end
    }
}

/// Minimal analyzer-section input (matches the {type,start,end} the app already has).
public struct ArrangementSectionInput {
    public let type: String
    public let start: Double
    public let end: Double
    public init(type: String, start: Double, end: Double) {
        self.type = type; self.start = start; self.end = end
    }
}

public enum Arrangement {

    private static func titleCase(_ str: String) -> String {
        let t = str.trimmingCharacters(in: .whitespaces)
        guard let first = t.first else { return "" }
        return String(first).uppercased() + t.dropFirst().lowercased()
    }

    /// Merge consecutive same-`type` sections into blocks. Typeless rows and
    /// non-finite / end<=start rows are dropped; rows are sorted by start first
    /// so out-of-order analysis still collapses. Empty in → empty out.
    public static func collapseSections(_ raw: [ArrangementSectionInput]) -> [ArrangementBlock] {
        var norm: [(type: String, start: Double, end: Double)] = []
        for r in raw {
            let type = r.type.trimmingCharacters(in: .whitespaces)
            if type.isEmpty { continue }
            let start = r.start, end = r.end
            if !start.isFinite || !end.isFinite || end <= start { continue }
            norm.append((type, start, end))
        }
        if norm.isEmpty { return [] }
        norm.sort { $0.start < $1.start }
        var blocks: [ArrangementBlock] = []
        var keys: [String] = []
        for n in norm {
            let key = n.type.lowercased()
            if let last = keys.last, last == key {
                if n.end > blocks[blocks.count - 1].end {
                    let b = blocks[blocks.count - 1]
                    blocks[blocks.count - 1] = ArrangementBlock(type: b.type, label: b.label, start: b.start, end: n.end)
                }
            } else {
                blocks.append(ArrangementBlock(type: n.type, label: titleCase(n.type), start: n.start, end: n.end))
                keys.append(key)
            }
        }
        return blocks
    }

    /// Index of the block whose [start,end) contains `t` (final block also owns
    /// its end). -1 for no blocks, non-finite t, before-first / after-last / gap.
    public static func blockIndexAtTime(_ blocks: [ArrangementBlock], _ t: Double) -> Int {
        if blocks.isEmpty || !t.isFinite { return -1 }
        for i in blocks.indices {
            let b = blocks[i]
            let last = i == blocks.count - 1
            if t >= b.start && (t < b.end || (last && t <= b.end)) { return i }
        }
        return -1
    }

    /// Replay set math: given the pads currently armed and the pads a block
    /// wants, which to newly arm and which to release.
    public static func diff(prev: [Int], next: [Int]) -> (toArm: [Int], toRelease: [Int]) {
        let p = Set(prev), n = Set(next)
        let toArm = next.filter { !p.contains($0) }
        let toRelease = prev.filter { !n.contains($0) }
        return (toArm, toRelease)
    }

    /// Parse the persisted map {blockIndex: [padIdx…]}. Invalid keys/pads,
    /// non-array values and duplicates are dropped; all-invalid → nil.
    public static func parse(_ json: String) -> [Int: [Int]]? {
        guard let data = json.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any] else { return nil }
        var out: [Int: [Int]] = [:]
        for (k, v) in dict {
            guard let bi = Int(k), bi >= 0, let arr = v as? [Any] else { continue }
            var pads: [Int] = []
            for e in arr {
                let pi: Int?
                if let n = e as? Int { pi = n }
                else if let d = e as? Double, d == d.rounded() { pi = Int(d) }
                else if let s = e as? String, let n = Int(s) { pi = n }
                else { pi = nil }
                if let pi = pi, pi >= 0, !pads.contains(pi) { pads.append(pi) }
            }
            if !pads.isEmpty { out[bi] = pads.sorted() }
        }
        return out.isEmpty ? nil : out
    }

    /// Serialize the captured map for persistence; nil when nothing to store.
    /// Round-trips loss-free with parse().
    public static func serialize(_ map: [Int: [Int]]) -> String? {
        var obj: [String: [Int]] = [:]
        for (bi, pads) in map {
            if bi < 0 { continue }
            var clean: [Int] = []
            for pi in pads where pi >= 0 && !clean.contains(pi) { clean.append(pi) }
            if !clean.isEmpty { obj[String(bi)] = clean.sorted() }
        }
        if obj.isEmpty { return nil }
        // Sorted keys → deterministic output (kit.js JSON key order is engine
        // insertion order; tests compare parsed maps, not raw strings).
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

/// Capture/replay state machine — the native-shared twin of kit.js
/// `arrangementTick`. Desktop and iOS both drive this from a ~100 ms tick so
/// the record/replay behavior can't drift between them (only the pad-arm and
/// persistence side effects, which each platform applies, differ). Pure apart
/// from mutating its own fields: it never touches an engine — the caller
/// applies the returned `toArm`/`toRelease` and persists when `capturedChanged`.
public struct ArrangementRuntime {
    public var blocks: [ArrangementBlock]
    /// blockIndex → pad indices captured for that block.
    public private(set) var captured: [Int: [Int]]
    public private(set) var recording = false
    public private(set) var playing = false
    /// Pads replay currently holds (so the next boundary can diff against them).
    public private(set) var armedSet: [Int] = []
    /// Last block replay armed for; -2 forces a diff on the first replay tick.
    private var curBlock = -2

    public init(blocks: [ArrangementBlock], captured: [Int: [Int]] = [:]) {
        self.blocks = blocks
        self.captured = captured
    }

    /// What one tick produced. `toArm`/`toRelease` are pad indices the caller
    /// must sound/silence; `capturedChanged` means persist `captured`;
    /// `activeBlock` (-1 = none) is the strip highlight; `playheadFrac`
    /// (0…1, or nil when time is unknown) positions the playhead.
    public struct TickResult: Equatable {
        public var toArm: [Int] = []
        public var toRelease: [Int] = []
        public var capturedChanged = false
        public var activeBlock = -1
        public var playheadFrac: Double? = nil
    }

    /// Rec toggle — mutually exclusive with replay (starting Rec stops replay
    /// and returns the pads to release). Returns nil release list if nothing held.
    public mutating func startRecording() -> [Int] {
        guard !blocks.isEmpty else { return [] }
        let released = stopReplayInternal()
        recording = true
        return released
    }
    public mutating func stopRecording() { recording = false }

    /// Play toggle. Returns pads to release if it was already playing (stop).
    public mutating func startPlaying() {
        guard !blocks.isEmpty else { return }
        recording = false
        playing = true
        curBlock = -2       // force a diff next tick
        armedSet = []
    }
    /// Stop replay; returns every pad it was holding so the caller releases them.
    @discardableResult
    public mutating func stopReplay() -> [Int] { stopReplayInternal() }

    private mutating func stopReplayInternal() -> [Int] {
        let held = armedSet
        armedSet = []
        curBlock = -2
        playing = false
        return held
    }

    /// Forget this song's capture. Returns pads to release if replay held any.
    public mutating func clear() -> [Int] {
        captured = [:]
        guard playing else { return [] }
        let held = armedSet
        armedSet = []
        curBlock = -2
        return held
    }

    /// One ~100 ms poll. `activePads` are the pad indices currently sounding
    /// (armed/playing) — the caller reads them from its own launchpad state.
    public mutating func tick(time: Double, isPlaying: Bool, activePads: [Int]) -> TickResult {
        var out = TickResult()
        guard !blocks.isEmpty else { return out }

        // Playhead across [firstStart, lastEnd].
        let span = max(blocks[blocks.count - 1].end - blocks[0].start, 1)
        if time.isFinite {
            out.playheadFrac = min(1, max(0, (time - blocks[0].start) / span))
        }

        let bi = time.isFinite ? Arrangement.blockIndexAtTime(blocks, time) : -1
        out.activeBlock = bi

        // Record: union the pads ON in the current block.
        if recording && isPlaying && bi >= 0 {
            var cur = captured[bi] ?? []
            var changed = false
            for p in activePads where !cur.contains(p) { cur.append(p); changed = true }
            if changed {
                captured[bi] = cur.sorted()
                out.capturedChanged = true
            }
        }

        // Replay: at each block boundary arm the captured set, release the rest.
        // Only advance while the song rolls (a pause holds the pads).
        if playing && isPlaying && bi != curBlock {
            curBlock = bi
            let want = (bi >= 0 ? captured[bi] : nil) ?? []
            let d = Arrangement.diff(prev: armedSet, next: want)
            out.toArm = d.toArm
            out.toRelease = d.toRelease
            armedSet = want
        }
        return out
    }
}
