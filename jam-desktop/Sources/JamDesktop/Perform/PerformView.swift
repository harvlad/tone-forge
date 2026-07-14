// PerformView.swift
//
// The full-play surface: now-playing header, tone card, chord ribbon,
// chord diagram + lead tab lane, section strip, stems mixer (right
// panel), transport bar and the attribution credit line. Mirrors the
// web jam Perform view.
//
// The 30 Hz display timer lives here — it pumps
// SessionController.tick(), which advances TransportController off
// the audio clock and mirrors position to bridge peers. Same cadence
// the web app uses.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct PerformView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var tabLane = TabLaneModel()
    @State private var toneCardDismissed = false

    private let displayTimer = Timer.publish(
        every: 1.0 / 30.0, on: .main, in: .common
    ).autoconnect()

    var body: some View {
        Group {
            if let loaded = model.session {
                content(for: loaded)
            } else {
                noSongPlaceholder
            }
        }
        .onReceive(displayTimer) { _ in
            session.tick()
        }
        .task(id: model.session?.bundle.analysisId) {
            toneCardDismissed = false
            if let loaded = model.session {
                await session.attach(loaded)
            }
        }
        .onChange(of: model.sidecar, initial: true) { _, sidecar in
            rebuildTabLane(sidecar)
        }
    }

    private func content(for loaded: LoadedSession) -> some View {
        HStack(spacing: 0) {
            VStack(spacing: 12) {
                NowPlayingHeaderView(meta: loaded.bundle.meta)

                if let tone = model.sidecar?.tone, !toneCardDismissed {
                    ToneCardView(
                        tone: tone,
                        activeChainId: session.monitor.activeChainId,
                        onApply: { session.applyToneChain(chainId: $0) },
                        onDismiss: { dismissToneCard(tone, for: loaded) }
                    )
                }

                if let ribbon = session.ribbon {
                    // Primary: chord label + diagram
                    chordLabelRow(ribbon: ribbon)
                    diagramAndTabRow(ribbon: ribbon)
                        .frame(maxHeight: .infinity)

                    // Secondary: ribbon strip + section strip
                    ChordRibbonStripView(
                        ribbon: ribbon,
                        positionSeconds: session.transport.positionSeconds
                    )
                    .frame(height: 56)

                    SectionStripView(
                        sections: ribbon.sections,
                        durationSeconds: session.transport.durationSeconds,
                        positionSeconds: session.transport.positionSeconds,
                        onSeek: { session.transport.seek(to: $0) }
                    )
                    .frame(height: 44)
                }

                TransportBar()

                CreditsView(
                    attribution: model.sidecar?.attribution,
                    meta: loaded.bundle.meta
                )

                if let error = session.engineError {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(JamTheme.error)
                }
            }
            .padding(16)

            Divider()

            StemsMixerView()
                .frame(width: 280)
        }
    }

    /// Big current/next chord labels (Am → Em).
    private func chordLabelRow(ribbon: ChordRibbonModel) -> some View {
        let window = ribbon.window(at: session.transport.positionSeconds, count: 2)
        let current = ribbon.currentChord(at: session.transport.positionSeconds)
        let next: ChordEvent? = {
            guard let first = window.first else { return nil }
            if current != nil {
                return window.count > 1 ? window[1] : nil
            }
            return first
        }()

        return HStack(alignment: .firstTextBaseline, spacing: 24) {
            Text(current?.symbol ?? "—")
                .font(.system(size: 72, weight: .bold, design: .rounded))
                .monospacedDigit()
            if let next {
                Text("→ \(next.symbol)")
                    .font(.system(size: 32, weight: .medium, design: .rounded))
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity)
        .animation(nil, value: session.transport.positionSeconds)
    }

    /// Current-chord diagram beside the scrolling lead tab lane.
    @ViewBuilder
    private func diagramAndTabRow(ribbon: ChordRibbonModel) -> some View {
        let symbol = ribbon.currentChord(
            at: session.transport.positionSeconds)?.symbol
        if !tabLane.notes.isEmpty || symbol != nil {
            HStack(alignment: .center, spacing: 24) {
                if let symbol, let diagram = ChordDiagram.make(symbol: symbol) {
                    ChordDiagramView(diagram: diagram)
                        .frame(width: 280, height: 340)
                }
                if !tabLane.notes.isEmpty {
                    VStack(alignment: .trailing, spacing: 4) {
                        TabLaneView(
                            model: tabLane,
                            positionSeconds: session.transport.positionSeconds
                        )
                        Picker("Glyph", selection: $tabLane.glyph) {
                            ForEach(TabLaneGlyph.allCases, id: \.self) {
                                Text($0.rawValue.capitalized).tag($0)
                            }
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                        .frame(width: 180)
                    }
                }
            }
            .frame(minHeight: 340)
        }
    }

    private func rebuildTabLane(_ sidecar: SessionSidecar?) {
        let duration = model.session?.bundle.meta.durationSec ?? 0
        let picked = LeadNotePicker.pick(
            stems: sidecar?.midiStems, durationSec: duration)
        tabLane.notes = picked.map {
            TabLaneNote(pitch: $0.pitch, startS: $0.start)
        }
    }

    private func dismissToneCard(
        _ tone: ToneRecommendation, for loaded: LoadedSession
    ) {
        toneCardDismissed = true
        let backend = model.backendBaseURL
        let chainId = tone.apply?.chainId ?? tone.match?.chainId
        let analysisId = loaded.bundle.analysisId
        let sourceUrl = loaded.bundle.meta.sourceUrl
        Task {
            await ToneIgnoredReporter.post(
                chainId: chainId,
                reason: "dismissed",
                analysisId: analysisId,
                sourceUrl: sourceUrl.isEmpty ? nil : sourceUrl,
                backend: backend
            )
        }
    }

    private var noSongPlaceholder: some View {
        VStack(spacing: 8) {
            Text("No song loaded")
                .font(.title2)
            Text("Pick a song from Intake (M2) — or load one by analysis id below.")
                .foregroundStyle(.secondary)
            DebugSessionLoaderView()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// M1 dev affordance: load a session by analysis id until the Intake
/// and history views land in M2.
private struct DebugSessionLoaderView: View {
    @EnvironmentObject private var model: AppModel
    @State private var analysisId = ""

    var body: some View {
        HStack {
            TextField("analysis id", text: $analysisId)
                .textFieldStyle(.roundedBorder)
                .frame(width: 320)
            Button("Load") {
                let id = analysisId.trimmingCharacters(in: .whitespaces)
                guard !id.isEmpty else { return }
                Task { await model.loadSession(analysisId: id) }
            }
            .disabled(model.isLoadingSession)
        }
        .padding(.top, 8)
        .overlay(alignment: .bottom) {
            if model.isLoadingSession {
                ProgressView().controlSize(.small).offset(y: 24)
            } else if let err = model.sessionError {
                Text(err).font(.caption).foregroundStyle(JamTheme.error).offset(y: 24)
            }
        }
    }
}
