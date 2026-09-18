// USBLaunchpadTransport.swift
//
// LaunchpadTransport for a USB-attached Launchpad Pro [MK3], ported
// from the mobile app. Wraps a MIDIInterface (CoreMIDIInterface in
// JamDesktopAudio, FakeMIDIInterface in tests) and the pure
// LaunchpadProMK3Protocol byte layer from ToneForgeEngine.
//
// Responsibilities:
//   - Hot-plug: rescans on every MIDI setup change, matching the
//     device's "LPProMK3 MIDI" interface (Programmer-mode I/O — the
//     DAW and DIN interfaces are ignored).
//   - Mode: sends Programmer Mode SysEx on connect / `resume()`, Live
//     Mode SysEx on `suspend()` so the standalone device returns to
//     normal when we let go.
//   - Input: Note On ch1 vel>0 → padDown, Note Off or vel-0 → padUp.
//     Events are stamped (song-seconds + hostTime) ON the MIDI thread
//     BEFORE the main-actor hop, then published via `onContribution`.
//   - LEDs: LaunchpadLight frames are diffed against a cache and sent
//     as batched RGB SysEx — a full 64-pad redraw is ONE message.
//   - Underpower heuristic: unpowered hubs brown the device out,
//     which shows up as connection flapping or send errors. Either
//     raises `underpowerSuspected` for a UI banner. A SUCCESSFUL
//     connect is NOT a flap — CoreMIDI fires a burst of setup-change
//     notifications on one plug-in (device, entity, endpoint), and
//     counting the resulting connects tripped the banner on a
//     perfectly stable cable. Only a torn-down link (disconnect after
//     connect, or endpoint re-enumeration) and failed connects count,
//     and 30 s of stable connection clears the banner again.
//
// @preconcurrency: LaunchpadTransport is a nonisolated protocol;
// every conforming member here is main-actor.

import Foundation
import Observation
import os
import ToneForgeEngine

/// Desktop-side widening of the shared LaunchpadTransport seam:
/// transports that stamp pad events on the MIDI receive thread can
/// deliver the press-time clocks WITH the pad, so the controller
/// quantizes (and measures) against the true press instant instead of
/// the main-queue arrival — under UI load (full-window grid repaints)
/// the main hop alone adds 10–50 ms. When a stamped callback is set,
/// the legacy `onPadDown`/`onPadUp` closure is NOT also called.
@MainActor
public protocol StampedPadTransport: AnyObject {
    var onPadDownStamped: ((LaunchpadPad, _ songSeconds: Double, _ hostTime: UInt64) -> Void)? { get set }
    var onPadUpStamped: ((LaunchpadPad, _ songSeconds: Double, _ hostTime: UInt64) -> Void)? { get set }
}

@MainActor
@Observable
public final class USBLaunchpadTransport: @preconcurrency LaunchpadTransport,
                                          StampedPadTransport,
                                          ControlButtonLightTransport {

    // MARK: - Observable state

    public private(set) var connectionState: LaunchpadConnectionState = .notConnected
    /// ≥3 connection flaps inside 10 s, or a send error while online.
    /// Dismissible: the banner clears it; the next flap re-raises it.
    /// Also self-clearing: `stableClearInterval` of trouble-free
    /// connection resets it, so a one-time transient can't pin the
    /// banner for the whole session.
    public var underpowerSuspected = false

    // MARK: - Callbacks

    @ObservationIgnored public var onPadDown: ((LaunchpadPad) -> Void)?
    @ObservationIgnored public var onPadUp: ((LaunchpadPad) -> Void)?
    /// Stamped pad callbacks (StampedPadTransport): pad + the clocks
    /// captured ON the MIDI receive thread, before the main hop.
    /// When set, these REPLACE the legacy closures above.
    @ObservationIgnored public var onPadDownStamped: ((LaunchpadPad, Double, UInt64) -> Void)?
    @ObservationIgnored public var onPadUpStamped: ((LaunchpadPad, Double, UInt64) -> Void)?

    /// RECEIVE-THREAD pad-up tap: fires ON the MIDI thread the instant a
    /// release message decodes, BEFORE the main hop. Audio-only fast
    /// path — the stamped padUp still follows on main with all the
    /// bookkeeping/LED/recording work, so the tap MUST be idempotent
    /// under that (ChopPlayer's fast fade is: the main release
    /// supersedes it via the voice's epoch gate). Exists because a
    /// press's SwiftUI commit can swallow the main hop for 50–145 ms
    /// under same-pad hammering, which audibly delayed releases while
    /// presses (parked voices, no commit in front of them) stayed
    /// sub-millisecond. Lock-boxed: assigned on main, read on the
    /// receive thread.
    @ObservationIgnored private let fastPadUpBox =
        OSAllocatedUnfairLock<(@Sendable (LaunchpadPad) -> Void)?>(initialState: nil)

    /// Install (or clear) the receive-thread pad-up tap.
    public func setFastPadUpTap(_ tap: (@Sendable (LaunchpadPad) -> Void)?) {
        fastPadUpBox.withLock { $0 = tap }
    }

    /// RECEIVE-THREAD pad-DOWN tap (D-038, the press twin of the pad-up
    /// tap above): fires ON the MIDI thread the instant a press decodes,
    /// with the receive-thread clocks, BEFORE the main hop — the seam
    /// the fast-press engine (armed plans + parked voices) rides so the
    /// audible start never waits on a stalled main queue. Audio-only and
    /// idempotent under the stamped padDown that still follows on main
    /// (the main trigger ADOPTS the fire; ChopPlayer's fire/adoption
    /// epoch contract). Lock-boxed: assigned on main, read on the
    /// receive thread.
    @ObservationIgnored private let fastPadDownBox =
        OSAllocatedUnfairLock<
            (@Sendable (LaunchpadPad, _ songSeconds: Double, _ hostTime: UInt64) -> Void)?
        >(initialState: nil)

    /// Install (or clear) the receive-thread pad-down tap.
    public func setFastPadDownTap(
        _ tap: (@Sendable (LaunchpadPad, Double, UInt64) -> Void)?
    ) {
        fastPadDownBox.withLock { $0 = tap }
    }
    /// Events arrive pre-stamped from the MIDI thread.
    @ObservationIgnored public var onContribution: ((ContributionEvent) -> Void)?
    /// Outer-button presses (Bool = down).
    @ObservationIgnored public var onControlButton: ((LaunchpadProMK3Protocol.ControlButton, Bool) -> Void)?

    // MARK: - Private

    @ObservationIgnored private let midi: any MIDIInterface
    /// Stamps (song-seconds, mach host ticks) — called on the MIDI
    /// thread, so it must be @Sendable.
    @ObservationIgnored private let nowProvider: @Sendable () -> (song: Double, host: UInt64)
    @ObservationIgnored private let dateProvider: () -> Date

    @ObservationIgnored private var inputEndpoint: MIDIEndpoint?
    @ObservationIgnored private var outputEndpoint: MIDIEndpoint?
    /// PadIndex.rawValue → last light sent, for diffing.
    @ObservationIgnored private var ledCache: [Int: LaunchpadLight] = [:]
    /// CC → last FUNCTION-BUTTON light sent. Separate from ledCache:
    /// the grid path gates on PadIndex.isValid (11..88) and must keep
    /// doing so, while control buttons live at CC addresses that gate
    /// rejects (1–8, the x0/x9 rows, 90+). The two address spaces are
    /// disjoint, but separate caches keep the redraw enumeration and
    /// the diffing story per-surface. Retained across suspend and
    /// unplug so a reconnect repaints every function LED too.
    @ObservationIgnored private var controlLedCache: [Int: LaunchpadLight] = [:]
    /// Recent FLAPS — torn-down links and failed connects. Successful
    /// connects deliberately do NOT land here (see noteFlap).
    @ObservationIgnored private var flapTimes: [Date] = []
    /// When the current connection was established (nil = offline).
    @ObservationIgnored private var connectedAt: Date?
    /// Last flap or send failure — the stable-clear timer measures
    /// trouble-free time from max(connectedAt, lastEvidence).
    @ObservationIgnored private var lastEvidenceAt: Date?
    /// Pending stable-connection review (cancelled on disconnect).
    @ObservationIgnored private var stableReview: DispatchWorkItem?
    /// True between `suspend()` and `resume()` — LEDs are cached but
    /// not sent, and the device stays in Live Mode.
    @ObservationIgnored private var suspended = false

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
    /// "LPProMK3 MIDI", falling back to the display name for hosts
    /// that decorate endpoint names.
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
            // Identity is the endpoint REF, not the whole struct: during
            // a single plug-in CoreMIDI resolves names progressively
            // ("LPProMK3 MIDI" → "Launchpad Pro MK3 LPProMK3 MIDI"), and
            // treating a property change as a new device forced a
            // reconnect per notification — the burst that false-fired
            // the underpower banner on a stable cable.
            guard inputEndpoint?.ref != source.ref
                    || outputEndpoint?.ref != destination.ref else {
                return  // already connected to this device
            }
            if inputEndpoint != nil {
                // An established link died and the device re-enumerated
                // (new refs) — that IS a flap, the brown-out signature.
                disconnect()
                noteFlap()
            }
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
        let fastPadUpBox = self.fastPadUpBox
        let fastPadDownBox = self.fastPadDownBox
        let connected = midi.connectInput(source) { [weak self] messages, packetHostTime in
            // MIDI receive thread: stamp BEFORE the hop.
            let now = nowProvider()
            let hostTime = packetHostTime != 0 ? packetHostTime : now.host
            // Fast pad taps, STILL on the receive thread: press/release
            // decoding mirrors deliver() (Note On ch1; vel>0 = press,
            // vel-0 or Note Off = release) through the same pure
            // protocol mapping, so the audible start (armed-plan fire,
            // D-038) and the audible fade (D-033) both begin ~a render
            // quantum after the packet instead of after the main hop.
            let downTap = fastPadDownBox.withLock { $0 }
            let upTap = fastPadUpBox.withLock { $0 }
            if downTap != nil || upTap != nil {
                for message in messages {
                    switch message {
                    case .noteOn(0, let note, let velocity) where velocity > 0:
                        if let downTap,
                           let pad = LaunchpadProMK3Protocol.padIndex(forNote: note) {
                            downTap(
                                LaunchpadPad(row: 8 - pad.row, col: pad.col - 1),
                                now.song, hostTime)
                        }
                    case .noteOn(0, let note, _),
                         .noteOff(0, let note, _):
                        if let upTap,
                           let pad = LaunchpadProMK3Protocol.padIndex(forNote: note) {
                            upTap(LaunchpadPad(row: 8 - pad.row, col: pad.col - 1))
                        }
                    default:
                        break
                    }
                }
            }
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
        // A SUCCESSFUL connect is not a flap (it used to count one,
        // which — combined with CoreMIDI's setup-notification burst —
        // raised the banner on a normal stable plug-in). Instead, start
        // the stable-connection clock that eventually CLEARS the banner.
        connectedAt = dateProvider()
        scheduleStableReview()

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
        connectedAt = nil
        stableReview?.cancel()
        stableReview = nil
    }

    // MARK: - Underpower heuristic

    /// Trouble-free connected time needed before the banner self-clears.
    public static let stableClearInterval: TimeInterval = 30
    /// ≥ this many flaps inside `flapWindow` raises the banner.
    static let flapThreshold = 3
    static let flapWindow: TimeInterval = 10

    /// A FLAP: an established link tore down, or a connect failed.
    /// Successful connects never call this.
    private func noteFlap() {
        let now = dateProvider()
        lastEvidenceAt = now
        flapTimes.append(now)
        flapTimes.removeAll { now.timeIntervalSince($0) > Self.flapWindow }
        if flapTimes.count >= Self.flapThreshold {
            underpowerSuspected = true
        }
    }

    /// Schedule (or re-arm) the stable-connection review that clears
    /// the banner after `stableClearInterval` of trouble-free uptime.
    private func scheduleStableReview() {
        stableReview?.cancel()
        let item = DispatchWorkItem { [weak self] in
            // Runs on the main queue (asyncAfter below).
            MainActor.assumeIsolated { self?.reviewStability() }
        }
        stableReview = item
        DispatchQueue.main.asyncAfter(
            deadline: .now() + Self.stableClearInterval, execute: item)
    }

    /// Clear the banner iff the connection has been up and quiet (no
    /// flap, no send failure) for `stableClearInterval`. Re-arms itself
    /// when evidence arrived after the timer was set. Internal so the
    /// tests can drive it against the injected dateProvider.
    func reviewStability() {
        stableReview = nil
        guard case .connected = connectionState, let connectedAt else { return }
        let now = dateProvider()
        let quietSince = max(connectedAt, lastEvidenceAt ?? .distantPast)
        if now.timeIntervalSince(quietSince) >= Self.stableClearInterval {
            underpowerSuspected = false
            flapTimes.removeAll()
        } else {
            scheduleStableReview()
        }
    }

    @discardableResult
    private func sendChecked(_ sysex: [UInt8]) -> Bool {
        guard let out = outputEndpoint else { return false }
        let ok = midi.send(sysex, to: out)
        if !ok {
            underpowerSuspected = true
            lastEvidenceAt = dateProvider()
            // Sends can recover (transient brown-out): keep the stable
            // clock running so a later quiet stretch clears the banner.
            if connectedAt != nil { scheduleStableReview() }
        }
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
            case .noteOn(0, let note, _),        // vel-0 release
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
        // LaunchpadPad callbacks (row 0 = top). Stamped variant wins:
        // it carries the receive-thread clocks so the controller can
        // quantize/measure against the true press instant, not the
        // main-queue arrival.
        let legacy = LaunchpadPad(row: 8 - pad.row, col: pad.col - 1)
        if down {
            if let stamped = onPadDownStamped {
                stamped(legacy, songSeconds, hostTime)
            } else {
                onPadDown?(legacy)
            }
        } else {
            if let stamped = onPadUpStamped {
                stamped(legacy, songSeconds, hostTime)
            } else {
                onPadUp?(legacy)
            }
        }
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

    // MARK: - Control-button LEDs (ControlButtonLightTransport)

    /// Light the FUNCTION buttons around the grid. Deliberately
    /// bypasses the PadIndex.isValid gate above — ColorSpec encodes
    /// any 7-bit address, and the MK3's LED lighting SysEx drives the
    /// whole surface (PDF p.12: pads AND surrounding buttons); the
    /// grid gate was the only obstacle to function-button LEDs.
    /// The inverse gate applies instead: CCs that ARE valid grid
    /// addresses are rejected, so a mis-caller can't paint pads
    /// through this path and desync ledCache.
    public func setControlLight(_ light: LaunchpadLight, cc: Int) {
        setControlLights([cc: light])
    }

    public func setControlLights(_ frame: [Int: LaunchpadLight]) {
        var specs: [LaunchpadProMK3Protocol.ColorSpec] = []
        for (cc, light) in frame {
            // Bypassing the grid gate must not mean owning the grid: a
            // CC that IS a valid pad address (11..88) is grid territory,
            // and painting it here would change the pad on the wire while
            // ledCache still holds the old light — the next grid diff
            // would then skip the repair. Reject, don't route.
            guard (0...127).contains(cc), !PadIndex(cc).isValid,
                  controlLedCache[cc] != light
            else { continue }
            controlLedCache[cc] = light
            specs.append(spec(for: light, at: PadIndex(cc)))
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
            // Pulse requires a palette entry — nearest of the
            // PDF-cited anchors.
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

    /// Repaint every pad from the cache — one SysEx message (chunked
    /// only if grid + control LEDs together exceed the 106-spec cap).
    /// Used on connect and `resume()` so the hardware matches the app
    /// state, function buttons included.
    private func redrawAll() {
        var specs: [LaunchpadProMK3Protocol.ColorSpec] = []
        for row in 1...8 {
            for col in 1...8 {
                let index = PadIndex.at(row: row, col: col)
                specs.append(spec(for: ledCache[index.rawValue] ?? .off, at: index))
            }
        }
        // Every control LED ever set (sorted for a stable wire order).
        for (cc, light) in controlLedCache.sorted(by: { $0.key < $1.key }) {
            specs.append(spec(for: light, at: PadIndex(cc)))
        }
        guard outputEndpoint != nil, !suspended else { return }
        for message in LaunchpadProMK3Protocol.ledMessages(specs) {
            sendChecked(message)
        }
    }

    // MARK: - Lifecycle

    /// App losing the device (window close, quit): hand the hardware
    /// back to Live Mode. The LED cache is retained so `resume()`
    /// restores the frame.
    public func suspend() {
        guard !suspended else { return }
        suspended = true
        sendChecked(LaunchpadProMK3Protocol.enterLiveMode)
    }

    /// Reclaim Programmer Mode and resync every LED.
    public func resume() {
        guard suspended else { return }
        suspended = false
        guard outputEndpoint != nil else { return }
        sendChecked(LaunchpadProMK3Protocol.enterProgrammerMode)
        redrawAll()
    }
}
