// StudioModel.swift
//
// Studio tab state: results deep-dive of a past analysis (Phase 1),
// admin quality analysis of a local file (Phase 2), and waveform
// trim + trimmed analyze-stream runs + arrangement (Phase 3). Owns
// the loaded history detail, resolves the descriptor fallback chain
// + derived rows.

import Combine
import Foundation
import ToneForgeEngine

@MainActor
public final class StudioModel: ObservableObject {
    @Published public private(set) var detail: StudioHistoryDetail?
    @Published public private(set) var isLoading = false
    @Published public private(set) var error: String?

    /// Local audio file picked as the quality-analysis source.
    @Published public var sourceFileURL: URL?
    @Published public private(set) var quality: QualityAnalysis?
    @Published public private(set) var isAnalyzingQuality = false
    @Published public private(set) var qualityError: String?

    // Phase 3: waveform + trim over the picked file.
    @Published public private(set) var waveform: WaveformPreview?
    @Published public var trim: TrimSelection?
    @Published public private(set) var isLoadingWaveform = false
    @Published public private(set) var waveformError: String?

    // Phase 3: trimmed analyze-stream run.
    @Published public private(set) var isRunningTrimmedAnalysis = false
    @Published public private(set) var trimmedRunProgress: String?
    @Published public private(set) var trimmedRunError: String?

    // Phase 3: arrangement (detect-sections + analyze-region).
    @Published public private(set) var arrangement: ArrangementAnalysisDTO?
    @Published public private(set) var isDetectingSections = false
    @Published public private(set) var arrangementError: String?
    @Published public private(set) var region: RegionAnalysisDTO?
    @Published public private(set) var isAnalyzingRegion = false
    @Published public private(set) var regionError: String?

    // Phase 4: local engine deep-mode.
    @Published public private(set) var localEngineStatus: LocalEngineStatus = .unknown
    @Published public private(set) var isRunningDeepAnalysis = false
    @Published public private(set) var deepProgress: DeepAnalysisProgress?
    @Published public private(set) var deepError: String?

    private let client: StudioFetching
    private let qualityClient: QualityAnalyzing
    private let waveformClient: WaveformPreviewing
    private let sectionClient: SectionDetecting
    private let regionClient: RegionAnalyzing
    private let streamClient: StudioStreamAnalyzing
    private let localEngineProbe: LocalEngineProbing
    private let deepClient: DeepAnalyzing

    public init(
        client: StudioFetching = StudioClient(),
        qualityClient: QualityAnalyzing = QualityClient(),
        waveformClient: WaveformPreviewing = WaveformPreviewClient(),
        sectionClient: SectionDetecting = SectionDetectClient(),
        regionClient: RegionAnalyzing = RegionAnalyzeClient(),
        streamClient: StudioStreamAnalyzing = StudioStreamClient(),
        localEngineProbe: LocalEngineProbing = LocalEngineClient(),
        deepClient: DeepAnalyzing = LocalEngineClient()
    ) {
        self.client = client
        self.qualityClient = qualityClient
        self.waveformClient = waveformClient
        self.sectionClient = sectionClient
        self.regionClient = regionClient
        self.streamClient = streamClient
        self.localEngineProbe = localEngineProbe
        self.deepClient = deepClient
    }

    public func load(baseURL: URL, id: String) async {
        guard !id.isEmpty else { return }
        isLoading = true
        error = nil
        do {
            detail = try await client.fetchHistoryDetail(baseURL: baseURL, id: id)
        } catch let decodingError as DecodingError {
            // Provide more detail for JSON decoding failures
            switch decodingError {
            case .keyNotFound(let key, let context):
                self.error = "Missing key '\(key.stringValue)' at \(context.codingPath.map(\.stringValue).joined(separator: "."))"
            case .typeMismatch(let type, let context):
                self.error = "Type mismatch for \(type) at \(context.codingPath.map(\.stringValue).joined(separator: "."))"
            case .valueNotFound(let type, let context):
                self.error = "Null value for \(type) at \(context.codingPath.map(\.stringValue).joined(separator: "."))"
            case .dataCorrupted(let context):
                self.error = "Data corrupted at \(context.codingPath.map(\.stringValue).joined(separator: ".")): \(context.debugDescription)"
            @unknown default:
                self.error = decodingError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    public func clear() {
        detail = nil
        error = nil
    }

    // MARK: - quality analysis (Phase 2)

    public func runQualityAnalysis(baseURL: URL) async {
        guard let fileURL = sourceFileURL, !isAnalyzingQuality else { return }
        isAnalyzingQuality = true
        qualityError = nil
        quality = nil
        do {
            quality = try await qualityClient.analyzeQuality(
                baseURL: baseURL,
                fileURL: fileURL,
                filename: fileURL.lastPathComponent
            )
        } catch {
            qualityError = error.localizedDescription
        }
        isAnalyzingQuality = false
    }

    public func clearQuality() {
        quality = nil
        qualityError = nil
    }

    // MARK: - waveform + trim (Phase 3)

    public func loadWaveform(baseURL: URL) async {
        guard let fileURL = sourceFileURL, !isLoadingWaveform else { return }
        isLoadingWaveform = true
        waveformError = nil
        waveform = nil
        trim = nil
        do {
            let preview = try await waveformClient.previewWaveform(
                baseURL: baseURL,
                fileURL: fileURL,
                filename: fileURL.lastPathComponent
            )
            waveform = preview
            trim = TrimSelection(duration: preview.durationSec ?? 0)
        } catch {
            waveformError = error.localizedDescription
        }
        isLoadingWaveform = false
    }

    public func clearWaveform() {
        waveform = nil
        waveformError = nil
        trim = nil
        arrangement = nil
        arrangementError = nil
        region = nil
        regionError = nil
        trimmedRunProgress = nil
        trimmedRunError = nil
    }

    // MARK: - trimmed analyze-stream run (Phase 3)

    /// Streams a studio-mode analysis of the picked file (optionally
    /// trimmed), then loads the resulting history entry so one
    /// renderer serves all paths.
    public func runTrimmedAnalysis(baseURL: URL) async {
        guard let fileURL = sourceFileURL, !isRunningTrimmedAnalysis
        else { return }
        isRunningTrimmedAnalysis = true
        trimmedRunError = nil
        trimmedRunProgress = "Uploading…"

        // Full range means no trim fields on the wire (web parity).
        let sendTrim = trim.map { !$0.isFullRange } ?? false
        let startTime = sendTrim ? trim?.startSeconds : nil
        let endTime = sendTrim ? trim?.endSeconds : nil

        do {
            var historyId: String?
            let stream = streamClient.analyze(
                baseURL: baseURL,
                fileURL: fileURL,
                filename: fileURL.lastPathComponent,
                startTime: startTime,
                endTime: endTime
            )
            for try await event in stream {
                switch event {
                case let .progress(message, percent):
                    if let percent {
                        trimmedRunProgress = String(
                            format: "%@ (%.0f%%)", message, percent)
                    } else {
                        trimmedRunProgress = message
                    }
                case let .completed(id):
                    historyId = id
                }
            }
            trimmedRunProgress = nil
            if let historyId {
                await load(baseURL: baseURL, id: historyId)
            }
        } catch {
            trimmedRunProgress = nil
            trimmedRunError = error.localizedDescription
        }
        isRunningTrimmedAnalysis = false
    }

    // MARK: - arrangement (Phase 3)

    public func detectSections(baseURL: URL) async {
        guard let fileURL = sourceFileURL, !isDetectingSections
        else { return }
        isDetectingSections = true
        arrangementError = nil
        region = nil
        regionError = nil
        do {
            arrangement = try await sectionClient.detectSections(
                baseURL: baseURL,
                fileURL: fileURL,
                filename: fileURL.lastPathComponent,
                tempo: detail?.result?.tempoBpm
            )
        } catch {
            arrangementError = error.localizedDescription
        }
        isDetectingSections = false
    }

    public func analyzeRegion(
        baseURL: URL, startTime: Double, endTime: Double,
        stemType: String = "other"
    ) async {
        guard let fileURL = sourceFileURL, !isAnalyzingRegion
        else { return }
        isAnalyzingRegion = true
        regionError = nil
        do {
            region = try await regionClient.analyzeRegion(
                baseURL: baseURL,
                fileURL: fileURL,
                filename: fileURL.lastPathComponent,
                startTime: startTime,
                endTime: endTime,
                stemType: stemType
            )
        } catch {
            regionError = error.localizedDescription
        }
        isAnalyzingRegion = false
    }

    // MARK: - local engine deep-mode (Phase 4)

    /// Probe the local engine to see if it's available.
    public func checkLocalEngine() async {
        localEngineStatus = await localEngineProbe.checkHealth()
    }

    /// Run deep analysis via local engine.
    public func runDeepAnalysis(baseURL: URL) async {
        guard let fileURL = sourceFileURL,
              !isRunningDeepAnalysis,
              localEngineStatus == .available
        else { return }

        isRunningDeepAnalysis = true
        deepError = nil
        deepProgress = nil

        // Full range means no trim fields (web parity).
        let sendTrim = trim.map { !$0.isFullRange } ?? false
        let startTime = sendTrim ? trim?.startSeconds : nil
        let endTime = sendTrim ? trim?.endSeconds : nil

        do {
            var historyId: String?
            let stream = deepClient.analyzeDeep(
                fileURL: fileURL,
                filename: fileURL.lastPathComponent,
                startTime: startTime,
                endTime: endTime
            )
            for try await event in stream {
                switch event {
                case .started:
                    deepProgress = DeepAnalysisProgress(
                        stage: "starting", message: "Starting analysis...", percent: 0
                    )
                case let .progress(progress):
                    deepProgress = progress
                case let .completed(result):
                    historyId = result.historyId
                }
            }
            deepProgress = nil
            if let historyId {
                // Load the completed analysis results
                await load(baseURL: baseURL, id: historyId)
            }
        } catch {
            deepProgress = nil
            deepError = error.localizedDescription
        }
        isRunningDeepAnalysis = false
    }

    public func clearDeepError() {
        deepError = nil
    }

    // MARK: - descriptor resolution (studio.html:2773 fallback chain)

    /// Type-nested descriptor first (result.guitar.descriptor …), then
    /// the top-level result.descriptor.
    public var descriptor: ToneDescriptor? {
        guard let result = detail?.result else { return nil }
        let nested: ToneDescriptor? = switch result.detectedType {
        case "guitar": result.guitar?.descriptor
        case "bass": result.bass?.descriptor
        case "synth": result.synth?.descriptor
        case "drums": result.drums?.descriptor
        default: nil
        }
        return nested
            ?? result.guitar?.descriptor
            ?? result.descriptor
    }

    // MARK: - derived rows

    public struct MidiStemRow: Identifiable, Sendable {
        public let stem: String
        public let info: MidiStemInfo
        public var id: String { stem }
    }

    /// MIDI stems in a stable display order (matches stem mixer).
    public var midiStemRows: [MidiStemRow] {
        let order = ["drums", "bass", "guitar", "piano", "other", "vocals"]
        guard let stems = detail?.result?.midiStems else { return [] }
        return stems
            .map { MidiStemRow(stem: $0.key, info: $0.value) }
            .sorted {
                (order.firstIndex(of: $0.stem) ?? order.count)
                    < (order.firstIndex(of: $1.stem) ?? order.count)
            }
    }

    /// Aggregate MIDI stats across stems (studio.html:2871 fallback).
    public var totalMidiNotes: Int {
        (detail?.result?.midiStems?.values).map {
            $0.reduce(0) { $0 + ($1.noteCount ?? 0) }
        } ?? 0
    }

    public struct StageRow: Identifiable, Sendable {
        public let name: String
        public let stage: ProfilingStage
        /// Fraction of the pipeline total, for the duration bar.
        public let fraction: Double
        public var id: String { name }
    }

    /// Profiling stages ordered by start time (wallclock story), with
    /// bar fractions against total_ms.
    public var stageRows: [StageRow] {
        guard let profiling = detail?.result?.profiling,
              let stages = profiling.stages else { return [] }
        let total = max(profiling.totalMs ?? 0, 1)
        return stages
            .map { name, stage in
                StageRow(
                    name: name, stage: stage,
                    fraction: min(1, max(0, (stage.durationMs ?? 0) / total)))
            }
            .sorted { a, b in
                let sa = a.stage.startedMs ?? Double.greatestFiniteMagnitude
                let sb = b.stage.startedMs ?? Double.greatestFiniteMagnitude
                if sa != sb { return sa < sb }
                return a.name < b.name
            }
    }
}
