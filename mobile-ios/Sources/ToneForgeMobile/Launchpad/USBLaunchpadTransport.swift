// USBLaunchpadTransport.swift
//
// LaunchpadTransport for a USB-attached Launchpad Pro [MK3]. Wraps a
// MIDIInterface (CoreMIDI in the app, FakeMIDIInterface in tests) and
// the pure LaunchpadProMK3Protocol byte layer.
//
// Responsibilities:
//   - Hot-plug: rescans on every MIDI setup change, matching the
//     device's "LPProMK3 MIDI" interface (Programmer-mode I/O — the
//     DAW and DIN interfaces are ignored).
//   - Mode: sends Programmer Mode SysEx on connect / `resume()`, Live
//     Mode SysEx on `suspend()` (app backgrounding, P7) so the
//     standalone device returns to normal when we let go.
//   - Input: Note On ch1 vel>0 → padDown, Note Off or vel-0 → padUp.
//     Events are stamped (song-seconds + hostTime) ON the MIDI thread
//     BEFORE the main-actor hop, per the ContributionEvent contract,
//     then published via `onContribution`.
//   - LEDs: LaunchpadLight frames are diffed against a cache and sent
//     as batched RGB SysEx — a full 64-pad redraw is ONE message.
//   - Underpower heuristic: unpowered hubs brown the device out,
//     which shows up as connection flapping or send errors. Either
//     raises `underpowerSuspected` for a UI banner.
//
// @preconcurrency: LaunchpadTransport is a nonisolated protocol (see
// OnScreenLaunchpadTransport for the precedent); every conforming
// member here is main-actor.

import Foundation
import ToneForgeEngine

@MainActor
public final class USBLaunchpadTransport: ObservableObject, @preconcurrency LaunchpadTransport {

    // MARK: - Published state

    @Published public private(set) var connectionState: LaunchpadConnectionState = .notConnected
    /// ≥3 connection flaps inside 10 s, or a send error while online.
    /// Dismissible: the banner clears it; the next flap re-raises it.
    @Published public var underpowerSuspected = false

    // MARK: - Callbacks

    public var onPadDown: ((LaunchpadPad) -> Void)?
    public var onPadUp: ((LaunchpadPad) -> Void)?
    /// The bus path: AppState wires this to `contributionBus.publish`.
    /// Events arrive pre-stamped from the MIDI thread.
    public var onContribution: ((ContributionEvent) -> Void)?
    /// Outer-button presses (Bool = down). Unmapped in v2 beyond
    /// logging hooks; P7 lifecycle may bind Shift/Stop actions.
    public var onControlButton: ((LaunchpadProMK3Protocol.ControlButton, Bool) -> Void)?

    // MARK: - Private

    private let midi: any MIDIInterface
    /// Stamps (song-seconds, mach host ticks) — called on the MIDI
    /// thread, so it must be @Sendable (TransportClock.nowSongSeconds
    /// is lock-protected and nonisolated).
    private let nowProvider: @Sendable () -> (song: Double, host: UInt64)
    private let dateProvider: () -> Date

    private var inputEndpoint: MIDIEndpoint?
    private var outputEndpoint: MIDIEndpoint?
    /// PadIndex.rawValue → last light sent, for diffing.
    private var ledCache: [Int: LaunchpadLight] = [:]
    /// Recent successful connects, for flap detection.
    private var connectTimes: [Date] = []
    /// True between `suspend()` and `resume()` — LEDs are cached but
    /// not sent, and the device stays in Live Mode.
    private var suspended = false

    public init(
        midi: any MIDIInterface,
        nowProvider: @escaping @Sendable () -> (song: Double, host: UInt64),
        dateProvider: @escaping () -> Date = Date.init
    ) {
        self.midi = midi
        self.nowProvider = nowProvider
        self.dateProvider = dateProvider
        midi.onSetupChanged = { [weak self] in
            self?.rescan()
        }
        rescan()
    }

    // MARK: - Hot-plug

    /// Match the Programmer-mode interface: endpoint name
    /// "LPProMK3 MIDI" (PDF p.6, hardware-verified), falling back to
    /// the display name for hosts that decorate endpoint names.
    private static func isLaunchpadMIDIPort(_ endpoint: MIDIEndpoint) -> Bool {
        if endpoint.name == LaunchpadProMK3Protocol.midiPortName { return true }
        return endpoint.displayName.contains(LaunchpadProMK3Protocol.deviceNameFragment)
            && endpoint.displayName.hasSuffix("MIDI")
    }

    private func rescan() {
        let source = midi.sources().first(where: Self.isLaunchpadMIDIPort)
        let destination = midi.destinations().first(where: Self.isLaunchpadMIDIPort)

        switch (source, destination) {
        case (let source?, let destination?):
            guard inputEndpoint != source || outputEndpoint != destination else {
                return  // already connected to this device
            }
            if inputEndpoint != nil { disconnect() }
            connect(source: source, destination: destination)
        default:
            if inputEndpoint != nil || outputEndpoint != nil {
                disconnect()
                noteFlap()
            }
        }
    }

    private func connect(source: MIDIEndpoint, destination: MIDIEndpoint) {
        let nowProvider = self.nowProvider
        let connected = midi.connectInput(source) { [weak self] messages, packetHostTime in
            // MIDI receive thread: stamp BEFORE the hop.
            let now = nowProvider()
            let hostTime = packetHostTime != 0 ? packetHostTime : now.host
            DispatchQueue.main.async {
                self?.deliver(messages, songSeconds: now.song, hostTime: hostTime)
            }
        }
        guard connected else {
            noteFlap()
            return
        }
        inputEndpoint = source
        outputEndpoint = destination
        connectionState = .connected(deviceName: LaunchpadProMK3Protocol.deviceNameFragment)
        noteFlap()

        if suspended {
            sendChecked(LaunchpadProMK3Protocol.enterLiveMode)
        } else {
            sendChecked(LaunchpadProMK3Protocol.enterProgrammerMode)
            redrawAll()
        }
    }

    private func disconnect() {
        // Best-effort: hand the device back to its standalone modes.
        // Skipped when it's already gone (send would just fail).
        if let out = outputEndpoint, midi.sources().contains(where: { $0 == inputEndpoint }) {
            midi.send(LaunchpadProMK3Protocol.enterLiveMode, to: out)
        }
        midi.disconnectAll()
        inputEndpoint = nil
        outputEndpoint = nil
        connectionState = .notConnected
    }

    // MARK: - Underpower heuristic

    private func noteFlap() {
        let now = dateProvider()
        connectTimes.append(now)
        connectTimes.removeAll { now.timeIntervalSince($0) > 10 }
        if connectTimes.count >= 3 {
            underpowerSuspected = true
        }
    }

    @discardableResult
    private func sendChecked(_ sysex: [UInt8]) -> Bool {
        guard let out = outputEndpoint else { return false }
        let ok = midi.send(sysex, to: out)
        if !ok { underpowerSuspected = true }
        return ok
    }

    // MARK: - Input

    private func deliver(
        _ messages: [MIDIMessage], songSeconds: Double, hostTime: UInt64
    ) {
        for message in messages {
            switch message {
            case .noteOn(0, let note, let velocity) where velocity > 0:
                padEvent(note: note, down: true,
                         velocity: Double(velocity) / 127.0,
                         songSeconds: songSeconds, hostTime: hostTime)
            case .noteOn(0, let note, _),        // vel-0 release (PDF p.6)
                 .noteOff(0, let note, _):
                padEvent(note: note, down: false, velocity: 0,
                         songSeconds: songSeconds, hostTime: hostTime)
            case .controlChange(0, let cc, let value):
                if let button = LaunchpadProMK3Protocol.controlButton(forCC: cc) {
                    onControlButton?(button, value > 0)
                }
            default:
                break
            }
        }
    }

    private func padEvent(
        note: UInt8, down: Bool, velocity: Double,
        songSeconds: Double, hostTime: UInt64
    ) {
        guard let pad = LaunchpadProMK3Protocol.padIndex(forNote: note) else { return }
        onContribution?(ContributionEvent(
            source: .launchpad,
            kind: down ? .padDown(row: pad.row, col: pad.col)
                       : .padUp(row: pad.row, col: pad.col),
            timestamp: songSeconds,
            hostTime: hostTime,
            velocity: down ? velocity : 1.0
        ))
        // Legacy LaunchpadPad callbacks (row 0 = top).
        let legacy = LaunchpadPad(row: 8 - pad.row, col: pad.col - 1)
        (down ? onPadDown : onPadUp)?(legacy)
    }

    // MARK: - LEDs

    public func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
        setLights([pad: light])
    }

    public func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
        var specs: [LaunchpadProMK3Protocol.ColorSpec] = []
        for (pad, light) in frame {
            let index = PadIndex.at(row: 8 - pad.row, col: pad.col + 1)
            guard index.isValid, ledCache[index.rawValue] != light else { continue }
            ledCache[index.rawValue] = light
            specs.append(spec(for: light, at: index))
        }
        flush(specs)
    }

    public func clearLights() {
        var specs: [LaunchpadProMK3Protocol.ColorSpec] = []
        for row in 1...8 {
            for col in 1...8 {
                let index = PadIndex.at(row: row, col: col)
                if ledCache[index.rawValue] != LaunchpadLight.off {
                    ledCache[index.rawValue] = .off
                    specs.append(.rgb(pad: index, colorHint: 0))
                }
            }
        }
        flush(specs)
    }

    private func spec(
        for light: LaunchpadLight, at index: PadIndex
    ) -> LaunchpadProMK3Protocol.ColorSpec {
        switch light {
        case .off:
            return .rgb(pad: index, colorHint: 0)
        case .solid(let hint):
            return .rgb(pad: index, colorHint: hint)
        case .pulse(let hint):
            // Pulse requires a palette entry (PDF p.12) — nearest of
            // the PDF-cited anchors.
            return .pulse(
                pad: index,
                palette: LaunchpadProMK3Protocol.nearestPaletteEntry(colorHint: hint)
            )
        }
    }

    private func flush(_ specs: [LaunchpadProMK3Protocol.ColorSpec]) {
        guard !specs.isEmpty, !suspended, outputEndpoint != nil else { return }
        for message in LaunchpadProMK3Protocol.ledMessages(specs) {
            sendChecked(message)
        }
    }

    /// Repaint every pad from the cache — one SysEx message. Used on
    /// connect and `resume()` so the hardware matches the app state.
    private func redrawAll() {
        var specs: [LaunchpadProMK3Protocol.ColorSpec] = []
        for row in 1...8 {
            for col in 1...8 {
                let index = PadIndex.at(row: row, col: col)
                specs.append(spec(for: ledCache[index.rawValue] ?? .off, at: index))
            }
        }
        guard outputEndpoint != nil, !suspended else { return }
        for message in LaunchpadProMK3Protocol.ledMessages(specs) {
            sendChecked(message)
        }
    }

    // MARK: - Lifecycle (P7)

    /// App going to background: hand the hardware back to Live Mode.
    /// The LED cache is retained so `resume()` restores the frame.
    public func suspend() {
        guard !suspended else { return }
        suspended = true
        sendChecked(LaunchpadProMK3Protocol.enterLiveMode)
    }

    /// App returning to foreground: reclaim Programmer Mode and
    /// resync every LED.
    public func resume() {
        guard suspended else { return }
        suspended = false
        guard outputEndpoint != nil else { return }
        sendChecked(LaunchpadProMK3Protocol.enterProgrammerMode)
        redrawAll()
    }
}
