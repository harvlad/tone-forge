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
import os
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
        /// Learned map: MIDI note number -> sample-pad index (0..<16).
        /// Covers controllers whose pads are NOT a contiguous note run —
        /// the Pioneer DJM-S7's two 8-pad decks, TE boxes, LPD8 custom
        /// programs. Built by the Settings "map controller pads" flow;
        /// unmapped notes are ignored (they don't fall through to the
        /// synth — half-mapped hardware spraying synth notes mid-jam
        /// would read as a haunted instrument).
        case mappedPads(map: [Int: Int])
    }

    /// General MIDI: pad boxes (LPD8/MPD) default their bottom-left pad to
    /// note 36 (C1). Sample pad idx = note − 36.
    public static let defaultPadBaseNote = 36

    /// Live routing. AppState drives this from the persisted setting.
    /// Mirrored into a lock box on every set so the RECEIVE THREAD can
    /// resolve pad cells for the fast press/release taps (D-038)
    /// without touching the main actor.
    @Published public var noteRouting: NoteRouting = .synth {
        didSet {
            let routing = noteRouting
            fastRoutingBox.withLock { $0 = routing }
        }
    }
    private let fastRoutingBox =
        OSAllocatedUnfairLock<NoteRouting>(initialState: .synth)

    /// RECEIVE-THREAD pad tap (D-038): fires the instant a routed pad
    /// note decodes — press AND release, with the receive-thread
    /// clocks — before the main hop, so MIDI-Learn pads ride the same
    /// fast press/release engine as the Launchpad hardware. Audio-only;
    /// the stamped event still follows on main through onContribution →
    /// launchpad.padDown/padUp with all bookkeeping.
    private let fastPadBox = OSAllocatedUnfairLock<
        (@Sendable (LaunchpadPad, _ down: Bool, _ songSeconds: Double, _ hostTime: UInt64) -> Void)?
    >(initialState: nil)

    /// Install (or clear) the receive-thread pad tap.
    public func setFastPadTap(
        _ tap: (@Sendable (LaunchpadPad, Bool, Double, UInt64) -> Void)?
    ) {
        fastPadBox.withLock { $0 = tap }
    }

    /// Note → contribution-convention grid cell for the current routing
    /// (nil = the note doesn't drive a pad: synth routing, out-of-range
    /// samplePads note, unmapped mappedPads note — dropped by design).
    /// Pure and nonisolated so the receive thread and `noteEvent` share
    /// one mapping — a drifted twin here would fast-fire one pad and
    /// main-trigger another.
    nonisolated static func padCell(
        note: Int, routing: NoteRouting
    ) -> (row: Int, col: Int)? {
        let idx: Int
        switch routing {
        case .synth:
            return nil
        case .samplePads(let baseNote):
            idx = note - baseNote
        case .mappedPads(let map):
            guard let mapped = map[note] else { return nil }
            idx = mapped
        }
        guard (0..<16).contains(idx) else { return nil }
        // Pack quadrant mapping (ModeCoordinator.sampleQuadrantContent):
        // pad idx N → grid row 8 - N/4, col N%4 + 1.
        return (row: 8 - idx / 4, col: idx % 4 + 1)
    }

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
    /// Raw note tap for the MIDI-Learn flow: fires for EVERY incoming
    /// note-on before routing, regardless of the routing mode, so the
    /// learn sheet can capture a controller's actual note numbers.
    /// nil (the default) costs nothing.
    public var onLearnNote: ((Int) -> Void)?

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

    /// Port-family fragment shared by ALL of the MK3's USB interfaces
    /// ("LPProMK3 DAW" / "LPProMK3 MIDI" / "LPProMK3 DIN"), served
    /// from the device's USB string descriptor — present at every
    /// enumeration stage, unlike the device-decorated display name.
    static let launchpadPortFamily = "LPProMK3"

    /// ANY Launchpad Pro MK3 interface — the grid is owned by
    /// USBLaunchpadTransport, so every one of the device's ports is
    /// excluded here; a connection would double-fire its notes.
    ///
    /// The old check (exact MIDI-port name OR device-decorated display
    /// name) was leaky: the MK3 exposes THREE interfaces, and CoreMIDI
    /// resolves display names LATE during a plug-in burst (D-031:
    /// "LPProMK3 MIDI" only later becomes "Launchpad Pro MK3 LPProMK3
    /// MIDI"). In that window the DAW/DIN interfaces matched neither
    /// branch, this transport connected to them, and — with the
    /// default `.synth` routing — every grid press ALSO voiced a
    /// wavetable note over its pad loop ("dual pad mapping", D-034).
    /// The port-family fragment matches all three ports in both name
    /// states, so pad hardware has exactly ONE delivery authority.
    private static func isLaunchpad(_ endpoint: MIDIEndpoint) -> Bool {
        endpoint.name.contains(launchpadPortFamily)
            || endpoint.displayName.contains(launchpadPortFamily)
            || endpoint.displayName.contains(LaunchpadProMK3Protocol.deviceNameFragment)
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
        let fastPadBox = self.fastPadBox
        let fastRoutingBox = self.fastRoutingBox
        for endpoint in desired {
            let connected = midi.connectInput(endpoint) { [weak self] messages, packetHostTime in
                // MIDI receive thread: stamp BEFORE the hop.
                let now = nowProvider()
                let hostTime = packetHostTime != 0 ? packetHostTime : now.host
                // Fast pad tap (D-038), STILL on the receive thread:
                // routed pad notes fire/release through the fast-press
                // engine within a render quantum; the stamped event
                // follows on main with all bookkeeping.
                if let tap = fastPadBox.withLock({ $0 }) {
                    let routing = fastRoutingBox.withLock { $0 }
                    for message in messages {
                        let note: UInt8, down: Bool
                        switch message {
                        case .noteOn(_, let n, let v) where v > 0:
                            note = n; down = true
                        case .noteOn(_, let n, _), .noteOff(_, let n, _):
                            note = n; down = false
                        default:
                            continue
                        }
                        if let cell = Self.padCell(note: Int(note), routing: routing),
                           let pad = PadEventMapping.launchpadPad(row: cell.row, col: cell.col) {
                            tap(pad, down, now.song, hostTime)
                        }
                    }
                }
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
        if on, let learn = onLearnNote {
            learn(Int(note))
        }
        let kind: ContributionEvent.Kind
        switch noteRouting {
        case .synth:
            kind = .midiNote(note: Int(note), velocity: Int(velocity), on: on)
        case .samplePads, .mappedPads:
            // Same pure mapping the receive-thread fast tap uses
            // (padCell) — one twin, so both lanes always name the same
            // grid cell. Unroutable notes are dropped by design.
            guard let cell = Self.padCell(note: Int(note), routing: noteRouting)
            else { return }
            kind = on ? .padDown(row: cell.row, col: cell.col)
                      : .padUp(row: cell.row, col: cell.col)
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
