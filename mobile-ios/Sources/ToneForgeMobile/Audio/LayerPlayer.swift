// LayerPlayer.swift
//
// Replays a `LayerTimeline` against the current transport. Uses the
// same trigger paths as live user input (SampleScheduler + PadSynth)
// so a replayed layer sounds identical to the live performance — no
// audio recording is stored, only the event stream.
//
// Playback model:
//   - `play(timeline:)`   remembers the timeline, resets the cursor to
//                         the first event ≥ current song-time, and
//                         starts a 60 Hz tick that dispatches events
//                         as the transport advances past their
//                         `songTimeSec`.
//   - `stop()`            halts the tick + drops the cursor.
//   - `seek(to:)`         recomputes cursor as first index with
//                         `songTimeSec ≥ target`; skipped noteOn/sampleOn
//                         events don't retro-trigger.
//
// Held/looped voices from the live take don't survive across replay
// (we'd need to record voice ids to do that). Layers are meant for
// discrete pad hits + short one-shots, which cover ~99% of what users
// actually play.

import Foundation
import Combine
import ToneForgeEngine

@MainActor
public final class LayerPlayer: ObservableObject {

    public enum State: Equatable {
        case idle
        case playing
    }

    @Published public private(set) var state: State = .idle
    /// Which layers are currently armed for replay. A layer plays when
    /// present here AND transport is playing. Mixer solos/mutes live
    /// on the layer bus, so no per-layer gain is needed here.
    @Published public private(set) var activeLayerIds: Set<String> = []

    // MARK: - Private

    private struct Cursor {
        let timeline: LayerTimeline
        var nextIndex: Int
    }

    /// One cursor per active layer. Keyed by `layerId`.
    private var cursors: [String: Cursor] = [:]

    private let clockNow: () -> Double
    /// Sample callbacks carry the pack the event was recorded on:
    /// `event.params.packIdOverride`, falling back to the timeline's
    /// `activePackId` for pre-multi-pack recordings. nil means "let
    /// the scheduler use its active pack" (legacy timelines with no
    /// pack info at all).
    private let onSampleOn: (Int, String?) -> Void
    private let onSampleOff: (Int, String?) -> Void
    private let onNoteOn: (Int, Double) -> Void
    private let onNoteOff: (Int) -> Void

    private var tickTimer: Timer?

    /// Wire the player to the live trigger paths. The four callbacks
    /// are kept as plain closures so this class stays test-friendly
    /// (SampleScheduler + PadSynth pull in AVFoundation).
    public init(
        clockNow: @escaping () -> Double,
        onSampleOn: @escaping (Int, String?) -> Void,
        onSampleOff: @escaping (Int, String?) -> Void,
        onNoteOn: @escaping (Int, Double) -> Void,
        onNoteOff: @escaping (Int) -> Void
    ) {
        self.clockNow = clockNow
        self.onSampleOn = onSampleOn
        self.onSampleOff = onSampleOff
        self.onNoteOn = onNoteOn
        self.onNoteOff = onNoteOff
    }

    // MARK: - Layer registration

    /// Add a layer to the active set. If the transport is already
    /// playing, its events will start firing on the next tick.
    public func addLayer(_ timeline: LayerTimeline) {
        let start = clockNow()
        let firstIdx = timeline.events.firstIndex { $0.songTimeSec >= start } ?? timeline.events.count
        cursors[timeline.layerId] = Cursor(timeline: timeline, nextIndex: firstIdx)
        activeLayerIds.insert(timeline.layerId)
    }

    /// Remove a layer from the active set.
    public func removeLayer(layerId: String) {
        cursors.removeValue(forKey: layerId)
        activeLayerIds.remove(layerId)
    }

    /// Replace the whole active set. Handy for "play only this layer".
    public func setActive(_ timelines: [LayerTimeline]) {
        cursors.removeAll()
        activeLayerIds.removeAll()
        for t in timelines { addLayer(t) }
    }

    /// Drop every active layer.
    public func clear() {
        stop()
        cursors.removeAll()
        activeLayerIds.removeAll()
    }

    // MARK: - Playback control

    /// Start the tick loop. Idempotent.
    public func start() {
        guard state == .idle else { return }
        state = .playing
        // Rewind every cursor to current song-time (transport may have
        // jumped since the layer was added).
        seek(to: clockNow())
        tickTimer?.invalidate()
        tickTimer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            Task { @MainActor in self.tick() }
        }
    }

    /// Halt playback. Cursors are preserved so a subsequent start
    /// picks up where the transport left off.
    public func stop() {
        state = .idle
        tickTimer?.invalidate()
        tickTimer = nil
    }

    /// Rewind/advance every cursor to the first event ≥ target
    /// song-time. Called on transport seek + on start().
    public func seek(to songTime: Double) {
        for (id, cursor) in cursors {
            let idx = cursor.timeline.events.firstIndex { $0.songTimeSec >= songTime }
                ?? cursor.timeline.events.count
            cursors[id] = Cursor(timeline: cursor.timeline, nextIndex: idx)
        }
    }

    // MARK: - Tick

    private func tick() {
        let now = clockNow()
        for (id, var cursor) in cursors {
            while cursor.nextIndex < cursor.timeline.events.count,
                  cursor.timeline.events[cursor.nextIndex].songTimeSec <= now {
                dispatch(
                    cursor.timeline.events[cursor.nextIndex],
                    fallbackPackId: cursor.timeline.activePackId
                )
                cursor.nextIndex += 1
            }
            cursors[id] = cursor
        }
    }

    /// Test-only: drive one tick synchronously. Production code uses
    /// the internal 60 Hz Timer instead.
    internal func tickForTests() {
        tick()
    }

    private func dispatch(_ event: LayerEvent, fallbackPackId: String?) {
        switch event.kind {
        case .sampleOn:
            if let padIdx = event.params.padIdx {
                onSampleOn(padIdx, event.params.packIdOverride ?? fallbackPackId)
            }
        case .sampleOff:
            if let padIdx = event.params.padIdx {
                onSampleOff(padIdx, event.params.packIdOverride ?? fallbackPackId)
            }
        case .noteOn:
            if let midi = event.params.midiNote {
                onNoteOn(midi, event.params.velocity ?? 1.0)
            }
        case .noteOff:
            if let midi = event.params.midiNote { onNoteOff(midi) }
        }
    }
}
