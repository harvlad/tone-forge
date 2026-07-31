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

/// Perform = four INDEPENDENT learning layers over one shared chord/playhead:
///   Motion — the animated hand on the neck (how the hand moves)
///   Pose   — a static rendered hand pose (what the hand should look like)
///   Chord  — the chord diagram (exact frets/strings)
///   TAB    — tablature
/// Toggled independently; the neck is always the hero. No layer knows another.
private let jamAccent = Color(red: 0.545, green: 0.427, blue: 1.0)

struct PerformView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var tabLane = TabLaneModel()
    @State private var toneCardDismissed = false
    // Four independent learning layers — persisted per user, all on by default;
    // at least one must always stay on. No layer knows whether another is on.
    @AppStorage("perf.layer.motion") private var showMotion = true   // animated hand on the neck
    @AppStorage("perf.layer.pose") private var showPose = true       // static rendered pose
    @AppStorage("perf.layer.chord") private var showChord = true     // chord diagram
    @AppStorage("perf.layer.tab") private var showTab = true         // tablature
    private var layersOn: Int { (showMotion ?1:0) + (showPose ?1:0) + (showChord ?1:0) + (showTab ?1:0) }

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
            VStack(spacing: 16) {
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
                    // Performance toolbar (independent layer toggles)
                    performanceToolbar

                    // Neck / fretboard — the hero, most vertical space
                    HandNeckView(chords: ribbon.chords,
                                 positionSeconds: session.transport.positionSeconds,
                                 showContacts: showMotion)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)

                    // Lower reference row — Pose / Chord / TAB (Motion lives on the neck)
                    lowerPanel(ribbon: ribbon)

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

    // MARK: performance toolbar (independent layer toggles)
    private var performanceToolbar: some View {
        HStack(spacing: 10) {
            Text("Display Layers")
                .font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            layerPill("Motion", systemImage: "waveform", on: showMotion) {
                if !(showMotion && layersOn == 1) { showMotion.toggle() }
            }
            layerPill("Pose", systemImage: "hand.raised.fill", on: showPose) {
                if !(showPose && layersOn == 1) { showPose.toggle() }
            }
            layerPill("Chord", systemImage: "tablecells", on: showChord) {
                if !(showChord && layersOn == 1) { showChord.toggle() }
            }
            layerPill("TAB", systemImage: "music.note.list", on: showTab) {
                if !(showTab && layersOn == 1) { showTab.toggle() }
            }
            Spacer()
            Button { resetLayout() } label: {
                Label("Reset Layout", systemImage: "arrow.clockwise")
                    .font(.system(size: 12, weight: .semibold))
            }
            .buttonStyle(.plain).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14).padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.gray.opacity(0.08)))
    }

    private func layerPill(_ title: String, systemImage: String, on: Bool,
                           _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: systemImage).font(.system(size: 12, weight: .semibold))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 13).padding(.vertical, 7)
        .background(on ? jamAccent : Color.gray.opacity(0.18))
        .foregroundStyle(on ? Color.white : Color.secondary)
        .clipShape(Capsule())
    }

    private func resetLayout() { showMotion = true; showPose = true; showChord = true; showTab = true }

    // MARK: lower reference row — the ON subset of {Pose, Chord, TAB}, split evenly.
    // Motion is the neck above, not a lower panel; if no lower layer is on the row
    // vanishes and the neck takes maximum height.
    @ViewBuilder
    private func lowerPanel(ribbon: ChordRibbonModel) -> some View {
        let symbol = ribbon.currentChord(at: session.transport.positionSeconds)?.symbol
        if showPose || showChord || showTab {
            HStack(alignment: .top, spacing: 16) {
                if showPose {
                    panelCard("Hand") { StaticHandPoseView(symbol: symbol) }
                }
                if showChord {
                    panelCard("Chord Diagram") {
                        if let symbol, let diagram = ChordDiagram.make(symbol: symbol) {
                            ChordDiagramView(diagram: diagram)
                        } else { Color.clear }
                    }
                }
                if showTab {
                    panelCard("TAB") {
                        if !tabLane.notes.isEmpty { tabLaneBlock() } else { Color.clear }
                    }
                }
            }
            .frame(height: 300)   // large — references read as a teacher's held-up hand
        }
    }

    /// A titled panel card (header dot + title) for a lower-panel layer.
    @ViewBuilder
    private func panelCard<Content: View>(_ title: String,
                                          @ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle().fill(jamAccent).frame(width: 8, height: 8)
                Text(title).font(.system(size: 13, weight: .semibold))
            }
            content().frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .padding(12)
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.gray.opacity(0.06)))
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
