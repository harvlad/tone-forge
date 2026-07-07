// SongDnaPackTests.swift
//
// Coverage for `SongDnaPack.synthesize(from:)`:
//   - Deterministic ordering by stem priority
//     (vocals > drums > bass > other > guitar > unknown alpha) then
//     stem alpha then sliceMode alpha.
//   - Empty-chop presets are skipped.
//   - Resolved packs carry the analysisId in `packId` and the stem
//     capitalization in `displayName`.
//   - Family mapping (via SampleBank.songDerived) preserved through
//     the round-trip.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

final class SongDnaPackTests: XCTestCase {

    // MARK: - Fixtures

    private func makeBundle(presets: [String: BundlePreset]) -> SongBundle {
        SongBundle(
            bundleVersion: 1,
            analysisId: "song-abc",
            meta: BundleMeta(
                title: "T", artist: "A", sourceUrl: "",
                durationSec: 60.0, tempoBpm: 120.0, detectedKey: nil
            ),
            timeline: BundleTimeline(),
            stems: [],
            presets: presets
        )
    }

    private func chop(_ idx: Int) -> Chop {
        Chop(idx: idx, startSec: Double(idx), endSec: Double(idx) + 1.0,
             durationSec: 1.0, chordSymbol: "C")
    }

    // MARK: - Ordering

    func testSynthesizeOrdersByStemPriority() {
        let presets: [String: BundlePreset] = [
            "guitar:chord":  BundlePreset(stem: "guitar", sliceMode: "chord", chops: [chop(0)]),
            "vocals:chord":  BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)]),
            "bass:chord":    BundlePreset(stem: "bass",   sliceMode: "chord", chops: [chop(0)]),
            "drums:beat":    BundlePreset(stem: "drums",  sliceMode: "beat",  chops: [chop(0)]),
            "other:section": BundlePreset(stem: "other",  sliceMode: "section", chops: [chop(0)]),
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs.map { $0.stem },
                       ["vocals", "drums", "bass", "other", "guitar"])
    }

    func testSynthesizeSortsSameStemBySliceModeAlpha() {
        let presets: [String: BundlePreset] = [
            "vocals:section": BundlePreset(stem: "vocals", sliceMode: "section", chops: [chop(0)]),
            "vocals:chord":   BundlePreset(stem: "vocals", sliceMode: "chord",   chops: [chop(0)]),
            "vocals:beat":    BundlePreset(stem: "vocals", sliceMode: "beat",    chops: [chop(0)]),
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs.map { $0.sliceMode }, ["beat", "chord", "section"])
    }

    func testSynthesizePlacesUnknownStemsLast() {
        let presets: [String: BundlePreset] = [
            "keys:chord":   BundlePreset(stem: "keys",   sliceMode: "chord", chops: [chop(0)]),
            "vocals:chord": BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)]),
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs.map { $0.stem }, ["vocals", "keys"])
    }

    // MARK: - Skip empty

    func testSynthesizeSkipsPresetsWithEmptyChops() {
        let presets: [String: BundlePreset] = [
            "vocals:chord":  BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)]),
            "drums:beat":    BundlePreset(stem: "drums",  sliceMode: "beat",  chops: []),
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs.count, 1)
        XCTAssertEqual(packs[0].stem, "vocals")
    }

    // MARK: - Pack shape

    func testSynthesizeCarriesAnalysisIdInPackId() {
        let presets: [String: BundlePreset] = [
            "vocals:chord": BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)])
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs[0].pack.pack.packId, "song-derived:song-abc:vocals-chord")
    }

    func testSynthesizeCapitalizesStemInDisplayName() {
        let presets: [String: BundlePreset] = [
            "vocals:chord": BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)])
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs[0].displayName, "Vocals — chord")
    }

    func testSynthesizeCarriesChopCountAndPresetKey() {
        let presets: [String: BundlePreset] = [
            "vocals:chord": BundlePreset(
                stem: "vocals", sliceMode: "chord",
                chops: [chop(0), chop(1), chop(2)]
            )
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        XCTAssertEqual(packs[0].chopCount, 3)
        XCTAssertEqual(packs[0].presetKey, "vocals:chord")
        XCTAssertEqual(packs[0].id, "vocals:chord")
    }

    // MARK: - Family mapping preserved

    func testSynthesizePreservesStemRoleFamilyMapping() {
        let presets: [String: BundlePreset] = [
            "vocals:chord": BundlePreset(stem: "vocals", sliceMode: "chord", chops: [chop(0)]),
            "drums:beat":   BundlePreset(stem: "drums",  sliceMode: "beat",  chops: [chop(0)]),
            "bass:chord":   BundlePreset(stem: "bass",   sliceMode: "chord", chops: [chop(0)]),
        ]
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: presets))
        let byStem = Dictionary(uniqueKeysWithValues: packs.map { ($0.stem, $0.pack.pack.family) })
        XCTAssertEqual(byStem["vocals"], .vocals)
        XCTAssertEqual(byStem["drums"],  .percussion)
        XCTAssertEqual(byStem["bass"],   .bass)
    }

    // MARK: - Empty bundle

    func testSynthesizeEmptyBundleReturnsEmpty() {
        let packs = SongDnaPack.synthesize(from: makeBundle(presets: [:]))
        XCTAssertTrue(packs.isEmpty)
    }
}
