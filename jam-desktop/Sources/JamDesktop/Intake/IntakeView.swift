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
            VStack(spacing: 32) {
                // Branding header
                VStack(spacing: 8) {
                    JamLogo()
                        .frame(width: 80, height: 80)
                    Text("jamn")
                        .font(.system(size: 36, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                    HStack(spacing: 20) {
                        tagline(icon: "book", accent: "Learn", rest: " songs.")
                        tagline(icon: "square.grid.3x3.fill", accent: "Jam", rest: " along.")
                        tagline(icon: "music.note", accent: "Create", rest: " something new.")
                    }
                    .padding(.top, 4)
                }
                .frame(maxWidth: .infinity)
                .padding(.top, 16)

                // Intake card
                VStack(alignment: .leading, spacing: 20) {
                    HStack {
                        Text("What are we playing?")
                            .font(.title2.bold())
                        Spacer()
                        engineBanner
                    }

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
                .padding(24)
                .jamCard()

                // History
                VStack(alignment: .leading, spacing: 12) {
                    Text("Recent songs")
                        .font(.headline)
                    HistoryListView()
                }
            }
            .padding(.horizontal, 40)
            .padding(.vertical, 24)
            .frame(maxWidth: 960)
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

    // MARK: - Tagline helper

    private func tagline(icon: String, accent: String, rest: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(JamTheme.brandGreenDark)
            (Text(accent)
                .foregroundStyle(JamTheme.brandGreenLight)
                .fontWeight(.semibold)
                + Text(rest)
                .foregroundStyle(.white.opacity(0.7)))
                .font(.caption)
        }
    }
}

// MARK: - Jam Logo

/// The Jam mark: waveform bars with a play triangle, brand gradient.
private struct JamLogo: View {
    private let bars: [CGFloat] = [0.4, 0.72, 1.0, 0.82, 0.58, 0.42]

    var body: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let barWidth = geo.size.width * 0.1
            let spacing = geo.size.width * 0.045
            HStack(alignment: .center, spacing: spacing) {
                ForEach(bars.indices, id: \.self) { i in
                    Capsule()
                        .frame(width: barWidth, height: h * bars[i])
                }
                Triangle()
                    .frame(width: barWidth * 1.4, height: barWidth * 1.6)
            }
            .frame(width: geo.size.width, height: h, alignment: .center)
            .foregroundStyle(JamTheme.brandGradient)
        }
    }
}

/// Right-pointing play triangle.
private struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}
