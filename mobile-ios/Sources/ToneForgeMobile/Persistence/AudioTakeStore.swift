// AudioTakeStore.swift
//
// On-disk store for "audio takes" — rendered .m4a captures of the
// session's master-bus output (see OutputRecorder). Distinct from
// SessionStore (which persists replayable ContributionEvents, no
// audio): a take IS the audio, so each take is a metadata JSON
// alongside the m4a it describes. Layout mirrors SessionStore:
//
//   {Documents}/takes/{id}.json   — AudioTake metadata
//   {Documents}/takes/{id}.m4a    — the recorded audio
//
// Same fault-tolerance policy as SessionStore/LayerStore: writes are
// atomic, JSON is prettyPrinted + sortedKeys, and `list()` skips
// corrupt metadata AND takes whose audio has gone missing so one bad
// entry never hides the rest or hands the UI a dead play button.
//
// `fileURL` is re-resolved from the takes dir on every load rather
// than trusted from the JSON: the app's container path is not stable
// across launches/reinstalls, so a persisted absolute URL rots. The
// id is the stable key; the URL is derived from it.

import Foundation

/// One recorded audio take. `fileURL` is populated by the store (it
/// points at `{takesDir}/{id}.m4a`) — the persisted value is only a
/// reference copy and is always re-resolved on load.
public struct AudioTake: Codable, Equatable, Identifiable, Sendable {
    public let id: UUID
    public var title: String
    public var fileURL: URL
    public let durationSec: Double
    public let createdAt: Date
    /// analysisId of the song that was loaded when the take was cut,
    /// or nil for a song-less (sketch/metronome) capture. Kept for
    /// future song-scoped filtering; the list is otherwise global.
    public let songId: String?

    public init(
        id: UUID,
        title: String,
        fileURL: URL,
        durationSec: Double,
        createdAt: Date,
        songId: String?
    ) {
        self.id = id
        self.title = title
        self.fileURL = fileURL
        self.durationSec = durationSec
        self.createdAt = createdAt
        self.songId = songId
    }
}

public final class AudioTakeStore: @unchecked Sendable {

    public enum StoreError: Error, LocalizedError {
        case notFound(id: UUID)
        case ingestFailed(String)

        public var errorDescription: String? {
            switch self {
            case .notFound(let id):
                return "Recording \(id) not found on disk"
            case .ingestFailed(let msg):
                return "Could not save recording: \(msg)"
            }
        }
    }

    /// Base directory override for tests; nil = the app's Documents
    /// directory (same convention as SessionStore).
    private let root: URL?
    private let fileManager: FileManager

    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{Documents}/takes/`. Created on first access.
    public func takesDir() throws -> URL {
        let base: URL
        if let root {
            base = root
        } else {
            base = try fileManager.url(
                for: .documentDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            )
        }
        let dir = base.appendingPathComponent("takes", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func metadataURL(id: UUID) throws -> URL {
        try takesDir().appendingPathComponent("\(id.uuidString).json")
    }

    public func audioURL(id: UUID) throws -> URL {
        try takesDir().appendingPathComponent("\(id.uuidString).m4a")
    }

    // MARK: - CRUD

    /// Move a freshly-recorded file into the store and write its
    /// metadata. The recorder writes to a temp URL; ingest owns final
    /// placement so the id ↔ filename invariant holds in one place.
    @discardableResult
    public func ingest(
        recordingAt src: URL,
        title: String,
        durationSec: Double,
        songId: String?,
        id: UUID = UUID(),
        createdAt: Date = Date()
    ) throws -> AudioTake {
        let dest = try audioURL(id: id)
        // Replace any stale audio at the slot, then move the capture in.
        if fileManager.fileExists(atPath: dest.path) {
            try? fileManager.removeItem(at: dest)
        }
        do {
            try fileManager.moveItem(at: src, to: dest)
        } catch {
            // moveItem fails across volumes / when the temp file is
            // already gone; fall back to a copy so a valid capture is
            // never lost to a filesystem quirk.
            do {
                try fileManager.copyItem(at: src, to: dest)
                try? fileManager.removeItem(at: src)
            } catch {
                throw StoreError.ingestFailed(error.localizedDescription)
            }
        }
        let take = AudioTake(
            id: id,
            title: title,
            fileURL: dest,
            durationSec: durationSec,
            createdAt: createdAt,
            songId: songId
        )
        try save(take)
        return take
    }

    /// Write metadata JSON. The audio file is expected to already sit
    /// at `audioURL(id:)` (ingest put it there).
    public func save(_ take: AudioTake) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(take)
        try data.write(to: metadataURL(id: take.id), options: .atomic)
    }

    public func load(id: UUID) throws -> AudioTake {
        let url = try metadataURL(id: id)
        guard fileManager.fileExists(atPath: url.path) else {
            throw StoreError.notFound(id: id)
        }
        var take = try JSONDecoder().decode(AudioTake.self, from: Data(contentsOf: url))
        take.fileURL = try audioURL(id: id)  // re-resolve; container path drifts
        return take
    }

    /// All takes on disk, newest first. Corrupt metadata and takes
    /// whose audio has vanished are skipped so the list never offers a
    /// dead row.
    public func list() -> [AudioTake] {
        guard let dir = try? takesDir(),
              let urls = try? fileManager.contentsOfDirectory(
                  at: dir, includingPropertiesForKeys: nil,
                  options: [.skipsHiddenFiles])
        else { return [] }
        let decoder = JSONDecoder()
        var out: [AudioTake] = []
        for url in urls where url.pathExtension.lowercased() == "json" {
            guard let data = try? Data(contentsOf: url),
                  var take = try? decoder.decode(AudioTake.self, from: data),
                  let audio = try? audioURL(id: take.id),
                  fileManager.fileExists(atPath: audio.path)
            else { continue }
            take.fileURL = audio
            out.append(take)
        }
        return out.sorted { $0.createdAt > $1.createdAt }
    }

    /// Rewrite the title in place (filename is keyed by id, not title).
    public func rename(id: UUID, to newTitle: String) throws {
        var take = try load(id: id)
        take.title = newTitle
        try save(take)
    }

    /// Delete both the metadata and the audio. No-op if already gone.
    public func delete(id: UUID) throws {
        let meta = try metadataURL(id: id)
        let audio = try audioURL(id: id)
        if fileManager.fileExists(atPath: meta.path) {
            try fileManager.removeItem(at: meta)
        }
        if fileManager.fileExists(atPath: audio.path) {
            try fileManager.removeItem(at: audio)
        }
    }

    /// "Recording N" for the next take — N counts existing takes so the
    /// default reads like a running tally, not a UUID.
    public func nextDefaultTitle() -> String {
        "Recording \(list().count + 1)"
    }
}
