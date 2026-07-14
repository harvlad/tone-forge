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

    private var launchpad: LaunchpadController { session.launchpad }

    var body: some View {
        VStack(spacing: 12) {
            header
            controls
            padGrid
                .aspectRatio(1, contentMode: .fit)
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
        .frame(minWidth: 620, idealWidth: 720, minHeight: 700, idealHeight: 800)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
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

            Button { dismiss() } label: {
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

    private var controls: some View {
        HStack(spacing: 12) {
            Picker("Quantize", selection: quantizeBinding) {
                ForEach(QuantizeMode.allCases, id: \.self) {
                    Text($0.rawValue).tag($0)
                }
            }
            .frame(maxWidth: 160)

            Picker("Stem", selection: $selectedStem) {
                ForEach(stemRoles, id: \.self) {
                    Text($0.capitalized).tag($0)
                }
            }
            .frame(maxWidth: 140)

            Picker("Slices", selection: $selectedSliceMode) {
                ForEach(LaunchpadController.sliceModes, id: \.self) {
                    Text($0.capitalized).tag($0)
                }
            }
            .frame(maxWidth: 140)

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
        }
    }

    private var quantizeBinding: Binding<QuantizeMode> {
        Binding(
            get: { launchpad.quantize },
            set: { launchpad.quantize = $0 }
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
    private var sequencePulse: SequencePulse? { launchpad.sequencePulses[padIdx] }
    /// Pad has something assigned (chop, sequence, or pack pad).
    private var hasContent: Bool {
        launchpad.assignments[pad] != nil || slotRef != nil
    }

    var body: some View {
        padContent
            .opacity(isDragSource ? 0.4 : 1.0)
            .contentShape(Rectangle())
            .gesture(moveMode ? nil : playGesture)
            .simultaneousGesture(TapGesture().modifiers(.control).onEnded { handleRightClick() })
            .draggable(String(padIdx)) { dragPreview(assignment: launchpad.assignments[pad]) }
            .dropDestination(for: String.self, action: handleDrop, isTargeted: handleDropTarget)
            .onChange(of: moveMode) { _, newValue in
                if !newValue { isDropTarget = false }
            }
            .overlay(SecondaryClickOverlay { handleRightClick() })
            .background(frameTracker)
            .onHover { hovered = $0 }
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

        guard let assignment else {
            return Color.white.opacity(0.06)
        }
        let hint = launchpad.colorHint(for: assignment)
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
        guard let chop = assignment?.chop else { return nil }
        return chop.chordSymbol ?? chop.sectionLabel ?? chop.kind
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

// MARK: - Secondary Click Overlay

/// NSViewRepresentable that captures right-click before system context menu.
private struct SecondaryClickOverlay: NSViewRepresentable {
    let onSecondaryClick: () -> Void

    func makeNSView(context: Context) -> NSView {
        let view = SecondaryClickView()
        view.onSecondaryClick = onSecondaryClick
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        if let view = nsView as? SecondaryClickView {
            view.onSecondaryClick = onSecondaryClick
        }
    }
}

private class SecondaryClickView: NSView {
    var onSecondaryClick: (() -> Void)?

    override func rightMouseDown(with event: NSEvent) {
        onSecondaryClick?()
        // Don't call super - prevents system context menu
    }

    override func acceptsFirstMouse(for event: NSEvent?) -> Bool {
        true
    }

    override var isFlipped: Bool { true }
}
