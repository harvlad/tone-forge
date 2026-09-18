// AudioTakeStoreTests.swift
//
// Disk contract for Documents/takes: ingest moves the audio in beside
// its metadata, save/load round-trips (with fileURL re-resolved from
// the id, not trusted from JSON), newest-first listing that skips
// corrupt metadata AND takes whose audio has vanished, rename, and
// delete removing both files.

import XCTest
@testable import ToneForgeMobile

final class AudioTakeStoreTests: XCTestCase {

    private var root: URL!
    private var store: AudioTakeStore!

    override func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("audio-take-store-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: root, withIntermediateDirectories: true)
        store = AudioTakeStore(root: root)
    }

    override func tearDownWithError() throws {
        try? FileManager.default.removeItem(at: root)
    }

    /// Write a throwaway file to stand in for a recorded m4a and return
    /// its URL (ingest MOVES it, so each call needs a fresh source).
    private func makeFakeRecording(bytes: String = "fake-audio") throws -> URL {
        let src = FileManager.default.temporaryDirectory
            .appendingPathComponent("rec-\(UUID().uuidString).m4a")
        try Data(bytes.utf8).write(to: src)
        return src
    }

    func testIngestPlacesAudioAndMetadata() throws {
        let src = try makeFakeRecording()
        let take = try store.ingest(
            recordingAt: src, title: "Recording 1",
            durationSec: 12.5, songId: "song-1")

        XCTAssertFalse(FileManager.default.fileExists(atPath: src.path),
                       "ingest moves the source file, not copies it")
        XCTAssertTrue(FileManager.default.fileExists(atPath: take.fileURL.path))
        XCTAssertEqual(take.fileURL, try store.audioURL(id: take.id))
        XCTAssertEqual(take.title, "Recording 1")
        XCTAssertEqual(take.durationSec, 12.5)
        XCTAssertEqual(take.songId, "song-1")
    }

    func testSaveLoadRoundTripResolvesFileURL() throws {
        let src = try makeFakeRecording()
        // Round epoch — Date() sub-second fractions aren't bit-stable
        // through JSON double formatting (same guard as SessionStore).
        let created = Date(timeIntervalSince1970: 1_700_000_000)
        let take = try store.ingest(
            recordingAt: src, title: "Take", durationSec: 3,
            songId: nil, createdAt: created)
        let loaded = try store.load(id: take.id)
        XCTAssertEqual(loaded, take)
        // fileURL is derived from the id, so it survives even if the
        // persisted path were stale.
        XCTAssertEqual(loaded.fileURL, try store.audioURL(id: take.id))
    }

    func testListIsNewestFirst() throws {
        let old = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "old",
            durationSec: 1, songId: nil,
            createdAt: Date(timeIntervalSince1970: 1_000))
        let new = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "new",
            durationSec: 1, songId: nil,
            createdAt: Date(timeIntervalSince1970: 2_000))
        XCTAssertEqual(store.list().map(\.id), [new.id, old.id])
    }

    func testListSkipsCorruptMetadataAndMissingAudio() throws {
        let good = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "good",
            durationSec: 1, songId: nil)

        // A corrupt metadata file must not hide the good take.
        try Data("not json".utf8).write(
            to: store.takesDir()
                .appendingPathComponent("\(UUID().uuidString).json"))

        // A take whose audio has been deleted out from under it is
        // skipped — the list never offers a dead play button.
        let orphan = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "orphan",
            durationSec: 1, songId: nil)
        try FileManager.default.removeItem(at: try store.audioURL(id: orphan.id))

        XCTAssertEqual(store.list().map(\.id), [good.id])
    }

    func testRenameRewritesTitleInPlace() throws {
        let take = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "before",
            durationSec: 1, songId: nil)
        try store.rename(id: take.id, to: "after")
        XCTAssertEqual(try store.load(id: take.id).title, "after")
        XCTAssertEqual(store.list().count, 1, "rename must not add a file")
    }

    func testDeleteRemovesBothFiles() throws {
        let take = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "gone",
            durationSec: 1, songId: nil)
        let audio = try store.audioURL(id: take.id)
        let meta = try store.metadataURL(id: take.id)

        try store.delete(id: take.id)
        XCTAssertFalse(FileManager.default.fileExists(atPath: audio.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: meta.path))
        XCTAssertTrue(store.list().isEmpty)
        // Deleting again is harmless.
        XCTAssertNoThrow(try store.delete(id: take.id))
    }

    func testNextDefaultTitleCountsUp() throws {
        XCTAssertEqual(store.nextDefaultTitle(), "Recording 1")
        _ = try store.ingest(
            recordingAt: try makeFakeRecording(), title: "Recording 1",
            durationSec: 1, songId: nil)
        XCTAssertEqual(store.nextDefaultTitle(), "Recording 2")
    }
}
