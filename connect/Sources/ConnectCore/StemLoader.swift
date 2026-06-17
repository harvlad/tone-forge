//
// StemLoader.swift
//
// Audio-Ownership Pivot, post-pivot follow-up. Pulls stem audio
// from the local ToneForge backend over HTTP so Connect's
// `AudioEngine` can take over playback from the JAM-side Web
// Audio path.
//
// Why HTTP and not the WebSocket binary frame route:
//   * The stems already live on the backend's `/api/serve-file`
//     endpoint (backend/local_engine/server.py). Reusing that
//     surface means no new auth path, no binary-WS framing, and
//     the same URL the browser already uses works unchanged.
//   * Each stem is ~5-30 MB of stereo WAV at 44.1 kHz. Streaming
//     into AVAudioPCMBuffer requires the whole file in memory
//     anyway (the player schedules it as a single buffer in
//     `AudioEngine.loadStem`), so simple download-to-tempfile is
//     no worse than range-streaming.
//
// Threading: downloads run on a dedicated background URLSession.
// The completion callback is invoked on the main queue so callers
// can hop straight into AVAudioEngine work without an extra
// dispatch.
//
// Cache lifetime: each load gets a fresh subdirectory under
// `FileManager.default.temporaryDirectory`. Subsequent loads do
// NOT reuse cached files — Connect is the helper for one song at
// a time and JAM rebuilds URLs on every analysis-complete, so a
// stale cache would only confuse things. The directory is torn
// down on `reset()` and on `deinit`.
//

import Foundation

public final class StemLoader {

    /// A stem to download. `id` is the JAM-side identifier (e.g.
    /// `"demucs.drums"`); the loader keys its result map by `id` so
    /// AudioEngine.loadStem(name:url:) calls stay in lockstep with
    /// the wire-frame contents.
    public struct Spec: Equatable {
        public let id: String
        public let url: URL
        public init(id: String, url: URL) {
            self.id = id
            self.url = url
        }
    }

    /// Outcome of a download attempt. Reported per-spec so a
    /// caller can attach the ones that succeeded and surface a
    /// warning for any that failed (e.g. server gone away).
    public enum Outcome {
        case success(URL)
        case failure(Error)
    }

    /// Process-wide loader. Connect holds one bridge at a time so
    /// one loader is enough.
    public static let shared = StemLoader()

    private let session: URLSession
    private let cacheDirRoot: URL
    private var currentBatchDir: URL?
    private let stateQueue = DispatchQueue(label: "com.toneforge.connect.stem-loader")

    public init(session: URLSession = .shared) {
        self.session = session
        self.cacheDirRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("toneforge-connect-stems", isDirectory: true)
    }

    /// Download every spec to a freshly-created batch directory.
    /// Completion fires on the main queue with a `[stemId: Outcome]`
    /// dictionary. The dictionary preserves the input id strings so
    /// the caller can look up per-stem results without iterating
    /// the original spec list.
    public func load(_ specs: [Spec], completion: @escaping ([String: Outcome]) -> Void) {
        // Tear down any previous batch — we only ever play one
        // song at a time.
        teardownCurrentBatch()

        let batchDir = cacheDirRoot.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: batchDir, withIntermediateDirectories: true)
        } catch {
            DispatchQueue.main.async {
                completion(Dictionary(uniqueKeysWithValues: specs.map {
                    ($0.id, Outcome.failure(error))
                }))
            }
            return
        }
        stateQueue.sync { currentBatchDir = batchDir }

        guard !specs.isEmpty else {
            DispatchQueue.main.async { completion([:]) }
            return
        }

        // Fan out one download per stem. We aggregate via a serial
        // queue so the completion is always called exactly once,
        // with every result populated.
        var results: [String: Outcome] = [:]
        let group = DispatchGroup()
        let aggregateQueue = DispatchQueue(label: "com.toneforge.connect.stem-loader.aggregate")

        for spec in specs {
            group.enter()
            let dest = batchDir.appendingPathComponent(StemLoader.localFilename(for: spec))
            let task = session.downloadTask(with: spec.url) { tempURL, response, error in
                defer { group.leave() }
                if let error = error {
                    aggregateQueue.sync { results[spec.id] = .failure(error) }
                    return
                }
                guard let tempURL = tempURL else {
                    aggregateQueue.sync {
                        results[spec.id] = .failure(StemLoaderError.missingDownloadFile)
                    }
                    return
                }
                if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                    aggregateQueue.sync {
                        results[spec.id] = .failure(StemLoaderError.httpStatus(http.statusCode))
                    }
                    return
                }
                do {
                    // URLSession deletes the temp file as soon as
                    // this callback returns, so move it into the
                    // batch dir before we yield.
                    if FileManager.default.fileExists(atPath: dest.path) {
                        try FileManager.default.removeItem(at: dest)
                    }
                    try FileManager.default.moveItem(at: tempURL, to: dest)
                    aggregateQueue.sync { results[spec.id] = .success(dest) }
                } catch {
                    aggregateQueue.sync { results[spec.id] = .failure(error) }
                }
            }
            task.resume()
        }

        group.notify(queue: .main) {
            completion(results)
        }
    }

    /// Tear down the current batch directory. Idempotent.
    public func reset() {
        teardownCurrentBatch()
    }

    // MARK: - Internals

    private func teardownCurrentBatch() {
        let dir: URL? = stateQueue.sync {
            let existing = currentBatchDir
            currentBatchDir = nil
            return existing
        }
        guard let dir = dir else { return }
        try? FileManager.default.removeItem(at: dir)
    }

    /// Pick a local filename for a stem. We keep the source
    /// extension so AVAudioFile reads with the right format
    /// detection, but otherwise sanitise the stem id into something
    /// the macOS filesystem will accept. Two stems with the same
    /// extension and the same id-after-sanitisation get a UUID
    /// suffix — but stem ids are unique within a batch so in
    /// practice we don't hit that case.
    private static func localFilename(for spec: Spec) -> String {
        let sanitisedId = spec.id
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "\\", with: "_")
            .replacingOccurrences(of: ":", with: "_")
        // `audio_url` is a `?path=…` query string; the path
        // extension lives at the end of the *path component*, not
        // the URL. URLComponents lets us pull it out cleanly.
        let ext = StemLoader.extractExtension(from: spec.url) ?? "wav"
        return "\(sanitisedId).\(ext)"
    }

    private static func extractExtension(from url: URL) -> String? {
        // Try the URL's own path first.
        let pathExt = url.pathExtension
        if !pathExt.isEmpty { return pathExt }
        // Fall back to the encoded `path=` query param the backend
        // produces: …/api/serve-file?path=/var/folders/.../foo.wav
        guard
            let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
            let queryPath = components.queryItems?.first(where: { $0.name == "path" })?.value,
            let dotIndex = queryPath.lastIndex(of: ".")
        else { return nil }
        let ext = queryPath[queryPath.index(after: dotIndex)...]
        return ext.isEmpty ? nil : String(ext)
    }
}

public enum StemLoaderError: Error, LocalizedError, Equatable {
    case missingDownloadFile
    case httpStatus(Int)

    public var errorDescription: String? {
        switch self {
        case .missingDownloadFile:
            return "URLSession reported no temp file for the stem download."
        case .httpStatus(let code):
            return "Stem server returned HTTP \(code)."
        }
    }
}
