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
                            if isEmpty(gridRow: gridRow, gridCol: gridCol) {
                                // "+" tile: open pack browser to assign sound
                                if let browse = onOpenBrowse {
                                    browse(gridRow, gridCol)
                                } else {
                                    chopPickerTarget = ChopPickerTarget(row: gridRow, col: gridCol)
                                }
                            } else {
                                coordinator.touchPadDown(row: gridRow, col: gridCol)
                            }
                        },
                        onPadUp: { row, col in
                            let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                            coordinator.touchPadUp(row: gridRow, col: gridCol)
                        },
                        onLongPress: { row, col in
                            let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                            // Show radial menu instead of sheet directly
                            if !isEmpty(gridRow: gridRow, gridCol: gridCol) {
                                let center = padCenter(
                                    localRow: row, localCol: col,
                                    size: geo.size
                                )
                                radialMenuState = makeRadialMenuState(
                                    gridRow: gridRow,
                                    gridCol: gridCol,
                                    center: center
                                )
                            } else {
                                // Empty pad: open pack browser
                                if let browse = onOpenBrowse {
                                    browse(gridRow, gridCol)
                                } else {
                                    chopPickerTarget = ChopPickerTarget(row: gridRow, col: gridCol)
                                }
                            }
                        },
                        onLongPressDrag: { point in
                            radialDragPosition = point
                        },
                        onLongPressEnd: { point in
                            // Determine action from final position
                            if let state = radialMenuState {
                                if let action = PadRadialMenu.action(at: point, center: state.center) {
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
        .sheet(item: $sequenceBuilderTarget) { target in
            SequenceBuilderSheet(gridRow: target.row, gridCol: target.col)
                .environmentObject(appState)
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
        center: CGPoint
    ) -> PadRadialMenuState {
        let padIdx = gridRow * 10 + gridCol
        // TODO: Get actual pack ID and loop state from coordinator
        return PadRadialMenuState(
            gridRow: gridRow,
            gridCol: gridCol,
            center: center,
            packId: nil,
            padIdx: padIdx,
            hasLoop: false
        )
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
            // TODO: Toggle loop transform on pad
            // Requires adding togglePadLoop to ModeCoordinator
            break
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
        // Screen top row = grid row 8 (pack padIdx 0–3).
        return VStack(spacing: 6) {
            ForEach([8, 7, 6, 5], id: \.self) { gridRow in
                HStack(spacing: 6) {
                    ForEach(1...4, id: \.self) { gridCol in
                        tile(
                            visual: visual(gridRow: gridRow, gridCol: gridCol),
                            pressed: coordinator.pressedPads.contains(
                                gridRow * 10 + gridCol),
                            ringing: ringing.contains(gridRow * 10 + gridCol)
                        )
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func tile(visual: PadVisual, pressed: Bool, ringing: Bool) -> some View {
        let tint = Self.color(fromHex: visual.colorHint)
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 10)
                .fill(visual.colorHint == 0
                    ? AnyShapeStyle(TFTheme.chipFill)
                    : AnyShapeStyle(tint.opacity(
                        (visual.isBright || ringing) ? 0.30 : 0.16)))

            if visual.colorHint == 0 {
                Image(systemName: "plus")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(TFTheme.textSecondary)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    Text(visual.label ?? "")
                        .font(.caption.weight(.semibold))
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

            if pressed {
                RoundedRectangle(cornerRadius: 10)
                    .fill(.white.opacity(0.25))
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(
                    pressed
                        ? Color.white
                        : (ringing ? .white.opacity(0.85) : TFTheme.stroke),
                    lineWidth: pressed ? 2 : (ringing ? 1.5 : 1)
                )
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel(visual.label ?? "Empty pad")
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

    // MARK: - ChopPicker Data

    /// Saved sequencer patterns available to assign to a pad.
    private var sequencesForPicker: [SequenceInfo] {
        appState.sequencerPatternStore.all().map { pattern in
            SequenceInfo(
                id: pattern.id,
                name: pattern.name,
                trackCount: pattern.tracks.count,
                stepCount: pattern.stepCount.rawValue
            )
        }
    }

    /// Bundle chops grouped by preset key from the current song.
    private var bundleChopsForPicker: [String: [Chop]] {
        guard let bundle = appState.currentBundle else { return [:] }
        var result: [String: [Chop]] = [:]

        for (key, preset) in bundle.presets {
            if !preset.chops.isEmpty {
                result[key] = preset.chops
            }
        }

        // Create section chops from timeline if no preset exists
        if result["sections"] == nil && !bundle.timeline.sections.isEmpty {
            result["sections"] = bundle.timeline.sections.enumerated().map { idx, section in
                Chop(
                    idx: idx,
                    startSec: section.start,
                    endSec: section.end,
                    durationSec: section.end - section.start,
                    kind: "section",
                    root: nil,
                    sectionLabel: section.label,
                    chordSymbol: nil,
                    colorHint: nil
                )
            }
        }

        return result
    }

    /// Sample packs available for the picker.
    private var samplePacksForPicker: [SamplePackInfo] {
        var packs: [SamplePackInfo] = []

        // Active sample pack
        if let active = appState.activeSamplePack {
            let pads = active.pack.pads.map { pad in
                SamplePadInfo(padIdx: pad.padIdx, name: pad.name, family: pad.family)
            }
            packs.append(SamplePackInfo(
                id: active.pack.packId,
                name: active.pack.packId.replacingOccurrences(of: "-", with: " ").capitalized,
                padCount: pads.count,
                pads: pads
            ))
        }

        // All carousel pages (other packs)
        for page in appState.carouselPages {
            if packs.contains(where: { $0.id == page.id }) { continue }
            if let resolved = appState.resolvedPack(for: page) {
                let pads = resolved.pack.pads.map { pad in
                    SamplePadInfo(padIdx: pad.padIdx, name: pad.name, family: pad.family)
                }
                packs.append(SamplePackInfo(
                    id: resolved.pack.packId,
                    name: page.displayName,
                    padCount: pads.count,
                    pads: pads
                ))
            }
        }

        return packs
    }

    /// Curated catalog packs not yet resolved into the picker (not
    /// downloaded) — surfaced as download rows so the user can pull
    /// them inline instead of leaving for the Library.
    private var downloadablePacksForPicker: [DownloadablePackInfo] {
        let present = Set(samplePacksForPicker.map { $0.id })
        return appState.curatedCatalog
            .filter { !present.contains($0.packId) }
            .map { entry in
                DownloadablePackInfo(
                    id: entry.packId,
                    name: entry.name,
                    family: entry.family,
                    padCount: entry.padCount
                )
            }
    }

    /// packIds with an in-flight (not-complete) curated download.
    private var downloadingPackIds: Set<String> {
        Set(appState.curatedDownloads.values
            .filter { !$0.isComplete }
            .map { $0.packId })
    }

    /// Handle selection from the ChopPickerSheet.
    private func handleChopSelection(_ ref: ChopReference, target: (row: Int, col: Int)) {
        print("[SamplePadGrid4x4] handleChopSelection called with ref: \(ref), target: \(target)")
        switch ref {
        case .packPad(let packId, let padIdx):
            print("[SamplePadGrid4x4] Assigning packPad - packId: \(packId), padIdx: \(padIdx)")
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
        case .bundleChop, .localSample, .customURL:
            print("[SamplePadGrid4x4] Unhandled ref type: \(ref)")
            break
        }
    }

    /// Preview a chop reference.
    private func previewChopReference(_ ref: ChopReference) {
        appState.previewChopReference(ref)
    }
}
