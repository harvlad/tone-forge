// MusicBusTests.swift
//
// Construction/wiring coverage only — we never start() the engine
// (CoreAudio device IO is hostile to CI sandboxes, same discipline
// as EngineControllerTests). attach/connect on a stopped engine is
// safe and exercises the full node graph.

import XCTest
import AVFoundation
import ToneForgeEngine
@testable import JamDesktopAudio

final class MusicBusTests: XCTestCase {

    @MainActor
    func testAttachIsIdempotentAndWiresIntoEngine() {
        let engine = AVAudioEngine()
        let bus = MusicBus(avEngine: engine)

        bus.attach()
        XCTAssertTrue(bus.input.engine === engine)

        // Second attach must not throw/duplicate (attach is a no-op
        // on attached nodes; connect replaces wiring).
        bus.attach()
        XCTAssertTrue(bus.input.engine === engine)
    }

    @MainActor
    func testReattachAfterAttachKeepsNodesAttached() {
        let engine = AVAudioEngine()
        let bus = MusicBus(avEngine: engine)
        bus.attach()
        bus.reattach()
        XCTAssertTrue(bus.input.engine === engine)
    }

    @MainActor
    func testApplyStoresClampedSettings() {
        let engine = AVAudioEngine()
        let bus = MusicBus(avEngine: engine)
        bus.attach()

        var s = FXSettings.neutral
        s.delay.mix = 250  // out of range, clamps to 100
        s.delay.timeSec = 0.4
        bus.apply(s)

        XCTAssertEqual(bus.settings.delay.mix, 100)
        XCTAssertEqual(bus.settings.delay.timeSec, 0.4)
    }

    func testPresetForSecondsBoundaries() {
        XCTAssertEqual(MusicBus.presetForSeconds(0.3), .smallRoom)
        XCTAssertEqual(MusicBus.presetForSeconds(0.8), .mediumRoom)
        XCTAssertEqual(MusicBus.presetForSeconds(1.4), .largeRoom)
        XCTAssertEqual(MusicBus.presetForSeconds(2.2), .mediumHall)
        XCTAssertEqual(MusicBus.presetForSeconds(2.8), .largeHall)
        XCTAssertEqual(MusicBus.presetForSeconds(3.6), .cathedral)
        XCTAssertEqual(MusicBus.presetForSeconds(10), .cathedral)
    }

    func testLinearFromDb() {
        XCTAssertEqual(MusicBus.linearFromDb(0), 1, accuracy: 0.0001)
        XCTAssertEqual(MusicBus.linearFromDb(-20), 0.1, accuracy: 0.0001)
        XCTAssertEqual(MusicBus.linearFromDb(6), 1.9953, accuracy: 0.001)
    }
}
