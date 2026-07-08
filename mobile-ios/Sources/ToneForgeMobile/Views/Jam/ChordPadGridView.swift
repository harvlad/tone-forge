// ChordPadGridView.swift
//
// The 4×4 diatonic chord grid, extracted from the standalone Chord
// Pads surface when it folded into the Jam tab as a pad-mode toggle
// (D-022 Phase 5, superseding D-019's separate surface). Decorative
// tiles over a PadTouchOverlay (multi-touch + slide migration),
// content from ChordPadGrid via the controller.
//
// All audio flows through ChordPadController → PadSynth (bus bypass
// per D-019); this view is pure paint. The Momentary/Latch switch
// and octave stepper live in JamView's chrome rows.

import SwiftUI
import ToneForgeEngine

struct ChordPadGridView: View {
    @ObservedObject var controller: ChordPadController

    var body: some View {
        ZStack {
            PadTouchOverlay(
                rows: 4,
                cols: 4,
                onPadDown: { row, col in
                    controller.padDown(index: Self.cellIndex(row: row, col: col))
                },
                onPadUp: { row, col in
                    controller.padUp(index: Self.cellIndex(row: row, col: col))
                },
                onLongPress: { _, _ in }
            )
            // Opaque backdrop so nothing behind the grid (the touch
            // view, snapshot placeholders) shows through tile gaps —
            // same trick as ModeGridView's Canvas fill.
            TFTheme.background
                .allowsHitTesting(false)
            tiles
                .allowsHitTesting(false)
        }
        // Flexible height (no square constraint) so the surface fits
        // any phone screen; see SamplePadGrid4x4 for the rationale.
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Overlay (row 1 = bottom) → display cell index (row-major from
    /// the top-left).
    static func cellIndex(row: Int, col: Int) -> Int {
        (4 - row) * 4 + (col - 1)
    }

    private var tiles: some View {
        let cells = controller.cells
        return VStack(spacing: 6) {
            ForEach(0..<4, id: \.self) { r in
                HStack(spacing: 6) {
                    ForEach(0..<4, id: \.self) { c in
                        let idx = r * 4 + c
                        if idx < cells.count {
                            tile(cells[idx])
                        } else {
                            Color.clear
                        }
                    }
                }
            }
        }
    }

    private func tile(_ cell: ChordPadCell) -> some View {
        let pressed = controller.heldCells.contains(cell.index)
        let latched = controller.latchedCells.contains(cell.index)
        return ZStack {
            RoundedRectangle(cornerRadius: 10)
                .fill(latched
                    ? AnyShapeStyle(TFTheme.chipActiveFill)
                    : AnyShapeStyle(TFTheme.chipFill))

            VStack(spacing: 2) {
                Text(cell.symbol)
                    .font(.body.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.6)
                HStack(spacing: 2) {
                    Text(cell.detail)
                    if cell.octaveShift > 0 {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 8, weight: .semibold))
                    }
                }
                .font(TFTheme.padLabel)
                .foregroundStyle(TFTheme.textSecondary)
            }
            .padding(4)

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
                        : (latched ? .white.opacity(0.85) : TFTheme.stroke),
                    lineWidth: pressed ? 2 : (latched ? 1.5 : 1)
                )
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("\(cell.symbol) chord pad")
    }
}
