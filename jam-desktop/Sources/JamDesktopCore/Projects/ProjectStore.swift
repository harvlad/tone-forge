// ProjectStore.swift
//
// Persists Projects (per-song pad workspaces — the shared
// ToneForgeEngine/Projects/ProjectSnapshot.swift contract) as JSON
// under Application Support/Jamn/projects/. Byte-compatible desktop
// port of the iOS ProjectStore (mobile keeps its files under
// Documents/projects/): one file per project named by id, atomic
// writes, prettyPrinted + sortedKeys so files diff cleanly, list()
// skips corrupt files silently. A project file written on either
// platform decodes on the other.
//
// Two kinds of file live here:
//   * durable projects  — `{id}.json`, listed in the Projects sheet.
//   * working projects  — `working/{sanitized analysisId}.json`, the
//     auto-saved workspace for a song the user hasn't explicitly
//     saved. One per song, overwritten on every debounced capture,
//     restored automatically when that song loads. Deliberately kept
//     out of `list()` so the sheet shows only named saves.

import Foundation
import ToneForgeEngine

public final class ProjectStore {

    private let root: URL?
    private let fileManager: FileManager

    /// - Parameter root: base directory override for tests; nil =
    ///   Application Support/Jamn.
    public init(root: URL? = nil, fileManager: FileManager = .default) {
        self.root = root
        self.fileManager = fileManager
    }

    // MARK: - Paths

    /// `{Application Support/Jamn}/projects/`. Created on first access.
    public func projectsDir() throws -> URL {
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
        let dir = base.appendingPathComponent("projects", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    public func jsonURL(projectId: UUID) throws -> URL {
        try projectsDir()
            .appendingPathComponent("\(projectId.uuidString).json")
    }

    /// `{…}/projects/working/`. Created on first access.
    private func workingDir() throws -> URL {
        let dir = try projectsDir()
            .appendingPathComponent("working", isDirectory: true)
        try fileManager.createDirectory(
            at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// analysisIds are backend-issued opaque strings; sanitize so a
    /// hostile/odd id can never traverse out of the working dir.
    private static func workingFileName(analysisId: String) -> String {
        let allowed = CharacterSet.alphanumerics
            .union(CharacterSet(charactersIn: "-_"))
        let safe = String(analysisId.unicodeScalars.map {
            allowed.contains($0) ? Character($0) : "_"
        })
        return "\(safe).json"
    }

    private func workingURL(analysisId: String) throws -> URL {
        try workingDir().appendingPathComponent(
            Self.workingFileName(analysisId: analysisId))
    }

    // MARK: - Durable CRUD

    public func save(_ project: Project) throws {
        try write(project, to: jsonURL(projectId: project.id))
    }

    public func load(projectId: UUID) throws -> Project {
        let data = try Data(contentsOf: jsonURL(projectId: projectId))
        return try JSONDecoder().decode(Project.self, from: data)
    }

    /// All durable projects on disk, newest-updated first. Corrupt
    /// files are skipped (one bad JSON never hides the rest).
    public func list() -> [Project] {
        guard let dir = try? projectsDir(),
              let urls = try? fileManager.contentsOfDirectory(
                  at: dir, includingPropertiesForKeys: nil)
        else { return [] }
        return urls
            .filter { $0.pathExtension == "json" }
            .compactMap { url -> Project? in
                guard let data = try? Data(contentsOf: url) else {
                    return nil
                }
                return try? JSONDecoder().decode(Project.self, from: data)
            }
            .sorted { $0.updatedAt > $1.updatedAt }
    }

    public func delete(projectId: UUID) throws {
        let url = try jsonURL(projectId: projectId)
        if fileManager.fileExists(atPath: url.path) {
            try fileManager.removeItem(at: url)
        }
    }

    /// Duplicate an existing project under a new name/id and persist
    /// the copy. Returns the copy.
    @discardableResult
    public func duplicate(projectId: UUID, name: String) throws -> Project {
        let original = try load(projectId: projectId)
        let copy = original.duplicated(name: name)
        try save(copy)
        return copy
    }

    /// Rename in place (bumps updatedAt). Returns the updated project.
    @discardableResult
    public func rename(projectId: UUID, to name: String) throws -> Project {
        var project = try load(projectId: projectId)
        project.name = name
        project.updatedAt = Date()
        try save(project)
        return project
    }

    // MARK: - Working project (auto-save target)

    /// Persist the auto-saved workspace for `analysisId`. Overwrites
    /// the previous working save for that song. A blank-canvas project
    /// (nil baseSongId, v2) has no per-song working slot — no-op.
    public func saveWorking(_ project: Project) throws {
        guard let analysisId = project.baseSongId else { return }
        try write(project, to: workingURL(analysisId: analysisId))
    }

    /// The auto-saved workspace for a song, or nil (never saved or
    /// corrupt — either way the song loads with its defaults).
    public func loadWorking(analysisId: String) -> Project? {
        guard let url = try? workingURL(analysisId: analysisId),
              let data = try? Data(contentsOf: url)
        else { return nil }
        return try? JSONDecoder().decode(Project.self, from: data)
    }

    public func deleteWorking(analysisId: String) {
        guard let url = try? workingURL(analysisId: analysisId) else { return }
        if fileManager.fileExists(atPath: url.path) {
            try? fileManager.removeItem(at: url)
        }
    }

    // MARK: - Shared write

    private func write(_ project: Project, to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(project)
        try data.write(to: url, options: .atomic)
    }
}
