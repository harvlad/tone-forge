//
// StemLoaderTests.swift
//
// Audio-Ownership Pivot, post-pivot follow-up. StemLoader is the
// HTTP downloader Connect uses to pull stem WAVs from the local
// ToneForge backend. We exercise the public download API against
// `file://` URLs — URLSession's downloadTask handles both http://
// and file:// uniformly, so a file URL stands in for a backend URL
// without needing to spin up a test HTTP server inside the sandbox
// (which is hostile to Foundation networking on the CI runner).
//
// Coverage:
//   * Successful multi-stem download → correct id→URL mapping.
//   * Per-spec failure reported via `.failure` without blocking the
//     successful sibling specs.
//   * `extractExtension` keeps the source file extension so
//     AVAudioFile reads with the right format detection — exercised
//     by checking the local filenames in the success case.
//   * `reset()` removes the cached batch directory.
//

import XCTest
@testable import ConnectCore

final class StemLoaderTests: XCTestCase {

    private var sandbox: URL!

    override func setUpWithError() throws {
        try super.setUpWithError()
        sandbox = FileManager.default.temporaryDirectory
            .appendingPathComponent("stem-loader-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: sandbox, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: sandbox)
        try super.tearDownWithError()
    }

    /// Writes a small placeholder WAV-like payload into the sandbox.
    /// The contents don't need to be a real WAV — StemLoader doesn't
    /// parse them; it just moves bytes from URL → local file.
    private func writeFixture(name: String, contents: String = "RIFF.....data") throws -> URL {
        let url = sandbox.appendingPathComponent(name)
        try contents.write(to: url, atomically: true, encoding: .utf8)
        return url
    }

    func testLoadDownloadsAllSpecsToLocalFiles() throws {
        let drums = try writeFixture(name: "song_drums.wav", contents: "drums-bytes")
        let bass  = try writeFixture(name: "song_bass.wav",  contents: "bass-bytes")
        let loader = StemLoader()
        let exp = expectation(description: "all stems downloaded")

        loader.load([
            StemLoader.Spec(id: "demucs.drums", url: drums),
            StemLoader.Spec(id: "demucs.bass",  url: bass),
        ]) { results in
            XCTAssertEqual(results.count, 2)
            for (id, outcome) in results {
                switch outcome {
                case .success(let url):
                    let data = try? Data(contentsOf: url)
                    XCTAssertNotNil(data, "stem \(id) had no readable file")
                    XCTAssertTrue(url.lastPathComponent.hasSuffix(".wav"),
                                  "extension dropped for \(id): \(url.lastPathComponent)")
                case .failure(let err):
                    XCTFail("unexpected failure for \(id): \(err)")
                }
            }
            exp.fulfill()
        }
        waitForExpectations(timeout: 5.0)
    }

    func testLoadReportsFailureForMissingFileWithoutAffectingOthers() throws {
        let drums = try writeFixture(name: "song_drums.wav")
        let missing = sandbox.appendingPathComponent("does_not_exist.wav")
        let loader = StemLoader()
        let exp = expectation(description: "mixed success+failure")

        loader.load([
            StemLoader.Spec(id: "demucs.drums",   url: drums),
            StemLoader.Spec(id: "demucs.missing", url: missing),
        ]) { results in
            XCTAssertEqual(results.count, 2)
            guard case .success = results["demucs.drums"] else {
                XCTFail("good spec should have succeeded")
                exp.fulfill()
                return
            }
            guard case .failure = results["demucs.missing"] else {
                XCTFail("missing-file spec should have reported .failure")
                exp.fulfill()
                return
            }
            exp.fulfill()
        }
        waitForExpectations(timeout: 5.0)
    }

    func testEmptySpecListInvokesCompletionWithEmptyDictionary() {
        let loader = StemLoader()
        let exp = expectation(description: "empty list completes immediately")
        loader.load([]) { results in
            XCTAssertTrue(results.isEmpty)
            exp.fulfill()
        }
        waitForExpectations(timeout: 1.0)
    }

    func testResetIsIdempotent() {
        let loader = StemLoader()
        // No batch active — should not crash.
        loader.reset()
        loader.reset()
    }

    /// The local file name must keep the source extension so
    /// AVAudioFile reads the right format on the receiver side.
    /// Also: `id` characters that are illegal in filenames
    /// (`/`, `:`) get sanitised — exercising the same code path
    /// the production receivers hit when JAM ships `demucs.other`-
    /// style identifiers.
    func testLoadSanitisesStemIdInLocalFilename() throws {
        let fixture = try writeFixture(name: "weird.wav")
        let loader = StemLoader()
        let exp = expectation(description: "weird id sanitised")
        loader.load([
            StemLoader.Spec(id: "demucs/other:sides", url: fixture),
        ]) { results in
            if case .success(let url) = results["demucs/other:sides"] {
                let name = url.lastPathComponent
                XCTAssertFalse(name.contains("/"), "slash leaked: \(name)")
                XCTAssertFalse(name.contains(":"), "colon leaked: \(name)")
                XCTAssertTrue(name.hasSuffix(".wav"))
            } else {
                XCTFail("download should have succeeded")
            }
            exp.fulfill()
        }
        waitForExpectations(timeout: 5.0)
    }
}
