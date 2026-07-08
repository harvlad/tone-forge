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

struct SamplePadGrid4x4: View {
    @ObservedObject var coordinator: ModeCoordinator
    @EnvironmentObject private var appState: AppState

    @State private var sheetTarget: PadSheetTarget?

    var body: some View {
        ZStack {
            PadTouchOverlay(
                rows: 4,
                cols: 4,
                onPadDown: { row, col in
                    let (gridRow, gridCol) = Self.gridIndex(row: row, col: col)
                    if isEmpty(gridRow: gridRow, gridCol: gridCol) {
                        // "+" tile: assign instead of trigger.
                        sheetTarget = coordinator.padSheetTarget(
                            row: gridRow, col: gridCol)
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
                    sheetTarget = coordinator.padSheetTarget(
                        row: gridRow, col: gridCol)
                }
            )
            tiles
                .allowsHitTesting(false)
        }
        .aspectRatio(1, contentMode: .fit)
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
            }
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
}
