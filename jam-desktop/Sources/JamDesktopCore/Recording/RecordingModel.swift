// RecordingModel.swift
//
// Coordinator for layer recording (P4): owns the engine's
// SessionCaptureRecorder (bus capture), SessionPlayer (bus replay)
// and the desktop SessionStore (JSON persistence), and exposes the
// recordings list to the UI.
//
// Desktop capture semantics (differs from iOS on purpose):
//   - appMode is always `.sample` — desktop pads are the Launchpad
//     chop grid, which ModeRouter routes as sample pads.
//   - padMapping is EMPTY. Desktop chop pads cannot be represented
//     as PadSampleReference (frozen wire enum: packPad / localSample
//     / sequence); instead the grid is deterministic from the song
//     bundle + chop edits, so replay and bounce resolve pads against
//     the current grid for the same songBackendId.
//
// The recorder and player are ObservableObjects (engine types) —
// views observe them via @ObservedObject; this model is @Observable
// for the list/replay state it owns.

import Foundation
import Observation
import ToneForgeEngine

@Observable
@MainActor
public final class RecordingModel {

    /// Bus capture state machine (idle → armed → recording).
    @ObservationIgnored public let recorder: SessionCaptureRecorder
    /// Bus replay pump (re-fires events with isReplay = true).
    @ObservationIgnored public let player: SessionPlayer

    /// Saved takes, newest first. Refreshed after stop/delete and on
    /// demand (list sheet appear).
    public private(set) var recordings: [SessionCapture] = []
    /// Session currently replaying, nil when idle.
    public private(set) var replayingSessionId: UUID?
    public var lastError: String?

    @ObservationIgnored private let store: SessionStore

    public init(
        bus: ContributionEventBus,
        clockNow: @escaping () -> Double,
        store: SessionStore = SessionStore()
    ) {
        self.store = store
        self.recorder = SessionCaptureRecorder(bus: bus, clockNow: clockNow)
        self.player = SessionPlayer(bus: bus, clockNow: clockNow)
        recorder.onAutosave = { [weak self] session in
            self?.persist(session)
        }
    }

    // MARK: - Recording

    /// Arm capture for the attached song (nil = song-less take).
    public func arm(songBackendId: String?, tempoBpm: Double?) {
        recorder.arm(
            songBackendId: songBackendId,
            appMode: .sample,
            tempoBpm: tempoBpm,
            padMapping: [:]
        )
    }

    /// Stop and persist the take (the recorder's final autosave
    /// callback saves it). Returns nil for an empty take.
    @discardableResult
    public func stopRecording() -> SessionCapture? {
        let session = recorder.stop()
        refresh()
        return session
    }

    public func cancelRecording() {
        recorder.cancel()
    }

    // MARK: - Library

    public func refresh() {
        recordings = store.list()
    }

    public func delete(_ session: SessionCapture) {
        if replayingSessionId == session.sessionId { stopReplay() }
        do {
            try store.delete(sessionId: session.sessionId)
        } catch {
            lastError = error.localizedDescription
        }
        refresh()
    }

    // MARK: - Replay

    /// Load a take into the bus replayer and start its pump. The
    /// caller aligns the transport (events fire when song time
    /// reaches their timestamps).
    public func startReplay(_ session: SessionCapture) {
        player.load(session)
        player.start()
        replayingSessionId = session.sessionId
    }

    public func stopReplay() {
        player.clear()
        replayingSessionId = nil
    }

    // MARK: - Private

    private func persist(_ session: SessionCapture) {
        do {
            try store.save(session)
        } catch {
            lastError = error.localizedDescription
        }
    }
}
