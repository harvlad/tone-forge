//
// AudioEngineSeamTests.swift
//
// Pins the jam-desktop embedding seam added to AudioEngine (avEngine
// accessor + onGraphRebuilt callback) AND the legacy stem API surface
// it must not disturb. Everything here is a compile-time signature
// pin via curried function references — we never instantiate
// AudioEngine, same CI-sandbox discipline as AudioEngineStateTests
// (CoreAudio device IO is hostile to CI).
//
// If any of these lines stop compiling, someone changed a public
// signature that Connect.app and/or jam-desktop depend on.
//

import XCTest
import AVFAudio
@testable import ConnectCore

final class AudioEngineSeamTests: XCTestCase {

    // MARK: - New seam (jam-desktop, Phase 3 desktop M1)

    /// `avEngine` must expose the underlying AVAudioEngine read-only,
    /// and `onGraphRebuilt` must be a settable optional zero-arg
    /// callback — jam-desktop re-attaches its stem subgraph there.
    func testEmbeddingSeamSurface() {
        let engineAccessor: (AudioEngine) -> AVAudioEngine = { $0.avEngine }
        let rebuiltGetter: (AudioEngine) -> (() -> Void)? = { $0.onGraphRebuilt }
        let rebuiltSetter: (AudioEngine, (() -> Void)?) -> Void = { $0.onGraphRebuilt = $1 }
        XCTAssertNotNil(engineAccessor)
        XCTAssertNotNil(rebuiltGetter)
        XCTAssertNotNil(rebuiltSetter)
    }

    // MARK: - Legacy stem API regression

    /// The buffer-based stem API Connect.app ships against. The
    /// jam-desktop seam is strictly additive; these signatures must
    /// not change shape.
    func testLegacyStemAPISurfaceUnchanged() {
        let load: (AudioEngine) -> (String, URL) throws -> Void =
            { e in { try e.loadStem(name: $0, url: $1) } }
        let playAll: (AudioEngine) -> (Bool) -> Void =
            { e in { e.playAllStems(loop: $0) } }
        let stopAll: (AudioEngine) -> () -> Void = { $0.stopAllStems }
        let setGain: (AudioEngine) -> (String, Float) -> Void =
            { e in { e.setStem(name: $0, gain: $1) } }
        let mute: (AudioEngine) -> (String, Bool) -> Void =
            { e in { e.muteStem(name: $0, muted: $1) } }
        let unloadAll: (AudioEngine) -> () -> Void = { $0.unloadAllStems }
        let stemsGain: (AudioEngine, Float) -> Void = { $0.stemsGain = $1 }

        XCTAssertNotNil(load)
        XCTAssertNotNil(playAll)
        XCTAssertNotNil(stopAll)
        XCTAssertNotNil(setGain)
        XCTAssertNotNil(mute)
        XCTAssertNotNil(unloadAll)
        XCTAssertNotNil(stemsGain)
    }

    /// The callback set Connect.app wires at startup — the new
    /// onGraphRebuilt must sit alongside these without altering them.
    func testLegacyCallbackSurfaceUnchanged() {
        let onState: (AudioEngine, ((AudioEngine.State) -> Void)?) -> Void =
            { $0.onStateChange = $1 }
        let onLost: (AudioEngine, ((String) -> Void)?) -> Void =
            { $0.onDeviceLost = $1 }
        let onSnapshot: (AudioEngine, ((AudioEngine.ConnectStateSnapshot) -> Void)?) -> Void =
            { $0.onConnectStateSnapshot = $1 }
        let onLatency: (AudioEngine, ((AudioEngine.LatencyReport) -> Void)?) -> Void =
            { $0.onLatencyReportReady = $1 }

        XCTAssertNotNil(onState)
        XCTAssertNotNil(onLost)
        XCTAssertNotNil(onSnapshot)
        XCTAssertNotNil(onLatency)
    }
}
