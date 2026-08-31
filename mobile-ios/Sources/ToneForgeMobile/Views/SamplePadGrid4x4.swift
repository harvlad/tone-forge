// SamplePadGrid4x4.swift
//
// The redesigned Contribute sample surface (Phase 9): the active
// pack's 16 pads as large named tiles. This is a *view* of the same
// top-left quadrant the 8×8 grid binds (sampleQuadrantContent maps
// pack padIdx p → grid row 8 - p/4, col p%4 + 1), so audio, LEDs,
// recording, and the Launchpad mirror all keep working unchanged —
// presses go through the identical touchPadDown/Up bus path.
//
// Decorative SwiftUI tiles (hit-testing off) sit over a
// PadTouchOverlay(rows: 4) for multi-touch + slide migration.
// Empty tiles show "+" and open the pad source sheet on tap;
// long-press opens the effects editor / source sheet exactly like
// the 8×8 grid.

import SwiftUI
import ToneForgeEngine

/// Target for the ChopPickerSheet - identifies which pad to assign to.
private struct ChopPickerTarget: Identifiable {
    let id = UUID()
    let row: Int
    let col: Int
}

/// Target for the SequenceBuilderSheet - the pad whose radial menu
/// opened the builder (the recorded sequence auto-assigns here).
private struct SequenceBuilderTarget: Identifiable {
    let id = UUID()
    let row: Int
    let col: Int
}

struct SamplePadGrid4x4: View {
    @ObservedObject var coordinator: ModeCoordinator
    /// Callback to open pack browser for assigning sounds to empty pads.
    var onOpenBrowse: ((Int, Int) -> Void)? = nil
    /// Callback to open Beat Capture from the empty-pad create radial.
    var onBeatCapture: (() -> Void)? = nil
    /// STAGE mode (Perform): the same kit rendered as an instrument to
    /// play, not a bench to edit — empty slots vanish (no "+", no
    /// tap-to-add), the hold-radial is off, filled tiles run hotter and
    /// glow while ringing. Jam keeps the workbench (stage = false).
    var stage: Bool = false
    /// PLACE mode (Jam's Sounds browser): when the binding holds a
    /// picked chop, the next pad tap ASSIGNS it there (replacing the
    /// pad) instead of triggering, then clears. Nil hosts (Contribute,
    /// Perform) never enter place mode.
    var pendingChop: Binding<ChopReference?>? = nil
    @EnvironmentObject private var appState: AppState

    @State private var sheetTarget: PadSheetTarget?
    @State private var chopPickerTarget: ChopPickerTarget?
    @State private var sequenceBuilderTarget: SequenceBuilderTarget?
    @State private var radialMenuState: PadRadialMenuState?
    @State private var gridFrame: CGRect = .zero
    @State private var gridSize: CGSize = .zero
    @State private var radialDragPosition: CGPoint?

    var body: some View {
        ZStack {
            GeometryReader { geo in
                ZStack {
                    PadTouchOverlay(
                        rows: 4,
                        cols: 4,
                        onPadDown: { row, col in
                            let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                            // Place mode: a picked chop is waiting — this tap
                            // assigns it to the pad instead of triggering.
                            if let pending = pendingChop, let ref = pending.wrappedValue {
                                handleChopSelection(ref, target: (gridRow, gridCol))
                                pending.wrappedValue = nil
                                Haptics.selectionChanged()
                                return
                            }
                            // Empty pad: a plain tap now opens the add-sound
                            // picker (long-press still gives the full create
                            // radial). Was a no-op — "can't add sounds here".
                            // Stage mode: empty slots are inert (build in Jam).
                            if isEmpty(gridRow: gridRow, gridCol: gridCol) {
                                guard !stage else { return }
                                if let onOpenBrowse {
                                    onOpenBrowse(gridRow, gridCol)
                                } else {
                                    chopPickerTarget = ChopPickerTarget(row: gridRow, col: gridCol)
                                }
                                return
                            }
                            coordinator.touchPadDown(row: gridRow, col: gridCol)
                        },
                        onPadUp: { row, col in
                            let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                            coordinator.touchPadUp(row: gridRow, col: gridCol)
                        },
                        onLongPress: { row, col in
                            // Stage mode is play-only — no edit radial.
                            guard !stage else { return }
                            let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                            // Anchor the wheel on the pressed pad; clamp
                            // keeps the full wheel on-screen near edges so
                            // it never clips under the pads or controls.
                            // Empty pads get a single "Add Sound" action;
                            // assigned pads get the full editing wheel.
                            let center = padCenter(
                                localRow: row, localCol: col, size: geo.size
                            )
                            let empty = isEmpty(gridRow: gridRow, gridCol: gridCol)
                            radialMenuState = makeRadialMenuState(
                                gridRow: gridRow,
                                gridCol: gridCol,
                                center: center,
                                containerSize: geo.size,
                                actions: empty
                                    ? PadRadialAction.empty
                                    : PadRadialAction.assigned
                            )
                        },
                        onLongPressDrag: { point in
                            radialDragPosition = point
                        },
                        onLongPressEnd: { point in
                            // Determine action from final position
                            if let state = radialMenuState {
                                if let action = PadRadialMenu.action(
                                    at: point,
                                    center: state.center,
                                    actions: state.actions
                                ) {
                                    handleRadialAction(action, state: state)
                                }
                                radialMenuState = nil
                                radialDragPosition = nil
                            }
                        }
                    )
                    tiles
                        .allowsHitTesting(false)
                }
                .onAppear { gridFrame = geo.frame(in: .global) }
                .onChange(of: geo.size) { _, _ in gridFrame = geo.frame(in: .global) }
            }
        }
        // Flexible height (no square constraint): the grid absorbs
        // whatever the Play stack has left, so the tab always fits
        // on-screen — squarish on phones, shorter when space is tight.
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(item: $sheetTarget) { target in
            switch target {
            case .effects(let target):
                PadEffectsEditor(
                    packId: target.packId,
                    padIdx: target.padIdx,
                    padName: target.padName,
                    manifestBaseline: target.manifestBaseline,
                    gridRaw: target.gridRow * 10 + target.gridCol,
                    onPreview: preview(row: target.gridRow, col: target.gridCol)
                )
            case .source(let target):
                PadSourceSheet(
                    target: target,
                    onPreview: preview(row: target.gridRow, col: target.gridCol)
                )
            case .trimmer(let target):
                SampleTrimmerSheet(
                    target: target,
                    onPreview: previewTrimmed(target: target)
                )
            }
        }
        .overlay {
            if let state = radialMenuState {
                PadRadialMenu(
                    state: state,
                    onAction: { action in
                        handleRadialAction(action, state: state)
                        radialMenuState = nil
                        radialDragPosition = nil
                    },
                    onDismiss: {
                        radialMenuState = nil
                        radialDragPosition = nil
                    },
                    externalDragPosition: radialDragPosition
                )
            }
        }
        // NOTE: SwiftUI only presents ONE `.sheet` reliably per view node.
        // The chop-picker and sequence-builder sheets are attached to
        // separate background nodes so they don't shadow `sheetTarget`
        // (which powers effects/source/trimmer, e.g. the radial "Chop").
        .background {
            Color.clear
                .sheet(item: $chopPickerTarget) { target in
                    ChopPickerSheet(
                onSelect: { [target] ref, _ in
                    handleChopSelection(ref, target: (target.row, target.col))
                },
                bundleChops: bundleChopsForPicker,
                samplePacks: samplePacksForPicker,
                localSamples: [],
                sequences: sequencesForPicker,
                downloadablePacks: downloadablePacksForPicker,
                downloadingPackIds: downloadingPackIds,
                downloadFractions: downloadFractions,
                onDownloadPack: { packId in
                    guard let entry = appState.curatedCatalog.first(where: { $0.packId == packId })
                    else { return }
                    Task { await appState.downloadCuratedPack(entry) }
                },
                onPreview: { ref in
                    previewChopReference(ref)
                },
                onStopPreview: {
                    coordinator.stopPreviewPad()
                },
                previewDurationProvider: { packId, padIdx in
                    appState.previewPadDurationSec(packId: packId, padIdx: padIdx)
                }
            )
            .task { await appState.refreshCuratedCatalog() }
                }
        }
        .background {
            Color.clear
                .sheet(item: $sequenceBuilderTarget) { target in
                    SequenceBuilderSheet(gridRow: target.row, gridCol: target.col)
                        .environmentObject(appState)
                }
        }
    }

    // MARK: - Radial Menu

    /// Tile spacing matches VStack/HStack spacing: 6 in tiles view.
    private let tileSpacing: CGFloat = 6

    /// Calculate the center of a tile in local coordinates, accounting for spacing.
    private func padCenter(localRow: Int, localCol: Int, size: CGSize) -> CGPoint {
        // With spacing, total space for 4 tiles + 3 gaps
        let totalGapWidth = tileSpacing * 3
        let totalGapHeight = tileSpacing * 3
        let cellWidth = (size.width - totalGapWidth) / 4
        let cellHeight = (size.height - totalGapHeight) / 4

        // localCol is 1-based (1–4), localRow is 1-based (1=bottom, 4=top)
        // screenCol 0-based: 0=left
        // screenRow 0-based: 0=top
        let screenCol = localCol - 1  // 0–3
        let screenRow = 4 - localRow  // 0–3 (0=top)

        // x = leading edge of cell + half cell width
        let x = CGFloat(screenCol) * (cellWidth + tileSpacing) + cellWidth / 2
        // y = top edge of cell + half cell height
        let y = CGFloat(screenRow) * (cellHeight + tileSpacing) + cellHeight / 2

        return CGPoint(x: x, y: y)
    }

    private func makeRadialMenuState(
        gridRow: Int,
        gridCol: Int,
        center: CGPoint,
        containerSize: CGSize,
        actions: [PadRadialAction]
    ) -> PadRadialMenuState {
        // Real scheduler identity + effective loop state from the
        // coordinator's bindings (empty pads have neither).
        let binding = coordinator.padBinding(row: gridRow, col: gridCol)
        return PadRadialMenuState(
            gridRow: gridRow,
            gridCol: gridCol,
            center: Self.clampWheelCenter(center, in: containerSize),
            packId: binding?.packId,
            padIdx: binding?.padIdx ?? (gridRow * 10 + gridCol),
            hasLoop: coordinator.padLoops(row: gridRow, col: gridCol),
            actions: actions
        )
    }

    /// Nudge the wheel center inward so the full 300pt wheel stays
    /// on-screen near edge/corner pads. Falls back to the container
    /// midpoint when there isn't room for the wheel on an axis.
    static func clampWheelCenter(_ c: CGPoint, in size: CGSize) -> CGPoint {
        let r: CGFloat = 158  // outerRadius (150) + drag/label margin
        func clamp(_ v: CGFloat, _ extent: CGFloat) -> CGFloat {
            guard extent > 2 * r else { return extent / 2 }
            return min(max(v, r), extent - r)
        }
        return CGPoint(x: clamp(c.x, size.width), y: clamp(c.y, size.height))
    }

    private func handleRadialAction(_ action: PadRadialAction, state: PadRadialMenuState) {
        switch action {
        case .effects:
            // Open effects editor
            sheetTarget = coordinator.padSheetTarget(
                row: state.gridRow, col: state.gridCol)
        case .chop:
            // Open sample waveform trimmer
            if let trimmerTarget = coordinator.padTrimmerTarget(
                row: state.gridRow, col: state.gridCol
            ) {
                sheetTarget = .trimmer(trimmerTarget)
            }
        case .loop:
            // Flip the pad between looping and one-shot (scheduler
            // per-pad override; badge repaints via the coordinator).
            coordinator.togglePadLoop(row: state.gridRow, col: state.gridCol)
        case .reset:
            // Reset pad to default state (clear effects, trim, loop)
            coordinator.resetPadToDefault(
                row: state.gridRow, col: state.gridCol)
        case .delete:
            // Hide the pad from the grid
            coordinator.hidePackPad(row: state.gridRow, col: state.gridCol)
        case .sequence:
            // Open the 4x4 launchpad sequence builder; the recorded
            // pattern auto-assigns back to this pad.
            sequenceBuilderTarget = SequenceBuilderTarget(
                row: state.gridRow, col: state.gridCol)
        case .addSound:
            // Empty pad: open the pack browser to assign a sound.
            if let browse = onOpenBrowse {
                browse(state.gridRow, state.gridCol)
            } else {
                chopPickerTarget = ChopPickerTarget(
                    row: state.gridRow, col: state.gridCol)
            }
        case .voiceRecord:
            // Empty pad: open the voice-record interface directly.
            sheetTarget = .source(PadSourceTarget(
                gridRow: state.gridRow, gridCol: state.gridCol, sample: nil))
        case .beatCapture:
            // Empty pad: mic rhythm → drum pattern. Owned by the host
            // (ContributeSurface) so the captured pattern lands in the
            // sequencer.
            onBeatCapture?()
        }
    }

    // MARK: - Quadrant mapping

    /// Local 4×4 (row 1 = bottom) → 8×8 PadIndex coordinates of the
    /// sample quadrant (grid rows 5–8, cols 1–4).
    static func gridIndex(row: Int, col: Int) -> (row: Int, col: Int) {
        (row + 4, col)
    }

    private func visual(gridRow: Int, gridCol: Int) -> PadVisual {
        coordinator.padVisuals[(gridRow - 1) * 8 + (gridCol - 1)]
    }

    private func isEmpty(gridRow: Int, gridCol: Int) -> Bool {
        visual(gridRow: gridRow, gridCol: gridCol).colorHint == 0
    }

    // MARK: - Tiles

    private var tiles: some View {
        let ringing = coordinator.ringingGridPads(
            from: appState.ringingPadKeys)
        // Armed = queued for the next lock/quantize boundary. Surfacing this
        // is what makes a locked tap read as "waiting for the beat" instead
        // of "broken silence" (UX audit fix #1).
        let armed = coordinator.ringingGridPads(
            from: appState.sampleVoicePool.pendingPadKeys)
        // Screen top row = grid row 8 (pack padIdx 0–3).
        return VStack(spacing: 6) {
            ForEach([8, 7, 6, 5], id: \.self) { gridRow in
                HStack(spacing: 6) {
                    ForEach(1...4, id: \.self) { gridCol in
                        tile(
                            visual: visual(gridRow: gridRow, gridCol: gridCol),
                            pressed: coordinator.pressedPads.contains(
                                gridRow * 10 + gridCol),
                            ringing: ringing.contains(gridRow * 10 + gridCol),
                            armed: armed.contains(gridRow * 10 + gridCol),
                            pulse: coordinator.sequencePulses[gridRow * 10 + gridCol],
                            padKey: padKey(gridRow: gridRow, gridCol: gridCol)
                        )
                    }
                }
            }
        }
    }

    /// The active pack's SamplePadKey for a sample-quadrant grid cell
    /// (rows 5–8, cols 1–4). Pack padIdx = (8-row)*4 + (col-1) — the inverse
    /// of the quadrant mapping. Nil when no pack is active.
    private func padKey(gridRow: Int, gridCol: Int) -> SamplePadKey? {
        guard let packId = appState.activeSamplePack?.pack.packId else { return nil }
        let padIdx = (8 - gridRow) * 4 + (gridCol - 1)
        return SamplePadKey(packId: packId, padIdx: padIdx)
    }

    @ViewBuilder
    private func tile(
        visual: PadVisual,
        pressed: Bool,
        ringing: Bool,
        armed: Bool = false,
        pulse: SequencePulse? = nil,
        padKey: SamplePadKey? = nil
    ) -> some View {
        let tint = Self.color(fromHex: visual.colorHint)
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 10)
                // Raised from 0.30/0.16 — at the old opacity an idle filled
                // pad was nearly indistinguishable from an empty one (PM
                // eval: "filled reads as dead"). Stage runs hotter still —
                // Perform should LOOK lit, the staged version of Jam's bench.
                .fill(visual.colorHint == 0
                    ? AnyShapeStyle(TFTheme.chipFill.opacity(stage ? 0.25 : 1.0))
                    : AnyShapeStyle(tint.opacity(
                        stage
                            ? ((visual.isBright || ringing) ? 0.75 : 0.50)
                            : ((visual.isBright || ringing) ? 0.50 : 0.30))))

            if visual.colorHint == 0 {
                // Workbench: "+" invites adding. Stage: empty slots recede —
                // you don't edit on stage.
                if !stage {
                    Image(systemName: "plus")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(TFTheme.textSecondary)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    // A filled pad ALWAYS gets a name — unlabeled chops were
                    // rendering as anonymous dead squares.
                    Text({ () -> String in
                        if let l = visual.label, !l.isEmpty { return l }
                        if let padKey { return "Pad \(padKey.padIdx + 1)" }
                        return "Pad"
                    }())
                        // Stage: bigger type, readable at arm's length.
                        .font(stage ? .subheadline.weight(.bold)
                                    : .caption.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    // Level-bar accent from the mockup — the pad's
                    // family tint as a short underline.
                    RoundedRectangle(cornerRadius: 2)
                        .fill(tint.opacity(0.9))
                        .frame(width: 26, height: 3)
                }
                .padding(8)
            }

            if let badge = visual.badge {
                Image(systemName: Self.symbolName(badge))
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(TFTheme.textSecondary)
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .padding(6)
            }

            if let pulse {
                // Whole-tile flash on downbeats — a heartbeat locked to
                // the loop tempo. Animation is keyed on the step so it
                // re-fires each downbeat.
                RoundedRectangle(cornerRadius: 10)
                    .fill(.white.opacity(pulse.isDownbeat ? 0.18 : 0.0))
                    .animation(
                        .easeOut(duration: max(0.05, pulse.secondsPerStep)),
                        value: pulse.step)
                // Step meter along the bottom — one segment per step so
                // 8/16/32 loops read at a glance; the lit segment advances
                // in lock with the tempo.
                VStack {
                    Spacer(minLength: 0)
                    SequenceLoopMeter(pulse: pulse, tint: tint)
                }
                .padding(4)
            }

            if pressed {
                RoundedRectangle(cornerRadius: 10)
                    .fill(.white.opacity(0.25))
            }

            // Loop playhead across the tile — only on a FILLED pad that's
            // ringing a loop (never on an empty "+" cell whose padKey happens
            // to match a stale ringing voice from a previous kit).
            if ringing, visual.colorHint != 0, let padKey {
                loopPlayhead(padKey: padKey)
            }

            // Armed: queued for the next lock/quantize boundary. Hourglass +
            // orange ring says "waiting for the beat", not silence.
            if armed, visual.colorHint != 0 {
                Image(systemName: "hourglass")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Color.orange)
                    .frame(maxWidth: .infinity, maxHeight: .infinity,
                           alignment: .topTrailing)
                    .padding(6)
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(
                    pressed
                        ? Color.white
                        : (armed
                            ? Color.orange.opacity(0.9)
                            : (ringing
                                ? .white.opacity(0.85)
                                : (pulse != nil ? tint.opacity(0.9) : TFTheme.stroke))),
                    lineWidth: pressed ? 2 : (armed || ringing || pulse != nil ? 1.5 : 1)
                )
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Stage lighting: a ringing pad throws its color as a glow.
        .shadow(color: stage && ringing && visual.colorHint != 0
                    ? tint.opacity(0.55) : .clear,
                radius: 12)
        .accessibilityLabel(visual.label ?? "Empty pad")
    }

    /// Elapsed sweep + a bright playhead line showing where the loop is at.
    /// Polled at 30 Hz; extracted so the tile body type-checks quickly.
    @ViewBuilder
    private func loopPlayhead(padKey: SamplePadKey) -> some View {
        SwiftUI.TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { _ in
            GeometryReader { geo in
                let p = CGFloat(appState.sampleVoicePool.loopPhase(padKey: padKey) ?? 0)
                let w = geo.size.width
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color.white.opacity(0.16))
                        .frame(width: max(0, w * p))
                    Rectangle()
                        .fill(Color.white.opacity(0.95))
                        .frame(width: 2)
                        .offset(x: max(0, w * p - 1))
                }
            }
            .allowsHitTesting(false)
        }
    }

    private static func symbolName(_ badge: PadBadge) -> String {
        switch badge {
        case .mic:         return "mic.fill"
        case .vocoded:     return "waveform"
        case .transformed: return "wand.and.stars"
        case .loop:        return "repeat"
        case .edited:      return "pencil"
        }
    }

    private static func color(fromHex hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    /// Fire the pad through the bus (down + short hold + up) so sheet
    /// previews follow the same path as a real tap.
    private func preview(row: Int, col: Int) -> () -> Void {
        { [coordinator] in
            coordinator.touchPadDown(row: row, col: col)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                coordinator.touchPadUp(row: row, col: col)
            }
        }
    }

    /// Preview a trimmed portion of a sample. Used by the waveform trimmer.
    private func previewTrimmed(target: SampleTrimmerTarget) -> (Double, Double) -> Void {
        { [coordinator] startFraction, endFraction in
            coordinator.previewTrimmed(
                packId: target.packId,
                padIdx: target.padIdx,
                startFraction: startFraction,
                endFraction: endFraction
            )
        }
    }

    // MARK: - ChopPicker Data (shared with JamView's Sounds chip —
    // see ChopPickerData.swift)

    private var sequencesForPicker: [SequenceInfo] { appState.pickerSequences }
    private var bundleChopsForPicker: [String: [Chop]] { appState.pickerBundleChops }
    private var samplePacksForPicker: [SamplePackInfo] { appState.pickerSamplePacks }
    private var downloadablePacksForPicker: [DownloadablePackInfo] {
        appState.pickerDownloadablePacks
    }
    private var downloadingPackIds: Set<String> { appState.pickerDownloadingPackIds }
    private var downloadFractions: [String: Double] { appState.pickerDownloadFractions }

    /// Handle selection from the ChopPickerSheet.
    private func handleChopSelection(_ ref: ChopReference, target: (row: Int, col: Int)) {
        switch ref {
        case .packPad(let packId, let padIdx):
            coordinator.assignPadFromPack(
                targetRow: target.row,
                targetCol: target.col,
                sourcePackId: packId,
                sourcePadIdx: padIdx
            )
        case .sequence(let patternId):
            coordinator.assignSequence(
                targetRow: target.row,
                targetCol: target.col,
                patternId: patternId
            )
        case .bundleChop(let presetKey, let chopIndex, _):
            // A song chop IS a pack pad — the bundle's presets live as
            // song-DNA packs. Translate (presetKey, sorted index) →
            // (packId, padIdx) and pin it like any pack pad. This case
            // was a silent no-op — "can't add samples to the Jam grid".
            guard let dna = appState.songDnaPacks.first(where: { $0.presetKey == presetKey })
            else { return }
            let sorted = dna.pack.pack.pads.sorted { $0.padIdx < $1.padIdx }
            guard sorted.indices.contains(chopIndex) else { return }
            appState.preloadSongDnaPack(dna)   // buffers for the pinned pad
            coordinator.assignPadFromPack(
                targetRow: target.row,
                targetCol: target.col,
                sourcePackId: dna.pack.pack.packId,
                sourcePadIdx: sorted[chopIndex].padIdx
            )
        case .localSample(let id):
            // Recorded voice/mic take from the picker (was also dropped).
            let grid = PadIndex.at(row: target.row, col: target.col)
            guard grid.isValid else { return }
            coordinator.assignLocalSample(id: id, toGridPad: grid.rawValue)
        case .customURL, .synthChord:
            break   // not offered by this picker's sources
        }
    }

    /// Preview a chop reference.
    private func previewChopReference(_ ref: ChopReference) {
        appState.previewChopReference(ref)
    }
}

/// Bottom-edge step meter for a running sequence pad. One segment per
/// step (8/16/32) so the loop length reads at a glance; the current
/// segment lights white and advances in lock with the loop tempo,
/// trailing segments hold the pad tint.
private struct SequenceLoopMeter: View {
    let pulse: SequencePulse
    let tint: Color

    var body: some View {
        HStack(spacing: 1) {
            ForEach(0..<max(1, pulse.stepCount), id: \.self) { i in
                RoundedRectangle(cornerRadius: 0.5)
                    .fill(color(for: i))
            }
        }
        .frame(height: 3)
        .animation(.linear(duration: 0.06), value: pulse.step)
    }

    private func color(for i: Int) -> Color {
        if i == pulse.step { return .white }
        if i < pulse.step { return tint.opacity(0.55) }
        return .white.opacity(0.12)
    }
}
