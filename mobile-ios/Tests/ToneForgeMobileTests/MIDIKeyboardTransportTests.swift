// MIDIKeyboardTransportTests.swift
//
// Generic MIDI note controller transport over FakeMIDIInterface:
// note-on/off → ContributionEvent.midiNote, running-status vel-0 as
// release, channel-agnostic mapping, Launchpad-port exclusion (so the
// MK3 grid isn't double-fired), CC surfaced to the hook, and reconnect
// on setup change.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class MIDIKeyboardTransportTests: XCTestCase {

    private var midi: FakeMIDIInterface!
    private var transport: MIDIKeyboardTransport!
    private var events: [ContributionEvent] = []
    private var ccs: [(UInt8, UInt8, UInt8)] = []

    // A generic keyboard endpoint (not a Launchpad).
    private static let keyboard = MIDIEndpoint(
        ref: 201, name: "KeyLab", displayName: "Arturia KeyLab MIDI"
    )

    override func setUp() {
        super.setUp()
        midi = FakeMIDIInterface()
        events = []
        ccs = []
    }

    @discardableResult
    private func makeTransport() -> MIDIKeyboardTransport {
        let t = MIDIKeyboardTransport(
            midi: midi,
            nowProvider: { (song: 12.0, host: 555) }
        )
        t.onContribution = { [weak self] in self?.events.append($0) }
        t.onControlChange = { [weak self] ch, cc, v in self?.ccs.append((ch, cc, v)) }
        transport = t
        return t
    }

    /// The receive handler hops to main via DispatchQueue.main.async;
    /// drain that queue before asserting.
    private func drainMainQueue() {
        let exp = expectation(description: "main queue drained")
        DispatchQueue.main.async { exp.fulfill() }
        wait(for: [exp], timeout: 1.0)
    }

    private func plugInKeyboard() {
        midi.fakeSources = [Self.keyboard]
        midi.fakeDestinations = [Self.keyboard]
        midi.onSetupChanged?()
    }

    // MARK: - Discovery

    func testConnectsToKeyboardSource() {
        _ = makeTransport()
        plugInKeyboard()
        XCTAssertEqual(midi.connectedInputs, [Self.keyboard])
    }

    func testExcludesLaunchpadPorts() {
        let t = makeTransport()
        midi.fakeSources = [FakeMIDIInterface.launchpadMIDI, Self.keyboard]
        midi.fakeDestinations = midi.fakeSources
        midi.onSetupChanged?()

        // Only the keyboard is bound — the MK3 grid stays with the
        // Launchpad transport.
        XCTAssertEqual(midi.connectedInputs, [Self.keyboard])
        XCTAssertEqual(t.connectedInputs, ["Arturia KeyLab MIDI"])
    }

    // MARK: - Note input

    func testNoteOnEmitsMidiNoteEvent() {
        _ = makeTransport()
        plugInKeyboard()
        midi.receive([.noteOn(channel: 0, note: 60, velocity: 100)],
                     hostTime: 777, from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.count, 1)
        XCTAssertEqual(events[0].source, .midiKeyboard)
        XCTAssertEqual(events[0].kind, .midiNote(note: 60, velocity: 100, on: true))
        XCTAssertEqual(events[0].hostTime, 777)
        XCTAssertEqual(events[0].timestamp, 12.0)
        XCTAssertEqual(events[0].velocity, 100.0 / 127.0, accuracy: 1e-9)
    }

    func testNoteOffEmitsRelease() {
        _ = makeTransport()
        plugInKeyboard()
        midi.receive([.noteOff(channel: 0, note: 60, velocity: 0)], from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind),
                       [.midiNote(note: 60, velocity: 0, on: false)])
    }

    func testVelocityZeroNoteOnIsRelease() {
        _ = makeTransport()
        plugInKeyboard()
        // Running-status note-on with velocity 0 = note off.
        midi.receive([.noteOn(channel: 0, note: 64, velocity: 0)], from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind),
                       [.midiNote(note: 64, velocity: 0, on: false)])
    }

    func testChannelIsIgnored() {
        _ = makeTransport()
        plugInKeyboard()
        midi.receive([.noteOn(channel: 9, note: 42, velocity: 120)], from: Self.keyboard)
        drainMainQueue()

        // Channel 10 (drums) still produces a midiNote — ModeRouter
        // keys on note number, not channel.
        XCTAssertEqual(events.map(\.kind),
                       [.midiNote(note: 42, velocity: 120, on: true)])
    }

    // MARK: - samplePads routing

    func testSamplePadsBaseNoteMapsToPadZero() {
        let t = makeTransport()
        plugInKeyboard()
        t.noteRouting = .samplePads(baseNote: 36)
        // note 36 = pad idx 0 → grid row 8, col 1.
        midi.receive([.noteOn(channel: 0, note: 36, velocity: 100)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind), [.padDown(row: 8, col: 1)])
        XCTAssertEqual(events[0].velocity, 100.0 / 127.0, accuracy: 1e-9)
    }

    func testSamplePadsSecondRow() {
        let t = makeTransport()
        plugInKeyboard()
        t.noteRouting = .samplePads(baseNote: 36)
        // idx 4 → row 8 - 4/4 = 7, col 4%4 + 1 = 1.
        midi.receive([.noteOn(channel: 0, note: 40, velocity: 64)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind), [.padDown(row: 7, col: 1)])
    }

    func testSamplePadsLastPad() {
        let t = makeTransport()
        plugInKeyboard()
        t.noteRouting = .samplePads(baseNote: 36)
        // idx 15 → row 8 - 15/4 = 5, col 15%4 + 1 = 4.
        midi.receive([.noteOn(channel: 0, note: 51, velocity: 90)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind), [.padDown(row: 5, col: 4)])
    }

    func testSamplePadsNoteOffEmitsPadUp() {
        let t = makeTransport()
        plugInKeyboard()
        t.noteRouting = .samplePads(baseNote: 36)
        midi.receive([.noteOff(channel: 0, note: 36, velocity: 0)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertEqual(events.map(\.kind), [.padUp(row: 8, col: 1)])
    }

    func testSamplePadsOutOfRangeIgnored() {
        let t = makeTransport()
        plugInKeyboard()
        t.noteRouting = .samplePads(baseNote: 36)
        // Below base (idx -1) and above idx 15 (note 52 = idx 16).
        midi.receive([.noteOn(channel: 0, note: 35, velocity: 100),
                      .noteOn(channel: 0, note: 52, velocity: 100)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertTrue(events.isEmpty)
    }

    // MARK: - Control Change

    func testControlChangeSurfacedNotRoutedToAudio() {
        _ = makeTransport()
        plugInKeyboard()
        midi.receive([.controlChange(channel: 0, controller: 1, value: 64)],
                     from: Self.keyboard)
        drainMainQueue()

        XCTAssertTrue(events.isEmpty)            // no audio event
        XCTAssertEqual(ccs.count, 1)
        XCTAssertEqual(ccs[0].0, 0)
        XCTAssertEqual(ccs[0].1, 1)
        XCTAssertEqual(ccs[0].2, 64)
    }
}
