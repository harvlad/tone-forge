// OnScreenLaunchpadTransport.swift
//
// LaunchpadTransport backed by the SwiftUI grids. The grids keep
// their existing render + touch paths; they additionally forward
// presses into `padDown`/`padUp` here, which fan out to the
// transport's `onPadDown`/`onPadUp` closures. Light frames land in
// the @Published `lights` dictionary so a future pass can render
// them (rendering is unchanged in this pass).

import Foundation
import Combine
import ToneForgeEngine

// @preconcurrency: LaunchpadTransport is a nonisolated protocol (a
// hardware transport may service MIDI callbacks off-main), while this
// implementation is main-actor-bound to the SwiftUI grids. All call
// sites are views/AppState (main actor), so the hop is safe.
@MainActor
public final class OnScreenLaunchpadTransport: ObservableObject, @preconcurrency LaunchpadTransport {

    /// Current light state per pad. Pads not present are `.off`.
    @Published public private(set) var lights: [LaunchpadPad: LaunchpadLight] = [:]

    public var onPadDown: ((LaunchpadPad) -> Void)?
    public var onPadUp: ((LaunchpadPad) -> Void)?

    public init() {}

    public var connectionState: LaunchpadConnectionState { .onScreen }

    // MARK: - Grid → transport (called by the grid views)

    public func padDown(_ pad: LaunchpadPad) {
        onPadDown?(pad)
    }

    public func padUp(_ pad: LaunchpadPad) {
        onPadUp?(pad)
    }

    // MARK: - Transport → lights

    public func setLight(_ light: LaunchpadLight, at pad: LaunchpadPad) {
        if case .off = light {
            lights.removeValue(forKey: pad)
        } else {
            lights[pad] = light
        }
    }

    public func setLights(_ frame: [LaunchpadPad: LaunchpadLight]) {
        for (pad, light) in frame {
            setLight(light, at: pad)
        }
    }

    public func clearLights() {
        lights.removeAll()
    }
}
