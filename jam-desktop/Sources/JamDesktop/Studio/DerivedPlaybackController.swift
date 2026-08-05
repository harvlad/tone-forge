// DerivedPlaybackController.swift
//
// Plays a session's DERIVED music — transcribed per-stem note events
// and/or chord-timeline pads — on the existing DesktopSynthNode. No
// original audio: this is the audible-evaluation surface for what the
// analysis pipeline stored (Studio "Derived Audio" section).
//
// Scheduling: one Task walks a merged, time-sorted event list and
// sleeps to each deadline; cancellation sends allNotesOff. Timing uses
// ContinuousClock so it doesn't drift with UI work. Chord pads reuse
// the note path (voicings come pre-baked from the server).

import Foundation
import JamDesktopCore

@MainActor
final class DerivedPlaybackController: ObservableObject {
    enum Mode: Equatable {
        case notes(role: String)
        case chords
        case both(role: String)
        case all            // every melodic stem together (no drums)
        case allWithChords
    }

    @Published private(set) var playingMode: Mode?
    @Published private(set) var status: String = ""

    private weak var session: SessionController?
    private var task: Task<Void, Never>?

    func bind(session: SessionController) {
        self.session = session
    }

    func stop() {
        task?.cancel()
        task = nil
        playingMode = nil
        session?.synthNode.allNotesOff()
    }

    /// Roles merged by .all — GM drum-map pitches through a melodic
    /// synth are noise, so drums stay out of the ensemble.
    static let ensembleRoles: Set<String> = ["bass", "guitar", "piano",
                                             "other", "vocals", "keys"]

    func play(_ mode: Mode, derived: DerivedAudio, from startSec: Double = 0,
              ensemble: [String: DerivedAudio] = [:]) {
        stop()
        guard let session else { return }
        // Studio can be the first surface the user touches — the audio
        // engine (and the synth's attach) only happens on engine start,
        // so a cold app played derived audio into an unattached synth:
        // silence. Start it on demand.
        session.ensureEngineStarted()

        // Merge into (time, isOn, pitch, velocity) events.
        var events: [(t: Double, on: Bool, pitch: Int, vel: Float)] = []
        func addNotes(_ notes: [DerivedNote], velocityScale: Float) {
            for n in notes where n.offset > startSec {
                events.append((max(n.onset, startSec), true, n.pitch,
                               Float(n.velocity) / 127.0 * velocityScale))
                events.append((n.offset, false, n.pitch, 0))
            }
        }
        func addChords(_ chords: [DerivedChord], velocityScale: Float) {
            for c in chords where c.end > startSec && !c.midiNotes.isEmpty {
                for p in c.midiNotes {
                    events.append((max(c.start, startSec), true, p, 0.45 * velocityScale))
                    events.append((max(c.end - 0.02, startSec), false, p, 0))
                }
            }
        }
        switch mode {
        case .notes:
            addNotes(derived.notes, velocityScale: 1.0)
        case .chords:
            addChords(derived.chords, velocityScale: 1.0)
        case .both:
            addNotes(derived.notes, velocityScale: 1.0)
            addChords(derived.chords, velocityScale: 0.6)  // pads behind the part
        case .all, .allWithChords:
            for (role, d) in ensemble
            where Self.ensembleRoles.contains(role) {
                addNotes(d.notes, velocityScale: 0.8)
            }
            if case .allWithChords = mode {
                addChords(derived.chords, velocityScale: 0.5)
            }
        }
        guard !events.isEmpty else {
            status = "nothing to play"
            return
        }
        events.sort { $0.t < $1.t || ($0.t == $1.t && !$0.on && $1.on) }

        playingMode = mode
        status = "playing \(events.count / 2) events"
        let origin = ContinuousClock.now
        let synthNode = session.synthNode
        // DETACHED, not Task {}: a child task here inherits MainActor, which
        // put every sleep+noteOn on the UI thread — any main-thread hitch
        // stalled the loop, then all past-deadline events fired back-to-back
        // (the intermittent "machine-gun burst" heard in Derived Audio).
        // The timing loop must never share the UI thread. Tolerance .zero
        // opts out of system timer coalescing (default tolerance permits
        // tens of ms of lateness for power savings). Each note event is
        // dispatched fire-and-forget to the MainActor synth wrapper — a UI
        // hitch can then delay emission by at most the hitch itself, and
        // late events no longer cascade.
        task = Task.detached(priority: .userInitiated) { [weak self] in
            for e in events {
                if Task.isCancelled { break }
                let deadline = origin.advanced(
                    by: .seconds(max(0, e.t - startSec)))
                do {
                    try await Task.sleep(until: deadline, tolerance: .zero,
                                         clock: .continuous)
                } catch { break }  // cancelled
                Task { @MainActor in
                    if e.on {
                        synthNode.noteOn(midi: e.pitch, velocity: e.vel)
                    } else {
                        synthNode.noteOff(midi: e.pitch)
                    }
                }
            }
            await MainActor.run { [weak self] in
                guard let self, !(self.task?.isCancelled ?? true) else { return }
                self.playingMode = nil
                self.status = "done"
                self.session?.synthNode.allNotesOff()
            }
        }
    }
}
