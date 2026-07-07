// ComplianceTests.swift
//
// P7 compliance gates, codified so they run on every test pass
// instead of relying on someone remembering the grep:
//
//   1. No streaming-ingestion strings anywhere in shipped source.
//   2. No TODO markers (style rule; `// FUTURE:` is the sanctioned
//      escape hatch).
//   3. The upload surface is EXACTLY AnalyzeClient + LayerClient,
//      and neither references the local sample/bounce stores — new
//      upload paths must consciously update this test.
//   4. `neverUpload` tripwire: mic/vocoded metadata can't be marked
//      uploadable via init, decode, or the store's save path.
//   5. Attestation gates original-song bounce BEFORE any disk work.
//   6. The 8 s cap is one constant everywhere and the sample store
//      enforces it (±100 ms per the ship spec; the store allows
//      +1 ms float slack).
//
// Source-tree scans resolve the package root from #filePath and
// skip (not fail) when the tree isn't present — the authoritative
// run is macOS `swift test` from the repo checkout (doctrine R12).

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

final class ComplianceTests: XCTestCase {

    // MARK: - Source-tree scanning

    /// mobile-ios package root, resolved from this file's location.
    private static let packageRoot = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent()   // ComplianceTests.swift
        .deletingLastPathComponent()   // ToneForgeMobileTests
        .deletingLastPathComponent()   // Tests

    /// Shipped-source directories the compliance greps cover.
    private static let scannedDirs = ["Sources", "App", "UITests"]

    /// Every readable text file under the scanned dirs, as
    /// (path-relative-to-root, contents). Skips the test run when
    /// the tree isn't present (bundle-only execution).
    private func shippedSources() throws -> [(path: String, text: String)] {
        let fm = FileManager.default
        var out: [(String, String)] = []
        for dir in Self.scannedDirs {
            let base = Self.packageRoot.appendingPathComponent(dir)
            guard fm.fileExists(atPath: base.path) else {
                throw XCTSkip("source tree not present at \(base.path)")
            }
            guard let walker = fm.enumerator(
                at: base, includingPropertiesForKeys: [.isRegularFileKey]
            ) else { continue }
            for case let url as URL in walker {
                guard (try? url.resourceValues(forKeys: [.isRegularFileKey])
                    .isRegularFile) == true else { continue }
                guard let text = try? String(contentsOf: url, encoding: .utf8)
                else { continue }   // binary — greps don't apply
                let rel = url.path.replacingOccurrences(
                    of: Self.packageRoot.path + "/", with: ""
                )
                out.append((rel, text))
            }
        }
        XCTAssertFalse(out.isEmpty, "scan found no source files")
        return out
    }

    func testNoStreamingIngestionStrings() throws {
        // Assembled to keep the banned words out of any literal that
        // a self-scan could ever trip on.
        let banned = ["you" + "tube", "yt" + "-dlp", "spo" + "tify"]
        var hits: [String] = []
        for (path, text) in try shippedSources() {
            let lower = text.lowercased()
            for word in banned where lower.contains(word) {
                hits.append("\(path): \(word)")
            }
        }
        XCTAssertTrue(hits.isEmpty,
                      "streaming-ingestion strings in shipped source:\n"
                      + hits.joined(separator: "\n"))
    }

    func testNoTODOMarkers() throws {
        var hits: [String] = []
        for (path, text) in try shippedSources() where path.hasSuffix(".swift") {
            for (n, line) in text.split(
                separator: "\n", omittingEmptySubsequences: false
            ).enumerated() {
                if line.contains("TO" + "DO"), !line.contains("FUTURE") {
                    hits.append("\(path):\(n + 1)")
                }
            }
        }
        XCTAssertTrue(hits.isEmpty,
                      "TO" + "DO markers (use // FUTURE:):\n"
                      + hits.joined(separator: "\n"))
    }

    /// The whole outbound-upload surface is two files, and neither
    /// touches the local sample/bounce stores. Adding `httpBody` /
    /// `uploadTask` anywhere else fails here until the new path is
    /// consciously reviewed and listed.
    func testUploadSurfaceIsClosedAndTouchesNoLocalAudioStores() throws {
        let allowedUploaders: Set<String> = [
            "Sources/ToneForgeEngine/AnalyzeClient.swift",
            "Sources/ToneForgeEngine/LayerClient.swift",
        ]
        let localStoreMarkers = [
            "Documents/samples", "Documents/bounces",
            "PadSampleStore", "bounces", "samplesDir",
        ]
        var uploaders: Set<String> = []
        for (path, text) in try shippedSources() where path.hasSuffix(".swift") {
            let uploads = text.contains("httpBody")
                || text.contains("uploadTask")
            guard uploads else { continue }
            uploaders.insert(path)
            for marker in localStoreMarkers {
                XCTAssertFalse(
                    text.contains(marker),
                    "\(path) is an upload path and references '\(marker)'"
                )
            }
        }
        XCTAssertEqual(uploaders, allowedUploaders,
                       "upload surface changed — review for compliance"
                       + " before allow-listing")
    }

    // MARK: - neverUpload tripwire

    func testMicAndVocodedMetadataCannotBeMarkedUploadable() {
        for source: PadSampleMetadata.Source in [.mic, .vocoded] {
            let meta = PadSampleMetadata(
                source: source, classification: .unknown, confidence: 0,
                durationSec: 1, sampleRate: 48_000, channels: 1,
                colorHint: 0,
                neverUpload: false   // hostile caller
            )
            XCTAssertTrue(meta.neverUpload,
                          "\(source) must force neverUpload")
        }
        // songChop is the only source that may opt in.
        let chop = PadSampleMetadata(
            source: .songChop, classification: .unknown, confidence: 0,
            durationSec: 1, sampleRate: 48_000, channels: 1,
            colorHint: 0, neverUpload: false
        )
        XCTAssertFalse(chop.neverUpload)
    }

    func testHandEditedSidecarCannotFlipNeverUpload() throws {
        // A user (or sync tool) editing the JSON sidecar on disk must
        // not be able to make a mic sample uploadable.
        let json = """
        {"schemaVersion":1,
         "id":"6F9619FF-8B86-D011-B42D-00C04FC964FF",
         "source":"mic","classification":"unknown","confidence":0,
         "createdAt":0,"durationSec":1,"sampleRate":48000,
         "channels":1,"colorHint":0,
         "neverUpload":false}
        """
        let decoded = try JSONDecoder().decode(
            PadSampleMetadata.self, from: Data(json.utf8)
        )
        XCTAssertTrue(decoded.neverUpload,
                      "decode re-enforces the tripwire")
    }

    @MainActor
    func testStoreSavePreservesTheTripwire() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("compliance-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let store = PadSampleStore(root: tmp)
        let saved = try await store.save(
            samples: [Float](repeating: 0.1, count: 4_800),
            sampleRate: 48_000,
            metadata: PadSampleMetadata(
                source: .mic, classification: .unknown, confidence: 0,
                durationSec: 0, sampleRate: 0, channels: 1,
                colorHint: 0, neverUpload: false
            )
        )
        XCTAssertTrue(saved.neverUpload)
        XCTAssertTrue(store.metadata(id: saved.id)?.neverUpload ?? false)
    }

    // MARK: - Attestation-gated bounce

    func testBounceWithOriginalSongRequiresAttestation() throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("compliance-bounce-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmp, withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: tmp) }

        let session = SessionCapture(
            sessionId: UUID(), songBackendId: "song-x", appMode: .sample,
            capturedAt: Date(), tempoBpm: 120,
            events: [ContributionEvent(
                source: .touch, kind: .padDown(row: 1, col: 1),
                timestamp: 0, hostTime: 0
            )],
            padMapping: [:]
        )
        XCTAssertThrowsError(try SessionBounceRenderer.bounceSession(
            session,
            padBuffers: [:],
            layout: SampleModeLayout(content: [:]),
            includeOriginalSong: true,
            attestationAccepted: false,
            outputDirectory: tmp
        )) { error in
            XCTAssertEqual(
                error as? SessionBounceRenderer.RenderError,
                .attestationRequired
            )
        }
        // The gate fires before ANY disk work — nothing appears in
        // the output directory, not even a partial file.
        let leftovers = try FileManager.default
            .contentsOfDirectory(atPath: tmp.path)
        XCTAssertTrue(leftovers.isEmpty,
                      "attestation throw must precede file creation: "
                      + "\(leftovers)")
    }

    // MARK: - 8 s cap

    func testEightSecondCapIsOneConstantEverywhere() {
        XCTAssertEqual(StemSlice.maxChopDurationSec, 8.0, accuracy: 0.0)
        XCTAssertEqual(MicRecorder.maxDurationSec,
                       StemSlice.maxChopDurationSec)
        XCTAssertEqual(VocoderCaptureSession.maxDurationSec,
                       StemSlice.maxChopDurationSec)
    }

    @MainActor
    func testSampleStoreEnforcesTheCapWithinTolerance() async throws {
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("compliance-cap-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: tmp) }
        let store = PadSampleStore(root: tmp)
        let rate = 48_000.0
        func meta() -> PadSampleMetadata {
            PadSampleMetadata(
                source: .mic, classification: .unknown, confidence: 0,
                durationSec: 0, sampleRate: 0, channels: 1, colorHint: 0
            )
        }

        // 7.90 s — comfortably legal.
        _ = try await store.save(
            samples: [Float](repeating: 0.1, count: Int(7.9 * rate)),
            sampleRate: rate, metadata: meta()
        )
        // 8.00 s — exactly the cap, legal.
        _ = try await store.save(
            samples: [Float](repeating: 0.1, count: Int(8.0 * rate)),
            sampleRate: rate, metadata: meta()
        )
        // 8.10 s — beyond the ±100 ms ship tolerance, rejected.
        do {
            _ = try await store.save(
                samples: [Float](repeating: 0.1, count: Int(8.1 * rate)),
                sampleRate: rate, metadata: meta()
            )
            XCTFail("8.1 s payload must be rejected")
        } catch let PadSampleStore.StoreError.durationExceedsCap(s) {
            XCTAssertEqual(s, 8.1, accuracy: 0.01)
        }
    }
}
