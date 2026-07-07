// PadSampleStoreTests.swift
//
// Disk round-trip, restart persistence, the 8 s compliance cap, and
// list hygiene for the local-sample store — all against a temp root.

import XCTest
import AVFoundation
import ToneForgeEngine
@testable import ToneForgeMobile

@MainActor
final class PadSampleStoreTests: XCTestCase {

    private var root: URL!

    override func setUp() {
        super.setUp()
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("PadSampleStoreTests-\(UUID().uuidString)")
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        super.tearDown()
    }

    private func makeStore() -> PadSampleStore {
        PadSampleStore(root: root)
    }

    private func makeMeta(id: UUID = UUID()) -> PadSampleMetadata {
        PadSampleMetadata(
            id: id,
            source: .mic,
            classification: .percussion,
            confidence: 0.8,
            durationSec: 0,  // store overwrites from payload
            sampleRate: 0,
            channels: 0,
            colorHint: 0xFF8C3A
        )
    }

    private func tone(_ seconds: Double, sr: Double = 48_000) -> [Float] {
        (0..<Int(seconds * sr)).map { i in
            0.5 * Float(sin(2 * .pi * 440 * Double(i) / sr))
        }
    }

    // MARK: - Save + round trip

    func testSaveWritesWAVAndSidecarAndPublishes() async throws {
        let store = makeStore()
        let payload = tone(0.5)
        let saved = try await store.save(
            samples: payload, sampleRate: 48_000, metadata: makeMeta()
        )

        XCTAssertTrue(FileManager.default.fileExists(
            atPath: try store.wavURL(id: saved.id).path
        ))
        XCTAssertTrue(FileManager.default.fileExists(
            atPath: try store.sidecarURL(id: saved.id).path
        ))
        XCTAssertEqual(store.samples.map(\.id), [saved.id])
        // Payload is the source of truth for audio facts.
        XCTAssertEqual(saved.durationSec, 0.5, accuracy: 0.001)
        XCTAssertEqual(saved.sampleRate, 48_000)
        XCTAssertEqual(saved.channels, 1)
    }

    func testLoadBufferRoundTripsSamplesLosslessly() async throws {
        let store = makeStore()
        let payload = tone(0.25)
        let saved = try await store.save(
            samples: payload, sampleRate: 48_000, metadata: makeMeta()
        )

        let buffer = try await store.loadBuffer(id: saved.id)
        XCTAssertEqual(Int(buffer.frameLength), payload.count)
        XCTAssertEqual(buffer.format.channelCount, 1)
        XCTAssertEqual(buffer.format.sampleRate, 48_000)

        // Float32 WAV is bit-exact.
        let data = buffer.floatChannelData![0]
        for i in stride(from: 0, to: payload.count, by: 997) {
            XCTAssertEqual(data[i], payload[i])
        }
    }

    func testRestartPersistence() async throws {
        let saved: PadSampleMetadata
        do {
            let store = makeStore()
            saved = try await store.save(
                samples: tone(0.3), sampleRate: 48_000, metadata: makeMeta()
            )
        }
        let reopened = makeStore()
        XCTAssertEqual(reopened.samples.map(\.id), [saved.id])
        XCTAssertEqual(reopened.samples.first, saved)
    }

    // MARK: - Compliance cap

    func testEightSecondCapEnforced() async throws {
        let store = makeStore()
        // Exactly at the cap: fine.
        _ = try await store.save(
            samples: tone(8.0), sampleRate: 48_000, metadata: makeMeta()
        )
        // Over the cap: rejected.
        do {
            _ = try await store.save(
                samples: tone(8.11), sampleRate: 48_000, metadata: makeMeta()
            )
            XCTFail("expected durationExceedsCap")
        } catch PadSampleStore.StoreError.durationExceedsCap {
            // expected
        }
    }

    func testEmptyPayloadRejected() async {
        let store = makeStore()
        do {
            _ = try await store.save(
                samples: [], sampleRate: 48_000, metadata: makeMeta()
            )
            XCTFail("expected emptyPayload")
        } catch {
            // expected
        }
    }

    // MARK: - neverUpload tripwire survives disk

    func testNeverUploadSurvivesDiskRoundTrip() async throws {
        let store = makeStore()
        let saved = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        XCTAssertTrue(saved.neverUpload)
        let reopened = makeStore()
        XCTAssertTrue(reopened.samples.first!.neverUpload)
    }

    // MARK: - Update / delete

    func testUpdateMetadataPersists() async throws {
        let store = makeStore()
        var meta = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        meta.userClassOverride = .vocalChop
        try store.updateMetadata(meta)

        XCTAssertEqual(store.samples.first?.userClassOverride, .vocalChop)
        let reopened = makeStore()
        XCTAssertEqual(reopened.samples.first?.userClassOverride, .vocalChop)
    }

    func testUpdateUnknownIdThrows() {
        let store = makeStore()
        XCTAssertThrowsError(try store.updateMetadata(makeMeta()))
    }

    func testDeleteRemovesFilesAndListing() async throws {
        let store = makeStore()
        let saved = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        store.delete(id: saved.id)

        XCTAssertTrue(store.samples.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: try store.wavURL(id: saved.id).path
        ))
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: try store.sidecarURL(id: saved.id).path
        ))
    }

    // MARK: - List hygiene

    func testCorruptSidecarSkippedNotFatal() async throws {
        let store = makeStore()
        let good = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        let junk = try store.samplesDir()
            .appendingPathComponent("\(UUID().uuidString).json")
        try Data("not json{".utf8).write(to: junk)

        let reopened = makeStore()
        XCTAssertEqual(reopened.samples.map(\.id), [good.id])
    }

    func testSidecarWithoutWAVSkipped() async throws {
        let store = makeStore()
        let saved = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        try FileManager.default.removeItem(at: try store.wavURL(id: saved.id))

        let reopened = makeStore()
        XCTAssertTrue(reopened.samples.isEmpty)
    }

    func testNewestFirstOrdering() async throws {
        let store = makeStore()
        let older = makeMeta()
        var olderMeta = older
        olderMeta.createdAt = Date(timeIntervalSinceNow: -100)
        _ = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: olderMeta
        )
        let newer = try await store.save(
            samples: tone(0.2), sampleRate: 48_000, metadata: makeMeta()
        )
        XCTAssertEqual(store.samples.first?.id, newer.id)
    }

    // MARK: - Storage accounting

    func testTotalBytesCountsPayloads() async throws {
        let store = makeStore()
        XCTAssertEqual(store.totalBytes(), 0)
        _ = try await store.save(
            samples: tone(1.0), sampleRate: 48_000, metadata: makeMeta()
        )
        // 48 000 Float32 frames ≈ 192 kB + header + sidecar.
        XCTAssertGreaterThan(store.totalBytes(), 190_000)
    }
}
