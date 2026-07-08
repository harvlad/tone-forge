// JamPadGrid12.swift
//
// The Jam tab's 12 big performance pads (D-022 Phase 5): a 3×4
// window onto the 8×8 open-jam grid via JamPadGrid12Mapping, so
// every press flows through coordinator.touchPadDown/Up → the
// ContributionEventBus. Session capture, replay and Launchpad LED
// mirroring all keep working because to the rest of the system this
// IS the 8×8 grid — just twelve chosen cells, drawn big.
//
// Same ZStack recipe as ModeGridView: the UIKit touch overlay sits
// UNDER the tiles (which opt out of hit testing) so multi-touch
// lands on the UIView while the paint stays pure SwiftUI — and
// ImageRenderer snapshots show the real tiles.
//
// Hold: when enabled, touch pad-ups are swallowed so pads stay in
// coordinator.pressedPads (lit on screen AND on the Launchpad).
// Purely visual — jam-mode pad-up routes no audio (PadSynth voices
// auto-release), so there is nothing to un-voice. Held pads flush
// when Hold is switched off or the surface disappears.

import SwiftUI
import ToneForgeEngine

struct JamPadGrid12: View {
    @ObservedObject var coordinator: ModeCoordinator
    let key: MusicalKey?
    let holdEnabled: Bool

    /// Pads whose touch pad-up was swallowed by Hold.
    @State private var heldByHold: Set<PadIndex> = []

    private static let uiRows = 3
    private static let uiCols = 4

    var body: some View {
        let pads = JamPadGrid12Mapping.pads(key: key)
        ZStack {
            PadTouchOverlay(
                rows: Self.uiRows,
                cols: Self.uiCols,
                onPadDown: { row, col in
                    padDown(Self.pad(in: pads, row: row, col: col))
                },
                onPadUp: { row, col in
                    padUp(Self.pad(in: pads, row: row, col: col))
                },
                onLongPress: { _, _ in }
            )
            // Opaque backdrop so nothing behind the grid (the touch
            // view, snapshot placeholders) shows through tile gaps —
            // same trick as ModeGridView's Canvas fill.
            TFTheme.background
                .allowsHitTesting(false)
            tiles(pads: pads)
                .allowsHitTesting(false)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onChange(of: holdEnabled) { _, enabled in
            if !enabled { releaseHeld() }
        }
        .onDisappear { releaseHeld() }
    }

    /// Overlay (row 1 = bottom, col 1 = left) → grid pad. The pads
    /// array is ascending by pitch, laid out low-left-bottom to
    /// high-right-top like the 8×8 grid.
    static func pad(in pads: [PadIndex], row: Int, col: Int) -> PadIndex {
        pads[(row - 1) * uiCols + (col - 1)]
    }

    // MARK: - Touch → bus

    private func padDown(_ pad: PadIndex) {
        coordinator.touchPadDown(row: pad.row, col: pad.col)
    }

    private func padUp(_ pad: PadIndex) {
        if holdEnabled {
            heldByHold.insert(pad)
        } else {
            coordinator.touchPadUp(row: pad.row, col: pad.col)
        }
    }

    private func releaseHeld() {
        for pad in heldByHold {
            coordinator.touchPadUp(row: pad.row, col: pad.col)
        }
        heldByHold.removeAll()
    }

    // MARK: - Tiles

    private func tiles(pads: [PadIndex]) -> some View {
        VStack(spacing: 8) {
            // Display rows top→bottom; overlay row 3 (top) holds the
            // highest four tones.
            ForEach((0..<Self.uiRows).reversed(), id: \.self) { r in
                HStack(spacing: 8) {
                    ForEach(0..<Self.uiCols, id: \.self) { c in
                        tile(pads[r * Self.uiCols + c])
                    }
                }
            }
        }
    }

    private func tile(_ pad: PadIndex) -> some View {
        let visual = visual(for: pad)
        let pressed = coordinator.pressedPads.contains(pad.rawValue)
        let fill: Color = {
            guard let visual, visual.colorHint != 0 else {
                // Unlit pad (out-of-key with mode .off): dark cell,
                // same treatment as ModeGridView's empty slots.
                return Color(white: 0.16)
            }
            return Self.color(fromHex: visual.colorHint)
                .opacity(visual.isBright ? 0.85 : 0.35)
        }()

        return ZStack {
            RoundedRectangle(cornerRadius: 12)
                .fill(fill)

            VStack(spacing: 2) {
                Text(noteName(for: pad))
                    .font(.title3.weight(.bold))
                    .foregroundStyle(TFTheme.textPrimary)
                if let degree = visual?.label, !degree.isEmpty {
                    Text(degree)
                        .font(TFTheme.padLabel)
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }

            if pressed {
                RoundedRectangle(cornerRadius: 12)
                    .fill(.white.opacity(0.3))
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(
                    pressed ? Color.white : TFTheme.stroke,
                    lineWidth: pressed ? 2 : 1
                )
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("\(noteName(for: pad)) pad")
    }

    /// The 8×8 layout visual for a grid pad (color + degree label).
    private func visual(for pad: PadIndex) -> PadVisual? {
        let idx = (pad.row - 1) * 8 + (pad.col - 1)
        guard coordinator.padVisuals.indices.contains(idx) else {
            return nil
        }
        return coordinator.padVisuals[idx]
    }

    private func noteName(for pad: PadIndex) -> String {
        guard let pc = OpenJamGrid.pitchClass(for: pad) else { return "?" }
        return NoteNames.name(pitchClass: pc, key: key)
    }

    private static func color(fromHex hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}
