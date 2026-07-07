// SampleBankTests.swift
//
// Loader tests for `SampleBank`. Uses a scratch directory in
// NSTemporaryDirectory() to stand in for both the bundled-resources
// root and the cached-packs root — the code path is identical, so this
// exercises `loadBundled` + `loadCached` + `loadFromDirectory`. Also
// checks the song-derived synthesis path (no I/O).
//
// Coverage:
//   - Well-formed manifest + pad files → loads with correct URLs.
//   - Missing manifest → `.manifestMissing`.
//   - Malformed manifest JSON → `.manifestDecode`.
//   - Missing pad audio file listed in manifest → `.padFileMissing`.
//   - Absent packId in the bundle root → `.bundledPackNotFound`.
//   - Absent packId in cache root → `.cachedPackNotFound`.
//   - nil bundle root → `.bundledPackNotFound` (matches SwiftPM-test
//     mode where no bundled resources exist).
//   - `songDerived` produces a virtual pack whose family reflects the
//     stem role and whose pads carry `stemSlice` windows.

import XCTest
@testable import ToneForgeEngine

final class SampleBankTests: XCTestCase {

    private var tempRoot: URL!
    private var fm: FileManager { .default }

    override func setUpWithError() throws {
        tempRoot = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("SampleBankTests-\(UUID().uuidString)",
                                   isDirectory: true)
        try fm.createDirectory(at: tempRoot, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        if let r = tempRoot, fm.fileExists(atPath: r.path) {
            try? fm.removeItem(at: r)
        }
    }

    // MARK: - Helpers

    /// Build a valid manifest.json + pad files for `packId` under `root`.
    private func writeValidPack(
        packId: String,
        root: URL,
        pads: [(idx: Int, filename: String)] = [(0, "0.m4a"), (1, "1.m4a")]
    ) throws -> URL {
        let packDir = root.appendingPathComponent(packId, isDirectory: true)
        let padsDir = packDir.appendingPathComponent("pads", isDirectory: true)
        try fm.createDirectory(at: padsDir, withIntermediateDirectories: true)

        let padObjs: [SamplePad] = pads.map {
            SamplePad(
                padIdx: $0.idx,
                name: "Pad \($0.idx)",
                family: .pads,
                filename: $0.filename
            )
        }
        let pack = SamplePack(
            packId: packId,
            name: packId.capitalized,
            family: .pads,
            pads: padObjs
        )
        let manifestData = try JSONEncoder().encode(pack)
        try manifestData.write(
            to: packDir.appendingPathComponent("manifest.json")
        )
        // Zero-byte audio files — SampleBank only checks existence at
        // load time; PCM decoding is the audio layer's job.
        for (_, filename) in pads {
            fm.createFile(
                atPath: padsDir.appendingPathComponent(filename).path,
                contents: Data()
            )
        }
        return packDir
    }

    private func makeBank(bundleRoot: URL?, cacheRoot: URL) -> SampleBank {
        SampleBank(
            bundleResourcesRoot: bundleRoot,
            cachedPacksRoot: cacheRoot
        )
    }

    // MARK: - Bundled load happy path

    func testLoadBundledReturnsResolvedPackWithURLs() throws {
        _ = try writeValidPack(packId: "starter", root: tempRoot)
        let bank = makeBank(bundleRoot: tempRoot, cacheRoot: tempRoot)

        let resolved = try bank.loadBundled(packId: "starter")

        XCTAssertEqual(resolved.pack.packId, "starter")
        XCTAssertEqual(resolved.pack.pads.count, 2)
        XCTAssertEqual(resolved.padFileURLs.count, 2)
        // URLs point into the pack's pads/ subdir.
        XCTAssertTrue(
            resolved.padFileURLs[0]?.path.hasSuffix("/starter/pads/0.m4a") ?? false
        )
        XCTAssertTrue(
            resolved.padFileURLs[1]?.path.hasSuffix("/starter/pads/1.m4a") ?? false
        )
    }

    // MARK: - Bundled load error paths

    func testLoadBundledMissingPackDirThrows() {
        let bank = makeBank(bundleRoot: tempRoot, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadBundled(packId: "does-not-exist")) { error in
            guard case SampleBank.BankError.bundledPackNotFound(let id) = error else {
                return XCTFail("expected .bundledPackNotFound, got \(error)")
            }
            XCTAssertEqual(id, "does-not-exist")
        }
    }

    func testLoadBundledNilRootAlwaysThrows() {
        // This is the SwiftPM-test scenario: no bundled resources.
        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadBundled(packId: "starter")) { error in
            guard case SampleBank.BankError.bundledPackNotFound = error else {
                return XCTFail("expected .bundledPackNotFound, got \(error)")
            }
        }
    }

    func testLoadBundledMissingManifestThrows() throws {
        let packDir = tempRoot.appendingPathComponent("starter", isDirectory: true)
        try fm.createDirectory(at: packDir, withIntermediateDirectories: true)
        // Directory exists but manifest.json does not.

        let bank = makeBank(bundleRoot: tempRoot, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadBundled(packId: "starter")) { error in
            guard case SampleBank.BankError.manifestMissing = error else {
                return XCTFail("expected .manifestMissing, got \(error)")
            }
        }
    }

    func testLoadBundledMalformedManifestThrows() throws {
        let packDir = tempRoot.appendingPathComponent("starter", isDirectory: true)
        try fm.createDirectory(at: packDir, withIntermediateDirectories: true)
        try Data("{ not json".utf8).write(
            to: packDir.appendingPathComponent("manifest.json")
        )

        let bank = makeBank(bundleRoot: tempRoot, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadBundled(packId: "starter")) { error in
            guard case SampleBank.BankError.manifestDecode = error else {
                return XCTFail("expected .manifestDecode, got \(error)")
            }
        }
    }

    func testLoadBundledMissingPadFileThrows() throws {
        // Valid manifest lists a pad, but the audio file is absent.
        let packDir = tempRoot.appendingPathComponent("starter", isDirectory: true)
        let padsDir = packDir.appendingPathComponent("pads", isDirectory: true)
        try fm.createDirectory(at: padsDir, withIntermediateDirectories: true)
        let pack = SamplePack(
            packId: "starter",
            name: "Starter",
            family: .pads,
            pads: [SamplePad(padIdx: 0, name: "P", family: .pads, filename: "missing.m4a")]
        )
        try JSONEncoder().encode(pack).write(
            to: packDir.appendingPathComponent("manifest.json")
        )

        let bank = makeBank(bundleRoot: tempRoot, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadBundled(packId: "starter")) { error in
            guard case SampleBank.BankError.padFileMissing(let id, let f) = error else {
                return XCTFail("expected .padFileMissing, got \(error)")
            }
            XCTAssertEqual(id, "starter")
            XCTAssertEqual(f, "missing.m4a")
        }
    }

    // MARK: - Cached load happy path + error

    func testLoadCachedHappyPath() throws {
        _ = try writeValidPack(packId: "shoegaze", root: tempRoot)
        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)

        let resolved = try bank.loadCached(packId: "shoegaze")
        XCTAssertEqual(resolved.pack.packId, "shoegaze")
        XCTAssertEqual(resolved.padFileURLs.count, 2)
    }

    func testLoadCachedMissingPackDirThrows() {
        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)
        XCTAssertThrowsError(try bank.loadCached(packId: "nope")) { error in
            guard case SampleBank.BankError.cachedPackNotFound = error else {
                return XCTFail("expected .cachedPackNotFound, got \(error)")
            }
        }
    }

    func testHasCachedReflectsManifestPresence() throws {
        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)
        XCTAssertFalse(bank.hasCached(packId: "shoegaze"))
        _ = try writeValidPack(packId: "shoegaze", root: tempRoot)
        XCTAssertTrue(bank.hasCached(packId: "shoegaze"))
    }

    // MARK: - listCachedPackIds

    func testListCachedPackIdsEmptyDirReturnsEmpty() {
        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)
        XCTAssertEqual(bank.listCachedPackIds(), [])
    }

    func testListCachedPackIdsReturnsSortedManifestBackedDirsOnly() throws {
        _ = try writeValidPack(packId: "zeta", root: tempRoot)
        _ = try writeValidPack(packId: "alpha", root: tempRoot)
        // Directory without a manifest — a half-finished download.
        try fm.createDirectory(
            at: tempRoot.appendingPathComponent("broken", isDirectory: true),
            withIntermediateDirectories: true
        )
        // Stray file at the root — must be ignored.
        fm.createFile(
            atPath: tempRoot.appendingPathComponent("stray.tmp").path,
            contents: Data()
        )

        let bank = makeBank(bundleRoot: nil, cacheRoot: tempRoot)
        XCTAssertEqual(bank.listCachedPackIds(), ["alpha", "zeta"])
    }

    func testListCachedPackIdsMissingRootReturnsEmpty() {
        let bank = makeBank(
            bundleRoot: nil,
            cacheRoot: tempRoot.appendingPathComponent("does-not-exist")
        )
        XCTAssertEqual(bank.listCachedPackIds(), [])
    }

    // MARK: - Song-derived synthesis

    func testSongDerivedProducesVirtualPack() throws {
        let chops = [
            Chop(idx: 0, startSec: 0.0, endSec: 1.0, durationSec: 1.0,
                 chordSymbol: "C"),
            Chop(idx: 1, startSec: 1.0, endSec: 2.0, durationSec: 1.0,
                 chordSymbol: "Am"),
        ]
        let preset = BundlePreset(stem: "vocals", sliceMode: "chord", chops: chops)

        let pack = SampleBank.songDerived(
            preset: preset,
            packId: "song-derived:test:vocals-chord",
            name: "Vocals — chord"
        )

        XCTAssertEqual(pack.pack.packId, "song-derived:test:vocals-chord")
        XCTAssertEqual(pack.pack.family, .vocals)
        XCTAssertEqual(pack.pack.pads.count, 2)
        // Filenames are absent — song-derived pads carry a stem slice.
        XCTAssertNil(pack.pack.pads[0].filename)
        let slice0 = try XCTUnwrap(pack.pack.pads[0].stemSlice)
        XCTAssertEqual(slice0.stemRole, "vocals")
        XCTAssertEqual(slice0.startSec, 0.0, accuracy: 1e-9)
        XCTAssertEqual(slice0.endSec, 1.0, accuracy: 1e-9)
        // Label falls back to chordSymbol.
        XCTAssertEqual(pack.pack.pads[0].name, "C")
        XCTAssertEqual(pack.pack.pads[1].name, "Am")
        // No file URLs for song-derived packs.
        XCTAssertTrue(pack.padFileURLs.isEmpty)
    }

    func testSongDerivedStemRoleMappingCoversKnownStems() {
        let cases: [(String, SampleFamily)] = [
            ("vocals", .vocals),
            ("drums", .percussion),
            ("bass", .bass),
            ("other", .stabs),
            ("guitar", .stabs),
            ("unknown-role", .mixed),
        ]
        for (role, expected) in cases {
            let preset = BundlePreset(stem: role, sliceMode: "chord", chops: [])
            let pack = SampleBank.songDerived(preset: preset, packId: "p", name: "n")
            XCTAssertEqual(pack.pack.family, expected,
                           "stem role \(role) should map to \(expected)")
        }
    }
}
