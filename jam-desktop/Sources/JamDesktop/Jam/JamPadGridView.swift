// JamPadGridView.swift
//
// The Jam in Key pad surface (iOS parity P5): 12 performance pads in
// a 3×4 grid, highest notes on top (display rows reversed — pad ids
// ascend by pitch from bottom-left, matching the iOS JamPadGrid12).
// Pads sound the wavetable synth via JamInKeyModel.onNoteOn/Off.
//
// A ~4 Hz timer pumps the chord ribbon's current chord into the
// model so the chord-tone glow follows the song.

import SwiftUI
import JamDesktopCore

struct JamPadGridView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var session: SessionController

    @State private var pumpTimer: Timer?

    private var jam: JamInKeyModel { session.jam }

    var body: some View {
        VStack(spacing: 12) {
            HStack {
                Text("Jam Pads")
                    .font(.title3.bold())
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.escape, modifiers: [])
            }
            KeyScalePickerView()
            padGrid
                .aspectRatio(3.0 / 4.0, contentMode: .fit)
        }
        .padding(16)
        .frame(minWidth: 380, minHeight: 520)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .onAppear { startChordPump() }
        .onDisappear {
            pumpTimer?.invalidate()
            pumpTimer = nil
            jam.releaseAll()
        }
    }

    // MARK: - Grid

    private var padGrid: some View {
        GeometryReader { geo in
            let spacing: CGFloat = 8
            let cols = 3, rows = 4
            let side = min(
                (geo.size.width - spacing * CGFloat(cols - 1)) / CGFloat(cols),
                (geo.size.height - spacing * CGFloat(rows - 1)) / CGFloat(rows)
            )
            let pads = jam.pads
            VStack(spacing: spacing) {
                // Highest pitches on the top row.
                ForEach((0..<rows).reversed(), id: \.self) { row in
                    HStack(spacing: spacing) {
                        ForEach(0..<cols, id: \.self) { col in
                            let index = row * cols + col
                            if pads.indices.contains(index) {
                                JamPadTile(
                                    info: pads[index],
                                    isPressed: jam.pressed[index] != nil,
                                    onDown: { jam.padDown(index) },
                                    onUp: { jam.padUp(index) }
                                )
                                .frame(width: side, height: side)
                            }
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    // MARK: - Chord pump

    private func startChordPump() {
        pumpChord()
        pumpTimer = Timer.scheduledTimer(
            withTimeInterval: 0.25, repeats: true
        ) { _ in
            Task { @MainActor in pumpChord() }
        }
    }

    private func pumpChord() {
        let t = session.engine.clock.nowSongSeconds
        jam.currentChordSymbol = session.ribbon?.currentChord(at: t)?.symbol
    }
}

/// One pad tile: note name + degree label over the layout's color
/// hint (iOS JamPadGrid12 recipe — 0.85 opacity bright, 0.35 dim,
/// near-black unlit).
private struct JamPadTile: View {
    let info: JamPadInfo
    let isPressed: Bool
    let onDown: () -> Void
    let onUp: () -> Void

    @State private var tracking = false

    var body: some View {
        RoundedRectangle(cornerRadius: 10)
            .fill(fill)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(
                        isPressed
                            ? Color.white.opacity(0.9)
                            : Color.white.opacity(0.08),
                        lineWidth: isPressed ? 2 : 1
                    )
            )
            .overlay {
                VStack(spacing: 2) {
                    Text(info.noteName)
                        .font(.title3.bold())
                    if let degree = info.degreeLabel {
                        Text(degree)
                            .font(.caption2)
                            .foregroundStyle(.white.opacity(0.7))
                    }
                }
                .foregroundStyle(.white)
            }
            .overlay {
                if isPressed {
                    RoundedRectangle(cornerRadius: 10)
                        .fill(Color.white.opacity(0.3))
                }
            }
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        guard !tracking else { return }
                        tracking = true
                        onDown()
                    }
                    .onEnded { _ in
                        tracking = false
                        onUp()
                    }
            )
    }

    private var fill: Color {
        guard info.colorHint != 0 else { return Color(white: 0.16) }
        let base = Color(
            red: Double((info.colorHint >> 16) & 0xFF) / 255.0,
            green: Double((info.colorHint >> 8) & 0xFF) / 255.0,
            blue: Double(info.colorHint & 0xFF) / 255.0
        )
        return base.opacity(info.isBright ? 0.85 : 0.35)
    }
}
