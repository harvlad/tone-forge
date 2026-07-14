// SessionController+BeatCapture.swift
//
// Beat Capture (D-024) desktop orchestration: the seam between the mic
// capture (BeatCaptureSession) and the pure engine pipeline
// (BeatOnsetExtractor → TempoEstimator → BeatPatternBuilder). Resolves
// musical context (song tempo → estimate → ask user), builds an
// editable SequencerPattern, registers the bundled `beatkit` so the
// pattern is audible, and logs user corrections as training data.
// Audio is analysis-only — never saved.
//
// Desktop sibling of iOS ModeCoordinator+BeatCapture.

import Foundation
import ToneForgeEngine
import ToneForgeML
import JamDesktopAudio
import JamDesktopCore

/// Outcome of analysing one beat take.
struct BeatCaptureResult: Sendable {
    /// Editable pattern ready for the sequencer.
    var pattern: SequencerPattern
    /// Classified hits (retained for review + correction logging).
    let hits: [DetectedHit]
    /// Resolved BPM used to build `pattern`.
    let bpm: Double
    /// Tempo confidence [0, 1]. 1 when following a loaded song.
    let tempoConfidence: Double
    /// True when the estimate was too weak — UI should ask the user.
    let needsManualTempo: Bool
    /// True when following a loaded song's tempo (no bpmOverride).
    let songSynced: Bool
}

extension SessionController {

    /// Minimum tempo confidence to auto-accept an estimate.
    private static var beatTempoConfidenceFloor: Double { 0.4 }

    /// Active drum classifier. Prefers a downloaded model, else the
    /// bundled baseline, running through the Core ML seam; falls back to
    /// the heuristic when no model loads.
    static var beatClassifier: BeatClassifier {
        if let url = BeatModelStore.activeModelURL() ?? BeatModel.bundledModelURL() {
            return CoreMLBeatClassifier.make(modelURL: url, confidenceFloor: 0.3)
        }
        return HeuristicBeatClassifier()
    }

    /// Register the bundled `beatkit` pack with the pack player once, so
    /// captured drum patterns are audible independent of any fronted
    /// curated pack. Silent-safe: logs and returns on resolve failure.
    func ensureBeatKitRegistered() {
        guard !beatKitRegistered else { return }
        do {
            let resolved = try BeatKitPack.resolve()
            packPlayer.register(resolved)
            beatKitRegistered = true
        } catch {
            print("[BeatCapture] beatkit resolve failed: \(error)")
        }
    }

    /// Analyse a captured take into a `BeatCaptureResult`. Heavy DSP
    /// runs off the main actor.
    func analyzeBeatTake(
        _ samples: [Float],
        quantize: BeatQuantize
    ) async -> BeatCaptureResult {
        let sr = BeatCaptureSession.canonicalSampleRate

        // Onset detection + classification (off-main).
        let classifier = Self.beatClassifier
        let hits = await Task.detached(priority: .userInitiated) {
            BeatOnsetExtractor.extract(
                samples, sampleRate: sr,
                classifier: classifier
            )
        }.value

        // Resolve tempo: song → estimate → manual.
        let songBPM = currentSongTempoBpm
        let bpm: Double
        let confidence: Double
        let songSynced: Bool
        let needsManual: Bool

        if let songBPM, songBPM > 0 {
            bpm = songBPM
            confidence = 1
            songSynced = true
            needsManual = false
        } else {
            let est = TempoEstimator.estimate(
                onsetTimesSec: hits.map(\.timeSec)
            )
            songSynced = false
            confidence = est.confidence
            if est.bpm > 0, est.confidence >= Self.beatTempoConfidenceFloor {
                bpm = est.bpm
                needsManual = false
            } else {
                bpm = est.bpm > 0 ? est.bpm : sequencer.songBPM
                needsManual = true
            }
        }

        let pattern = BeatPatternBuilder.build(
            hits: hits, bpm: bpm, quantize: quantize, songSynced: songSynced
        )

        return BeatCaptureResult(
            pattern: pattern,
            hits: hits,
            bpm: bpm,
            tempoConfidence: confidence,
            needsManualTempo: needsManual,
            songSynced: songSynced
        )
    }

    /// Rebuild a pattern from already-classified hits — used when the
    /// review UI changes quantize or the user overrides BPM. Pure, so
    /// it's cheap to call live.
    func buildBeatPattern(
        hits: [DetectedHit],
        bpm: Double,
        quantize: BeatQuantize,
        songSynced: Bool
    ) -> SequencerPattern {
        BeatPatternBuilder.build(
            hits: hits, bpm: bpm, quantize: quantize, songSynced: songSynced
        )
    }

    /// Persist a pattern for the sequencer to load; returns its id.
    @discardableResult
    func commitBeatPattern(_ pattern: SequencerPattern) -> UUID {
        patternStore.save(pattern)
        return pattern.id
    }

    /// Seed the live sequencer with a captured pattern and register the
    /// kit so it plays back. Call before presenting the sequencer panel.
    func openBeatPatternInSequencer(_ pattern: SequencerPattern) {
        ensureBeatKitRegistered()
        ensureEngineStarted()
        commitBeatPattern(pattern)
        sequencer.pattern = pattern
        if pattern.bpmOverride == nil, let songBPM = currentSongTempoBpm {
            sequencer.songBPM = songBPM
        }
    }

    /// Check the backend for a newer drum-classifier model and download
    /// it in the background. Cheap no-op when already current; failures
    /// are ignored (the app keeps using the cached/bundled model). Call
    /// on launch — the next capture picks up any freshly cached model.
    func refreshBeatModelInBackground() {
        guard let baseURL = backendBaseURL else { return }
        Task.detached(priority: .background) {
            _ = try? await BeatModelClient().updateIfAvailable(baseURL: baseURL)
        }
    }

    /// Record a user correction of a detected hit (training data). When
    /// the user has opted in, the queued corrections upload to the
    /// backend and clear locally on success.
    func logBeatCorrection(hit: DetectedHit, corrected: DrumRole) {
        beatTrainingStore.log(
            features: hit.features,
            original: hit.role,
            corrected: corrected
        )
        guard BeatTrainingStore.shareOptIn, let baseURL = backendBaseURL else {
            return
        }
        let store = beatTrainingStore
        Task { await store.flush(baseURL: baseURL) }
    }
}
