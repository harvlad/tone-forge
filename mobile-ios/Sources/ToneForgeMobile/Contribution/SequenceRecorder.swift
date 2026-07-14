// SequenceRecorder.swift
//
// Live-record core for the 4x4 Launchpad Sequence Builder. Captures
// thumb taps on a 4x4 grid into a SequencerPattern. Song-aware but not
// song-dependent: three interchangeable sources feed one pattern —
//
//   - pads       one sample pack's pads (.packPad)
//   - songChords the loaded song's harmonic chops (.bundleChop; needs a song)
//   - keyChords  diatonic triads of a musical key voiced on padSynth
//                (.synthChord; works with NO song loaded)
//
// The user can switch source mid-build without losing what's already
// recorded: tracks are keyed by (source, cell) so cell 0 under Pads and
// cell 0 under Key Chords are distinct tracks in the same pattern.
//
// Transport: when a song is playing the builder ticks the recorder from
// the song clock (useSongClock). With no song the recorder runs its own
// 30 Hz metronome-driven clock so recording still works; finalize then
// bakes the tempo into bpmOverride so playback doesn't need a song.
//
// Two capture modes:
//   - quantized: taps snap to the current 16th step (SequencerClock).
//   - free: taps snap to the NEAREST 16th (round vs floor). The frozen
//     SequencerStep model has no sub-step offset, so "free" stays on the
//     16th grid but rounds instead of truncating.
//
// The recorder builds data only; audio is triggered separately by the
// builder via AppState.previewChopReference so the player hears taps.

import Foundation
import ToneForgeEngine

@MainActor
public final class SequenceRecorder: ObservableObject {

    // MARK: - Types

    /// Which bank of sounds the grid taps currently address.
    public enum PadSource: String, CaseIterable, Equatable {
        case pads
        case songChords
        case keyChords
    }

    /// How taps are quantized into steps.
    public enum CaptureMode: String, CaseIterable {
        case quantized
        case free
    }

    /// Loop length options for the recorded pattern.
    public enum LoopLength: String, CaseIterable {
        case oneBar
        case twoBar
        case section
    }

    /// Track key: a cell qualified by the source it was tapped under, so
    /// the three sources build independent tracks in one pattern.
    private struct SourceCell: Hashable {
        let source: PadSource
        let cell: Int
    }

    // MARK: - Published State

    /// The pattern being recorded (observable for tile state).
    @Published public private(set) var pattern: SequencerPattern
    /// Whether the recorder is actively capturing taps.
    @Published public private(set) var isRecording = false
    /// Current step for playhead highlight (valid while recording).
    @Published public private(set) var currentStep = 0
    /// Snap vs free capture.
    @Published public var captureMode: CaptureMode = .quantized
    /// Which source the grid currently addresses.
    @Published public private(set) var activeSource: PadSource = .keyChords
    /// Current loop length.
    @Published public private(set) var loopLength: LoopLength = .oneBar
    /// Whole-octave transpose for the `.keyChords` synth source, −3…+3
    /// (matches Jam). Baked into each `.synthChord` reference at capture.
    @Published public private(set) var octaveShift: Int = 0

    // MARK: - Source metadata (all sources held at once)

    /// Pack id for the `.pads` source (nil when no pack fronted).
    private var packId: String?
    /// Valid pad count for the `.pads` source.
    private var padCount = 0
    /// Valid chop count for the `.songChords` source.
    private var songChordCount = 0
    /// Chord symbols for the `.keyChords` source (diatonic triads).
    private var keyChordSymbols: [String] = []

    // MARK: - Private

    /// (source, cell) -> track index in `pattern`.
    private var cellTrackIndex: [SourceCell: Int] = [:]
    private let clock = SequencerClock(stepCount: 16, bpm: 120)
    private var recordStartSongSeconds: Double = 0
    private var sessionBPM: Double = 120

    /// Whether the active session is driven by the song clock. When
    /// false the recorder runs its own timer and bakes bpmOverride on
    /// finalize so the saved pattern plays standalone.
    private var useSongClock = true
    /// Latest clock time (song seconds, or internal elapsed). Used as the
    /// capture time when the builder doesn't pass an explicit override.
    private var lastClockSeconds: Double = 0

    /// Internal 30 Hz driver for song-less recording.
    private var internalTimer: Timer?
    private var internalStartUptime: Double = 0

    // MARK: - Init

    public init() {
        self.pattern = SequencerPattern(
            name: "Sequence",
            stepCount: .sixteen,
            isLooping: true
        )
    }

    // MARK: - Configuration

    /// Reset the pattern and load metadata for all three sources for a
    /// fresh session. Called once when the builder appears.
    /// - Parameters:
    ///   - loopLength: 1 bar / 2 bar / section.
    ///   - songBPM: song tempo (or fallback) driving the clock.
    ///   - sectionSteps: resolved step count for `.section` loop length.
    ///   - packId: pack id for the pads source (nil = no pack).
    ///   - padCount: valid pad count for the pads source.
    ///   - songChordCount: valid chop count for the song-chords source.
    ///   - keyChordSymbols: diatonic triad symbols for the key source.
    ///   - initialSource: source the grid starts on.
    public func configure(
        loopLength: LoopLength,
        songBPM: Double,
        sectionSteps: Int,
        packId: String?,
        padCount: Int,
        songChordCount: Int,
        keyChordSymbols: [String],
        initialSource: PadSource
    ) {
        self.loopLength = loopLength
        self.sessionBPM = songBPM > 0 ? songBPM : 120
        self.packId = packId
        self.padCount = max(0, padCount)
        self.songChordCount = max(0, songChordCount)
        self.keyChordSymbols = keyChordSymbols
        self.activeSource = initialSource

        let stepCount = Self.stepCount(for: loopLength, sectionSteps: sectionSteps)
        cellTrackIndex.removeAll()
        pattern = SequencerPattern(
            name: pattern.name,
            stepCount: stepCount,
            bpmOverride: nil,        // resolved on finalize
            tracks: [],
            isLooping: true
        )
        stopRecording()
        currentStep = 0
    }

    /// Switch which source the grid addresses. Non-destructive — keeps
    /// every already-recorded track (they're keyed by source).
    public func setSource(_ source: PadSource) {
        activeSource = source
    }

    /// Replace the key-chord symbol set (called when the key changes).
    /// Existing key-chord tracks keep their recorded chord; new taps use
    /// the new symbols.
    public func setKeyChords(_ symbols: [String]) {
        keyChordSymbols = symbols
    }

    /// Set the key-chord octave transpose (−3…+3). Re-voices every
    /// already-recorded synth-chord track to the new octave so the whole
    /// loop shifts together (and newly captured taps use it too).
    public func setOctaveShift(_ shift: Int) {
        let clamped = max(-3, min(3, shift))
        guard clamped != octaveShift else { return }
        octaveShift = clamped
        for i in pattern.tracks.indices {
            if case .synthChord(let symbol, _) = pattern.tracks[i].chopRef {
                pattern.tracks[i].chopRef = .synthChord(symbol: symbol, octaveShift: clamped)
            }
        }
    }

    /// Load an existing saved pattern for in-place editing. Preserves the
    /// pattern id (so `finalize`/save updates in place), rebuilds the
    /// (source, cell) track index by reverse-mapping each track's
    /// ChopReference, and restores tempo/loop metadata. Source metadata
    /// (packId, keyChordSymbols, counts) must already be set via
    /// `configure`. Sets `activeSource` to the first mapped track's source.
    public func load(_ existing: SequencerPattern) {
        stopRecording()
        pattern = existing
        loopLength = Self.loopLength(for: existing.stepCount)
        if let bpm = existing.bpmOverride {
            useSongClock = false
            sessionBPM = bpm > 0 ? bpm : sessionBPM
        } else {
            useSongClock = true
        }
        cellTrackIndex.removeAll()
        for (idx, track) in pattern.tracks.enumerated() {
            guard let key = sourceCell(for: track.chopRef) else { continue }
            cellTrackIndex[key] = idx
        }
        // Restore the octave from the first synth-chord track so the
        // stepper matches what plays back.
        for track in pattern.tracks {
            if case .synthChord(_, let oct) = track.chopRef {
                octaveShift = oct
                break
            }
        }
        if let first = pattern.tracks.first,
           let key = sourceCell(for: first.chopRef) {
            activeSource = key.source
        }
        currentStep = 0
    }

    /// Reverse-map a saved track's ChopReference back to the (source, cell)
    /// the builder grid addresses. Nil for refs the builder can't edit
    /// (e.g. a synth chord not in the current key, or a nested sequence);
    /// such tracks still play and are preserved on save.
    private func sourceCell(for ref: ChopReference) -> SourceCell? {
        switch ref {
        case .packPad(_, let padIdx):
            return SourceCell(source: .pads, cell: padIdx)
        case .bundleChop(let presetKey, let chopIndex, _):
            guard presetKey == "harmonic" else { return nil }
            return SourceCell(source: .songChords, cell: chopIndex)
        case .synthChord(let symbol, _):
            guard let cell = keyChordSymbols.firstIndex(of: symbol) else { return nil }
            return SourceCell(source: .keyChords, cell: cell)
        default:
            return nil
        }
    }

    /// Change loop length. Destructive: the step grid changes size, so
    /// recorded content is cleared.
    public func setLoopLength(_ loopLength: LoopLength, sectionSteps: Int) {
        self.loopLength = loopLength
        let stepCount = Self.stepCount(for: loopLength, sectionSteps: sectionSteps)
        cellTrackIndex.removeAll()
        pattern = SequencerPattern(
            name: pattern.name,
            stepCount: stepCount,
            bpmOverride: nil,
            tracks: [],
            isLooping: true
        )
        stopRecording()
        currentStep = 0
    }

    /// Rename the in-progress pattern.
    public func setName(_ name: String) {
        pattern.name = name.isEmpty ? "Sequence" : name
    }

    // MARK: - Transport

    /// Begin capturing taps.
    /// - Parameters:
    ///   - useSongClock: true = the builder ticks us from song time;
    ///     false = run an internal 30 Hz clock (no song).
    ///   - songSeconds: starting song time (song-clock mode only).
    ///   - bpm: tempo for step timing.
    public func startRecording(
        useSongClock: Bool,
        atSongSeconds songSeconds: Double,
        bpm: Double
    ) {
        self.useSongClock = useSongClock
        self.sessionBPM = bpm > 0 ? bpm : 120
        clock.stepCount = pattern.stepCount.rawValue
        clock.bpm = sessionBPM
        clock.isLooping = true

        if useSongClock {
            recordStartSongSeconds = songSeconds
            lastClockSeconds = songSeconds
            clock.start(at: songSeconds)
            stopInternalTimer()
        } else {
            recordStartSongSeconds = 0
            lastClockSeconds = 0
            clock.start(at: 0)
            startInternalTimer()
        }
        currentStep = 0
        isRecording = true
    }

    /// Song-clock convenience (also used by tests): record locked to the
    /// given song time using the configured session tempo.
    public func startRecording(atSongSeconds songSeconds: Double) {
        startRecording(useSongClock: true, atSongSeconds: songSeconds, bpm: sessionBPM)
    }

    /// Stop capturing (keeps recorded content).
    public func stopRecording() {
        isRecording = false
        clock.stop()
        stopInternalTimer()
    }

    /// Advance the playhead from the song clock (call on song-time change,
    /// song-clock mode only).
    public func tick(songSeconds: Double) {
        guard isRecording, useSongClock else { return }
        lastClockSeconds = songSeconds
        if let step = clock.stepAt(songSeconds: songSeconds), step != currentStep {
            currentStep = step
        }
    }

    // MARK: - Internal clock

    private func startInternalTimer() {
        stopInternalTimer()
        internalStartUptime = ProcessInfo.processInfo.systemUptime
        let timer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.isRecording else { return }
                let elapsed = ProcessInfo.processInfo.systemUptime - self.internalStartUptime
                self.lastClockSeconds = elapsed
                if let step = self.clock.stepAt(songSeconds: elapsed), step != self.currentStep {
                    self.currentStep = step
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        internalTimer = timer
    }

    private func stopInternalTimer() {
        internalTimer?.invalidate()
        internalTimer = nil
    }

    // MARK: - Capture

    /// Valid cell count for the active source (cells beyond it no-op).
    private var activeCellCount: Int {
        switch activeSource {
        case .pads: return padCount
        case .songChords: return songChordCount
        case .keyChords: return keyChordSymbols.count
        }
    }

    /// Resolve the sound source for a grid cell (0..15) under the active
    /// source. Nil when the cell has no source.
    public func chopRef(forCell cellIdx: Int) -> ChopReference? {
        guard cellIdx >= 0, cellIdx < activeCellCount else { return nil }
        switch activeSource {
        case .pads:
            guard let packId else { return nil }
            return .packPad(packId: packId, padIdx: cellIdx)
        case .songChords:
            return .bundleChop(presetKey: "harmonic", chopIndex: cellIdx, resolvedId: nil)
        case .keyChords:
            return .synthChord(symbol: keyChordSymbols[cellIdx], octaveShift: octaveShift)
        }
    }

    /// Record a tap on `cell` into the pattern under the active source.
    /// - Parameter atSongSeconds: explicit capture time; nil uses the
    ///   recorder's own clock (song tick or internal elapsed).
    public func capture(cell cellIdx: Int, atSongSeconds override: Double? = nil) {
        guard isRecording, let ref = chopRef(forCell: cellIdx) else { return }

        let key = SourceCell(source: activeSource, cell: cellIdx)
        let trackIdx: Int
        if let existing = cellTrackIndex[key] {
            trackIdx = existing
        } else {
            pattern.addTrack(for: ref, name: nil)
            trackIdx = pattern.tracks.count - 1
            cellTrackIndex[key] = trackIdx
        }

        let step = quantizedStep(atSongSeconds: override ?? lastClockSeconds)
        pattern.tracks[trackIdx].setStepVelocity(at: step, velocity: 1.0)
    }

    /// Overdub a hit for `cell` at an explicit step index. Used by the
    /// builder's preview loop for in-place editing: the playing
    /// SequencerPlayer supplies the current playhead step, so this does
    /// NOT require `isRecording`. Creates the track on first tap.
    public func captureAtStep(cell cellIdx: Int, step: Int) {
        guard let ref = chopRef(forCell: cellIdx) else { return }
        let count = pattern.stepCount.rawValue
        guard count > 0 else { return }

        let key = SourceCell(source: activeSource, cell: cellIdx)
        let trackIdx: Int
        if let existing = cellTrackIndex[key] {
            trackIdx = existing
        } else {
            pattern.addTrack(for: ref, name: nil)
            trackIdx = pattern.tracks.count - 1
            cellTrackIndex[key] = trackIdx
        }

        let s = ((step % count) + count) % count
        pattern.tracks[trackIdx].setStepVelocity(at: s, velocity: 1.0)
    }

    /// True if `cell` (under the active source) has any recorded step.
    public func hasSteps(cell cellIdx: Int) -> Bool {
        let key = SourceCell(source: activeSource, cell: cellIdx)
        guard let idx = cellTrackIndex[key], idx < pattern.tracks.count
        else { return false }
        return pattern.tracks[idx].steps.contains { $0.isActive }
    }

    /// True if `cell` (under the active source) fires ON `step` — drives
    /// the Launchpad-style pad flash as the sequence plays through.
    public func fires(cell cellIdx: Int, step: Int) -> Bool {
        guard step >= 0 else { return false }
        let key = SourceCell(source: activeSource, cell: cellIdx)
        guard let idx = cellTrackIndex[key], idx < pattern.tracks.count
        else { return false }
        let steps = pattern.tracks[idx].steps
        guard steps.indices.contains(step) else { return false }
        return steps[step].isActive
    }

    /// Whether anything has been recorded yet (any source).
    public var isEmpty: Bool {
        !pattern.tracks.contains { track in track.steps.contains { $0.isActive } }
    }

    // MARK: - Editing

    /// Discard recorded content, keep sources + length.
    public func clear() {
        cellTrackIndex.removeAll()
        pattern.tracks.removeAll()
    }

    /// Produce the final pattern to save: silent tracks pruned, named,
    /// looping. Bakes the session tempo into bpmOverride for song-less
    /// sessions so playback doesn't require a song; song-synced sessions
    /// leave it nil so they follow the song. Keeps the same id so
    /// re-saving updates in place.
    public func finalize(name: String) -> SequencerPattern {
        var p = pattern
        p.name = name.isEmpty ? "Sequence" : name
        p.isLooping = true
        p.tracks = p.tracks.filter { track in
            track.steps.contains { $0.isActive }
        }
        p.bpmOverride = useSongClock ? nil : sessionBPM
        return p
    }

    // MARK: - Step math

    /// Map a song time to a step index within the loop.
    private func quantizedStep(atSongSeconds songSeconds: Double) -> Int {
        let stepCount = pattern.stepCount.rawValue
        guard stepCount > 0 else { return 0 }

        switch captureMode {
        case .quantized:
            let step = clock.stepAt(songSeconds: songSeconds) ?? 0
            return ((step % stepCount) + stepCount) % stepCount
        case .free:
            let dur = clock.stepDuration
            guard dur > 0 else { return 0 }
            let elapsed = songSeconds - recordStartSongSeconds
            let raw = Int((elapsed / dur).rounded())
            return ((raw % stepCount) + stepCount) % stepCount
        }
    }

    /// Resolve a PatternStepCount for the loop length. `.section` clamps
    /// the computed step count into the allowed {16, 32} set.
    static func stepCount(for loopLength: LoopLength, sectionSteps: Int) -> PatternStepCount {
        switch loopLength {
        case .oneBar:
            return .sixteen
        case .twoBar:
            return .thirtyTwo
        case .section:
            return sectionSteps <= 16 ? .sixteen : .thirtyTwo
        }
    }

    /// Best-effort inverse of `stepCount(for:)` — used when loading an
    /// existing pattern to restore the loop-length selector.
    static func loopLength(for stepCount: PatternStepCount) -> LoopLength {
        switch stepCount {
        case .eight, .sixteen: return .oneBar
        case .thirtyTwo: return .twoBar
        }
    }
}
