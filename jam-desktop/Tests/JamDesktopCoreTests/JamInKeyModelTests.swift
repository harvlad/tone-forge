// JamInKeyModelTests.swift
//
// Jam in Key surface state: grid derivation from the effective key
// (12 scale tones from the root at/above E2), key override
// resolution + persistence, minor scale variant, octave shift
// clamping, and pad press/release note bookkeeping (a mid-hold
// layout change must still release the note that fired).

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

@MainActor
final class JamInKeyModelTests: XCTestCase {

    private var defaults: UserDefaults!
    private var suiteName: String!
    private var model: JamInKeyModel!

    override func setUp() async throws {
        suiteName = "jam-inkey-tests-\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        model = JamInKeyModel(defaults: defaults)
    }

    override func tearDown() async throws {
        defaults.removePersistentDomain(forName: suiteName)
    }

    // MARK: - Pads

    func testCMajorGridStartsAtC3() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        let pads = model.pads
        XCTAssertEqual(pads.count, 12)
        // First C at/above E2 (40) is C3 (48); scale walks diatonic.
        XCTAssertEqual(pads[0].midi, 48)
        XCTAssertEqual(pads[1].midi, 50)
        XCTAssertEqual(pads[0].noteName, "C")
        // Degree label is Roman numeral "I" for root.
        XCTAssertEqual(pads[0].degreeLabel, "I")
    }

    func testNoKeyFallsBackChromaticFromE2() {
        model.configure(detectedKey: nil, analysisId: nil)
        let pads = model.pads
        XCTAssertEqual(pads.map(\.midi), Array(40...51))
    }

    func testOctaveShiftMovesGrid() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        model.octaveShift = 1
        XCTAssertEqual(model.pads[0].midi, 60)
    }

    func testOctaveShiftClamps() {
        model.octaveShift = 9
        XCTAssertEqual(model.octaveShift, 3)
        model.octaveShift = -9
        XCTAssertEqual(model.octaveShift, -3)
    }

    // MARK: - Key resolution

    func testOverrideWinsOverDetected() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        model.setKeyOverride("D minor")
        XCTAssertEqual((model.effectiveKey?.root.rawValue ?? -1) % 12, 2)
        XCTAssertEqual(model.effectiveKey?.scale, .minor)
        XCTAssertEqual(model.keyDisplayName, "D minor")
    }

    func testOverridePersistsPerSong() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        model.setKeyOverride("D minor")
        // Other song: no override.
        model.configure(detectedKey: "G major", analysisId: "a2")
        XCTAssertNil(model.keyOverride)
        // Back: override restored, incl. across a fresh instance.
        model.configure(detectedKey: "C major", analysisId: "a1")
        XCTAssertEqual(model.keyOverride, "D minor")
        let reloaded = JamInKeyModel(defaults: defaults)
        reloaded.configure(detectedKey: "C major", analysisId: "a1")
        XCTAssertEqual(reloaded.keyOverride, "D minor")
    }

    func testUnparseableDetectedKeyShowsNoKey() {
        model.configure(detectedKey: "garbage", analysisId: "a1")
        XCTAssertNil(model.effectiveKey)
        XCTAssertEqual(model.keyDisplayName, "No key")
    }

    // MARK: - Scale variant

    func testVariantAppliesOnlyToMinor() {
        model.configure(detectedKey: "A minor", analysisId: "a1")
        model.scaleVariant = .harmonic
        XCTAssertEqual(model.effectiveKey?.scale, .harmonicMinor)
        model.configure(detectedKey: "C major", analysisId: "a2")
        XCTAssertEqual(model.effectiveKey?.scale, .major)
    }

    func testVariantPersists() {
        model.scaleVariant = .melodic
        XCTAssertEqual(JamInKeyModel(defaults: defaults).scaleVariant, .melodic)
    }

    // MARK: - Touch

    func testPadDownFiresAndPadUpReleasesStoredNote() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        var on: [(Int, Float)] = []
        var off: [Int] = []
        model.onNoteOn = { on.append(($0, $1)) }
        model.onNoteOff = { off.append($0) }

        model.padDown(0, velocity: 0.8)
        XCTAssertEqual(on.count, 1)
        XCTAssertEqual(on[0].0, 48)
        XCTAssertEqual(on[0].1, 0.8)

        // Layout changes mid-hold; release still frees midi 48.
        model.octaveShift = 1
        model.padUp(0)
        XCTAssertEqual(off, [48])
    }

    func testDoubleDownIgnoredAndReleaseAll() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        var on = 0
        var off: [Int] = []
        model.onNoteOn = { _, _ in on += 1 }
        model.onNoteOff = { off.append($0) }

        model.padDown(0)
        model.padDown(0)
        XCTAssertEqual(on, 1)
        model.padDown(1)
        model.releaseAll()
        XCTAssertEqual(Set(off), [48, 50])
        XCTAssertTrue(model.pressed.isEmpty)
    }

    func testConfigureReleasesHeldNotes() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        var off: [Int] = []
        model.onNoteOff = { off.append($0) }
        model.padDown(0)
        model.configure(detectedKey: nil, analysisId: nil)
        XCTAssertEqual(off, [48])
    }

    // MARK: - Chord highlight

    func testChordHighlightBrightensChordTones() {
        model.configure(detectedKey: "C major", analysisId: "a1")
        model.highlightCurrentChord = true
        model.currentChordSymbol = "C"
        let pads = model.pads
        // Pad 0 = C (chord root) must be bright; pad 1 = D must not.
        XCTAssertTrue(pads[0].isBright)
        XCTAssertFalse(pads[1].isBright)
        model.highlightCurrentChord = false
        XCTAssertFalse(model.pads[0].isBright)
    }
}
