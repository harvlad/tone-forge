// MelodySequence.swift
//
// Playable song-melody sequence over the BundleMelody wire model —
// the Swift mirror of the launchpad.js melody follow-along
// (onMelodyLoaded / onMelodyPosition). Keep the cursor semantics in
// lock-step with launchpad.js: a change to either should land in the
// same commit so drift stays visible (CLAUDE.md engine-port rule).
//
// Pure logic — no AVFoundation. Hosts drive `MelodySequencePlayer`
// from their transport clock (per-frame `advance(to:)`); the voice
// seam matches WavetableSynth's noteOn/noteOff signatures so the
// synth engine conforms for free.

import Foundation

// MARK: - Cursor

/// Position within the melody at one instant of song time.
/// `nowIndex` is the sounding note (nil in inter-note gaps),
/// `nextIndex` the first upcoming note (nil past the last onset).
public struct MelodyCursor: Equatable, Sendable {
    public let nowIndex: Int?
    public let nextIndex: Int?

    public init(nowIndex: Int?, nextIndex: Int?) {
        self.nowIndex = nowIndex
        self.nextIndex = nextIndex
    }
}

// MARK: - Sequence

/// The song melody as an ordered, strictly monophonic note sequence
/// with per-section phrase windows. Notes are absolute song seconds —
/// loopers subtract the phrase start locally.
public struct MelodySequence: Equatable, Sendable {
    public let sourceStem: String
    public let confidence: Double
    public let notes: [BundleMelodyNote]
    public let phrases: [BundleMelodyPhrase]

    public init?(bundle: BundleMelody?) {
        guard let bundle, !bundle.notes.isEmpty else { return nil }
        self.sourceStem = bundle.sourceStem
        self.confidence = bundle.confidence
        // Defensive re-sort: the server guarantees onset order, but a
        // corrupt cached bundle must not break the binary search.
        self.notes = bundle.notes.sorted { ($0.start, $0.pitch) < ($1.start, $1.pitch) }
        self.phrases = bundle.phrases
    }

    /// Binary-search the monophonic note list — parity with
    /// launchpad.js `onMelodyPosition`. O(log n) per call, so hosts
    /// may drive it per frame.
    public func cursor(at time: Double) -> MelodyCursor {
        var lo = 0, hi = notes.count - 1, last = -1
        while lo <= hi {
            let mid = (lo + hi) / 2
            if notes[mid].start <= time {
                last = mid
                lo = mid + 1
            } else {
                hi = mid - 1
            }
        }
        let now = (last >= 0 && time < notes[last].end) ? last : nil
        let next = (last + 1 < notes.count) ? last + 1 : nil
        return MelodyCursor(nowIndex: now, nextIndex: next)
    }

    /// Notes whose onset falls inside [start, end) — the same slicing
    /// rule the backend uses to build phrases.
    public func notes(from start: Double, to end: Double) -> [BundleMelodyNote] {
        notes.filter { $0.start >= start && $0.start < end }
    }

    /// The note events for one phrase window (phrase notes are not
    /// duplicated on the wire).
    public func notes(in phrase: BundleMelodyPhrase) -> [BundleMelodyNote] {
        notes(from: phrase.start, to: phrase.end)
    }
}

// MARK: - Voice seam

/// Matches WavetableSynth's public note API exactly so the synth
/// conforms with an empty extension; tests inject a recorder.
/// @MainActor because the player drives it from the main thread —
/// WavetableSynth's nonisolated (any-thread) methods satisfy the
/// isolated requirements, and MainActor hosts like jam-desktop's
/// DesktopSynthNode conform directly.
@MainActor
public protocol MelodyVoice: AnyObject {
    func noteOn(midi: Int, velocity: Float)
    func noteOff(midi: Int)
    func allNotesOff()
}

// MARK: - Player

/// Transport-driven melody playback. The host owns time (stem player
/// position, TransportClock, …) and calls `advance(to:)` every frame;
/// the player diffs the cursor and emits noteOn/noteOff edges to the
/// voice. Seeks need no special casing — a time jump just produces
/// one off/on edge pair. `@MainActor` to match the other sequencer
/// surfaces; the voice methods themselves are thread-safe on
/// WavetableSynth (pending-event queue).
@MainActor
public final class MelodySequencePlayer {
    public let sequence: MelodySequence
    private weak var voice: (any MelodyVoice)?
    private var soundingIndex: Int?
    /// Velocity trim applied to every noteOn (hosts mix the melody
    /// under the stems, so full-scale synth hits would swamp the song).
    public var gainScale: Float = 1.0

    public init(sequence: MelodySequence, voice: any MelodyVoice) {
        self.sequence = sequence
        self.voice = voice
    }

    /// Drive from the host transport. Idempotent per cursor state.
    public func advance(to time: Double) {
        let cursor = sequence.cursor(at: time)
        guard cursor.nowIndex != soundingIndex else { return }
        if let prev = soundingIndex {
            voice?.noteOff(midi: sequence.notes[prev].pitch)
        }
        if let now = cursor.nowIndex {
            let note = sequence.notes[now]
            let vel = Float(max(1, min(127, note.velocity))) / 127.0
            voice?.noteOn(midi: note.pitch, velocity: vel * gainScale)
        }
        soundingIndex = cursor.nowIndex
    }

    /// Silence and forget position (transport stop / song unload).
    /// No-op when nothing is sounding, so hosts may call it every
    /// paused tick without spamming the voice queue.
    public func stop() {
        guard let prev = soundingIndex else { return }
        voice?.noteOff(midi: sequence.notes[prev].pitch)
        soundingIndex = nil
    }
}

// MARK: - Grid follow-along

extension MelodySequence {
    /// Reverse of the open-jam (row, col) → MIDI mapping: the pad a
    /// melody pitch should light on the 8×8 fourths grid. Mirrors
    /// launchpad.js `_padForMidi`: out-of-range pitches are
    /// octave-shifted into the grid span, and among duplicate
    /// positions the one nearest the middle rows wins so consecutive
    /// notes stay in one hand position. Returns (row, col) with
    /// row 0 = bottom, or nil if the pitch class is unreachable.
    public static func padForMidi(_ midi: Int) -> (row: Int, col: Int)? {
        var m = midi
        let lo = OPEN_JAM_BASE_MIDI            // E2 = 40
        let hi = OPEN_JAM_BASE_MIDI + 7 * 5 + 7 // 82
        while m < lo { m += 12 }
        while m > hi { m -= 12 }
        guard m >= lo else { return nil }
        var best: (row: Int, col: Int, dist: Double)?
        for row in 0..<8 {
            let col = m - (OPEN_JAM_BASE_MIDI + row * 5)
            guard (0..<8).contains(col) else { continue }
            let dist = abs(Double(row) - 3.5)
            if best == nil || dist < best!.dist {
                best = (row, col, dist)
            }
        }
        guard let best else { return nil }
        return (best.row, best.col)
    }
}

extension WavetableSynth: MelodyVoice {}
