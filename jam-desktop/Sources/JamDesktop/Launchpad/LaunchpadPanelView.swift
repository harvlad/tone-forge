// LaunchpadPanelView.swift
//
// On-screen 8x8 mirror of the Launchpad chop grid plus the chop
// controls (quantize, stem, slice mode). Pads route straight into
// LaunchpadController.padDown/padUp — the same methods the hardware
// transport calls — so screen and device stay interchangeable.
//
// Hardware status: shows the USB transport's connection state and
// the underpower banner (unpowered hubs brown the device out).

import SwiftUI
import AppKit
import AVFoundation
import JamDesktopCore
import JamDesktopAudio
import ToneForgeEngine

extension Int: @retroactive Identifiable {
    public var id: Int { self }
}

struct LaunchpadPanelView: View {
    @Environment(\.dismiss) private var dismiss
    /// Set when the panel is hosted as a non-modal overlay (UX audit fix #5)
    /// — the ✕ calls this instead of the sheet dismiss.
    var onClose: (() -> Void)? = nil
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var selectedStem = ""
    @State private var selectedSliceMode = ""
    @State private var editorTarget: ChopEditorTarget?
    @State private var transformTarget: TransformEditTarget?
    @State private var vocoderTarget: VocoderCaptureTarget?
    @State private var patternAssignTarget: Int?  // padIdx to assign pattern
    @State private var soundPickerTarget: Int?    // padIdx to add sound
    @State private var radialMenuState: PadRadialMenuState?
    @State private var showSequencerEditor = false
    @State private var moveMode = false
    @State private var dragSourcePad: Int?
    @State private var showLayers = false

    /// ONE 30 Hz driver for every animated readout in the panel (per-pad
    /// loop playheads + the cycle strip). The per-cell
    /// TimelineView(.animation) instances silently stopped ticking on
    /// macOS release builds — playheads froze and only jumped when an
    /// unrelated re-render (hover!) redrew the cell.
    @State private var animTick = 0
    private let animTimer = Timer.publish(
        every: 1.0 / 30.0, on: .main, in: .common).autoconnect()

    private var launchpad: LaunchpadController { session.launchpad }

    var body: some View {
        VStack(spacing: 12) {
            header
            controls
            // The visible musical clock (UX audit fix #1): sweep of the
            // shared loop cycle + countdown to the next lock boundary, so
            // "why is my pad waiting" reads as timing, not lag.
            cycleStrip
            if showLayers {
                LayerStackView().environmentObject(session)
            } else {
                padGrid
                    .aspectRatio(1, contentMode: .fit)
            }
            if let error = launchpad.fetchError {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(JamTheme.error)
            }
            if session.usbLaunchpad?.underpowerSuspected == true {
                underpowerBanner
            }
        }
        .padding(20)
        // FIXED size: as a floating overlay the panel must not negotiate
        // with the window's proposal at all — min/ideal/max still let it
        // stretch in practice. One deterministic footprint.
        .frame(width: 1040, height: 860)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        // Tick only while something is actually animating (sounding pads or
        // a rolling transport) so an idle panel doesn't redraw at 30 Hz.
        .onReceive(animTimer) { _ in
            if !launchpad.activePads.isEmpty || session.transport.isPlaying {
                animTick &+= 1
            }
        }
        .onAppear {
            selectedStem = launchpad.stem ?? ""
            selectedSliceMode = launchpad.sliceMode ?? ""
        }
        .sheet(item: $editorTarget) { target in
            ChopEditorSheet(target: target)
                .environmentObject(session)
        }
        .sheet(item: $transformTarget) { target in
            TransformEditSheet(target: target)
                .environmentObject(session)
        }
        .sheet(item: $vocoderTarget) { target in
            VocoderCaptureSheet(target: target)
                .environmentObject(session)
        }
        .sheet(item: $patternAssignTarget) { padIdx in
            PatternAssignSheet(
                padIdx: padIdx,
                patterns: session.patternStore.all(),
                onSelect: { patternId in
                    session.padAssignmentStore.assign(
                        .sequence(patternId: patternId),
                        padIdx: padIdx
                    )
                    patternAssignTarget = nil
                },
                onClear: {
                    session.padAssignmentStore.assign(nil, padIdx: padIdx)
                    session.sequencePadManager.stop(padIdx: padIdx)
                    patternAssignTarget = nil
                },
                onCancel: {
                    patternAssignTarget = nil
                }
            )
        }
        .sheet(item: $soundPickerTarget) { padIdx in
            SoundPickerSheet(
                padIdx: padIdx,
                packs: session.packs,
                backendURL: model.backendBaseURL,
                onAssign: { packId, sourcePadIdx in
                    session.padAssignmentStore.assign(
                        .packPad(packId: packId, padIdx: sourcePadIdx),
                        padIdx: padIdx
                    )
                    soundPickerTarget = nil
                },
                onCancel: {
                    soundPickerTarget = nil
                }
            )
        }
        .overlay {
            if let state = radialMenuState {
                PadRadialMenu(
                    state: state,
                    onAction: { action in
                        handleRadialAction(action, state: state)
                        radialMenuState = nil
                    },
                    onDismiss: {
                        radialMenuState = nil
                    }
                )
            }
        }
        .sheet(isPresented: $showSequencerEditor) {
            SequencerPanelView()
                .environmentObject(model)
                .environmentObject(session)
        }
    }

    // MARK: - Radial Menu Actions

    private func handleRadialAction(_ action: PadRadialAction, state: PadRadialMenuState) {
        let pad = LaunchpadPad(row: state.gridRow, col: state.gridCol)
        let padIdx = state.padIdx

        switch action {
        case .effects:
            if let assignment = launchpad.assignments[pad] {
                transformTarget = transformTarget(for: pad, assignment: assignment)
            }

        case .chop:
            if let assignment = launchpad.assignments[pad] {
                editorTarget = editorTarget(for: assignment)
            }

        case .loop:
            if let assignment = launchpad.assignments[pad] {
                toggleLoop(assignment: assignment)
            }

        case .reset:
            if let assignment = launchpad.assignments[pad] {
                resetTransforms(assignment: assignment)
            }

        case .delete:
            // Clear custom pad assignment if present
            if let slot = session.padAssignmentStore.slot(padIdx: padIdx) {
                if case .sequence = slot {
                    session.sequencePadManager.stop(padIdx: padIdx)
                }
                session.padAssignmentStore.assign(nil, padIdx: padIdx)
            }
            // TODO: Clear chop assignment when supported

        case .sequence:
            // Open pattern picker to assign a pattern to this pad
            patternAssignTarget = padIdx

        case .edit:
            // Load the pattern into the sequencer for editing
            if case .sequence(let patternId) = session.padAssignmentStore.slot(padIdx: padIdx),
               let pattern = session.patternStore.all().first(where: { $0.id == patternId }) {
                session.sequencer.pattern = pattern
                showSequencerEditor = true
            }

        case .addToSequence:
            // Add this pad's sample to the step sequencer as a new track,
            // then open the sequencer so the user can place its steps.
            session.addPadToSequence(padIdx: padIdx, pad: pad)
            showSequencerEditor = true

        case .addSound:
            soundPickerTarget = padIdx

        case .voiceRecord:
            vocoderTarget = VocoderCaptureTarget(padIndex: padIdx)
        }
    }

    /// Chop editing needs a preset-sourced grid (edits are keyed by
    /// presetKey), a real bundle chop (idx >= 0 — synthetic split
    /// chops aren't editable) and the stem file on disk.
    private func editorTarget(for assignment: PadAssignment) -> ChopEditorTarget? {
        guard let presetKey = launchpad.presetKey,
              assignment.chop.idx >= 0,
              let loaded = model.session,
              let stemURL = loaded.stemURLs[assignment.stem]
        else { return nil }
        return ChopEditorTarget(
            analysisId: loaded.bundle.analysisId,
            presetKey: presetKey,
            chop: assignment.chop,
            stemURL: stemURL,
            stemDurationSec: loaded.bundle.meta.durationSec
        )
    }

    /// Transform editing needs a bundle chop (idx >= 0) and the stem
    /// file on disk.
    private func transformTarget(for pad: LaunchpadPad, assignment: PadAssignment) -> TransformEditTarget? {
        guard assignment.chop.idx >= 0,
              let loaded = model.session,
              let stemURL = loaded.stemURLs[assignment.stem]
        else { return nil }
        return TransformEditTarget(
            pad: pad,
            assignment: assignment,
            stemURL: stemURL,
            analysisId: loaded.bundle.analysisId
        )
    }

    /// Toggle loop transform on a pad.
    private func toggleLoop(assignment: PadAssignment) {
        guard let loaded = model.session,
              let stemURL = loaded.stemURLs[assignment.stem],
              let file = try? AVAudioFile(forReading: stemURL)
        else { return }

        let packId = loaded.bundle.analysisId
        let padIdx = assignment.chop.idx
        let host = session.transformHost
        let hasLoop = host.loops(packId: packId, padIdx: padIdx)

        Task {
            guard let baseBuffer = await session.transformBakeService.loadBuffer(
                file: file,
                startSec: assignment.chop.startSec,
                endSec: assignment.chop.endSec
            ) else { return }

            let chain: [PadTransform] = hasLoop ? [] : [.loop]
            host.setChain(
                chain,
                packId: packId,
                padIdx: padIdx,
                base: baseBuffer,
                tempoBpm: session.sequencer.songBPM,
                chord: []
            )
        }
    }

    /// Reset all transforms on a pad.
    private func resetTransforms(assignment: PadAssignment) {
        guard let loaded = model.session else { return }
        let packId = loaded.bundle.analysisId
        let padIdx = assignment.chop.idx
        session.transformHost.setChain(
            [],
            packId: packId,
            padIdx: padIdx,
            base: nil,
            tempoBpm: session.sequencer.songBPM,
            chord: []
        )
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            Text("Launchpad")
                .font(.title3.bold())

            Button {
                moveMode.toggle()
            } label: {
                Image(systemName: "arrow.up.and.down.and.arrow.left.and.right")
                    .font(.body)
                    .foregroundStyle(moveMode ? JamTheme.accent : .secondary)
            }
            .buttonStyle(.plain)
            .help(moveMode ? "Exit move mode" : "Move mode: drag pads to swap positions")

            Button {
                resetAllAssignments()
            } label: {
                Image(systemName: "arrow.counterclockwise")
                    .font(.body)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .help("Clear all pad assignments")

            Spacer()
            hardwareStatus

            Button {
                if let onClose { onClose() } else { dismiss() }
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
    }

    private func resetAllAssignments() {
        // Stop any playing sequences
        for (padIdx, _) in session.padAssignmentStore.assignments {
            session.sequencePadManager.stop(padIdx: padIdx)
        }
        // Clear all assignments
        session.padAssignmentStore.clearAll()
    }

    @ViewBuilder
    private var hardwareStatus: some View {
        switch session.usbLaunchpad?.connectionState {
        case .connected(let name):
            Label(name, systemImage: "cable.connector")
                .font(.caption)
                .foregroundStyle(.green)
        default:
            Label("No device", systemImage: "cable.connector.slash")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Controls

    /// Tiny secondary caption naming the picker to its right. Rendered as
    /// part of a tight caption+picker pair (see call sites) so the pair
    /// reads as one labeled control, not two floating row items.
    private func pickerCaption(_ title: String) -> some View {
        Text(title)
            .font(.caption2)
            .foregroundStyle(.secondary)
    }

    /// Shared loop-cycle strip: elapsed sweep of the current cycle, a flash
    /// on each cycle start (the shared downbeat), and a countdown to the
    /// next lock boundary while the song is rolling. Hidden when stopped.
    @ViewBuilder
    private var cycleStrip: some View {
        let length = launchpad.loopLengthSeconds
        if length > 0, session.transport.isPlaying {
            // Shares the panel's animTick driver (see playheadOverlay).
            let _ = animTick
            let t = session.transport.positionSeconds
            let phase = (t.truncatingRemainder(dividingBy: length)) / length
            let remaining = length - t.truncatingRemainder(dividingBy: length)
            HStack(spacing: 10) {
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.white.opacity(0.08))
                        Capsule()
                            .fill(JamTheme.accent.opacity(0.6))
                            .frame(width: max(4, geo.size.width * phase))
                        if phase < 0.06 {   // cycle-start flash
                            Capsule().fill(Color.white.opacity(0.35))
                        }
                    }
                }
                .frame(height: 5)
                if launchpad.loopLockEnabled {
                    Text(String(format: "next lock %.1fs", remaining))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                        .fixedSize()
                }
            }
            .frame(height: 12)
            .accessibilityLabel("Shared loop cycle position")
        }
    }

    private var controls: some View {
        HStack(spacing: 12) {
            // Global stop — silence every sounding pad/layer at once.
            Button {
                launchpad.stopAllPads()
            } label: {
                Image(systemName: "stop.circle.fill")
                    .font(.title3)
                    .foregroundStyle(launchpad.activePads.isEmpty ? Color.secondary : JamTheme.error)
            }
            .buttonStyle(.plain)
            .help("Stop all pads")
            .disabled(launchpad.activePads.isEmpty)

            // Grid ⇄ Layers view.
            Picker("", selection: $showLayers) {
                Image(systemName: "square.grid.3x3.fill").tag(false)
                Image(systemName: "slider.horizontal.3").tag(true)
            }
            .pickerStyle(.segmented)
            .frame(width: 84)

            // Tap = momentary (sounds only while held); Loop = latched seamless
            // loop (tap on, tap off).
            Picker("Play", selection: playbackModeBinding) {
                ForEach(LaunchpadController.PadPlaybackMode.allCases, id: \.self) {
                    Text($0.title).tag($0)
                }
            }
            .labelsHidden()   // the "Play" title was wrapping to "P l a y"
            .pickerStyle(.segmented)
            .fixedSize()

            // Loop lock: hold triggered loops until the shared cycle restarts
            // so every pad phase-locks to one 8 s grid and stacks coherently.
            // Text + icon (UX audit fix #3: icon-only was undecodable).
            Button {
                launchpad.loopLockEnabled.toggle()
            } label: {
                Label("Lock", systemImage: launchpad.loopLockEnabled ? "lock.fill" : "lock.open")
                    .font(.caption)
                    .foregroundStyle(launchpad.loopLockEnabled ? JamTheme.accent : Color.secondary)
            }
            .buttonStyle(.plain)
            .help(launchpad.loopLockEnabled
                  ? "Loop lock ON: loops start on the shared cycle (stack in sync)"
                  : "Loop lock OFF: loops start immediately (bar-quantized)")

            // Augment: triggering a sample ducks the song's own stem while it
            // plays, then restores it — the sample "takes over" that part.
            // Text + icon (the bare ⇄ read as "transpose", not "stem swap").
            Button {
                session.stemTakeoverEnabled.toggle()
            } label: {
                Label("Augment", systemImage: "arrow.left.arrow.right")
                    .font(.caption)
                    .foregroundStyle(session.stemTakeoverEnabled ? JamTheme.accent : Color.secondary)
            }
            .buttonStyle(.plain)
            .help(session.stemTakeoverEnabled
                  ? "Augment ON: samples replace the song's stem while playing (tap to layer instead)"
                  : "Augment OFF: samples layer over the song (tap to replace the stem)")

            // Each picker gets a small visible caption (UX audit fix #3),
            // paired tightly so caption+control read as one labeled unit.
            HStack(spacing: 5) {
                pickerCaption("Quantize")
                Picker("Quantize", selection: quantizeBinding) {
                    ForEach(QuantizeMode.allCases, id: \.self) {
                        Text($0.rawValue).tag($0)
                    }
                }
                .labelsHidden()
                .fixedSize()
            }
            .help("Quantize")

            HStack(spacing: 5) {
                pickerCaption("Stem")
                Picker("Stem", selection: $selectedStem) {
                    ForEach(stemRoles, id: \.self) {
                        Text($0.capitalized).tag($0)
                    }
                }
                .labelsHidden()
                .fixedSize()
            }
            .help("Source stem")

            HStack(spacing: 5) {
                pickerCaption("Slices")
                Picker("Slices", selection: $selectedSliceMode) {
                    ForEach(LaunchpadController.sliceModes, id: \.self) {
                        Text($0.capitalized).tag($0)
                    }
                }
                .labelsHidden()
                .fixedSize()
            }
            .help("Slice mode")

            Button("Load") {
                let stem = selectedStem
                let mode = selectedSliceMode
                let backend = model.backendBaseURL
                guard !stem.isEmpty, !mode.isEmpty else { return }
                Task {
                    await launchpad.loadChops(
                        stem: stem, sliceMode: mode, backend: backend)
                }
            }
            .disabled(
                launchpad.isFetching
                    || selectedStem.isEmpty || selectedSliceMode.isEmpty
                    || (selectedStem == launchpad.stem
                        && selectedSliceMode == launchpad.sliceMode)
            )

            if launchpad.isFetching {
                ProgressView().controlSize(.small)
            }

            Spacer()

            // Performance Intelligence: one tap loads the auto-built, seamlessly-
            // loopable kit for this song (GET /api/song/{id}/kit).
            Button {
                Task { await session.loadAutoKit() }
            } label: {
                Label("Auto Kit", systemImage: "wand.and.stars")
                    .font(.caption)
            }
            .disabled(session.autoKitLoading)
            .help("Auto Kit — load the auto-built Launchpad kit for this song")
            if session.autoKitLoading {
                ProgressView().controlSize(.small)
            }

            // Instant Groove: one tap fires the best loop in each category
            // (drums/bass/chords/lead/…), all bar-synced — jam immediately.
            Button {
                session.launchpad.instantGroove()
            } label: {
                Label("Groove", systemImage: "bolt.fill")
                    .font(.caption)
            }
            .buttonStyle(.borderedProminent)
            .disabled(session.launchpad.assignments.isEmpty)
            .help("Instant Groove — start the best loop of each category, locked to the grid")
        }
        .overlay(alignment: .bottomTrailing) {
            if let err = session.autoKitError {
                Text(err).font(.caption2).foregroundStyle(JamTheme.error)
            }
        }
    }

    private var quantizeBinding: Binding<QuantizeMode> {
        Binding(
            get: { launchpad.quantize },
            set: { launchpad.quantize = $0 }
        )
    }

    private var playbackModeBinding: Binding<LaunchpadController.PadPlaybackMode> {
        Binding(
            get: { launchpad.playbackMode },
            set: { launchpad.playbackMode = $0 }
        )
    }

    private var stemRoles: [String] {
        model.session?.bundle.stems.map(\.role) ?? []
    }

    // MARK: - Grid

    private var padGrid: some View {
        GeometryReader { geo in
            let spacing: CGFloat = 8
            let side = (min(geo.size.width, geo.size.height) - spacing * 7) / 8
            VStack(spacing: spacing) {
                ForEach(0..<8, id: \.self) { row in
                    HStack(spacing: spacing) {
                        ForEach(0..<8, id: \.self) { col in
                            let pad = LaunchpadPad(row: row, col: col)
                            let padIdx = row * 8 + col
                            PadCell(
                                pad: pad,
                                launchpad: launchpad,
                                animTick: animTick,
                                padAssignmentStore: session.padAssignmentStore,
                                transformHost: session.transformHost,
                                analysisId: model.session?.bundle.analysisId,
                                moveMode: moveMode,
                                isDragSource: dragSourcePad == padIdx,
                                onShowRadial: { state in
                                    radialMenuState = state
                                },
                                onEmptyTap: { idx in
                                    soundPickerTarget = idx
                                },
                                onDragStart: { dragSourcePad = padIdx },
                                onDrop: { sourcePadIdx in
                                    swapPads(sourcePadIdx, padIdx)
                                    dragSourcePad = nil
                                },
                                onDragEnd: { dragSourcePad = nil }
                            )
                            .frame(width: side, height: side)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func swapPads(_ source: Int, _ target: Int) {
        guard source != target else { return }
        let sourceRef = session.padAssignmentStore.slot(padIdx: source)
        let targetRef = session.padAssignmentStore.slot(padIdx: target)
        session.padAssignmentStore.assign(targetRef, padIdx: source)
        session.padAssignmentStore.assign(sourceRef, padIdx: target)
    }

    private var underpowerBanner: some View {
        Label(
            "Launchpad may be underpowered — use a powered hub or direct port.",
            systemImage: "bolt.trianglebadge.exclamationmark"
        )
        .font(.caption)
        .foregroundStyle(.orange)
    }
}

/// One on-screen pad: colored by its chop assignment, brightened
/// while sounding. Press/release maps to padDown/padUp via a
/// zero-distance drag (SwiftUI's touch-down primitive).
/// Right-click opens radial menu.
private struct PadCell: View {
    let pad: LaunchpadPad
    let launchpad: LaunchpadController
    /// Panel-level 30 Hz pulse — the playhead's re-render dependency.
    let animTick: Int
    let padAssignmentStore: PadAssignmentStore
    let transformHost: PadTransformHost
    let analysisId: String?
    let moveMode: Bool
    let isDragSource: Bool
    /// Open radial menu at this pad.
    let onShowRadial: (PadRadialMenuState) -> Void
    /// Called when empty pad is tapped (open sound picker).
    let onEmptyTap: (Int) -> Void
    let onDragStart: () -> Void
    let onDrop: (Int) -> Void
    let onDragEnd: () -> Void

    @State private var pressed = false
    @State private var hovered = false
    @State private var padFrame: CGRect = .zero
    @State private var isDropTarget = false

    private var padIdx: Int { pad.row * 8 + pad.col }
    private var slotRef: PadSlotReference? { padAssignmentStore.slot(padIdx: padIdx) }
    private var isSequencePad: Bool {
        if case .sequence = slotRef { return true }
        return false
    }
    private var isPackPad: Bool {
        if case .packPad = slotRef { return true }
        return false
    }
    private var isLocalSample: Bool {
        if case .localSample = slotRef { return true }
        return false
    }
    private var sequencePulse: SequencePulse? { launchpad.sequencePulses[padIdx] }
    /// Pad has something assigned (chop, sequence, or pack pad).
    private var hasContent: Bool {
        launchpad.assignments[pad] != nil || slotRef != nil
    }

    var body: some View {
        padContent
            .opacity(isDragSource ? 0.4 : 1.0)
            .contentShape(Rectangle())
            .draggable(String(padIdx)) { dragPreview(assignment: launchpad.assignments[pad]) }
            .dropDestination(for: String.self, action: handleDrop, isTargeted: handleDropTarget)
            .onChange(of: moveMode) { _, newValue in
                if !newValue { isDropTarget = false }
            }
            .overlay(
                PadClickOverlay(
                    moveMode: moveMode,
                    onPrimaryDown: handlePrimaryDown,
                    onPrimaryUp: handlePrimaryUp,
                    onSecondaryClick: handleRightClick
                )
            )
            .background(frameTracker)
            .onHover { hovered = $0 }
    }

    /// Mouse-down on a pad: filled pads sound for the hold (Tap = momentary,
    /// stopped on release by handlePrimaryUp; Loop toggles on/off). Empty
    /// pads open the create radial.
    private func handlePrimaryDown() {
        guard !moveMode else { return }
        if hasContent {
            launchpad.padDown(pad)
        } else {
            showRadialMenu()
        }
    }

    /// Mouse-up: release the pad so Tap stops on finger-lift (Loop stays
    /// latched — padUp is a no-op for it in the controller).
    private func handlePrimaryUp() {
        guard !moveMode, hasContent else { return }
        launchpad.padUp(pad)
    }

    private var padContent: some View {
        let assignment = launchpad.assignments[pad]
        let active = launchpad.activePads.contains(pad) || sequencePulse != nil
        let borderColor = borderColor(active: active)
        let borderWidth: CGFloat = isDropTarget ? 3 : active ? 2 : 1

        return RoundedRectangle(cornerRadius: 6)
            .fill(fillColor(assignment: assignment, active: active))
            .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(borderColor, lineWidth: borderWidth))
            .shadow(color: hovered ? glowColor(assignment: assignment).opacity(0.5) : .clear, radius: 8)
            .overlay(alignment: .bottomLeading) { labelOverlay(assignment: assignment) }
            .overlay { sequenceOverlay }
            .overlay { moveModeOverlay }
            .overlay { playheadOverlay }
            // Right-click affordance (UX audit fix #3): the radial menu was
            // invisible. A ⋯ on hover says "this pad has more".
            .overlay(alignment: .topTrailing) {
                if hovered, hasContent, !moveMode {
                    Image(systemName: "ellipsis.circle.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(.white.opacity(0.55))
                        .padding(4)
                        .help("Right-click for pad actions (chop, effects, loop, sequence)")
                }
            }
    }

    /// Loop playhead: a clear position indicator for where the loop is at —
    /// a dimmed "elapsed" fill sweeping left→right across the whole pad, a
    /// bright vertical playhead line at the current position, and a bottom
    /// progress bar. Snaps back to 0 each loop. Only while the pad loops.
    @ViewBuilder private var playheadOverlay: some View {
        if launchpad.activePads.contains(pad), launchpad.loopProgress(pad) != nil {
            // Driven by the panel's shared animTick (a per-cell
            // TimelineView(.animation) froze on macOS release builds:
            // playheads only jumped when hover re-rendered the cell).
            GeometryReader { geo in
                let _ = animTick   // re-render dependency, 30 Hz
                let p = CGFloat(launchpad.loopProgress(pad) ?? 0)
                let w = geo.size.width
                ZStack(alignment: .leading) {
                    // Elapsed sweep across the whole tile.
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.white.opacity(0.18))
                        .frame(width: max(0, w * p))
                    // Playhead line at the current position.
                    Rectangle()
                        .fill(Color.white.opacity(0.95))
                        .frame(width: 2)
                        .offset(x: max(0, w * p - 1))
                    // Bottom progress bar.
                    VStack {
                        Spacer()
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color.white.opacity(0.22)).frame(height: 4)
                            RoundedRectangle(cornerRadius: 2)
                                .fill(Color.white.opacity(0.95))
                                .frame(width: max(0, w * p), height: 4)
                        }
                    }
                }
            }
            .allowsHitTesting(false)
        }
    }

    private func borderColor(active: Bool) -> Color {
        if isDropTarget { return JamTheme.accent }
        if active { return Color.white.opacity(0.9) }
        if hovered { return Color.white.opacity(0.25) }
        return Color.white.opacity(0.08)
    }

    @ViewBuilder
    private func labelOverlay(assignment: PadAssignment?) -> some View {
        if let label = padLabel(assignment) {
            Text(label)
                .font(.system(size: 11, weight: .medium))
                .foregroundStyle(.white.opacity(0.85))
                .padding(4)
                .lineLimit(1)
        }
    }

    @ViewBuilder
    private var sequenceOverlay: some View {
        if let pulse = sequencePulse {
            sequencePulseOverlay(pulse: pulse)
        } else if isSequencePad {
            Image(systemName: "waveform")
                .font(.body)
                .foregroundStyle(.white.opacity(0.6))
        } else if isPackPad {
            Image(systemName: "speaker.wave.2.fill")
                .font(.body)
                .foregroundStyle(.white.opacity(0.6))
        } else if isLocalSample {
            Image(systemName: "mic.fill")
                .font(.body)
                .foregroundStyle(.white.opacity(0.6))
        }
    }

    @ViewBuilder
    private var moveModeOverlay: some View {
        if moveMode {
            RoundedRectangle(cornerRadius: 6)
                .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
                .foregroundStyle(JamTheme.accent.opacity(0.6))
            if hasContent {
                Image(systemName: "arrow.up.and.down.and.arrow.left.and.right")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.5))
            }
        }
    }

    private func handleDrop(_ items: [String], _ location: CGPoint) -> Bool {
        guard moveMode, let first = items.first, let sourcePadIdx = Int(first) else { return false }
        onDrop(sourcePadIdx)
        return true
    }

    private func handleDropTarget(_ targeted: Bool) {
        isDropTarget = moveMode && targeted
    }

    private func handleRightClick() {
        guard !moveMode else { return }
        showRadialMenu()
    }

    private var frameTracker: some View {
        GeometryReader { geo in
            Color.clear
                .onAppear { padFrame = geo.frame(in: .global) }
                .onChange(of: geo.frame(in: .global)) { _, f in padFrame = f }
        }
    }

    @ViewBuilder
    private func dragPreview(assignment: PadAssignment?) -> some View {
        let color = fillColor(assignment: assignment, active: false)
        let label = padLabel(assignment)
        RoundedRectangle(cornerRadius: 6)
            .fill(color)
            .frame(width: 60, height: 60)
            .overlay {
                if let label {
                    Text(label)
                        .font(.caption)
                        .foregroundStyle(.white)
                }
            }
    }

    private var playGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { _ in
                guard !pressed else { return }
                pressed = true
                if !hasContent {
                    onEmptyTap(padIdx)
                } else {
                    launchpad.padDown(pad)
                }
            }
            .onEnded { _ in
                pressed = false
                if hasContent {
                    launchpad.padUp(pad)
                }
            }
    }

    private func showRadialMenu() {
        let center = CGPoint(x: padFrame.midX, y: padFrame.midY)
        let assignment = launchpad.assignments[pad]
        let hasLoop = analysisId.map {
            transformHost.loops(packId: $0, padIdx: assignment?.chop.idx ?? 0)
        } ?? false
        onShowRadial(PadRadialMenuState(
            gridRow: pad.row,
            gridCol: pad.col,
            center: center,
            hasAssignment: assignment != nil,
            isSequencePad: isSequencePad,
            isPackPad: isPackPad,
            hasLoop: hasLoop
        ))
    }

    @ViewBuilder
    private func sequencePulseOverlay(pulse: SequencePulse) -> some View {
        // Step progress arc
        Circle()
            .trim(from: 0, to: pulse.progress)
            .stroke(Color.white.opacity(0.7), lineWidth: 2)
            .rotationEffect(.degrees(-90))
            .padding(4)
        // Downbeat flash
        if pulse.isDownbeat {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color.white.opacity(0.3))
                .animation(.easeOut(duration: pulse.secondsPerStep), value: pulse.step)
        }
    }

    private func fillColor(assignment: PadAssignment?, active: Bool) -> Color {
        // Sequence pads get purple color
        if isSequencePad {
            let base = Color(red: 0.6, green: 0.2, blue: 0.8)  // Purple
            return active ? base : base.opacity(0.55)
        }
        // Pack pads get teal color
        if isPackPad {
            let base = Color(hex: 0xA855F7)  // Purple like iOS
            return active ? base : base.opacity(0.55)
        }
        // Local (vocoder/mic) samples: vocoded purple.
        if isLocalSample {
            let base = Color(hex: 0x9B4DFF)
            return active ? base : base.opacity(0.55)
        }

        guard let assignment else {
            return Color.white.opacity(0.06)
        }
        // Color by musical CATEGORY (grouped rack) when the pad carries Riley
        // metadata; fall back to the raw color hint for legacy chops.
        let hint: Int
        if assignment.chop.contentType != nil {
            hint = LaunchpadController.category(
                stem: assignment.stem, contentType: assignment.chop.contentType).colorHex
        } else {
            hint = Int(launchpad.colorHint(for: assignment))
        }
        let base = Color(
            red: Double((hint >> 16) & 0xFF) / 255.0,
            green: Double((hint >> 8) & 0xFF) / 255.0,
            blue: Double(hint & 0xFF) / 255.0
        )
        return active ? base : base.opacity(0.55)
    }

    private func glowColor(assignment: PadAssignment?) -> Color {
        if isSequencePad {
            return Color(red: 0.6, green: 0.2, blue: 0.8)
        }
        if isPackPad {
            return Color(hex: 0xA855F7)
        }
        if isLocalSample {
            return Color(hex: 0x9B4DFF)
        }
        guard let assignment else {
            return Color.white.opacity(0.3)
        }
        let hint = launchpad.colorHint(for: assignment)
        return Color(
            red: Double((hint >> 16) & 0xFF) / 255.0,
            green: Double((hint >> 8) & 0xFF) / 255.0,
            blue: Double(hint & 0xFF) / 255.0
        )
    }

    private func padLabel(_ assignment: PadAssignment?) -> String? {
        if isLocalSample { return "Voice" }
        guard let chop = assignment?.chop else { return nil }
        // CONTENT-first (mobile parity): sample tiles say what they sound
        // like, not their harmonic function — harmonic grids were painting
        // bare numerals ("iv", "VII") across the launchpad.
        if let s = chop.sectionLabel, !s.isEmpty { return s }
        if let k = chop.kind, !k.isEmpty, k != "chord" { return k.capitalized }
        return chop.chordSymbol
    }
}

// MARK: - Pattern Assignment Sheet

/// Sheet to select a saved sequencer pattern to assign to a pad.
private struct PatternAssignSheet: View {
    let padIdx: Int
    let patterns: [SequencerPattern]
    let onSelect: (UUID) -> Void
    let onClear: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Text("Assign Pattern to Pad")
                .font(.headline)

            if patterns.isEmpty {
                VStack(spacing: 8) {
                    Image(systemName: "waveform.badge.plus")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                    Text("No saved patterns")
                        .foregroundStyle(.secondary)
                    Text("Create patterns in the Sequencer panel and save them.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
                .frame(maxHeight: .infinity)
            } else {
                List(patterns) { pattern in
                    Button {
                        onSelect(pattern.id)
                    } label: {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(pattern.name)
                                    .font(.body)
                                HStack(spacing: 8) {
                                    Text("\(pattern.stepCount.rawValue) steps")
                                    Text("\(pattern.tracks.count) tracks")
                                }
                                .font(.caption)
                                .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .foregroundStyle(.tertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                .listStyle(.plain)
            }

            HStack {
                Button("Cancel") { onCancel() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Clear", role: .destructive) { onClear() }
            }
        }
        .padding()
        .frame(width: 320, height: 400)
    }
}

// MARK: - Sound Picker Sheet

/// Sheet for adding sounds from sample packs to pads.
private struct SoundPickerSheet: View {
    let padIdx: Int
    let packs: PacksModel
    let backendURL: URL
    let onAssign: (String, Int) -> Void  // packId, sourcePadIdx
    let onCancel: () -> Void

    @State private var selectedPackId: String?

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
        }
        .frame(width: 500, height: 450)
        .background(JamTheme.background)
        .task {
            await packs.loadCatalog(baseURL: backendURL)
        }
    }

    private var header: some View {
        HStack {
            if selectedPackId != nil {
                Button { selectedPackId = nil } label: {
                    Image(systemName: "chevron.left")
                }
                .buttonStyle(.plain)
            }
            Text(selectedPackId != nil ? "Select Pad" : "Add Sound")
                .font(.headline)
            Spacer()
            Button { onCancel() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }

    @ViewBuilder
    private var content: some View {
        if let packId = selectedPackId {
            packPadGrid(packId: packId)
        } else {
            packList
        }
    }

    private var packList: some View {
        List(packs.entries) { entry in
            Button {
                if packs.isCached(entry.packId) {
                    selectedPackId = entry.packId
                    packs.activate(packId: entry.packId)
                }
            } label: {
                HStack {
                    RoundedRectangle(cornerRadius: 4)
                        .fill(packColor(entry.family))
                        .frame(width: 8, height: 28)
                    VStack(alignment: .leading) {
                        Text(entry.name)
                            .font(.callout.weight(.medium))
                        Text("\(entry.padCount) pads")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    if !packs.isCached(entry.packId) {
                        if packs.downloading[entry.packId] != nil {
                            ProgressView().controlSize(.small)
                        } else {
                            Button {
                                packs.download(baseURL: backendURL, packId: entry.packId)
                            } label: {
                                Image(systemName: "arrow.down.circle")
                            }
                            .buttonStyle(.plain)
                        }
                    } else {
                        Image(systemName: "chevron.right")
                            .foregroundStyle(.tertiary)
                    }
                }
            }
            .buttonStyle(.plain)
            .listRowBackground(Color.clear)
        }
        .scrollContentBackground(.hidden)
    }

    // Grid indices for 4x4 pack pad display (row 3 at top, row 0 at bottom)
    private static let packPadIndices = [12, 13, 14, 15, 8, 9, 10, 11, 4, 5, 6, 7, 0, 1, 2, 3]

    private func packPadGrid(packId: String) -> some View {
        let resolved = packs.activePack
        return VStack(spacing: 12) {
            if let resolved, resolved.pack.packId == packId {
                Text(resolved.pack.name)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                // 4x4 grid of pads (bottom-left = 0, matching iOS convention)
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                    ForEach(Self.packPadIndices, id: \.self) { idx in
                        packPadCell(
                            packId: packId,
                            padIdx: idx,
                            pad: resolved.pack.pads.first { $0.padIdx == idx },
                            playable: resolved.padFileURLs[idx] != nil
                        )
                    }
                }
                .padding()
            } else {
                ProgressView("Loading pack...")
            }
            Spacer()
        }
    }

    private func packPadCell(packId: String, padIdx: Int, pad: SamplePad?, playable: Bool) -> some View {
        Button {
            onAssign(packId, padIdx)
        } label: {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color(hex: pad?.colorHint) ?? sampleFamilyColor(pad?.family ?? .mixed))
                .opacity(playable ? 1 : 0.3)
                .frame(height: 60)
                .overlay {
                    Text(pad?.name ?? "Pad \(padIdx + 1)")
                        .font(.caption)
                        .foregroundStyle(.white)
                        .lineLimit(1)
                }
        }
        .buttonStyle(.plain)
        .disabled(!playable)
    }

    private func sampleFamilyColor(_ family: SampleFamily) -> Color {
        switch family {
        case .pads: return Color(hex: 0xA855F7)
        case .percussion: return Color(hex: 0xF97316)
        case .textures: return Color(hex: 0x14B8A6)
        case .stabs: return Color(hex: 0xEC4899)
        case .bass: return Color(hex: 0x3B82F6)
        case .fx: return Color(hex: 0xEAB308)
        case .vocals: return Color(hex: 0x22C55E)
        case .mixed: return Color(hex: 0x9CA3AF)
        }
    }

    private func packColor(_ family: SampleFamily) -> Color {
        sampleFamilyColor(family)
    }
}

// MARK: - Pad Click Overlay

/// NSViewRepresentable that captures both primary and secondary clicks.
/// SwiftUI TapGesture can be unreliable on macOS; NSView gives consistent behavior.
/// When moveMode is true, passes events through to allow drag gestures.
private struct PadClickOverlay: NSViewRepresentable {
    let moveMode: Bool
    /// Fired on mouse-DOWN (press). Paired with `onPrimaryUp` so a pad can
    /// gate audio for the hold duration (Tap = momentary).
    let onPrimaryDown: () -> Void
    /// Fired on mouse-UP (release), even if the cursor left the pad first.
    let onPrimaryUp: () -> Void
    let onSecondaryClick: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = PadClickView()
        view.moveMode = moveMode
        view.onPrimaryDown = onPrimaryDown
        view.onPrimaryUp = onPrimaryUp
        view.onSecondaryClick = onSecondaryClick
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        if let view = nsView as? PadClickView {
            view.moveMode = moveMode
            view.onPrimaryDown = onPrimaryDown
            view.onPrimaryUp = onPrimaryUp
            view.onSecondaryClick = onSecondaryClick
        }
    }
}

private class PadClickView: NSView {
    var moveMode = false
    var onPrimaryDown: (() -> Void)?
    var onPrimaryUp: (() -> Void)?
    var onSecondaryClick: (() -> Void)?

    override func hitTest(_ point: NSPoint) -> NSView? {
        // In move mode, let clicks pass through to SwiftUI for drag handling
        if moveMode { return nil }
        return super.hitTest(point)
    }

    override func mouseDown(with event: NSEvent) {
        onPrimaryDown?()
        // Cocoa delivers mouseUp to the view that got mouseDown even if the
        // cursor leaves the bounds first, so a release always pairs the press.
    }

    override func mouseUp(with event: NSEvent) {
        onPrimaryUp?()
    }

    override func rightMouseDown(with event: NSEvent) {
        onSecondaryClick?()
        // Don't call super - prevents system context menu
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        !moveMode
    }

    override var isFlipped: Bool { true }
}
