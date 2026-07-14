// IntakeView.swift
//
// "What are we playing?" — parity with the web intake card:
// engine banner, URL paste, file drop/browse (with ownership
// attestation), curated demo tracks, instrument selector, and the
// recent-history list underneath. Kicking off any flow flips the app
// to the Band Room where progress renders.

import SwiftUI
import JamDesktopCore

struct IntakeView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var intake: IntakeModel
    @EnvironmentObject private var queue: AnalysisQueueModel
    @EnvironmentObject private var history: HistoryModel

    @State private var sourceUrl = ""
    @State private var showingDemoTracks = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                engineBanner

                Text("What are we playing?")
                    .font(.largeTitle.bold())

                VStack(alignment: .leading, spacing: 20) {
                    urlRow

                    UploadDropZone { fileURL in
                        startUpload(fileURL)
                    }

                    HStack(spacing: 12) {
                        Button("Browse demo tracks…") {
                            showingDemoTracks = true
                        }

                        Spacer()

                        instrumentPicker
                    }
                }
                .padding(20)
                .jamCard()

                Divider()

                HistoryListView()
            }
            .padding(24)
            .frame(maxWidth: 720)
            .frame(maxWidth: .infinity)
        }
        .sheet(isPresented: $showingDemoTracks) {
            DemoTracksSheet { track in
                showingDemoTracks = false
                queue.enqueueDemoImport(
                    baseURL: model.backendBaseURL,
                    trackId: track.id,
                    title: track.title
                )
                model.view = .bandRoom
            }
        }
        .task {
            await intake.refreshEngineStatus(baseURL: model.backendBaseURL)
            await history.refresh(baseURL: model.backendBaseURL)
        }
    }

    // MARK: - Engine banner

    @ViewBuilder
    private var engineBanner: some View {
        if let status = intake.engineStatus {
            HStack(spacing: 6) {
                Circle()
                    .fill(status.online ? Color.green : Color.orange)
                    .frame(width: 8, height: 8)
                Text(status.online
                    ? "Analysis engine online\(status.device.map { " (\($0))" } ?? "")"
                    : "Analysis engine offline — jobs will queue")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - URL paste

    private var urlRow: some View {
        HStack(spacing: 8) {
            TextField("Paste a song URL…", text: $sourceUrl)
                .textFieldStyle(.roundedBorder)
                .onSubmit(startURLAnalysis)

            Button("Analyze") {
                startURLAnalysis()
            }
            .disabled(sourceUrl.trimmingCharacters(in: .whitespaces).isEmpty)
        }
    }

    // MARK: - Instrument

    private var instrumentPicker: some View {
        Picker("Instrument", selection: instrumentBinding) {
            ForEach(IntakeInstrument.all) { item in
                Text(item.label)
                    .tag(item.id)
                    .selectionDisabled(!item.enabled)
            }
        }
        .frame(maxWidth: 260)
    }

    private var instrumentBinding: Binding<String> {
        Binding(get: { intake.instrument }, set: { intake.instrument = $0 })
    }

    // MARK: - Flow starts

    private func startURLAnalysis() {
        let trimmed = sourceUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        queue.enqueueURL(baseURL: model.backendBaseURL, sourceUrl: trimmed)
        sourceUrl = ""
        model.view = .bandRoom
    }

    private func startUpload(_ fileURL: URL) {
        queue.enqueueUpload(
            baseURL: model.backendBaseURL,
            fileURL: fileURL,
            filename: fileURL.lastPathComponent,
            attested: intake.attested
        )
        model.view = .bandRoom
    }
}
