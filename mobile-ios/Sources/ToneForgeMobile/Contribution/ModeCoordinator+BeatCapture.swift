// ModeCoordinator+BeatCapture.swift
//
// Beat Capture (D-024): orchestration seam between the mic capture and
// the pure engine pipeline (BeatOnsetExtractor → TempoEstimator →
// BeatPatternBuilder). Resolves musical context (song tempo → estimate
// → ask user), builds an editable SequencerPattern, and logs user
// corrections as training data. Audio is analysis-only — never saved.

import Foundation
import ToneForgeEngine
import ToneForgeML

/// Outcome of analysing one beat take.
public struct BeatCaptureResult: Sendable {
    /// Editable pattern ready for the sequencer.
    public var pattern: SequencerPattern
    /// Classified hits (retained for review + correction logging).
    public let hits: [DetectedHit]
    /// Resolved BPM used to build `pattern`.
    public let bpm: Double
    /// Tempo confidence [0, 1]. 1 when following a loaded song.
    public let tempoConfidence: Double
    /// True when the estimate was too weak — UI should ask the user.
    public let needsManualTempo: Bool
    /// True when following a loaded song's tempo (no bpmOverride).
    public let songSynced: Bool

    public init(
        pattern: SequencerPattern,
        hits: [DetectedHit],
        bpm: Double,
        tempoConfidence: Double,
        needsManualTempo: Bool,
        songSynced: Bool
    ) {
        self.pattern = pattern
        self.hits = hits
        self.bpm = bpm
        self.tempoConfidence = tempoConfidence
        self.needsManualTempo = needsManualTempo
        self.songSynced = songSynced
    }
}

extension ModeCoordinator {

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

    /// Analyse a captured take into a `BeatCaptureResult`. Heavy DSP
    /// runs off the main actor.
    public func analyzeBeatTake(
        _ samples: [Float],
        quantize: BeatQuantize,
        detectKick: Bool = true
    ) async -> BeatCaptureResult {
        let sr = AudioEngine.canonicalSampleRate

        // Onset detection + classification (off-main).
        let classifier = Self.beatClassifier
        let hits = await Task.detached(priority: .userInitiated) {
            BeatOnsetExtractor.extract(
                samples, sampleRate: sr,
                classifier: classifier,
                detectKick: detectKick
            )
        }.value

        // Resolve tempo: song → estimate → manual.
        let songBPM = app.currentBundle?.meta.tempoBpm
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
                bpm = est.bpm > 0 ? est.bpm : app.sketchSettings.tempoBpm
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
    public func buildBeatPattern(
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
    public func commitBeatPattern(_ pattern: SequencerPattern) -> UUID {
        app.sequencerPatternStore.save(pattern)
        return pattern.id
    }

    /// Check the backend for a newer drum-classifier model and download
    /// it in the background. Cheap no-op when already current; failures
    /// are ignored (the app keeps using the cached/bundled model). Call
    /// on launch — the next capture picks up any freshly cached model.
    public func refreshBeatModelInBackground() {
        let baseURL = app.backendBaseURL
        Task.detached(priority: .background) {
            _ = try? await BeatModelClient().updateIfAvailable(baseURL: baseURL)
        }
    }

    /// Record a user correction of a detected hit (training data). When
    /// the user has opted in, the queued corrections upload to the
    /// backend and clear locally on success.
    public func logBeatCorrection(hit: DetectedHit, corrected: DrumRole) {
        app.beatTrainingStore.log(
            features: hit.features,
            original: hit.role,
            corrected: corrected
        )
        guard BeatTrainingStore.shareOptIn else { return }
        let store = app.beatTrainingStore
        let baseURL = app.backendBaseURL
        Task { await store.flush(baseURL: baseURL) }
    }
}
