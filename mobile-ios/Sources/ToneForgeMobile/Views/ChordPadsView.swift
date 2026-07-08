// ChordPadsView.swift
//
// The Chord Pads surface of the Play tab (redesign Phase 12).
// Composition per the mockup:
//
//   - CurrentChordStrip: the song's sounding chord + the next few
//     upcoming from timeline.chords (song loaded only)
//   - controls row: key label, [Momentary | Latch] switch, octave
//     stepper
//   - 4×4 chord grid — decorative tiles over a PadTouchOverlay
//     (multi-touch + slide migration), content from ChordPadGrid
//   - layer fader
//
// All audio flows through ChordPadController → PadSynth (bus bypass
// per D-019); this view is pure paint.

import SwiftUI
import ToneForgeEngine

struct ChordPadsView: View {
    @ObservedObject var controller: ChordPadController
    @ObservedObject var sampleSettings: SampleSettingsStore
    @EnvironmentObject private var appState: AppState

    var body: some View {
        if appState.currentBundle != nil {
            chordStrip
        }

        controlsRow

        chordGrid
            .padding(.horizontal, 12)

        LayerFader(dbValue: $sampleSettings.layerFaderDb)
    }

    // MARK: - Current chord strip

    /// Sounding chord + up to three upcoming ones from the timeline.
    private var chordStrip: some View {
        HStack(spacing: 8) {
            if let symbol = appState.currentChord?.symbol {
                Text(symbol)
                    .tfChip(active: true)
            } else {
                Text("—")
                    .tfChip(active: false)
            }
            ForEach(Array(upcomingSymbols.enumerated()), id: \.offset) { _, symbol in
                Image(systemName: "arrow.right")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text(symbol)
                    .tfChip(active: false)
            }
            Spacer()
        }
        .padding(.horizontal, 12)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(chordStripAccessibility)
    }

    private var upcomingSymbols: [String] {
        let now = appState.songSeconds
        let chords = appState.currentBundle?.timeline.chords ?? []
        return chords.filter { $0.start > now }.prefix(3).map { $0.symbol }
    }

    private var chordStripAccessibility: String {
        var parts: [String] = []
        if let symbol = appState.currentChord?.symbol {
            parts.append("Current chord \(symbol)")
        }
        if !upcomingSymbols.isEmpty {
            parts.append("then " + upcomingSymbols.joined(separator: ", "))
        }
        return parts.isEmpty ? "No chord" : parts.joined(separator: ", ")
    }

    // MARK: - Controls row

    private var controlsRow: some View {
        HStack(spacing: 8) {
            Text(controller.keyLabel)
                .font(TFTheme.chipFont)
                .foregroundStyle(TFTheme.textSecondary)

            Spacer()

            modeChip(title: "Momentary", mode: .momentary)
            modeChip(title: "Latch", mode: .latch)

            octaveStepper
        }
        .padding(.horizontal, 12)
    }

    private func modeChip(
        title: String, mode: ChordPadController.TriggerMode
    ) -> some View {
        Button {
            controller.triggerMode = mode
            if mode == .momentary {
                // Latched visuals make no sense in momentary mode.
                controller.clearLatches()
            }
        } label: {
            Text(title)
                .tfChip(active: controller.triggerMode == mode)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title) trigger mode")
    }

    private var octaveStepper: some View {
        HStack(spacing: 4) {
            Button {
                controller.setOctaveShift(controller.octaveShift - 1)
            } label: {
                Image(systemName: "minus.circle")
            }
            .accessibilityLabel("Octave down")
            Text("Oct \(controller.octaveShift, specifier: "%+d")")
                .font(TFTheme.readout)
                .foregroundStyle(TFTheme.textPrimary)
                .frame(width: 52)
            Button {
                controller.setOctaveShift(controller.octaveShift + 1)
            } label: {
                Image(systemName: "plus.circle")
            }
            .accessibilityLabel("Octave up")
        }
        .foregroundStyle(TFTheme.textSecondary)
    }

    // MARK: - Chord grid

    private var chordGrid: some View {
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
            tiles
                .allowsHitTesting(false)
        }
        .aspectRatio(1, contentMode: .fit)
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
