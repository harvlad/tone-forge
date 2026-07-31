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

/// The learning stack: three layers that teach different things, shown together
/// and synchronized to the current chord — motion (how the hand moves),
/// orientation (what it should look like now), precision (exact frets/strings).
enum PerfLayer: String, CaseIterable, Hashable {
    case motion = "Motion", pose = "Pose", chord = "Chord", tab = "TAB"
}
private let jamAccent = Color(red: 0.545, green: 0.427, blue: 1.0)

struct PerformView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var tabLane = TabLaneModel()
    @State private var toneCardDismissed = false
    /// Perform visualization: Hand (default), TAB, or Both.
    // All layers on by default — beginners live on Pose, intermediates on Motion,
    // advanced on Chord; anyone can hide layers they don't want.
    @State private var layers: Set<PerfLayer> = [.motion, .pose, .chord, .tab]

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
    /// Perform visualization surface: Hand (default), TAB, or Both.
    /// The synchronized learning stack: motion on top, then a row of orientation
    /// (static pose) + precision (chord diagram) + the lead TAB lane — all driven
    /// by the same current chord / playhead. Layers are toggled, not exclusive.
    @ViewBuilder
    private func diagramAndTabRow(ribbon: ChordRibbonModel) -> some View {
        let pos = session.transport.positionSeconds
        let symbol = ribbon.currentChord(at: pos)?.symbol
        VStack(spacing: 14) {
            layerToolbar

            if layers.contains(.motion) {
                HandNeckView(chords: ribbon.chords, positionSeconds: pos)
                    .frame(maxWidth: .infinity, minHeight: 220)
            }

            let showRow = layers.contains(.pose) || layers.contains(.chord)
                || (layers.contains(.tab) && !tabLane.notes.isEmpty)
            if showRow {
                HStack(alignment: .top, spacing: 16) {
                    if layers.contains(.pose) {
                        stackCard(title: "SHAPE") { StaticHandPoseView(symbol: symbol) }
                            .frame(width: 210, height: 232)
                    }
                    if layers.contains(.chord) {
                        stackCard(title: "FRETS") {
                            if let symbol, let diagram = ChordDiagram.make(symbol: symbol) {
                                ChordDiagramView(diagram: diagram)
                            } else { Color.clear }
                        }
                        .frame(width: 210, height: 232)
                    }
                    if layers.contains(.tab) && !tabLane.notes.isEmpty {
                        tabLaneBlock().frame(maxWidth: .infinity)
                    }
                }
            }
        }
        .frame(minHeight: 320)
    }

    /// Layer toggles — Motion · Pose · Chord · TAB. All on by default.
    private var layerToolbar: some View {
        HStack(spacing: 8) {
            ForEach(PerfLayer.allCases, id: \.self) { layer in
                let on = layers.contains(layer)
                Button(layer.rawValue) {
                    if on { layers.remove(layer) } else { layers.insert(layer) }
                }
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .semibold))
                .padding(.horizontal, 13).padding(.vertical, 6)
                .background(on ? jamAccent : Color.gray.opacity(0.18))
                .foregroundStyle(on ? Color.white : Color.secondary)
                .clipShape(Capsule())
            }
        }
    }

    /// A titled reference card for a static stack layer.
    @ViewBuilder
    private func stackCard<Content: View>(title: String,
                                          @ViewBuilder _ content: () -> Content) -> some View {
        VStack(spacing: 6) {
            content().frame(maxWidth: .infinity, maxHeight: .infinity)
            Text(title).font(.system(size: 10, design: .monospaced)).tracking(1.5)
                .foregroundStyle(.tertiary)
        }
        .padding(10)
        .background(Color.gray.opacity(0.06))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func tabLaneBlock() -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            TabLaneView(model: tabLane, positionSeconds: session.transport.positionSeconds)
            Picker("Glyph", selection: $tabLane.glyph) {
                ForEach(TabLaneGlyph.allCases, id: \.self) { Text($0.rawValue.capitalized).tag($0) }
            }
            .pickerStyle(.segmented).labelsHidden().frame(width: 180)
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
