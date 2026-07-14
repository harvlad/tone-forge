// LearnSessionModel.swift
//
// Practice session state for Learn mode. Desktop port of the mobile
// LearnSessionController pattern with local ownership adaptations:
//
//   - configure(bundle:)  — load sections and chords; release held notes.
//   - startSection        — A/B-loop the section, reset pass counters.
//   - recordPress         — buffer press, hit/miss flash, streak fold.
//   - passCompleted       — score pass (LearnScorer), persist progress.
//   - stopPractice        — clear loop, persist streak.
//
// The model doesn't own the transport (that's SessionController's job).
// Instead it exposes loopRegion and callbacks for the controller to wire.

import Foundation
import Observation
import ToneForgeEngine

/// Practice lifecycle.
public enum LearnPhase: Equatable, Sendable {
    case idle
    case practicing
}

@Observable
@MainActor
public final class LearnSessionModel {

    // MARK: - Published state

    public private(set) var phase: LearnPhase = .idle
    public private(set) var activeSection: SectionEvent?

    /// Hit/miss tallies for the pass in flight (overlay readouts).
    public private(set) var passHits = 0
    public private(set) var passMisses = 0

    /// Outcome of the most recent press (drives flash). nil until first press.
    public private(set) var lastPressHit: Bool?

    /// Consecutive-hit streak across the whole session (resets on miss).
    public private(set) var currentStreak = 0
    public private(set) var sessionLongestStreak = 0

    /// Score of the last completed pass.
    public private(set) var lastPassResult: LearnPassResult?

    /// Loop region for the active section (SessionController applies).
    public var loopRegion: LoopRegion? {
        guard let section = activeSection else { return nil }
        return LoopRegion(inSeconds: section.start, outSeconds: section.end)
    }

    // MARK: - Callbacks (SessionController wires these)

    /// Called when a chord should play. (symbol)
    @ObservationIgnored
    public var onPlayChord: ((String) -> Void)?

    /// Called when section loop wraps (notify passCompleted).
    @ObservationIgnored
    public var onLoopWrap: (() -> Void)?

    // MARK: - Private

    private var presses: [LearnPress] = []
    private var sections: [SectionEvent] = []
    private var chords: [ChordEvent] = []
    private var analysisId: String?

    private var cachedProgress: SongLearnProgress?
    private var cachedAnalysisId: String?

    private let progressStore: LearnProgressStore

    public init(progressStore: LearnProgressStore) {
        self.progressStore = progressStore
    }

    /// Convenience init using the default store. Must be called from MainActor.
    public convenience init() {
        self.init(progressStore: LearnProgressStore())
    }

    // MARK: - Configuration

    public func configure(bundle: SongBundle?) {
        if phase == .practicing { stopPractice() }
        sections = bundle?.timeline.sections ?? []
        chords = (bundle?.timeline.chords ?? []).sorted { $0.start < $1.start }
        analysisId = bundle?.analysisId
        cachedProgress = nil
        cachedAnalysisId = nil
    }

    public func configure(sections: [SectionEvent], chords: [ChordEvent], analysisId: String?) {
        if phase == .practicing { stopPractice() }
        self.sections = sections
        self.chords = chords.sorted { $0.start < $1.start }
        self.analysisId = analysisId
        cachedProgress = nil
        cachedAnalysisId = nil
    }

    // MARK: - Progress (persisted)

    public var progress: SongLearnProgress? {
        guard let id = analysisId else { return nil }
        if cachedAnalysisId != id {
            cachedProgress = progressStore.load(analysisId: id)
                ?? SongLearnProgress(analysisId: id)
            cachedAnalysisId = id
        }
        return cachedProgress
    }

    private func save(_ progress: SongLearnProgress) {
        cachedProgress = progress
        cachedAnalysisId = progress.analysisId
        try? progressStore.save(progress)
    }

    // MARK: - Sections

    /// Stable progress key for a section: lowercased label or start-time.
    public static func sectionKey(for section: SectionEvent) -> String {
        if let label = section.label, !label.isEmpty {
            return SongLearnProgress.sectionKey(label)
        }
        return "t\(Int(section.start.rounded()))"
    }

    public var allSections: [SectionEvent] { sections }

    /// First occurrence per section key. The learnable targets.
    public var uniqueSections: [SectionEvent] {
        var seen = Set<String>()
        return sections.filter { seen.insert(Self.sectionKey(for: $0)).inserted }
    }

    public func isLearned(_ section: SectionEvent) -> Bool {
        progress?.sections[Self.sectionKey(for: section)]?.learned ?? false
    }

    /// First unlearned section.
    public var nextUpSection: SectionEvent? {
        uniqueSections.first { !isLearned($0) }
    }

    /// Every distinct chord in the song, first-appearance order.
    public var songChords: [String] {
        var seen = Set<String>()
        return chords.map(\.symbol).filter { seen.insert($0).inserted }
    }

    /// Unique chord progression across the section's bars.
    public func progressionChords(for section: SectionEvent) -> [String] {
        var out: [String] = []
        for chord in chords where chord.end > section.start && chord.start < section.end {
            if chord.symbol != out.last { out.append(chord.symbol) }
        }
        return out
    }

    // MARK: - Stats

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

    public var longestStreak: Int {
        max(progress?.longestStreak ?? 0, sessionLongestStreak)
    }

    // MARK: - Chord prediction

    public struct ChordPrediction: Equatable, Sendable {
        public let currentSymbol: String
        public let nextSymbol: String
        public let progress: Double
        public let remainingSec: Double
        public var imminent: Bool { remainingSec <= 0.6 }
    }

    public func prediction(atTime: Double, currentChord: ChordEvent?) -> ChordPrediction? {
        guard let current = currentChord else { return nil }
        var next: ChordEvent?
        for c in chords where c.start > current.start {
            if c.symbol != current.symbol {
                next = c
                break
            }
        }
        guard let next else { return nil }
        let remaining = max(0, next.start - atTime)
        let lookahead = min(8.0, max(0.5, next.start - current.start))
        let prog = min(1.0, max(0.0, 1.0 - remaining / lookahead))
        return ChordPrediction(
            currentSymbol: current.symbol,
            nextSymbol: next.symbol,
            progress: prog,
            remainingSec: remaining
        )
    }

    // MARK: - Session lifecycle

    /// Start practice on a section. Caller should seek+play and apply loop.
    public func startSection(_ section: SectionEvent) {
        activeSection = section
        resetPass()
        lastPassResult = nil
        phase = .practicing
    }

    /// Record a chord press. Always voices chord; scores only while practicing.
    public func recordPress(symbol: String, atTime: Double) {
        onPlayChord?(symbol)
        guard phase == .practicing else { return }

        let press = LearnPress(songTime: atTime, symbol: symbol)
        presses.append(press)

        let hit = LearnScorer.isHit(press: press, chords: chords)
        lastPressHit = hit
        if hit { passHits += 1 } else { passMisses += 1 }
        let folded = LearnScorer.foldStreak(
            current: currentStreak, longest: sessionLongestStreak, hit: hit)
        currentStreak = folded.current
        sessionLongestStreak = folded.longest
    }

    /// Score pass and persist. Called from loop-wrap hook.
    public func passCompleted() {
        guard phase == .practicing, let section = activeSection else { return }
        let result = LearnScorer.score(
            presses: presses,
            chords: chords,
            sectionStart: section.start,
            sectionEnd: section.end
        )
        lastPassResult = result
        if var record = progress {
            record.recordPass(result, sectionLabel: Self.sectionKey(for: section))
            record.recordStreak(sessionLongestStreak)
            save(record)
        }
        resetPass()
    }

    /// Leave practice: clear loop, persist streak.
    public func stopPractice() {
        if phase == .practicing, var record = progress {
            record.recordStreak(sessionLongestStreak)
            save(record)
        }
        phase = .idle
        activeSection = nil
        resetPass()
        lastPassResult = nil
    }

    private func resetPass() {
        presses = []
        passHits = 0
        passMisses = 0
        lastPressHit = nil
    }
}
