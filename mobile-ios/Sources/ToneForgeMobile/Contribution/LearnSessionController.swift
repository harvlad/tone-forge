// LearnSessionController.swift
//
// UI-facing state + actions for the Learn surface (redesign
// Phase 8). Owns the practice session lifecycle:
//
//   startSection  — A/B-loop the section (AppState.setLoop) and
//                   seekAndPlay from its start; presses buffer from
//                   here on.
//   recordPress   — voice the chord on PadSynth (bus bypass, same
//                   D-019 precedent as the jam degree pads), buffer
//                   a LearnPress, flash hit/miss, fold the streak.
//   passCompleted — called from AppState's loop-wrap hook when the
//                   playhead wraps: score the pass (LearnScorer),
//                   fold it into SongLearnProgress, persist.
//   stopPractice  — clear the loop, persist the session streak.
//
// Scoring and progress records are pure engine types; this class is
// the thin stateful shell that wires them to the transport and disk.

import Foundation
import Combine
import ToneForgeEngine

/// Practice lifecycle.
public enum LearnPhase: Equatable, Sendable {
    case idle
    case practicing
}

@MainActor
public final class LearnSessionController: ObservableObject {

    @Published public private(set) var phase: LearnPhase = .idle
    /// Section being practiced (nil when idle).
    @Published public private(set) var activeSection: SectionEvent?
    /// Hit/miss tallies for the pass in flight (overlay readouts).
    @Published public private(set) var passHits = 0
    @Published public private(set) var passMisses = 0
    /// Outcome of the most recent press (drives the flash). nil
    /// until the first press of a pass.
    @Published public private(set) var lastPressHit: Bool?
    /// Consecutive-hit streak across the whole session (survives
    /// pass boundaries; resets on a miss).
    @Published public private(set) var currentStreak = 0
    @Published public private(set) var sessionLongestStreak = 0
    /// Score of the last completed pass (overlay summary).
    @Published public private(set) var lastPassResult: LearnPassResult?

    private var presses: [LearnPress] = []
    private unowned let app: AppState

    /// Progress cache, keyed by the loaded song. Reloaded lazily
    /// when the bundle changes.
    private var cachedProgress: SongLearnProgress?
    private var cachedAnalysisId: String?

    public init(app: AppState) {
        self.app = app
    }

    // MARK: - Progress (persisted)

    /// Progress record for the loaded song; nil without a song.
    /// Fresh record when nothing is on disk (or the file is corrupt).
    public var progress: SongLearnProgress? {
        guard let id = app.currentBundle?.analysisId else { return nil }
        if cachedAnalysisId != id {
            cachedProgress = app.learnProgressStore.load(analysisId: id)
                ?? SongLearnProgress(analysisId: id)
            cachedAnalysisId = id
        }
        return cachedProgress
    }

    private func save(_ progress: SongLearnProgress) {
        cachedProgress = progress
        cachedAnalysisId = progress.analysisId
        try? app.learnProgressStore.save(progress)
        objectWillChange.send()
    }

    // MARK: - Sections

    /// Stable progress key for a section: its lowercased label, or a
    /// start-time key for unlabelled sections so they don't collide.
    nonisolated public static func sectionKey(for section: SectionEvent) -> String {
        if let label = section.label, !label.isEmpty {
            return SongLearnProgress.sectionKey(label)
        }
        return "t\(Int(section.start.rounded()))"
    }

    /// All timeline sections of the loaded song.
    public var sections: [SectionEvent] {
        app.currentBundle?.timeline.sections ?? []
    }

    /// First occurrence per section key, timeline order. These are
    /// the learnable targets ("verse" counts once even when the song
    /// has three verses).
    public var uniqueSections: [SectionEvent] {
        var seen = Set<String>()
        return sections.filter { seen.insert(Self.sectionKey(for: $0)).inserted }
    }

    public func isLearned(_ section: SectionEvent) -> Bool {
        progress?.sections[Self.sectionKey(for: section)]?.learned ?? false
    }

    /// First unlearned section, timeline order. nil when everything
    /// is learned (or no song is loaded).
    public var nextUpSection: SectionEvent? {
        uniqueSections.first { !isLearned($0) }
    }

    /// Every distinct chord in the song, first-appearance order. The
    /// practice grid shows all of them: bar-level progressions miss
    /// passing chords (a D#sus2 between bars would be expected by the
    /// scorer but absent from the pads), and a stable grid across
    /// sections doubles as muscle-memory layout.
    public var songChords: [String] {
        var seen = Set<String>()
        return (app.currentBundle?.timeline.chords ?? [])
            .map(\.symbol)
            .filter { seen.insert($0).inserted }
    }

    /// Unique chord progression across the section's bars,
    /// consecutive repeats collapsed ("Dm Bb C F", not one per bar).
    public func progressionChords(for section: SectionEvent) -> [String] {
        let bars = SectionBars.bars(
            section: section,
            downbeats: app.currentBundle?.timeline.downbeats ?? [],
            chords: app.currentBundle?.timeline.chords ?? [],
            tempoBpm: app.currentBundle?.meta.tempoBpm
        )
        var out: [String] = []
        for bar in bars {
            guard let symbol = bar.chordSymbol, out.last != symbol else {
                continue
            }
            out.append(symbol)
        }
        return out
    }

    // MARK: - Stats (Learn header)

    public var totalSections: Int { uniqueSections.count }

    public var learnedCount: Int {
        uniqueSections.filter { isLearned($0) }.count
    }

    public var percentComplete: Double {
        progress?.percentComplete(totalSections: totalSections) ?? 0
    }

    public var overallAccuracy: Double {
        progress?.overallAccuracy ?? 0
    }

    /// Best streak ever, including the session in flight.
    public var longestStreak: Int {
        max(progress?.longestStreak ?? 0, sessionLongestStreak)
    }

    // MARK: - Chord prediction (practice grid + Launchpad)

    /// Web-jam-parity anticipation state: where we are between the
    /// current chord and the next DISTINCT chord. Drives the on-screen
    /// countdown bar, the blinking "up next" pad, and the Launchpad
    /// mirror of both.
    public struct ChordPrediction: Equatable, Sendable {
        public let currentSymbol: String
        public let nextSymbol: String
        /// 0 = far away, 1 = at the chord change.
        public let progress: Double
        public let remainingSec: Double
        /// Web parity: last 600 ms before the change flashes.
        public var imminent: Bool { remainingSec <= 0.6 }
    }

    /// Prediction at `now`. Walks the timeline for the first chord
    /// after `current` with a DIFFERENT symbol (repeated bars of the
    /// same chord don't count as a change — same rule as the web
    /// launchpad mode). The lookahead window auto-scales to the gap
    /// (clamped 0.5–8 s) so short chords still get a readable ramp.
    nonisolated public static func prediction(
        chords: [ChordEvent], current: ChordEvent?, now: Double
    ) -> ChordPrediction? {
        guard let current else { return nil }
        var next: ChordEvent?
        for c in chords where c.start > current.start {
            if c.symbol != current.symbol {
                next = c
                break
            }
        }
        guard let next else { return nil }
        let remaining = max(0, next.start - now)
        let lookahead = min(8.0, max(0.5, next.start - current.start))
        let progress = min(1.0, max(0.0, 1.0 - remaining / lookahead))
        return ChordPrediction(
            currentSymbol: current.symbol,
            nextSymbol: next.symbol,
            progress: progress,
            remainingSec: remaining
        )
    }

    /// Prediction against the live transport.
    public func prediction(atTime: Double? = nil) -> ChordPrediction? {
        Self.prediction(
            chords: app.currentBundle?.timeline.chords ?? [],
            current: app.currentChord,
            now: atTime ?? app.songSeconds
        )
    }

    // MARK: - Session lifecycle

    /// Loop the section and start playing from its top. Presses
    /// buffer from here until the loop wraps (passCompleted).
    public func startSection(_ section: SectionEvent) {
        activeSection = section
        resetPass()
        lastPassResult = nil
        phase = .practicing
        if let region = LoopRegion(
            startSec: section.start, endSec: section.end
        ) {
            app.setLoop(region)
        }
        app.seekAndPlay(to: section.start)
    }

    /// Practice-pad press. Always voices the chord; buffers/scores
    /// only while practicing.
    /// - Parameter atTime: song-time override for tests; nil = the
    ///   transport's published songSeconds.
    public func recordPress(symbol: String, atTime: Double? = nil) {
        let midis = ChordVoicing.midiNotes(symbol: symbol)
        if !midis.isEmpty {
            app.padSynth.triggerChord(midis: midis)
        }
        guard phase == .practicing else { return }

        let press = LearnPress(
            songTime: atTime ?? app.songSeconds, symbol: symbol)
        presses.append(press)

        let hit = LearnScorer.isHit(
            press: press,
            chords: app.currentBundle?.timeline.chords ?? []
        )
        lastPressHit = hit
        if hit { passHits += 1 } else { passMisses += 1 }
        let folded = LearnScorer.foldStreak(
            current: currentStreak,
            longest: sessionLongestStreak,
            hit: hit
        )
        currentStreak = folded.current
        sessionLongestStreak = folded.longest
    }

    /// Score the pass in flight and fold it into the persisted
    /// progress. Called from AppState's loop-wrap hook while the
    /// Learn surface is active; the next pass starts immediately
    /// (streak carries over, press buffer resets).
    public func passCompleted() {
        guard phase == .practicing, let section = activeSection else { return }
        let result = LearnScorer.score(
            presses: presses,
            chords: app.currentBundle?.timeline.chords ?? [],
            sectionStart: section.start,
            sectionEnd: section.end
        )
        lastPassResult = result
        if var record = progress {
            record.recordPass(
                result, sectionLabel: Self.sectionKey(for: section))
            record.recordStreak(sessionLongestStreak)
            save(record)
        }
        resetPass()
    }

    /// Leave practice: stop the song, clear the loop, and persist the
    /// session streak. The partial pass in flight is discarded.
    public func stopPractice() {
        if phase == .practicing, var record = progress {
            record.recordStreak(sessionLongestStreak)
            save(record)
        }
        phase = .idle
        activeSection = nil
        resetPass()
        lastPassResult = nil
        app.setLoop(nil)
        app.pause()
    }

    private func resetPass() {
        presses = []
        passHits = 0
        passMisses = 0
        lastPressHit = nil
    }
}
