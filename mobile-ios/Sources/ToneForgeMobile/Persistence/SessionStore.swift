// SessionStore.swift
//
// Persists SessionCaptures as JSON under `Documents/sessions/` (P6).
// One file per session, named by sessionId, so the recorder's
// autosaves and final save all overwrite the same file. Sessions
// hold no audio — only event timings and pad references — so they
// are tiny (a busy 3-minute take is well under 100 KB) and the
// session-load ≤2 s ship gate is comfortably met by plain JSON.
//
// Same fault-tolerance policy as LayerStore: `list()` skips corrupt
// files silently so one bad JSON never hides the rest, writes are
// atomic, and JSON is prettyPrinted + sortedKeys so files diff
// cleanly.

import Foundation
import ToneForgeEngine

public final class SessionStore {

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   the app's Documents directory.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{Documents}/sessions/`. Created on first access.
    public func sessionsDir() throws -> URL {
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
        let dir = base.appendingPathComponent("sessions", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func jsonURL(sessionId: UUID) throws -> URL {
        try sessionsDir()
            .appendingPathComponent("\(sessionId.uuidString).json")
    }

    // MARK: - CRUD

    public func save(_ session: SessionCapture) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(session)
        try data.write(
            to: jsonURL(sessionId: session.sessionId),
            options: .atomic
        )
    }

    public func load(sessionId: UUID) throws -> SessionCapture {
        let data = try Data(contentsOf: jsonURL(sessionId: sessionId))
        return try JSONDecoder().decode(SessionCapture.self, from: data)
    }

    /// All sessions on disk, newest first. Corrupt files are skipped.
    public func list() -> [SessionCapture] {
        guard let dir = try? sessionsDir(),
              let urls = try? fileManager.contentsOfDirectory(
                  at: dir, includingPropertiesForKeys: nil)
        else { return [] }
        return urls
            .filter { $0.pathExtension == "json" }
            .compactMap { url -> SessionCapture? in
                guard let data = try? Data(contentsOf: url) else {
                    return nil
                }
                return try? JSONDecoder().decode(
                    SessionCapture.self, from: data)
            }
            .sorted { $0.capturedAt > $1.capturedAt }
    }

    public func delete(sessionId: UUID) throws {
        let url = try jsonURL(sessionId: sessionId)
        if fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
    }
}
