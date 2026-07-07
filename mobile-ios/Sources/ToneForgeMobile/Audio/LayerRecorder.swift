// LayerRecorder.swift
//
// Captures the user's live contribution as a stream of `LayerEvent`s,
// keyed to song-time (not wall-time). The recording model is:
//
//   arm()   →   state=.armed. Nothing captured yet. On the next event,
//               capture starts and the recorder transitions to .recording.
//               This lets the user hit Record before Play so the very
//               first pad hit is included.
//
//   append(_) → in .armed OR .recording: buffer the event, track
//               durationSec = max(durationSec, event.songTimeSec).
//
//   stop()  →   .idle again. Returns the finalized LayerTimeline (nil
//               if no events were captured). AppState routes that to
//               LayerStore.save.
//
// Recorder is intentionally state-driven + AVFoundation-free. The
// SampleScheduler pipes its `onEvent` closure into `append(_:)`; the
// PadSynth trigger paths do the same from AppState. Because we record
// events, not audio, this file has no timing/render concerns — it's
// pure bookkeeping.

import Foundation
import Combine
import ToneForgeEngine

@MainActor
public final class LayerRecorder: ObservableObject {

    public enum State: Equatable {
        /// No recording in progress.
        case idle
        /// User hit Record. Waiting for the first captured event.
        case armed
        /// Actively capturing events into `buffer`.
        case recording
    }

    // MARK: - Published state

    @Published public private(set) var state: State = .idle
    /// Live event count so the UI can badge the Record button
    /// ("● 12 events" style, if we want it).
    @Published public private(set) var eventCount: Int = 0

    // MARK: - Private

    private let store: LayerStore

    /// Buffer of captured events, in arrival order. Sorted at stop()
    /// so any out-of-order arrivals from concurrent triggers still
    /// produce a monotone timeline.
    private var buffer: [LayerEvent] = []

    /// Snapshot of song context at the moment the recording started
    /// capturing (first event). Used to build the final LayerTimeline
    /// even if the pack was swapped mid-recording (per-event
    /// packIdOverride still records the swap).
    private var analysisId: String?
    private var activePackIdAtStart: String?
    /// Sketch metadata snapshotted at arm-time (nil for song layers).
    private var sketchTempoBpm: Double?
    private var sketchTimeSigNumerator: Int?
    private var packName: String?

    /// The largest songTimeSec seen so far — used as durationSec on
    /// stop(). Grows monotonically inside a recording.
    private var maxSongTime: Double = 0

    public init(store: LayerStore) {
        self.store = store
    }

    // MARK: - Lifecycle

    /// Arm the recorder for a specific song. `activePackId` is the
    /// pack loaded at arm-time; if the user swaps packs mid-recording
    /// each event's `packIdOverride` records what was actually played.
    /// The sketch parameters are snapshotted into the timeline's
    /// optional metadata for song-less (`__sketch__`) takes; song
    /// arms leave them nil.
    public func arm(
        analysisId: String,
        activePackId: String?,
        sketchTempoBpm: Double? = nil,
        sketchTimeSigNumerator: Int? = nil,
        packName: String? = nil
    ) {
        guard state == .idle else { return }
        self.analysisId = analysisId
        self.activePackIdAtStart = activePackId
        self.sketchTempoBpm = sketchTempoBpm
        self.sketchTimeSigNumerator = sketchTimeSigNumerator
        self.packName = packName
        self.buffer = []
        self.eventCount = 0
        self.maxSongTime = 0
        self.state = .armed
    }

    /// Ingest a live event. No-op when idle. Transitions armed →
    /// recording on the first event.
    public func append(_ event: LayerEvent) {
        switch state {
        case .idle:
            return
        case .armed:
            state = .recording
        case .recording:
            break
        }
        buffer.append(event)
        eventCount = buffer.count
        if event.songTimeSec > maxSongTime {
            maxSongTime = event.songTimeSec
        }
    }

    /// Stop capture + freeze the buffer into a `LayerTimeline`. Returns
    /// nil when nothing was captured (armed → stop with no events).
    /// The caller (AppState) decides whether to persist via LayerStore.
    @discardableResult
    public func stop() -> LayerTimeline? {
        defer { reset() }
        guard state != .idle, !buffer.isEmpty, let analysisId = analysisId else {
            return nil
        }
        let sorted = buffer.sorted { $0.songTimeSec < $1.songTimeSec }
        return LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: analysisId,
            name: LayerRecorder.defaultName(),
            createdAtEpoch: Date().timeIntervalSince1970,
            durationSec: maxSongTime,
            events: sorted,
            activePackId: activePackIdAtStart,
            sketchTempoBpm: sketchTempoBpm,
            sketchTimeSigNumerator: sketchTimeSigNumerator,
            packName: packName
        )
    }

    /// Abandon a recording without producing a timeline. Used when the
    /// user disarms without wanting to save.
    public func cancel() {
        reset()
    }

    private func reset() {
        state = .idle
        buffer = []
        eventCount = 0
        maxSongTime = 0
        analysisId = nil
        activePackIdAtStart = nil
        sketchTempoBpm = nil
        sketchTimeSigNumerator = nil
        packName = nil
    }

    /// Convenience: `stop()` + persist. Returns the saved timeline or
    /// nil if nothing was captured. Throws on disk errors.
    @discardableResult
    public func stopAndSave() throws -> LayerTimeline? {
        guard let timeline = stop() else { return nil }
        try store.save(timeline)
        return timeline
    }

    // MARK: - Convenience

    private static func defaultName() -> String {
        let df = DateFormatter()
        df.dateFormat = "'Layer' yyyy-MM-dd HH:mm"
        return df.string(from: Date())
    }
}
