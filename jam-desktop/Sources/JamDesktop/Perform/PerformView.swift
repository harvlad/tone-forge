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

/// Perform = OVERLAY architecture. The neck is the stage; the eye almost never
/// leaves it. Five INDEPENDENT layers over one shared chord/playhead — three are
/// drawn ON the neck, two are small supporting reference cards beneath it:
///   Motion     — trajectories, ghosts, arrival pulses, movement timing (on neck)
///   Hand       — realistic pose overlaid on the neck at low opacity (on neck)
///   Dots       — coloured finger contacts + finger identity, the primary layer (on neck)
///   Chord      — chord diagram reference card (below neck)
///   TAB        — tablature reference card (below neck)
/// Priority: fretboard > dots+motion > hand > chord/TAB. No layer knows another.
private let jamAccent = Color(red: 0.545, green: 0.427, blue: 1.0)

struct PerformView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var tabLane = TabLaneModel()
    @State private var toneCardDismissed = false
    @StateObject private var handScene = HandPoseModel()   // runtime 3D hand pose
    private var handSceneURL: URL? {
        Bundle.module.url(forResource: "hand", withExtension: "usdz", subdirectory: "Hand")
    }
    // Five independent display layers — persisted per user, all on by default;
    // at least one must always stay on. No layer knows whether another is on.
    @AppStorage("perf.layer.motion") private var showMotion = true   // trajectories/ghosts/pulses (on neck)
    @AppStorage("perf.layer.hand") private var showHand = true       // abstract posed hand (on neck)
    @AppStorage("perf.hand.mesh") private var handMesh = true        // realistic MPFB mesh is the default beginner view
    @AppStorage("perf.layer.dots") private var showDots = true       // finger contact dots (on neck)
    // Chord and TAB are the two reference cards — mutually exclusive (either/or).
    @AppStorage("perf.layer.chord") private var showChord = true     // chord diagram (reference card)
    @AppStorage("perf.layer.tab") private var showTab = false        // tablature (reference card)
    private var layersOn: Int {
        (showMotion ?1:0) + (showHand ?1:0) + (showDots ?1:0) + (showChord ?1:0) + (showTab ?1:0)
    }

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
        .onAppear { if showChord && showTab { showTab = false } }   // enforce either/or
        .onChange(of: currentHandSymbol) { _, sym in
            handScene.targetPose = sym.flatMap { HandStates.pose(for: $0) } ?? [:]
        }
    }

    /// Chord symbol under the playhead — drives the 3D hand pose target.
    private var currentHandSymbol: String? {
        session.ribbon?.currentChord(at: session.transport.positionSeconds)?.symbol
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
                    ZStack {
                        HandNeckView(chords: ribbon.chords,
                                     positionSeconds: session.transport.positionSeconds,
                                     showDots: showDots,
                                     showMotion: showMotion,
                                     showHand: showHand,
                                     useMesh: false)
                        // Phase 2 proof: runtime 3D rigged hand overlaid on the neck.
                        if handMesh, let url = handSceneURL {
                            HandSceneView(sceneURL: url, poseModel: handScene)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    // Supporting reference cards beneath the neck — Chord / TAB only.
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
            layerPill("Motion", systemImage: "waveform.path", on: showMotion) {
                if !(showMotion && layersOn == 1) { showMotion.toggle() }
            }
            layerPill("Hand", systemImage: "hand.raised.fill", on: showHand) {
                if !(showHand && layersOn == 1) { showHand.toggle() }
            }
            // Optional: swap the abstract hand silhouette for the realistic MPFB mesh.
            if showHand {
                layerPill("Realistic", systemImage: "cube.transparent", on: handMesh) {
                    handMesh.toggle()
                }
            }
            layerPill("Dots", systemImage: "circlebadge.fill", on: showDots) {
                if !(showDots && layersOn == 1) { showDots.toggle() }
            }
            // Chord / TAB are mutually exclusive: enabling one disables the other.
            layerPill("Chord", systemImage: "tablecells", on: showChord) {
                if showChord { if layersOn > 1 { showChord = false } }
                else { showChord = true; showTab = false }
            }
            layerPill("TAB", systemImage: "music.note.list", on: showTab) {
                if showTab { if layersOn > 1 { showTab = false } }
                else { showTab = true; showChord = false }
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

    private func resetLayout() {
        showMotion = true; showHand = true; showDots = true; showChord = true; showTab = false
    }

    // MARK: supporting reference cards — Chord Diagram / TAB only.
    // These verify the exact fingering; they never compete with the neck, so they
    // stay small. When neither is on, the row vanishes and the neck takes all the
    // height (Motion / Hand / Dots all live on the neck itself).
    @ViewBuilder
    private func lowerPanel(ribbon: ChordRibbonModel) -> some View {
        let symbol = ribbon.currentChord(at: session.transport.positionSeconds)?.symbol
        // Either/or: Chord and TAB never show at once.
        if showChord {
            referenceCard {
                panelCard("Chord Diagram") {
                    if let symbol, let diagram = ChordDiagram.make(symbol: symbol) {
                        ChordDiagramView(diagram: diagram)
                    } else { Color.clear }
                }
            }
        } else if showTab {
            referenceCard {
                panelCard("TAB") {
                    if !tabLane.notes.isEmpty { tabLaneBlock() } else { Color.clear }
                }
            }
        }
    }

    /// The single supporting reference card beneath the neck — small, so it never
    /// competes with the fretboard.
    @ViewBuilder
    private func referenceCard<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        content().frame(height: 190)
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
