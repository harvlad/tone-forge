// SessionStore.swift
//
// Persists SessionCaptures as JSON — the desktop port of the mobile
// SessionStore (iOS keeps them under Documents/sessions/; here they
// live under Application Support/Jamn/sessions/). One file per
// session, named by sessionId, so the recorder's autosaves and final
// save all overwrite the same file. Sessions hold no audio — only
// event timings and pad references — so plain pretty JSON is fine.
//
// Fault tolerance matches iOS: `list()` skips corrupt files silently
// so one bad JSON never hides the rest, writes are atomic, and JSON
// is prettyPrinted + sortedKeys so files diff cleanly. The wire
// format is the frozen SessionCapture v1 shape — files written here
// decode on iOS and vice versa.

import Foundation
import ToneForgeEngine

public final class SessionStore {

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   Application Support/Jamn.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{Application Support/Jamn}/sessions/`. Created on first access.
    public func sessionsDir() throws -> URL {
        let base: URL
        if let root {
            base = root
        } else {
            base = try fileManager.url(
                for: .applicationSupportDirectory,
                in: .userDomainMask,
                appropriateFor: nil,
                create: true
            ).appendingPathComponent("Jamn", isDirectory: true)
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
