// StudioView.swift
//
// Studio: results deep-dive of a past analysis (Phase 1: overview +
// tone cards + MIDI stats + pipeline profiling, reached via the
// toolbar segment or "Open in Studio" on a history row), admin
// quality analysis of a local file (Phase 2: source bar + quality
// panels), and waveform trim + trimmed re-runs + arrangement
// (Phase 3).

import SwiftUI
import JamDesktopCore
import UniformTypeIdentifiers

struct StudioView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var studio: StudioModel

    @State private var showFilePicker = false
    @State private var selectedSectionID: Double?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                sourceBar
                waveformSection
                arrangementSection
                if studio.isAnalyzingQuality {
                    ProgressView("Analyzing quality…").controlSize(.small)
                }
                if let error = studio.qualityError {
                    Text(error).font(.callout).foregroundStyle(JamTheme.error)
                }
                if let quality = studio.quality {
                    QualityMetricsView(quality: quality)
                }
                if studio.isLoading {
                    ProgressView("Loading analysis…").controlSize(.small)
                }
                if let error = studio.error {
                    Text(error).font(.callout).foregroundStyle(JamTheme.error)
                }
                if let detail = studio.detail {
                    overview(detail)
                    if let descriptor = studio.descriptor {
                        ToneCardsView(
                            descriptor: descriptor,
                            detectedType: detail.result?.detectedType
                                ?? detail.detectedType)
                    }
                    if !studio.midiStemRows.isEmpty {
                        midiCard
                    }
                    if !studio.stageRows.isEmpty {
                        profilingCard
                    }
                } else if !studio.isLoading, studio.quality == nil,
                          !studio.isAnalyzingQuality {
                    emptyState
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .fileImporter(
            isPresented: $showFilePicker,
            allowedContentTypes: [.audio]
        ) { result in
            if case let .success(url) = result {
                studio.sourceFileURL = url
                studio.clearQuality()
                studio.clearWaveform()
                selectedSectionID = nil
                Task {
                    await withSourceAccess(url) {
                        await studio.loadWaveform(
                            baseURL: model.backendBaseURL)
                    }
                }
            }
        }
        .task {
            // Auto-load current session's analysis if none loaded yet
            if studio.detail == nil, !studio.isLoading,
               let analysisId = model.session?.bundle.analysisId {
                await studio.load(
                    baseURL: model.backendBaseURL, id: analysisId)
            }
        }
    }

    /// Security-scoped wrapper: files picked outside the sandbox need
    /// explicit access around every read.
    private func withSourceAccess(
        _ url: URL, _ work: () async -> Void
    ) async {
        let accessing = url.startAccessingSecurityScopedResource()
        defer {
            if accessing { url.stopAccessingSecurityScopedResource() }
        }
        await work()
    }

    // MARK: - source bar (Phase 2 + Phase 4 deep mode)

    private var sourceBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Button {
                    showFilePicker = true
                } label: {
                    Label("Choose audio file", systemImage: "folder")
                }
                if let url = studio.sourceFileURL {
                    Text(url.lastPathComponent)
                        .font(.caption)
                        .foregroundStyle(JamTheme.textSecondary)
                        .lineLimit(1)
                    Button("Analyze quality") {
                        Task {
                            await withSourceAccess(url) {
                                await studio.runQualityAnalysis(
                                    baseURL: model.backendBaseURL)
                            }
                        }
                    }
                    .disabled(studio.isAnalyzingQuality)
                    Button(studio.trim?.isFullRange == false
                           ? "Analyze selection" : "Analyze file") {
                        Task {
                            await withSourceAccess(url) {
                                await studio.runTrimmedAnalysis(
                                    baseURL: model.backendBaseURL)
                            }
                        }
                    }
                    .disabled(studio.isRunningTrimmedAnalysis)
                    // Phase 4: Deep analysis via local engine
                    if studio.localEngineStatus == .available {
                        Button("Deep Analyze (GPU)") {
                            Task {
                                await withSourceAccess(url) {
                                    await studio.runDeepAnalysis(
                                        baseURL: model.backendBaseURL)
                                }
                            }
                        }
                        .disabled(studio.isRunningDeepAnalysis)
                    }
                    Button("Detect sections") {
                        selectedSectionID = nil
                        Task {
                            await withSourceAccess(url) {
                                await studio.detectSections(
                                    baseURL: model.backendBaseURL)
                            }
                        }
                    }
                    .disabled(studio.isDetectingSections)
                }
                Spacer()
                localEngineIndicator
            }
            // Deep analysis progress
            if studio.isRunningDeepAnalysis, let progress = studio.deepProgress {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(progress.message)
                        .font(.callout)
                        .foregroundStyle(JamTheme.textSecondary)
                    if let pct = progress.percent {
                        Text(String(format: "%.0f%%", pct))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(JamTheme.textSecondary)
                    }
                }
            }
            if let error = studio.deepError {
                HStack {
                    Text(error).font(.callout).foregroundStyle(JamTheme.error)
                    Button("Dismiss") { studio.clearDeepError() }
                        .controlSize(.small)
                }
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
        .task {
            await studio.checkLocalEngine()
        }
    }

    @ViewBuilder
    private var localEngineIndicator: some View {
        switch studio.localEngineStatus {
        case .available:
            HStack(spacing: 4) {
                Circle()
                    .fill(.green)
                    .frame(width: 8, height: 8)
                Text("Local GPU")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .unavailable:
            HStack(spacing: 4) {
                Circle()
                    .fill(.gray)
                    .frame(width: 8, height: 8)
                Text("No local engine")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        case .unknown:
            EmptyView()
        }
    }

    // MARK: - waveform + trim (Phase 3)

    @ViewBuilder
    private var waveformSection: some View {
        if studio.isLoadingWaveform {
            ProgressView("Loading waveform…").controlSize(.small)
        }
        if let error = studio.waveformError {
            Text(error).font(.callout).foregroundStyle(JamTheme.error)
        }
        if let waveform = studio.waveform, studio.trim != nil {
            VStack(alignment: .leading, spacing: 8) {
                WaveformTrimView(
                    waveform: waveform,
                    trim: Binding(
                        get: {
                            studio.trim ?? TrimSelection(duration: 0)
                        },
                        set: { studio.trim = $0 }))
                if studio.trim?.isFullRange == false {
                    Button("Reset selection") {
                        studio.trim?.reset()
                    }
                    .controlSize(.small)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .jamCard()
        }
        if studio.isRunningTrimmedAnalysis {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text(studio.trimmedRunProgress ?? "Analyzing…")
                    .font(.callout)
                    .foregroundStyle(JamTheme.textSecondary)
            }
        }
        if let error = studio.trimmedRunError {
            Text(error).font(.callout).foregroundStyle(JamTheme.error)
        }
    }

    // MARK: - arrangement (Phase 3)

    @ViewBuilder
    private var arrangementSection: some View {
        if studio.isDetectingSections {
            ProgressView("Detecting sections…").controlSize(.small)
        }
        if let error = studio.arrangementError {
            Text(error).font(.callout).foregroundStyle(JamTheme.error)
        }
        if let arrangement = studio.arrangement {
            ArrangementTimelineView(
                arrangement: arrangement,
                selectedSectionID: selectedSectionID
            ) { section in
                selectedSectionID = section.id
                guard let url = studio.sourceFileURL else { return }
                Task {
                    await withSourceAccess(url) {
                        await studio.analyzeRegion(
                            baseURL: model.backendBaseURL,
                            startTime: section.startTime,
                            endTime: section.endTime)
                    }
                }
            }
        }
        if studio.isAnalyzingRegion {
            ProgressView("Analyzing region…").controlSize(.small)
        }
        if let error = studio.regionError {
            Text(error).font(.callout).foregroundStyle(JamTheme.error)
        }
        if let region = studio.region {
            RegionInspectorView(region: region)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "waveform.badge.magnifyingglass")
                .font(.system(size: 36))
                .foregroundStyle(JamTheme.textSecondary)
            Text("Open a song from the Band Room history to inspect its "
                + "analysis, or pick an audio file above for a quality check.")
                .font(.callout)
                .foregroundStyle(JamTheme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 80)
    }

    // MARK: - overview

    private func overview(_ detail: StudioHistoryDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(detail.name ?? detail.filename ?? "Untitled")
                    .font(.title2.bold())
                if detail.deepAnalysis == true {
                    Text("DEEP")
                        .font(.caption2.bold())
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Capsule().fill(JamTheme.accent.opacity(0.25)))
                }
                Spacer()
                Text(DebugFormat.timestamp(detail.timestamp))
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
            if let attribution = detail.attribution {
                Text(attribution)
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
            HStack(spacing: 10) {
                if let type = detail.result?.detectedType ?? detail.detectedType {
                    fact(type.capitalized)
                }
                if let duration = detail.result?.durationSec ?? detail.duration {
                    fact(String(format: "%.0fs", duration))
                }
                if let rate = detail.result?.sampleRate {
                    fact("\(rate) Hz")
                }
                if let tempo = detail.result?.tempoBpm {
                    fact(String(format: "%.1f BPM", tempo))
                }
                if let key = detail.result?.detectedKey {
                    fact(key)
                }
                Spacer()
            }
            if let summary = detail.summary {
                Text(summary)
                    .font(.callout)
                    .foregroundStyle(JamTheme.textSecondary)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func fact(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(JamTheme.surfaceElevated))
            .overlay(Capsule().strokeBorder(JamTheme.stroke))
    }

    // MARK: - MIDI stats

    private var midiCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("MIDI extraction").font(.headline)
                Spacer()
                Text("\(studio.totalMidiNotes) notes total")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(JamTheme.textSecondary)
            }
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 6) {
                GridRow {
                    Text("stem").font(.caption.bold())
                    Text("notes").font(.caption.bold())
                    Text("notes/s").font(.caption.bold())
                    Text("tempo").font(.caption.bold())
                    Text("method").font(.caption.bold())
                }
                .foregroundStyle(JamTheme.textSecondary)
                ForEach(studio.midiStemRows) { row in
                    GridRow {
                        Text(row.stem).font(.caption.bold())
                        Text(DebugFormat.num(row.info.noteCount))
                            .font(.caption.monospacedDigit())
                        Text(row.info.notesPerSecond.map {
                            String(format: "%.2f", $0)
                        } ?? "—")
                            .font(.caption.monospacedDigit())
                        Text(row.info.extractionTempoBpm.map {
                            String(format: "%.0f BPM", $0)
                        } ?? "—")
                            .font(.caption.monospacedDigit())
                        Text(row.info.method ?? "—")
                            .font(.caption)
                            .foregroundStyle(JamTheme.textSecondary)
                            .lineLimit(1)
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    // MARK: - profiling

    private var profilingCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Pipeline profiling").font(.headline)
                Spacer()
                if let total = studio.detail?.result?.profiling?.totalMs {
                    Text(String(format: "%.1fs total", total / 1000))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(JamTheme.textSecondary)
                }
            }
            VStack(spacing: 5) {
                ForEach(studio.stageRows) { row in
                    stageRow(row)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func stageRow(_ row: StudioModel.StageRow) -> some View {
        HStack(spacing: 10) {
            Text(row.name.replacingOccurrences(of: "_", with: " "))
                .font(.caption)
                .frame(width: 190, alignment: .leading)
                .lineLimit(1)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Color.black.opacity(0.25))
                    RoundedRectangle(cornerRadius: 3)
                        .fill(row.stage.error != nil
                            ? JamTheme.error.opacity(0.7)
                            : JamTheme.accent.opacity(0.7))
                        .frame(width: max(2, geo.size.width * row.fraction))
                }
            }
            .frame(height: 12)
            if row.stage.gpuUsed == true {
                Text("GPU")
                    .font(.system(size: 9).bold())
                    .padding(.horizontal, 4)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(Color.green.opacity(0.2)))
                    .foregroundStyle(.green)
            }
            if row.stage.skipped == true {
                Text("skipped")
                    .font(.system(size: 9))
                    .foregroundStyle(JamTheme.textSecondary)
            }
            Text(String(format: "%.2fs", (row.stage.durationMs ?? 0) / 1000))
                .font(.caption.monospacedDigit())
                .foregroundStyle(JamTheme.textSecondary)
                .frame(width: 60, alignment: .trailing)
        }
    }
}
