// MIDIKeyboardTransport.swift
//
// Input-only transport for generic MIDI note controllers — USB/BT/
// network keyboards and pad boxes (Akai LPD8/MPD, Arturia KeyLab,
// M-Audio, etc.). Wraps a MIDIInterface (CoreMIDI in the app,
// FakeMIDIInterface in tests) and turns Note On/Off into
// ContributionEvent.midiNote, which ModeRouter already routes to the
// wavetable synth in every implemented mode.
//
// Scope:
//   - NOTES ONLY drive audio. Note On ch-any vel>0 → midiNote on;
//     Note Off or vel-0 → midiNote off. Channel is ignored (ModeRouter
//     keys on note number).
//   - Control Change is surfaced via `onControlChange` for callers that
//     want to map knobs/faders, but has NO audio route yet.
//   - No LED output: these devices have no host-addressable RGB grid,
//     so there is nothing to mirror padVisuals onto.
//
// Device selection: connects to EVERY source EXCEPT the Launchpad Pro
// MK3 interfaces — the MK3 grid is owned by USBLaunchpadTransport and
// would otherwise double-fire (its pad notes 11–88 would also arrive
// here as keyboard notes).
//
// Threading: mirrors USBLaunchpadTransport. The receive handler runs
// on CoreMIDI's thread and stamps (song-seconds + hostTime) BEFORE the
// main-actor hop, per the ContributionEvent contract.

import Foundation
import ToneForgeEngine

@MainActor
public final class MIDIKeyboardTransport: ObservableObject {

    // MARK: - Note routing

    /// Where incoming Note On/Off go.
    ///   - `.synth`: emit `.midiNote` (wavetable synth, ModeRouter default).
    ///   - `.samplePads(baseNote:)`: map note → sample-pad index
    ///     (`note - baseNote`, valid 0..<16) and emit `.padDown`/`.padUp`
    ///     at the pack quadrant's grid cell, so LPD8/MPD pads fire the
    ///     active sample pack instead of the synth.
    public enum NoteRouting: Sendable, Equatable {
        case synth
        case samplePads(baseNote: Int)
    }

    /// General MIDI: pad boxes (LPD8/MPD) default their bottom-left pad to
    /// note 36 (C1). Sample pad idx = note − 36.
    public static let defaultPadBaseNote = 36

    /// Live routing. AppState drives this from the persisted setting.
    @Published public var noteRouting: NoteRouting = .synth

    // MARK: - Published state

    /// Display names of every connected input (for a settings readout).
    @Published public private(set) var connectedInputs: [String] = []

    // MARK: - Callbacks

    /// The bus path: AppState wires this to `contributionBus.publish`.
    /// Events arrive pre-stamped from the MIDI thread.
    public var onContribution: ((ContributionEvent) -> Void)?
    /// Raw Control Change (channel, controller, value). Unrouted to
    /// audio today — a hook for future knob/fader mapping.
    public var onControlChange: ((UInt8, UInt8, UInt8) -> Void)?

    // MARK: - Private

    private let midi: any MIDIInterface
    /// Stamps (song-seconds, mach host ticks) — called on the MIDI
    /// thread, so it must be @Sendable.
    private let nowProvider: @Sendable () -> (song: Double, host: UInt64)

    /// Endpoints we currently receive from.
    private var connectedEndpoints: [MIDIEndpoint] = []

    public init(
        midi: any MIDIInterface,
        nowProvider: @escaping @Sendable () -> (song: Double, host: UInt64)
    ) {
        self.midi = midi
        self.nowProvider = nowProvider
        midi.onSetupChanged = { [weak self] in
            self?.rescan()
        }
        rescan()
    }

    // MARK: - Discovery

    /// A Launchpad Pro MK3 interface — owned by USBLaunchpadTransport,
    /// so excluded here to avoid double-firing its grid notes.
    private static func isLaunchpad(_ endpoint: MIDIEndpoint) -> Bool {
        if endpoint.name == LaunchpadProMK3Protocol.midiPortName { return true }
        return endpoint.displayName.contains(LaunchpadProMK3Protocol.deviceNameFragment)
    }

    private func rescan() {
        let desired = midi.sources().filter { !Self.isLaunchpad($0) }
        guard desired != connectedEndpoints else { return }

        // Coarse reconnect: this transport owns its own MIDIInterface,
        // so disconnectAll only tears down our own input ports (the
        // Launchpad's separate client is untouched).
        midi.disconnectAll()
        connectedEndpoints = []

        let nowProvider = self.nowProvider
        for endpoint in desired {
            let connected = midi.connectInput(endpoint) { [weak self] messages, packetHostTime in
                // MIDI receive thread: stamp BEFORE the hop.
                let now = nowProvider()
                let hostTime = packetHostTime != 0 ? packetHostTime : now.host
                DispatchQueue.main.async {
                    self?.deliver(messages, songSeconds: now.song, hostTime: hostTime)
                }
            }
            if connected { connectedEndpoints.append(endpoint) }
        }
        connectedInputs = connectedEndpoints.map(\.displayName)
    }

    // MARK: - Input

    private func deliver(
        _ messages: [MIDIMessage], songSeconds: Double, hostTime: UInt64
    ) {
        for message in messages {
            switch message {
            case .noteOn(_, let note, let velocity) where velocity > 0:
                noteEvent(note: note, on: true, velocity: velocity,
                          songSeconds: songSeconds, hostTime: hostTime)
            case .noteOn(_, let note, _),         // running-status vel-0 release
                 .noteOff(_, let note, _):
                noteEvent(note: note, on: false, velocity: 0,
                          songSeconds: songSeconds, hostTime: hostTime)
            case .controlChange(let channel, let controller, let value):
                onControlChange?(channel, controller, value)
            case .sysex:
                break
            }
        }
    }

    private func noteEvent(
        note: UInt8, on: Bool, velocity: UInt8,
        songSeconds: Double, hostTime: UInt64
    ) {
        let kind: ContributionEvent.Kind
        switch noteRouting {
        case .synth:
            kind = .midiNote(note: Int(note), velocity: Int(velocity), on: on)
        case .samplePads(let baseNote):
            let idx = Int(note) - baseNote
            guard (0..<16).contains(idx) else { return }
            // Pack quadrant mapping (ModeCoordinator.sampleQuadrantContent):
            // pad idx N → grid row 8 - N/4, col N%4 + 1.
            let row = 8 - idx / 4
            let col = idx % 4 + 1
            kind = on ? .padDown(row: row, col: col) : .padUp(row: row, col: col)
        }
        onContribution?(ContributionEvent(
            source: .midiKeyboard,
            kind: kind,
            timestamp: songSeconds,
            hostTime: hostTime,
            velocity: on ? Double(velocity) / 127.0 : 0
        ))
    }
}
