//
// AudioEngineStateTests.swift
//
// Pins the AudioEngine.State value semantics. We deliberately do NOT
// spin up an AVAudioEngine here — CoreAudio device IO is hostile to
// CI sandboxes and the state-machine bugs we care about (Equatable
// quirks on the associated-value cases, stop()-from-stopped no-ops)
// are pure value-type behavior.
//

import XCTest
@testable import ConnectCore

final class AudioEngineStateTests: XCTestCase {

    /// Same case + same associated value compares equal; different
    /// associated value compares unequal. This is what lets the
    /// `didSet` guard suppress duplicate state callbacks.
    func testStateEqualityWithAssociatedValues() {
        XCTAssertEqual(AudioEngine.State.stopped, AudioEngine.State.stopped)
        XCTAssertEqual(AudioEngine.State.starting, AudioEngine.State.starting)
        XCTAssertEqual(AudioEngine.State.running, AudioEngine.State.running)

        XCTAssertEqual(
            AudioEngine.State.reconfiguring(reason: "device change"),
            AudioEngine.State.reconfiguring(reason: "device change")
        )
        XCTAssertNotEqual(
            AudioEngine.State.reconfiguring(reason: "device change"),
            AudioEngine.State.reconfiguring(reason: "user requested")
        )

        XCTAssertEqual(
            AudioEngine.State.failed(error: "boom"),
            AudioEngine.State.failed(error: "boom")
        )
        XCTAssertNotEqual(
            AudioEngine.State.failed(error: "boom"),
            AudioEngine.State.failed(error: "different boom")
        )
    }

    /// Distinct cases never compare equal, regardless of associated
    /// payloads. Guards against an accidental override of `==` that
    /// only inspected one component.
    func testDifferentCasesAreNotEqual() {
        XCTAssertNotEqual(AudioEngine.State.stopped, AudioEngine.State.starting)
        XCTAssertNotEqual(AudioEngine.State.starting, AudioEngine.State.running)
        XCTAssertNotEqual(AudioEngine.State.running,
                          AudioEngine.State.reconfiguring(reason: ""))
        XCTAssertNotEqual(AudioEngine.State.reconfiguring(reason: "x"),
                          AudioEngine.State.failed(error: "x"))
    }
}
