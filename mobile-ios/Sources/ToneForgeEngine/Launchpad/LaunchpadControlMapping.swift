// LaunchpadControlMapping.swift
//
// The Launchpad Pro MK3 FUNCTION-BUTTON assignment table (desktop
// D-036), as a PURE mapping in the engine target so the table itself
// is host-testable and platform surfaces can't drift from each other.
// jam-desktop's LaunchpadControlSurface carries the same table over
// its own controller types; iOS consumes THIS one (its surface layer
// translates functions to AppState actions). A change to either table
// is a parity bug — the CC → function assignment is the approved map.
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

import Foundation

public enum LaunchpadControlMapping {

    /// Pad trigger mode as the map names it — platform-neutral so the
    /// table lives in the engine. iOS maps to SampleTriggerMode,
    /// desktop's own table uses LaunchpadController.PadPlaybackMode;
    /// raw values match both.
    public enum TriggerMode: String, CaseIterable, Sendable {
        case oneShot, follow, latch
    }

    /// The approved function set. Platform surfaces may leave a
    /// function unwired (e.g. iOS has no sequencer PANEL, so
    /// `.sequencerPanelToggle` / `.patternSelect` / `.sequencerPlayStop`
    /// stay inert there) — but the ASSIGNMENT is fixed: a button never
    /// means something different on another platform.
    public enum Function: Equatable, Sendable {
        /// ▷ Play (CC 20) — song transport play/pause.
        case playPause
        /// ○ Record/Capture MIDI (CC 10) — global stop.
        case globalStop
        /// Fixed Length / Quantise / Duplicate (CC 30/40/50) —
        /// One-Shot | Follow | Latch trigger-mode select.
        case selectMode(TriggerMode)
        /// Clear (CC 60) — pad loop-lock toggle. The physically
        /// labeled Quantise button (CC 40) is consumed by the approved
        /// Tier-1 mode-select triplet, so loop-lock lands on the free
        /// button directly above it.
        case loopLockToggle
        /// ◄ / ► (CC 91/92) — surface size select (16 / 64), the
        /// on-screen SIZE control incl. borrow-relayout semantics.
        case gridSize(Int)
        /// Session (CC 93) — open/close the Sequencer panel.
        case sequencerPanelToggle
        /// Chord (CC 95) — Instant Groove (inert on an empty grid).
        case instantGroove
        /// Record Arm (CC 1) — session take record toggle.
        case recordToggle
        /// Stop Clip (CC 8) — stop every sounding pad (NOT the song:
        /// that stays on CC 10).
        case stopAllPads
        /// Mute…Device (CC 2–7) — per-category layer toggles, 0-based
        /// column into the platform's layer row (drums, bass, chords,
        /// synth, lead, texture).
        case layerToggle(Int)
        /// Track select (CC 101–108) — sequencer pattern select,
        /// 0-based index into the pattern store's sorted list.
        case patternSelect(Int)
        /// > (CC 89, top-right scene arrow) — sequencer play/stop.
        case sequencerPlayStop
        /// > (CC 79…19, top→bottom) — arrangement/section blocks 0…6:
        /// press jumps the transport to the block start (same path as
        /// the on-screen section strip).
        case sectionJump(Int)
    }

    /// The full physical assignment. nil = deliberately unmapped
    /// (Shift/logo reserved; ▲▼, Note, Custom, Sequencer, Projects
    /// free for future tiers; tap tempo parked).
    public static func function(
        for button: LaunchpadProMK3Protocol.ControlButton
    ) -> Function? {
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
            return .layerToggle(col - 2)
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

    /// Inverse of LaunchpadProMK3Protocol.controlButton(forCC:) — LED
    /// frames are keyed by CC.
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
}
