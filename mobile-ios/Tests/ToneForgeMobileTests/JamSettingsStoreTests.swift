// JamSettingsStoreTests.swift
//
// Pins the JamSettingsStore blob (redesign Phase 7): defaults,
// autosave round-trip, per-song key overrides, effective-key
// resolution (override ?? detected, scale variant applied), the
// octave clamp, and corrupt-blob recovery.

import XCTest
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class JamSettingsStoreTests: XCTestCase {

    private var defaults: UserDefaults!
    private let suiteName = "toneforge.tests.jamsettings"
    private let blobKey = "toneforge.jamSettings"

    override func setUp() {
        super.setUp()
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    // MARK: - Defaults

    func testFreshStoreDefaults() {
        let store = JamSettingsStore(defaults: defaults)
        XCTAssertEqual(store.scaleVariant, .natural)
        XCTAssertTrue(store.highlightCurrentChord)
        XCTAssertEqual(store.soundPresetId, "dreamyLead")
        XCTAssertEqual(store.octaveShift, 0)
        XCTAssertTrue(store.strumEnabled)
        XCTAssertEqual(store.quantizeMode, .off)
        XCTAssertTrue(store.keyOverrideBySong.isEmpty)
        XCTAssertFalse(store.metronomeEnabled)
        XCTAssertEqual(store.metronomeAccent, .downbeat)
        XCTAssertEqual(store.metronomeSound, .sine)
        XCTAssertFalse(store.metronomeSubdivide)
    }

    // MARK: - Round trip

    func testRoundTripAcrossInstances() {
        let a = JamSettingsStore(defaults: defaults)
        a.scaleVariant = .harmonic
        a.highlightCurrentChord = false
        a.soundPresetId = "pluck"
        a.octaveShift = -2
        a.strumEnabled = false
        a.quantizeMode = .quarter
        a.setKeyOverride("Bb major", analysisId: "song-1")
        a.metronomeEnabled = true
        a.metronomeAccent = .everyBeat
        a.metronomeSound = .woodBlock
        a.metronomeSubdivide = true

        let b = JamSettingsStore(defaults: defaults)
        XCTAssertEqual(b.scaleVariant, .harmonic)
        XCTAssertFalse(b.highlightCurrentChord)
        XCTAssertEqual(b.soundPresetId, "pluck")
        XCTAssertEqual(b.octaveShift, -2)
        XCTAssertFalse(b.strumEnabled)
        XCTAssertEqual(b.quantizeMode, .quarter)
        XCTAssertEqual(b.keyOverride(analysisId: "song-1"), "Bb major")
        XCTAssertTrue(b.metronomeEnabled)
        XCTAssertEqual(b.metronomeAccent, .everyBeat)
        XCTAssertEqual(b.metronomeSound, .woodBlock)
        XCTAssertTrue(b.metronomeSubdivide)
    }

    func testCorruptBlobFallsBackToDefaults() {
        defaults.set(Data("not json".utf8), forKey: blobKey)
        let store = JamSettingsStore(defaults: defaults)
        XCTAssertEqual(store.soundPresetId, "dreamyLead")
        XCTAssertEqual(store.quantizeMode, .off)
    }

    func testOctaveShiftClampedOnLoad() {
        let a = JamSettingsStore(defaults: defaults)
        a.octaveShift = 7 // in-memory unclamped; save clamps
        let b = JamSettingsStore(defaults: defaults)
        XCTAssertEqual(b.octaveShift, 3)
    }

    // MARK: - Key overrides

    func testKeyOverridePerSongAndSketch() {
        let store = JamSettingsStore(defaults: defaults)
        store.setKeyOverride("D minor", analysisId: "song-1")
        store.setKeyOverride("G major", analysisId: nil) // sketch

        XCTAssertEqual(store.keyOverride(analysisId: "song-1"), "D minor")
        XCTAssertNil(store.keyOverride(analysisId: "song-2"))
        XCTAssertEqual(store.keyOverride(analysisId: nil), "G major")

        store.setKeyOverride(nil, analysisId: "song-1")
        XCTAssertNil(store.keyOverride(analysisId: "song-1"))
    }

    // MARK: - Effective key

    func testEffectiveKeyPrefersOverride() {
        let store = JamSettingsStore(defaults: defaults)
        store.setKeyOverride("A minor", analysisId: "song-1")
        let key = store.effectiveKey(detectedKey: "C major", analysisId: "song-1")
        XCTAssertEqual(key?.root.rawValue, 9)
        XCTAssertEqual(key?.scale, .minor)
    }

    func testEffectiveKeyFallsBackToDetected() {
        let store = JamSettingsStore(defaults: defaults)
        let key = store.effectiveKey(detectedKey: "D minor", analysisId: "song-1")
        XCTAssertEqual(key?.root.rawValue, 2)
        XCTAssertEqual(key?.scale, .minor)
    }

    func testEffectiveKeyAppliesVariantToMinorOnly() {
        let store = JamSettingsStore(defaults: defaults)
        store.scaleVariant = .harmonic

        let minor = store.effectiveKey(detectedKey: "D minor", analysisId: nil)
        XCTAssertEqual(minor?.scale, .harmonicMinor)

        let major = store.effectiveKey(detectedKey: "C major", analysisId: nil)
        XCTAssertEqual(major?.scale, .major, "variant leaves major keys alone")

        let modal = store.effectiveKey(detectedKey: "C dorian", analysisId: nil)
        XCTAssertEqual(modal?.scale, .dorian, "variant leaves modal keys alone")
    }

    func testEffectiveKeyNilWhenNothingParses() {
        let store = JamSettingsStore(defaults: defaults)
        XCTAssertNil(store.effectiveKey(detectedKey: nil, analysisId: nil))
        XCTAssertNil(store.effectiveKey(detectedKey: "??", analysisId: nil))
    }
}
