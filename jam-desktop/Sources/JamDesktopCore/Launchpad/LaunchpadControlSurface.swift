// LaunchpadControlSurface.swift
//
// The Launchpad Pro MK3 FUNCTION-BUTTON map (D-036): every button
// around the 8×8 grid that Jamn drives — what it does, and what its
// LED shows. Pure Core so the assignment table and the LED contract
// are test-pinned; SessionController wires the few host actions this
// layer can't reach (transport, recorder, sequencer panel/patterns).
//
// Physical → CC resolution comes from the MK3 hardware overview (User
// Guide p.11) crossed with the Programmer-mode CC scheme
// (LaunchpadProMK3Protocol.ControlButton, PDF p.19):
//
//   left column, top→bottom:  ▲80 ▼70 Clear60 Duplicate50 Quantise40
//                             FixedLength30 ▷Play20 ○Record10
//   top row, left→right:      Shift90 ◄91 ►92 Session93 Note94
//                             Chord95 Custom96 Sequencer97 Projects98
//   right column, top→bottom: >89 >79 >69 >59 >49 >39 >29 >19
//   track select:             101–108      track control: 1–8
//                             (RecordArm1 Mute2 Solo3 Volume4 Pan5
//                              Sends6 Device7 StopClip8)
//
// Shift (90), Setup and the logo (99) are reserved/untouched; tap
// tempo is explicitly parked (approved map).
//
// LEDs go through ControlButtonLightTransport — a seam SEPARATE from
// LaunchpadTransport.setLights because the grid path validates
// PadIndex (11..88) and must keep doing so; function buttons live at
// CC addresses that gate rejects.

import Foundation
import ToneForgeEngine

/// LED seam for the function buttons around the grid. The transport
/// keeps its own control-LED cache (diffing + reconnect repaint), so
/// callers may re-send full frames cheaply.
@MainActor
public protocol ControlButtonLightTransport: AnyObject {
    func setControlLights(_ frame: [Int: LaunchpadLight])
}

@MainActor
public final class LaunchpadControlSurface {

    // MARK: - The approved function map

    public enum HardwareFunction: Equatable, Sendable {
        /// ▷ Play (CC 20) — song transport play/pause (pre-existing).
        case playPause
        /// ○ Record/Capture MIDI (CC 10) — global stop (pre-existing).
        case globalStop
        /// Fixed Length / Quantise / Duplicate (CC 30/40/50) —
        /// One-Shot | Follow | Latch trigger-mode select.
        case selectMode(LaunchpadController.PadPlaybackMode)
        /// Clear (CC 60) — pad loop-lock toggle. The physically
        /// labeled Quantise button (CC 40) is consumed by the
        /// approved Tier-1 mode-select triplet, so loop-lock lands on
        /// the free button directly above it.
        case loopLockToggle
        /// ◄ / ► (CC 91/92) — surface size select (16 / 64), the
        /// on-screen SIZE control incl. borrow-relayout semantics.
        case gridSize(Int)
        /// Session (CC 93) — open/close the Sequencer panel.
        case sequencerPanelToggle
        /// Chord (CC 95) — Instant Groove.
        case instantGroove
        /// Record Arm (CC 1) — session take record toggle.
        case recordToggle
        /// Stop Clip (CC 8) — stop every sounding pad (NOT the song:
        /// that stays on CC 10).
        case stopAllPads
        /// Mute…Device (CC 2–7) — per-category layer toggles,
        /// mirroring the on-screen Layers stack.
        case layerToggle(LaunchpadController.PadCategory)
        /// Track select (CC 101–108) — sequencer pattern select,
        /// 0-based index into the pattern store's sorted list.
        case patternSelect(Int)
        /// > (CC 89, top-right, the scene arrow beside the
        /// Sequencer/Projects cluster) — sequencer play/stop.
        case sequencerPlayStop
        /// > (CC 79…19, top→bottom) — arrangement/section blocks
        /// 0…6: press jumps the transport to the block start (same
        /// path as clicking the on-screen section strip).
        case sectionJump(Int)
    }

    /// CC 2–7 in track-control order. Mirrors the on-screen Layers
    /// stack (LayerStackView: drums, bass, chords, synth, lead,
    /// texture, vocal) truncated to the six free cells — Record Arm
    /// (CC 1) and Stop Clip (CC 8) bookend the row, so vocal (and
    /// rhythm, which the Layers stack never showed) don't fit.
    public static let layerRow: [LaunchpadController.PadCategory] =
        [.drums, .bass, .chords, .synth, .lead, .texture]

    /// The full physical assignment. nil = deliberately unmapped
    /// (Shift/logo reserved; ▲▼, Note, Custom, Sequencer, Projects
    /// free for future tiers; tap tempo parked).
    public static func function(
        for button: LaunchpadProMK3Protocol.ControlButton
    ) -> HardwareFunction? {
        switch button {
        case .left(row: 1):         return .globalStop
        case .left(row: 2):         return .playPause
        case .left(row: 3):         return .selectMode(.oneShot)
        case .left(row: 4):         return .selectMode(.follow)
        case .left(row: 5):         return .selectMode(.latch)
        case .left(row: 6):         return .loopLockToggle
        case .top(col: 1):          return .gridSize(16)
        case .top(col: 2):          return .gridSize(64)
        case .top(col: 3):          return .sequencerPanelToggle
        case .top(col: 5):          return .instantGroove
        case .trackControl(col: 1): return .recordToggle
        case .trackControl(col: 8): return .stopAllPads
        case .trackControl(let col) where (2...7).contains(col):
            return .layerToggle(layerRow[col - 2])
        case .trackSelect(let col) where (1...8).contains(col):
            return .patternSelect(col - 1)
        case .right(row: 8):        return .sequencerPlayStop
        case .right(let row) where (1...7).contains(row):
            // Top scene button under CC89 is block 0; reading order
            // matches the on-screen strip (top→bottom = song order).
            return .sectionJump(7 - row)
        default:
            return nil
        }
    }

    /// Inverse of LaunchpadProMK3Protocol.controlButton(forCC:) — the
    /// LED frame is keyed by CC.
    public static func cc(
        for button: LaunchpadProMK3Protocol.ControlButton
    ) -> Int {
        switch button {
        case .shift:                  return 90
        case .logo:                   return 99
        case .left(let row):          return 10 * row
        case .right(let row):         return 10 * row + 9
        case .top(let col):           return 90 + col
        case .trackSelect(let col):   return 100 + col
        case .trackControl(let col):  return col
        }
    }

    // MARK: - Host wiring

    /// Session take recording, as the LED sees it (idle → armed →
    /// recording; RecordToggle's state machine).
    public enum RecordState: Equatable, Sendable {
        case idle, armed, recording
    }

    public var onPlayPause: () -> Void = {}
    public var onGlobalStop: () -> Void = {}
    public var onRecordToggle: () -> Void = {}
    /// Jump the transport to a section-block start (seconds) — the
    /// SAME seek path the on-screen section strip click uses, so loop
    /// regions / recorder gap markers / clock re-anchoring all behave
    /// identically.
    public var onSectionJump: (Double) -> Void = { _ in }
    public var onSequencerPanelToggle: () -> Void = {}
    public var onSequencerPlayStop: () -> Void = {}
    /// Select the Nth pattern (0-based, pattern-store sort order).
    public var onPatternSelect: (Int) -> Void = { _ in }

    public var isTransportPlaying: () -> Bool = { false }
    public var recordState: () -> RecordState = { .idle }
    public var isSequencerPanelOpen: () -> Bool = { false }
    public var isSequencerPlaying: () -> Bool = { false }
    /// Saved pattern ids in the pattern store's display order.
    public var patternIds: () -> [UUID] = { [] }
    /// Identity of the pattern currently loaded in the sequencer.
    public var currentPatternId: () -> UUID? = { nil }

    /// Momentary press feedback duration (Stop Clip amber, Chord).
    public var flashDuration: TimeInterval = 0.18
    /// Injectable so tests end flashes deterministically.
    var scheduleFlashEnd: (TimeInterval, @escaping @MainActor () -> Void) -> Void =
        { delay, work in
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) {
                MainActor.assumeIsolated { work() }
            }
        }

    // MARK: - State

    private let launchpad: LaunchpadController
    private let arrangement: ArrangementController
    private weak var lights: (any ControlButtonLightTransport)?
    /// CCs currently showing a momentary press flash.
    private var flashing: Set<Int> = []

    public init(
        launchpad: LaunchpadController, arrangement: ArrangementController
    ) {
        self.launchpad = launchpad
        self.arrangement = arrangement
    }

    /// Take over the transport's function-button LEDs. The transport
    /// keeps its own cache, so attaching (or reconnecting) just needs
    /// a repaint.
    public func attachLights(_ transport: any ControlButtonLightTransport) {
        lights = transport
        repaintControls()
    }

    // MARK: - Input

    /// Route a hardware control-button event. All mapped actions fire
    /// on press (down); releases are ignored — every mapped function
    /// is a tap, and Shift stays reserved as a (future) modifier.
    public func handle(
        _ button: LaunchpadProMK3Protocol.ControlButton, down: Bool
    ) {
        guard down, let function = Self.function(for: button) else { return }
        switch function {
        case .playPause:
            onPlayPause()
        case .globalStop:
            onGlobalStop()
        case .selectMode(let mode):
            launchpad.playbackMode = mode
        case .loopLockToggle:
            launchpad.loopLockEnabled.toggle()
        case .gridSize(let count):
            launchpad.padCount = count
        case .sequencerPanelToggle:
            onSequencerPanelToggle()
        case .instantGroove:
            // Parity with the on-screen Groove button, which is DISABLED
            // on an empty grid: instantGroove() unconditionally latches
            // playbackMode, so an empty-grid hardware press would flip
            // the trigger mode with nothing to play. No-op, no flash.
            guard !launchpad.assignments.isEmpty else { return }
            launchpad.instantGroove()
            flash(cc: Self.cc(for: button))
        case .recordToggle:
            onRecordToggle()
        case .stopAllPads:
            launchpad.stopAllPads()
            flash(cc: Self.cc(for: button))
        case .layerToggle(let category):
            launchpad.toggleLayer(category)
        case .patternSelect(let index):
            guard patternIds().indices.contains(index) else { return }
            onPatternSelect(index)
        case .sequencerPlayStop:
            onSequencerPlayStop()
        case .sectionJump(let index):
            let blocks = arrangement.blocks
            guard blocks.indices.contains(index) else { return }
            onSectionJump(blocks[index].start)
        }
        repaintControls()
    }

    private func flash(cc: Int) {
        flashing.insert(cc)
        scheduleFlashEnd(flashDuration) { [weak self] in
            self?.flashing.remove(cc)
            self?.repaintControls()
        }
    }

    // MARK: - LEDs

    private enum Color {
        static let bright: UInt32 = 0xFFFFFF
        static let dim: UInt32 = 0x1E1E1E
        static let green: UInt32 = 0x00FF00
        static let greenDim: UInt32 = 0x0A280A
        static let red: UInt32 = 0xFF0000
        static let redDim: UInt32 = 0x400808
        static let orange: UInt32 = 0xFF8800
        static let amber: UInt32 = 0xFFBF00
        static let amberDim: UInt32 = 0x33230A
        static let lockOn: UInt32 = 0xF59E0B
        static let section: UInt32 = 0x404040
        static let pattern: UInt32 = 0x202020
    }

    /// ~quarter brightness of a 0xRRGGBB accent, for "available but
    /// silent" states.
    static func dimmed(_ hex: UInt32) -> UInt32 {
        let r = ((hex >> 16) & 0xFF) >> 2
        let g = ((hex >> 8) & 0xFF) >> 2
        let b = (hex & 0xFF) >> 2
        return (r << 16) | (g << 8) | b
    }

    /// The full function-button LED frame. Cheap to call every UI
    /// tick — the transport diffs against its control-LED cache and
    /// only changed buttons hit the wire.
    public func controlLightFrame() -> [Int: LaunchpadLight] {
        var frame: [Int: LaunchpadLight] = [:]

        // Transport pair (kept assignments).
        frame[20] = isTransportPlaying()
            ? .pulse(colorHint: Color.green)
            : .solid(colorHint: Color.greenDim)
        frame[10] = .solid(colorHint: Color.redDim)

        // Trigger-mode select: selected lit, others dim.
        let modeCC: [(Int, LaunchpadController.PadPlaybackMode)] =
            [(30, .oneShot), (40, .follow), (50, .latch)]
        for (cc, mode) in modeCC {
            frame[cc] = .solid(colorHint:
                launchpad.playbackMode == mode ? Color.bright : Color.dim)
        }

        // Loop lock.
        frame[60] = .solid(colorHint:
            launchpad.loopLockEnabled ? Color.lockOn
                                      : Self.dimmed(Color.lockOn))

        // Grid size: the active size's arrow lit (◄ 16, ► 64).
        frame[91] = .solid(colorHint:
            launchpad.padCount == 16 ? Color.bright : Color.dim)
        frame[92] = .solid(colorHint:
            launchpad.padCount == 64 ? Color.bright : Color.dim)

        // Session = sequencer panel; Chord = Instant Groove.
        frame[93] = .solid(colorHint:
            isSequencerPanelOpen() ? Color.bright : Color.dim)
        frame[95] = flashing.contains(95)
            ? .solid(colorHint: Color.amber)
            : .solid(colorHint: Color.amberDim)

        // Record Arm: idle dim red → armed orange → recording red pulse.
        switch recordState() {
        case .idle:      frame[1] = .solid(colorHint: Color.redDim)
        case .armed:     frame[1] = .solid(colorHint: Color.orange)
        case .recording: frame[1] = .pulse(colorHint: Color.red)
        }

        // Stop Clip: amber flash on press, else dim amber.
        frame[8] = flashing.contains(8)
            ? .solid(colorHint: Color.amber)
            : .solid(colorHint: Color.amberDim)

        // Layer toggles: sounding layer pulses its category accent,
        // available-but-silent shows it dim, empty category dark.
        for (offset, category) in Self.layerRow.enumerated() {
            let cc = offset + 2
            if launchpad.activeLayer(category) != nil {
                frame[cc] = .pulse(colorHint: UInt32(category.colorHex))
            } else if !launchpad.pads(in: category).isEmpty {
                frame[cc] = .solid(colorHint:
                    Self.dimmed(UInt32(category.colorHex)))
            } else {
                frame[cc] = .off
            }
        }

        // Pattern select: saved pattern dim, the loaded one bright
        // (pulsing while the sequencer runs it).
        let ids = patternIds()
        let current = currentPatternId()
        let playing = isSequencerPlaying()
        for slot in 0..<8 {
            let cc = 101 + slot
            guard ids.indices.contains(slot) else {
                frame[cc] = .off
                continue
            }
            if ids[slot] == current {
                frame[cc] = playing
                    ? .pulse(colorHint: Color.bright)
                    : .solid(colorHint: Color.bright)
            } else {
                frame[cc] = .solid(colorHint: Color.pattern)
            }
        }

        // Sequencer play/stop (top-right scene arrow).
        frame[89] = isSequencerPlaying()
            ? .pulse(colorHint: Color.green)
            : .solid(colorHint: Color.greenDim)

        // Section blocks: lit where a block exists, pulsing on the
        // block under the playhead.
        let blocks = arrangement.blocks
        for index in 0..<7 {
            let cc = (7 - index) * 10 + 9
            guard blocks.indices.contains(index) else {
                frame[cc] = .off
                continue
            }
            frame[cc] = arrangement.activeBlock == index
                ? .pulse(colorHint: Color.bright)
                : .solid(colorHint: Color.section)
        }

        return frame
    }

    /// Push the current frame; diffed by the transport's cache.
    public func repaintControls() {
        lights?.setControlLights(controlLightFrame())
    }
}
