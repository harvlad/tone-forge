// SampleBank.swift
//
// Resolves SamplePack manifests + on-disk file URLs from the three
// pack sources the app supports:
//
//   1. Bundled — shipped inside `App/Resources/Samples/{packId}/`.
//   2. Cached  — downloaded curated packs under
//                `~/Library/Caches/toneforge/packs/{packId}/`.
//   3. Song-derived — synthesised at bundle-load time by mapping
//                `SongBundle.presets` (stem + chops) into virtual
//                packs whose pads carry a `StemSlice` instead of a
//                `filename`.
//
// The bank is intentionally AVFoundation-free — it produces a
// `ResolvedSamplePack` (metadata + local URLs), and the audio
// subsystem on the mobile side reads it into `AVAudioPCMBuffer`s when
// the user activates the pack. That split keeps this file testable
// under SwiftPM without an audio device, and keeps the ~50 MB voice-
// buffer LRU cache next to the code that owns the AVAudioEngine.
//
// Directory layout:
//   {bundleResourcesRoot}/{packId}/manifest.json
//   {bundleResourcesRoot}/{packId}/pads/{filename}
//   {cachedPacksRoot}/{packId}/manifest.json
//   {cachedPacksRoot}/{packId}/pads/{filename}

import Foundation

// MARK: - Resolved pack

/// Manifest + file-URL resolution for a specific SamplePack. Song-
/// derived pads carry their stem-slice info directly on `SamplePad`,
/// so this struct only needs to map file-backed pads to local URLs.
public struct ResolvedSamplePack: Sendable, Equatable {
    public let pack: SamplePack
    /// Pad idx → local audio file URL. Populated only for pads with
    /// non-nil `filename`. Song-derived pads are absent here; their
    /// audio comes from the parent stem via `SamplePad.stemSlice`.
    public let padFileURLs: [Int: URL]

    public init(pack: SamplePack, padFileURLs: [Int: URL]) {
        self.pack = pack
        self.padFileURLs = padFileURLs
    }
}

// MARK: - SampleBank

public final class SampleBank: @unchecked Sendable {

    public enum BankError: Error, LocalizedError {
        case bundledPackNotFound(packId: String)
        case cachedPackNotFound(packId: String)
        case manifestMissing(path: String)
        case manifestDecode(String)
        case padFileMissing(packId: String, filename: String)

        public var errorDescription: String? {
            switch self {
            case .bundledPackNotFound(let id):
                return "Bundled sample pack '\(id)' not found in app resources"
            case .cachedPackNotFound(let id):
                return "Cached sample pack '\(id)' not found on disk"
            case .manifestMissing(let path):
                return "Sample pack manifest missing at \(path)"
            case .manifestDecode(let msg):
                return "Sample pack manifest failed to decode: \(msg)"
            case .padFileMissing(let id, let f):
                return "Sample pack '\(id)' pad file missing: \(f)"
            }
        }
    }

    /// Root directory inside the app bundle where bundled packs live.
    /// nil when running under SwiftPM tests with no bundled resources.
    private let bundleResourcesRoot: URL?
    /// Root directory on disk for downloaded curated packs. Created
    /// on first access. Exposed to `PackClient` so downloads land
    /// under the same tree `loadCached` reads from.
    public let cachedPacksRoot: URL
    private let fileManager: FileManager

    public init(
        bundleResourcesRoot: URL?,
        cachedPacksRoot: URL,
        fileManager: FileManager = .default
    ) {
        self.bundleResourcesRoot = bundleResourcesRoot
        self.cachedPacksRoot = cachedPacksRoot
        self.fileManager = fileManager
    }

    /// Convenience constructor that resolves both roots at their
    /// production paths: `Bundle.main`'s `Samples` subdirectory + the
    /// standard cache dir under Application Support.
    public static func defaultBank(
        mainBundle: Bundle = .main,
        fileManager: FileManager = .default
    ) throws -> SampleBank {
        let bundleRoot = mainBundle.url(
            forResource: "Samples",
            withExtension: nil
        )
        let caches = try fileManager.url(
            for: .cachesDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )
        let packsDir = caches
            .appendingPathComponent("toneforge", isDirectory: true)
            .appendingPathComponent("packs", isDirectory: true)
        try fileManager.createDirectory(at: packsDir, withIntermediateDirectories: true)
        return SampleBank(
            bundleResourcesRoot: bundleRoot,
            cachedPacksRoot: packsDir,
            fileManager: fileManager
        )
    }

    // MARK: - Bundled

    /// Load a bundled pack shipped inside `App/Resources/Samples/`.
    /// Throws `.bundledPackNotFound` if the pack directory is absent,
    /// which is the expected failure mode when running tests without
    /// resource bundling.
    public func loadBundled(packId: String) throws -> ResolvedSamplePack {
        guard let root = bundleResourcesRoot else {
            throw BankError.bundledPackNotFound(packId: packId)
        }
        let packDir = root.appendingPathComponent(packId, isDirectory: true)
        if !fileManager.fileExists(atPath: packDir.path) {
            throw BankError.bundledPackNotFound(packId: packId)
        }
        return try loadFromDirectory(packDir, packId: packId)
    }

    // MARK: - Cached

    /// Local directory where a curated pack's files live.
    public func cachedPackDir(packId: String) -> URL {
        cachedPacksRoot.appendingPathComponent(packId, isDirectory: true)
    }

    /// True if the pack's manifest.json is on disk.
    public func hasCached(packId: String) -> Bool {
        let manifest = cachedPackDir(packId: packId)
            .appendingPathComponent("manifest.json")
        return fileManager.fileExists(atPath: manifest.path)
    }

    /// Every pack id with a manifest.json on disk under
    /// `cachedPacksRoot`, sorted for stable ordering. Source of truth
    /// for "which curated packs are downloaded" — unlike filtering the
    /// network catalog, this works offline and covers packs that later
    /// drop out of the catalog.
    public func listCachedPackIds() -> [String] {
        guard let entries = try? fileManager.contentsOfDirectory(
            at: cachedPacksRoot,
            includingPropertiesForKeys: [.isDirectoryKey],
            options: [.skipsHiddenFiles]
        ) else { return [] }
        return entries
            .filter { url in
                let manifest = url.appendingPathComponent("manifest.json")
                return fileManager.fileExists(atPath: manifest.path)
            }
            .map { $0.lastPathComponent }
            .sorted()
    }

    public func loadCached(packId: String) throws -> ResolvedSamplePack {
        let dir = cachedPackDir(packId: packId)
        guard fileManager.fileExists(atPath: dir.path) else {
            throw BankError.cachedPackNotFound(packId: packId)
        }
        return try loadFromDirectory(dir, packId: packId)
    }

    // MARK: - Song-derived

    /// Build a virtual pack from a SongBundle preset. The pack's
    /// pads carry `stemSlice = (preset.stem, chop.startSec, chop.endSec)`
    /// so the audio side reads them out of the stem file at trigger
    /// time. `filename` is nil for every pad.
    ///
    /// - Parameters:
    ///   - preset: one of `SongBundle.presets` values (e.g. the
    ///     "vocals-chord" or "sections-vocals" preset).
    ///   - packId: stable id to use for this virtual pack. Convention:
    ///     `"song-derived:\(analysisId):\(presetKey)"`.
    ///   - name: display name shown in Browse Packs → Song DNA.
    public static func songDerived(
        preset: BundlePreset,
        packId: String,
        name: String
    ) -> ResolvedSamplePack {
        let family = family(forStemRole: preset.stem)
        let pads: [SamplePad] = preset.chops.map { chop in
            SamplePad(
                padIdx: chop.idx,
                name: label(for: chop),
                family: family,
                colorHint: chop.colorHint,
                filename: nil,
                chokeGroup: nil,
                loopPointSec: nil,
                gainDb: 0,
                defaultQuantize: nil,
                // `.clamped()` enforces the ≤8 s compliance cap on chop
                // duration for every song-derived pad (see StemSlice).
                stemSlice: StemSlice(
                    stemRole: preset.stem,
                    startSec: chop.startSec,
                    endSec: chop.endSec
                ).clamped()
            )
        }
        let pack = SamplePack(
            packId: packId,
            name: name,
            family: family,
            paletteHint: nil,
            pads: pads
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: [:])
    }

    // MARK: - Private

    private func loadFromDirectory(_ dir: URL, packId: String) throws -> ResolvedSamplePack {
        let manifestURL = dir.appendingPathComponent("manifest.json")
        guard fileManager.fileExists(atPath: manifestURL.path) else {
            throw BankError.manifestMissing(path: manifestURL.path)
        }
        let data: Data
        do {
            data = try Data(contentsOf: manifestURL)
        } catch {
            throw BankError.manifestDecode(error.localizedDescription)
        }
        let pack: SamplePack
        do {
            pack = try JSONDecoder().decode(SamplePack.self, from: data)
        } catch {
            throw BankError.manifestDecode(error.localizedDescription)
        }

        let padsDir = dir.appendingPathComponent("pads", isDirectory: true)
        var urls: [Int: URL] = [:]
        for pad in pack.pads {
            guard let filename = pad.filename, !filename.isEmpty else { continue }
            let url = padsDir.appendingPathComponent(filename)
            if !fileManager.fileExists(atPath: url.path) {
                throw BankError.padFileMissing(packId: packId, filename: filename)
            }
            urls[pad.padIdx] = url
        }
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    private static func family(forStemRole role: String) -> SampleFamily {
        switch role.lowercased() {
        case "vocals":            return .vocals
        case "drums":             return .percussion
        case "bass":              return .bass
        case "other", "guitar":   return .stabs
        default:                  return .mixed
        }
    }

    private static func label(for chop: Chop) -> String {
        if let sym = chop.chordSymbol, !sym.isEmpty { return sym }
        if let section = chop.sectionLabel, !section.isEmpty { return section }
        if let kind = chop.kind, !kind.isEmpty { return kind.capitalized }
        return "Chop \(chop.idx)"
    }
}
