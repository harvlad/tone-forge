// CrateDedupeGuardTests.swift
//
// Regression guard for the "double-defined Crate types" clean-build break.
//
// The Vinyl Crate landed its `Crate*` DTOs (CrateTrack, CrateCandidate,
// CrateFeatures, CrateLicense, CrateFacetQuery, …) in TWO modules at once:
// ToneForgeEngine (the shared engine iOS + desktop both depend on) AND
// JamDesktopCore. The JamDesktop app target imports both, so every Crate name
// became "ambiguous for type lookup" — a clean `swift build` failed with ~358
// errors. The canonical definitions now live ONCE, in ToneForgeEngine; the
// JamDesktopCore copies were deleted. These two guards make a re-introduction
// fail fast and by name instead of as a wall of ambiguity errors.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class CrateDedupeGuardTests: XCTestCase {

    /// Compile-time guard. This target imports BOTH JamDesktopCore and
    /// ToneForgeEngine, and names each `Crate*` type UNQUALIFIED below. They only
    /// build while each name resolves to exactly one module. Re-declare any of
    /// them in JamDesktopCore and these references go ambiguous — the 358-error
    /// app-target regression collapses into a single, named build failure here.
    func testCrateTypesResolveUnambiguously() {
        _ = CrateTrack.self
        _ = CrateCandidate.self
        _ = CrateCandidatesResponse.self
        _ = CrateSearchResponse.self
        _ = CrateFeatures.self
        _ = CrateLicense.self
        _ = CrateLicenseKind.self
        _ = CrateCamelot.self
        _ = CrateFacetQuery.self
        _ = CrateGenreMode.self

        // Exercise the unified model from the desktop target — usable, not just
        // importable (the memberwise inits the picker/tests synthesize with).
        let t = CrateTrack(
            id: "crate:x:1", title: "Guard",
            license: CrateLicense(licenseId: "CC0", attribution: "x"),
            features: CrateFeatures(tempoBpm: 120))
        XCTAssertEqual(t.id, "crate:x:1")
        XCTAssertEqual(t.tempo, 120)
    }

    /// Source-scan guard. No `public` type whose name starts with `Crate` may be
    /// defined in BOTH ToneForgeEngine and JamDesktopCore — that co-definition is
    /// exactly what made the app target ambiguous. Fails loudly with the
    /// offending names, before the app target ever sees an ambiguity error.
    func testNoCrateTypeIsDefinedInBothModules() throws {
        let repoRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()   // JamDesktopCoreTests
            .deletingLastPathComponent()   // Tests
            .deletingLastPathComponent()   // jam-desktop
            .deletingLastPathComponent()   // repo root

        let engineDir = repoRoot
            .appendingPathComponent("mobile-ios/Sources/ToneForgeEngine")
        let coreDir = repoRoot
            .appendingPathComponent("jam-desktop/Sources/JamDesktopCore")

        let engineTypes = try publicCrateTypeNames(in: engineDir)
        let coreTypes = try publicCrateTypeNames(in: coreDir)

        // Sanity: the canonical home really is the engine. Guards the scan
        // itself against silently finding nothing and passing vacuously.
        XCTAssertTrue(
            engineTypes.contains("CrateTrack"),
            "expected ToneForgeEngine to own CrateTrack; scan found \(engineTypes.sorted())")

        let collisions = engineTypes.intersection(coreTypes).sorted()
        XCTAssertTrue(
            collisions.isEmpty,
            "Crate* type(s) defined in BOTH ToneForgeEngine and JamDesktopCore "
                + "— each is ambiguous in the JamDesktop app target (which imports "
                + "both modules). Keep ONE definition, in ToneForgeEngine. "
                + "Duplicated: \(collisions)")
    }

    /// Every `public`/`open` type name beginning with "Crate" declared in the
    /// `.swift` sources under `dir`.
    private func publicCrateTypeNames(in dir: URL) throws -> Set<String> {
        let fm = FileManager.default
        guard let walker = fm.enumerator(at: dir, includingPropertiesForKeys: nil)
        else {
            XCTFail("could not enumerate \(dir.path)")
            return []
        }
        // Leading whitespace, public|open, optional `final`, the type kind, then
        // a name starting with "Crate".
        let re = try NSRegularExpression(
            pattern:
                #"(?m)^[ \t]*(?:public|open)[ \t]+(?:final[ \t]+)?"#
                + #"(?:struct|class|enum|actor|protocol)[ \t]+(Crate[A-Za-z0-9_]*)"#)
        var names: Set<String> = []
        for case let url as URL in walker where url.pathExtension == "swift" {
            let src = try String(contentsOf: url, encoding: .utf8)
            let ns = src as NSString
            let all = re.matches(in: src, range: NSRange(location: 0, length: ns.length))
            for m in all { names.insert(ns.substring(with: m.range(at: 1))) }
        }
        return names
    }
}
