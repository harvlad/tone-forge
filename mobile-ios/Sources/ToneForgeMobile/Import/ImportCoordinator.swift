// ImportCoordinator.swift
//
// State machine for the whole ingestion flow:
//
//   idle → awaitingAttestation → transcoding → uploading → loading
//        → done(historyId) | failed(message)
//
// One coordinator covers both ingestion paths (Music library + Files),
// so the ownership-attestation gate lives here and can't be bypassed
// by either picker. Audio is transcoded to the canonical analysis WAV
// off the main actor, then submitted as a server-side job (POST
// /api/analyze-job). The job outlives this connection: the foreground
// streams /events for live percent, but final completion terminates in
// the app-lifetime JobCompletionCenter, not here — so a lock, a
// backgrounding, or a kill can't lose the analysis.
//
// The job transport is injectable (`JobSubmitting`) so UI tests can stub
// the network entirely (`-uitest-stub-import`).

import Foundation
import Combine
import ToneForgeEngine
#if canImport(AVFoundation)
import AVFoundation
#endif

public enum ImportError: Error, LocalizedError {
    case trackNotAnalysable(reason: String)

    public var errorDescription: String? {
        switch self {
        case .trackNotAnalysable(let reason):
            return "This track can't be analysed: \(reason)."
        }
    }
}

@MainActor
public final class ImportCoordinator: ObservableObject {

    public enum ImportSource {
        case mediaItem(LibraryTrack)
        case fileURL(URL)

        var displayName: String {
            switch self {
            case .mediaItem(let track): return track.title
            case .fileURL(let url): return url.deletingPathExtension().lastPathComponent
            }
        }
    }

    public enum Phase: Equatable {
        case idle
        case awaitingAttestation
        case transcoding
        case uploading(message: String, percent: Double?)
        case loading
        case done(historyId: String)
        case failed(String)
    }

    @Published public private(set) var phase: Phase = .idle
    /// Title of the track being imported, for the progress card.
    @Published public private(set) var trackTitle: String = ""

    public let attestation: AttestationStore
    /// Fired when the analysis loaded into AppState successfully —
    /// the Library view uses it to flip to the Play tab.
    public var onLoaded: (() -> Void)?
    /// Album-art JPEG captured by the picker *before* `start` (the
    /// analysisId doesn't exist yet at pick time). Saved to
    /// ArtworkStore keyed by historyId when the import completes;
    /// cleared on dismiss/cancel so a stale image can't attach to a
    /// different song. Survives `retry()` on purpose — the retried
    /// source is the same track.
    public var pendingArtworkData: Data?

    private let jobClient: any JobSubmitting
    private weak var appState: AppState?
    private var pendingSource: ImportSource?
    private var lastSource: ImportSource?
    private var worker: Task<Void, Never>?

    public init(
        attestation: AttestationStore? = nil,
        jobClient: any JobSubmitting = BackendJobClient()
    ) {
        self.attestation = attestation ?? AttestationStore()
        self.jobClient = jobClient
    }

    /// True while the progress sheet should be up.
    public var isImporting: Bool {
        switch phase {
        case .transcoding, .uploading, .loading, .done, .failed: return true
        case .idle, .awaitingAttestation: return false
        }
    }

    // MARK: - Entry points

    public func start(source: ImportSource, appState: AppState) {
        guard case .idle = phase else { return }
        self.appState = appState
        guard attestation.isAccepted else {
            pendingSource = source
            phase = .awaitingAttestation
            return
        }
        run(source)
    }

    /// Curated cc-track import (D-024). The job was already created
    /// server-side (POST /api/cc-tracks/{id}/import) — the server owns
    /// the audio and the license, so there is no attestation gate and
    /// nothing to transcode or upload; just follow the job like any
    /// other import so the user gets the same progress sheet.
    public func startCuratedJob(jobId: String, title: String, appState: AppState) {
        guard case .idle = phase else { return }
        self.appState = appState
        pendingSource = nil
        lastSource = nil  // retry() is a no-op: the job is already running
        trackTitle = title
        phase = .uploading(message: "Analyzing…", percent: nil)
        worker = Task {
            do {
                try await self.followSubmittedJob(jobId: jobId, title: title)
            } catch is CancellationError {
                self.phase = .idle
            } catch {
                self.phase = .failed(error.localizedDescription)
            }
        }
    }

    /// Attestation sheet callbacks.
    public func attestationAccepted() {
        guard let source = pendingSource else {
            phase = .idle
            return
        }
        run(source)
    }

    public func attestationCancelled() {
        pendingSource = nil
        pendingArtworkData = nil
        phase = .idle
    }

    public func retry() {
        guard case .failed = phase, let source = lastSource else { return }
        phase = .idle
        run(source)
    }

    /// Detach the UI from a running import, or dismiss a finished one.
    ///
    /// Cancels only the foreground `/events` stream — NOT the server job.
    /// Once a job is submitted, JobCompletionCenter owns its completion
    /// (background poll + notification), so closing this sheet must not
    /// kill an in-flight analysis. The worker before submit is safe to
    /// cancel (nothing registered yet).
    public func dismiss() {
        worker?.cancel()
        worker = nil
        pendingSource = nil
        pendingArtworkData = nil
        phase = .idle
    }

    // MARK: - Pipeline

    private func run(_ source: ImportSource) {
        pendingSource = nil
        lastSource = source
        trackTitle = source.displayName
        phase = .transcoding
        worker = Task { await performImport(source) }
    }

    private func performImport(_ source: ImportSource) async {
        let tempWAV = FileManager.default.temporaryDirectory
            .appendingPathComponent("import-\(UUID().uuidString).wav")
        defer { try? FileManager.default.removeItem(at: tempWAV) }

        do {
            // Attribution (D-024): the transcode below produces a bare
            // WAV — file tags don't survive it, so title/artist must be
            // captured from the ORIGINAL source and sent as form fields.
            let attribution = await attributionFields(for: source)

            try await produceAnalysisWAV(source, output: tempWAV)
            try Task.checkCancellation()

            guard let appState else { return }
            phase = .uploading(message: "Uploading…", percent: nil)

            let baseURL = appState.backendBaseURL
            let filename = source.displayName
                .replacingOccurrences(of: "/", with: "-") + ".wav"

            // Submit the job. From here the analysis lives server-side and
            // is owned by JobCompletionCenter: even if this sheet closes,
            // the phone locks, or the app is killed, the background poll
            // finishes it and notifies.
            let jobId = try await jobClient.submit(
                baseURL: baseURL, wavFileURL: tempWAV, filename: filename,
                extraFields: attribution
            )
            try Task.checkCancellation()
            try await followSubmittedJob(jobId: jobId, title: source.displayName)
        } catch is CancellationError {
            phase = .idle
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// Follow an already-submitted server job to completion: register
    /// it with JobCompletionCenter, stream /events for the foreground
    /// percent, then load the finished bundle in place. Shared by user
    /// imports and curated cc-track imports (D-024), which create the
    /// job server-side and have nothing to transcode or upload.
    private func followSubmittedJob(jobId: String, title: String) async throws {
        guard let appState else { return }
        let baseURL = appState.backendBaseURL
        JobCompletionCenter.shared.register(
            jobId: jobId,
            title: title,
            baseURL: baseURL,
            artworkData: pendingArtworkData
        )

        // Foreground: stream /events for live percent. If this stream
        // drops (lock/background), the background session takes over —
        // so a stream error here is not fatal to the analysis.
        var historyId: String?
        do {
            let events = jobClient.events(baseURL: baseURL, jobId: jobId)
            for try await event in events {
                try Task.checkCancellation()
                switch event {
                case .progress(let message, let percent):
                    phase = .uploading(message: message, percent: percent)
                case .completed(let id):
                    historyId = id
                }
            }
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            // Stream broke but the job keeps running server-side;
            // hand off to the background path and detach the UI.
            phase = .idle
            return
        }

        guard let historyId else {
            // Stream ended without a terminal result; background poll
            // will still finish it. Detach quietly.
            phase = .idle
            return
        }

        // Foreground won the race. Claim completion so the background
        // path won't double-fire, then load the bundle in-place.
        JobCompletionCenter.shared.foregroundCompleted(jobId: jobId)
        phase = .loading
        await appState.loadBundle(analysisId: historyId)
        if let loadError = appState.loadingError {
            phase = .failed(loadError)
        } else {
            if let art = pendingArtworkData {
                _ = try? ArtworkStore().save(art, analysisId: historyId)
                pendingArtworkData = nil
            }
            phase = .done(historyId: historyId)
            onLoaded?()
        }
    }

    /// Attribution form fields for the submit (D-024). Only non-empty
    /// values are sent — the server treats empty as "unknown" anyway.
    /// License fields are never sent from user imports; only the
    /// curated cc-tracks path carries a license.
    func attributionFields(
        for source: ImportSource
    ) async -> [(name: String, value: String)] {
        var title = ""
        var artist = ""
        switch source {
        case .mediaItem(let track):
            title = track.title
            artist = track.artist
        case .fileURL(let url):
            #if canImport(AVFoundation)
            // The picker's security-scoped grant must be active while
            // AVFoundation reads the file's metadata.
            let scoped = url.startAccessingSecurityScopedResource()
            defer {
                if scoped { url.stopAccessingSecurityScopedResource() }
            }
            let asset = AVURLAsset(url: url)
            if let items = try? await asset.load(.commonMetadata) {
                for item in items {
                    guard let value = try? await item.load(.stringValue),
                          !value.isEmpty else { continue }
                    switch item.commonKey {
                    case .commonKeyTitle?: title = value
                    case .commonKeyArtist?: artist = value
                    default: break
                    }
                }
            }
            #endif
        }
        var fields: [(name: String, value: String)] = []
        if !title.isEmpty { fields.append((name: "title", value: title)) }
        if !artist.isEmpty { fields.append((name: "artist", value: artist)) }
        return fields
    }

    /// Produce the canonical analysis WAV at `output` for either source.
    ///
    /// Music-library items are decoded straight from their
    /// `ipod-library://` asset URL with AVAssetReader — `AVAudioFile`
    /// can't open those URLs. File-picker URLs are copied into tmp
    /// under security-scoped access, then transcoded via `AVAudioFile`.
    private func produceAnalysisWAV(_ source: ImportSource, output: URL) async throws {
        switch source {
        case .mediaItem(let track):
            guard track.isAnalysable, let assetURL = track.assetURL else {
                throw ImportError.trackNotAnalysable(
                    reason: track.unavailabilityReason ?? "unavailable"
                )
            }
            #if canImport(AVFoundation)
            // MPMediaItem.hasProtectedAsset is unreliable: it reports
            // false for downloaded Apple Music tracks even though the
            // file is FairPlay-protected. Ask the asset itself so those
            // tracks get the proper DRM message instead of an opaque
            // CoreAudio read error.
            let isProtected = (try? await AVURLAsset(url: assetURL)
                .load(.hasProtectedContent)) ?? false
            if isProtected {
                throw ImportError.trackNotAnalysable(
                    reason: "streaming (DRM) — not analysable"
                )
            }
            // AVAudioFile / AVAssetReader can't open `ipod-library://`
            // URLs directly. AVAssetExportSession is Apple's sanctioned
            // path: export the media item to a readable local .m4a,
            // then transcode that ordinary file.
            let exported = try await exportMediaItem(assetURL: assetURL)
            defer { try? FileManager.default.removeItem(at: exported) }
            _ = try await Task.detached(priority: .userInitiated) {
                try AudioTranscoder.transcodeToAnalysisWAV(input: exported, output: output)
            }.value
            #endif

        case .fileURL(let url):
            let staged = try stageFileURL(url)
            defer { staged.cleanup() }
            #if canImport(AVFoundation)
            let input = staged.url
            _ = try await Task.detached(priority: .userInitiated) {
                try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: output)
            }.value
            #endif
        }
    }

    #if canImport(AVFoundation)
    /// Export a Music-library asset to a readable temp .m4a via
    /// AVAssetExportSession — the only reliable way to get audio out of
    /// an `ipod-library://` item into a file AVFoundation can decode.
    private func exportMediaItem(assetURL: URL) async throws -> URL {
        let asset = AVURLAsset(url: assetURL)
        guard let session = AVAssetExportSession(
            asset: asset, presetName: AVAssetExportPresetAppleM4A
        ) else {
            throw ImportError.trackNotAnalysable(reason: "export unavailable")
        }
        let output = FileManager.default.temporaryDirectory
            .appendingPathComponent("import-media-\(UUID().uuidString).m4a")
        try? FileManager.default.removeItem(at: output)
        session.outputURL = output
        session.outputFileType = .m4a

        await withCheckedContinuation { continuation in
            session.exportAsynchronously { continuation.resume() }
        }

        guard session.status == .completed else {
            // Downloaded Apple Music tracks stay FairPlay-encrypted, so
            // export fails (AVFoundation -11800 / OSStatus -16979).
            // hasProtectedContent under-reports these, so this is where
            // most DRM tracks actually get caught.
            throw ImportError.trackNotAnalysable(
                reason: "Apple Music / protected track — import a song you own"
            )
        }
        return output
    }
    #endif

    /// Copy a file-picker URL into tmp under security-scoped access so
    /// the read outlives the picker's grant.
    private func stageFileURL(_ url: URL) throws -> (url: URL, cleanup: () -> Void) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer {
            if scoped { url.stopAccessingSecurityScopedResource() }
        }
        let copy = FileManager.default.temporaryDirectory
            .appendingPathComponent("import-src-\(UUID().uuidString)")
            .appendingPathExtension(url.pathExtension)
        try FileManager.default.copyItem(at: url, to: copy)
        return (copy, { try? FileManager.default.removeItem(at: copy) })
    }
}
