// LaunchpadTransport.swift
//
// Transport abstraction for pad-grid controllers. v1 ships two
// implementations in ToneForgeMobile:
//
//   OnScreenLaunchpadTransport — the SwiftUI grids, which forward
//     their existing touch closures into `onPadDown`/`onPadUp` and
//     mirror light frames into an @Published dictionary.
//   USBLaunchpadTransport — a no-op stub that always reports
//     `.notConnected`. Real CoreMIDI support is v2; nothing in this
//     module may import CoreMIDI.
//
// The protocol is deliberately dumb: pads in, lights out. Musical
// meaning (chords, chops, sections) stays in the app layer so a
// hardware transport can be dropped in without touching it.

import Foundation

/// One pad on the grid. `row` 0 is the top row, `col` 0 is the left
/// column — matching the on-screen render order of both grids.
public struct LaunchpadPad: Hashable, Sendable, Codable {
    public let row: Int
    public let col: Int

    public init(row: Int, col: Int) {
        self.row = row
        self.col = col
    }
}

/// What a pad's LED should do. `colorHint` is a 0xRRGGBB value the
/// transport maps to its nearest displayable color (hardware palettes
/// are coarse; on-screen can honour it exactly).
public enum LaunchpadLight: Hashable, Sendable {
    case off
    case solid(colorHint: UInt32)
    case pulse(colorHint: UInt32)
}

/// Whether a transport currently has a device to talk to.
public enum LaunchpadConnectionState: Hashable, Sendable {
    /// The on-screen grid — always available.
    case onScreen
    /// Hardware transport with no device attached.
    case notConnected
    /// Hardware transport bound to a device.
    case connected(deviceName: String)
}

/// Pads in, lights out. Implementations must deliver pad callbacks on
/// the main actor (UI + audio-trigger paths both hop from there).
public protocol LaunchpadTransport: AnyObject {
    var connectionState: LaunchpadConnectionState { get }

    /// Fired when a pad is pressed / released.
    var onPadDown: ((LaunchpadPad) -> Void)? { get set }
    var onPadUp: ((LaunchpadPad) -> Void)? { get set }

    /// Set a single pad's light.
    func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad)

    /// Replace the lights for every pad in `frame`; pads absent from
    /// the frame are left untouched (callers send full frames when
    /// they want a clean slate — see `clearLights`).
    func setLights(_ frame: [LaunchpadPad: LaunchpadLight])

    /// Turn every pad off.
    func clearLights()
}
