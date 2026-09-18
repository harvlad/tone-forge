// LaunchpadControlSurface.swift
//
// iOS host layer for the Launchpad Pro MK3 FUNCTION-BUTTON map: the
// buttons around the 8×8 grid drive the session, with state LEDs.
// The CC → function ASSIGNMENT is the shared engine table
// (LaunchpadControlMapping, the D-036 map desktop pinned); this class
// only translates functions to AppState actions and paints the LED
// contract. Everything is closure-injected so the whole surface is
// unit-testable with no AppState/audio engine.
//
// iOS deviations from the desktop wiring (assignment unchanged, the
// buttons are just INERT and dark where the concept doesn't exist):
//   * Session (CC 93) toggles the Contribute sequencer panel and
//     sequencer play/stop (CC 89) drives that panel's preview loop —
//     iOS DOES have a sequencer (the Contribute "Sequencer" chip opens
//     SequencerTabView; the Sequence Builder records pad loops). These
//     mirror desktop's onSequencerPanelToggle / onSequencerPlayStop +
//     isSequencerPanelOpen / isSequencerPlaying seam (D-039).
//   * Pattern select (CC 101–108) stays unmapped + dark — iOS has no
//     pattern-SLOT model (desktop's A–D grid); the Sequence Builder
//     records ONE pad sequence, so there are no slots to pick. PARITY
//     marks these `na`, not `missing`.
//   * Record Arm (CC 1) drives the session OUTPUT recorder
//     (OutputRecorder), which has no distinct `armed` state on iOS —
//     the LED goes dim-red ↔ pulsing red.
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

    /// How a kit-category layer reads for its LED (CC 2–7).
    public enum LayerActivity: Equatable, Sendable {
        /// A pad of the category is sounding/armed — pulse the accent.
        case active
        /// Pads exist but none sound — dim accent.
        case available
        /// The active kit has no pad in the category — dark.
        case empty
    }

    // MARK: - Host wiring (actions)

    public var onPlayPause: () -> Void = {}
    public var onGlobalStop: () -> Void = {}
    public var onSelectMode: (SampleTriggerMode) -> Void = { _ in }
    public var onLoopLockToggle: () -> Void = {}
    /// Select the surface size (16 | 64) — the on-screen SIZE control
    /// including borrow-relayout semantics.
    public var onGridSize: (Int) -> Void = { _ in }
    /// Fired only when the grid is non-empty (parity with the
    /// on-screen Groove button, which is disabled on an empty grid).
    public var onInstantGroove: () -> Void = {}
    public var onRecordToggle: () -> Void = {}
    public var onStopAllPads: () -> Void = {}
    /// 0-based column into the layer row (drums…texture).
    public var onLayerToggle: (Int) -> Void = { _ in }
    /// Jump to section block `index` — the SAME path as tapping the
    /// on-screen section strip (lock-follow semantics included).
    public var onSectionJump: (Int) -> Void = { _ in }
    /// Session (CC 93) — open/close the Contribute sequencer panel (the
    /// same `showSequencer` state the on-screen chip drives). Mirrors
    /// desktop's onSequencerPanelToggle.
    public var onSequencerPanelToggle: () -> Void = {}
    /// Sequencer play/stop (CC 89) — start/stop the sequencer panel's
    /// preview loop. Reachable while the panel is open (its
    /// SequencerTabView owns the player); an accepted iOS constraint.
    public var onSequencerPlayStop: () -> Void = {}

    // MARK: - Host wiring (LED state)

    public var isTransportPlaying: () -> Bool = { false }
    public var triggerMode: () -> SampleTriggerMode = { .follow }
    public var isLoopLocked: () -> Bool = { false }
    public var padCount: () -> Int = { 16 }
    public var isRecording: () -> Bool = { false }
    public var gridIsEmpty: () -> Bool = { true }
    public var layerActivity: (Int) -> LayerActivity = { _ in .empty }
    /// 0xRRGGBB accent for a layer column (category color).
    public var layerAccent: (Int) -> UInt32 = { _ in 0 }
    public var sectionCount: () -> Int = { 0 }
    /// Section block currently under the playhead, or nil.
    public var activeSectionIndex: () -> Int? = { nil }
    /// Session LED: lit while the Contribute sequencer panel is open.
    public var isSequencerPanelOpen: () -> Bool = { false }
    /// Sequencer play/stop LED: pulses while the preview loop runs.
    public var isSequencerPlaying: () -> Bool = { false }

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

    private weak var lights: (any ControlButtonLightTransport)?
    /// CCs currently showing a momentary press flash.
    private var flashing: Set<Int> = []

    public init() {}

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
        guard down,
              let function = LaunchpadControlMapping.function(for: button)
        else { return }
        switch function {
        case .playPause:
            onPlayPause()
        case .globalStop:
            onGlobalStop()
        case .selectMode(let mode):
            onSelectMode(Self.sampleTriggerMode(mode))
        case .loopLockToggle:
            onLoopLockToggle()
        case .gridSize(let count):
            onGridSize(count)
        case .sequencerPanelToggle:
            onSequencerPanelToggle()
        case .sequencerPlayStop:
            onSequencerPlayStop()
        case .patternSelect:
            // iOS has no pattern-SLOT model (desktop's A–D grid); the
            // Sequence Builder records ONE pad sequence, nothing to
            // select. Genuinely inert + dark (PARITY `na`), no flash.
            return
        case .instantGroove:
            // Parity with the on-screen Groove chip, disabled on an
            // empty grid: a hardware press with nothing to play must
            // not flip trigger modes or flash.
            guard !gridIsEmpty() else { return }
            onInstantGroove()
            flash(cc: LaunchpadControlMapping.cc(for: button))
        case .recordToggle:
            onRecordToggle()
        case .stopAllPads:
            onStopAllPads()
            flash(cc: LaunchpadControlMapping.cc(for: button))
        case .layerToggle(let index):
            guard layerActivity(index) != .empty else { return }
            onLayerToggle(index)
        case .sectionJump(let index):
            guard index < sectionCount() else { return }
            onSectionJump(index)
        }
        repaintControls()
    }

    /// Engine map mode → the mobile trigger-mode store value.
    static func sampleTriggerMode(
        _ mode: LaunchpadControlMapping.TriggerMode
    ) -> SampleTriggerMode {
        switch mode {
        case .oneShot: return .oneShot
        case .follow:  return .follow
        case .latch:   return .latch
        }
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
        static let amber: UInt32 = 0xFFBF00
        static let amberDim: UInt32 = 0x33230A
        static let lockOn: UInt32 = 0xF59E0B
        static let section: UInt32 = 0x404040
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

        // Transport pair.
        frame[20] = isTransportPlaying()
            ? .pulse(colorHint: Color.green)
            : .solid(colorHint: Color.greenDim)
        frame[10] = .solid(colorHint: Color.redDim)

        // Trigger-mode select: selected lit, others dim.
        let modeCC: [(Int, SampleTriggerMode)] =
            [(30, .oneShot), (40, .follow), (50, .latch)]
        for (cc, mode) in modeCC {
            frame[cc] = .solid(colorHint:
                triggerMode() == mode ? Color.bright : Color.dim)
        }

        // Loop lock.
        frame[60] = .solid(colorHint:
            isLoopLocked() ? Color.lockOn : Self.dimmed(Color.lockOn))

        // Grid size: the active size's arrow lit (◄ 16, ► 64).
        frame[91] = .solid(colorHint:
            padCount() == 16 ? Color.bright : Color.dim)
        frame[92] = .solid(colorHint:
            padCount() == 64 ? Color.bright : Color.dim)

        // Session = sequencer panel: lit while the Contribute panel is
        // open (desktop parity), dim otherwise.
        frame[93] = .solid(colorHint:
            isSequencerPanelOpen() ? Color.bright : Color.dim)
        // Chord = Instant Groove.
        frame[95] = flashing.contains(95)
            ? .solid(colorHint: Color.amber)
            : .solid(colorHint: Color.amberDim)

        // Record Arm: idle dim red → recording red pulse (no armed
        // state on the iOS output recorder).
        frame[1] = isRecording()
            ? .pulse(colorHint: Color.red)
            : .solid(colorHint: Color.redDim)

        // Stop Clip: amber flash on press, else dim amber.
        frame[8] = flashing.contains(8)
            ? .solid(colorHint: Color.amber)
            : .solid(colorHint: Color.amberDim)

        // Layer toggles: sounding layer pulses its category accent,
        // available-but-silent shows it dim, empty category dark.
        for index in 0..<6 {
            let cc = index + 2
            switch layerActivity(index) {
            case .active:
                frame[cc] = .pulse(colorHint: layerAccent(index))
            case .available:
                frame[cc] = .solid(colorHint: Self.dimmed(layerAccent(index)))
            case .empty:
                frame[cc] = .off
            }
        }

        // Pattern select: no iOS slot model (PARITY `na`) — dark.
        for cc in 101...108 { frame[cc] = .off }
        // Sequencer play/stop: pulse green while the preview loop runs,
        // dim green when idle (desktop parity).
        frame[89] = isSequencerPlaying()
            ? .pulse(colorHint: Color.green)
            : .solid(colorHint: Color.greenDim)

        // Section blocks: lit where a block exists, pulsing on the
        // block under the playhead.
        let count = sectionCount()
        let active = activeSectionIndex()
        for index in 0..<7 {
            let cc = (7 - index) * 10 + 9
            guard index < count else {
                frame[cc] = .off
                continue
            }
            frame[cc] = active == index
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
