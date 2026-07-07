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
// off the main actor, streamed to POST /api/analyze-stream, and the
// finished analysis is loaded into AppState like any history entry.
//
// The analyze transport is injectable (`AnalyzeStreaming`) so UI tests
// can stub the network entirely (`-uitest-stub-import`).

import Foundation
import Combine
import ToneForgeEngine

/// Minimal transport seam over AnalyzeClient for test stubbing.
public protocol AnalyzeStreaming: Sendable {
    func stream(
        baseURL: URL, wavFileURL: URL, filename: String
    ) -> AsyncThrowingStream<AnalyzeEvent, Error>
}

/// Production transport: the real SSE client with the app-wide upload
/// timeout.
public struct BackendAnalyzeClient: AnalyzeStreaming {
    public init() {}
    public func stream(
        baseURL: URL, wavFileURL: URL, filename: String
    ) -> AsyncThrowingStream<AnalyzeEvent, Error> {
        AnalyzeClient.analyzeStream(
            baseURL: baseURL,
            wavFileURL: wavFileURL,
            filename: filename,
            timeout: AppConfig.analyzeTimeout
        )
    }
}

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

    private let analyzeClient: any AnalyzeStreaming
    private weak var appState: AppState?
    private var pendingSource: ImportSource?
    private var lastSource: ImportSource?
    private var worker: Task<Void, Never>?

    public init(
        attestation: AttestationStore? = nil,
        analyzeClient: any AnalyzeStreaming = BackendAnalyzeClient()
    ) {
        self.attestation = attestation ?? AttestationStore()
        self.analyzeClient = analyzeClient
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

    /// Cancel a running import / dismiss a finished one.
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
            let staged = try stageInput(source)
            defer { staged.cleanup() }

            #if canImport(AVFoundation)
            let input = staged.url
            _ = try await Task.detached(priority: .userInitiated) {
                try AudioTranscoder.transcodeToAnalysisWAV(input: input, output: tempWAV)
            }.value
            #endif
            try Task.checkCancellation()

            guard let appState else { return }
            phase = .uploading(message: "Uploading…", percent: nil)

            let filename = source.displayName
                .replacingOccurrences(of: "/", with: "-") + ".wav"
            var historyId: String?
            let events = analyzeClient.stream(
                baseURL: appState.backendBaseURL, wavFileURL: tempWAV, filename: filename
            )
            for try await event in events {
                try Task.checkCancellation()
                switch event {
                case .progress(let message, let percent):
                    phase = .uploading(message: message, percent: percent)
                case .completed(let id):
                    historyId = id
                }
            }
            guard let historyId else {
                throw AnalyzeClientError.streamEndedWithoutResult
            }

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
        } catch is CancellationError {
            phase = .idle
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// Resolve the source to a readable local URL. File-picker URLs
    /// are copied into tmp under security-scoped access so the read
    /// outlives the picker's grant; media items are read in place.
    private func stageInput(
        _ source: ImportSource
    ) throws -> (url: URL, cleanup: () -> Void) {
        switch source {
        case .mediaItem(let track):
            guard track.isAnalysable, let assetURL = track.assetURL else {
                throw ImportError.trackNotAnalysable(
                    reason: track.unavailabilityReason ?? "unavailable"
                )
            }
            return (assetURL, {})

        case .fileURL(let url):
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
}
