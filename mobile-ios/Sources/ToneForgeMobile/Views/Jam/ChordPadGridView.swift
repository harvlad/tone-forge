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
    /// Symbol of the chord currently playing in the song timeline.
    var currentChordSymbol: String?
    /// Symbol of the next upcoming chord in the song timeline.
    var nextChordSymbol: String?
    /// Whether follow mode is enabled (highlights current/next pads).
    var followEnabled: Bool = false

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
        let isCurrent = followEnabled && symbolMatches(cell.symbol, currentChordSymbol)
        let isNext = followEnabled && symbolMatches(cell.symbol, nextChordSymbol)

        return ChordPadTile(
            cell: cell,
            pressed: pressed,
            latched: latched,
            isCurrent: isCurrent,
            isNext: isNext
        )
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityLabel("\(cell.symbol) chord pad")
    }

    /// Chord symbol matching: normalizes symbols before comparing
    /// (e.g. "Dm" matches "Dm7", "D minor" matches "Dm").
    private func symbolMatches(_ padSymbol: String, _ timelineSymbol: String?) -> Bool {
        guard let timeline = timelineSymbol else { return false }
        // Normalize: extract root + quality
        let padNorm = normalizeChord(padSymbol)
        let timelineNorm = normalizeChord(timeline)
        return padNorm == timelineNorm
    }

    /// Normalize chord symbol to root + basic quality for matching.
    /// E.g. "Dm7" → "Dm", "D minor" → "Dm", "Cmaj7" → "C"
    private func normalizeChord(_ symbol: String) -> String {
        var s = symbol.trimmingCharacters(in: .whitespaces)
        // Handle "X minor" → "Xm"
        if s.lowercased().hasSuffix(" minor") {
            s = String(s.dropLast(6)) + "m"
        }
        // Handle "X major" → "X"
        if s.lowercased().hasSuffix(" major") {
            s = String(s.dropLast(6))
        }
        // Strip extensions (7, 9, 11, etc.) - keep root + m/dim/aug
        // Simple approach: take first 1-3 chars that define the chord
        let root = extractRoot(s)
        let quality = extractQuality(s, afterRoot: root.count)
        return root + quality
    }

    private func extractRoot(_ s: String) -> String {
        guard let first = s.first else { return "" }
        var root = String(first).uppercased()
        if s.count > 1 {
            let second = s[s.index(after: s.startIndex)]
            if second == "#" || second == "b" || second == "♯" || second == "♭" {
                root += String(second)
            }
        }
        return root
    }

    private func extractQuality(_ s: String, afterRoot: Int) -> String {
        guard afterRoot < s.count else { return "" }
        let rest = String(s.dropFirst(afterRoot)).lowercased()
        if rest.hasPrefix("m") && !rest.hasPrefix("maj") {
            return "m"
        }
        if rest.hasPrefix("dim") {
            return "dim"
        }
        if rest.hasPrefix("aug") {
            return "aug"
        }
        return ""
    }
}

// MARK: - Chord pad tile with follow highlighting

private struct ChordPadTile: View {
    let cell: ChordPadCell
    let pressed: Bool
    let latched: Bool
    let isCurrent: Bool
    let isNext: Bool

    @State private var pulsePhase: Bool = false

    var body: some View {
        ZStack {
            // Background
            RoundedRectangle(cornerRadius: 10)
                .fill(backgroundColor)

            // Content
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

            // Press overlay
            if pressed {
                RoundedRectangle(cornerRadius: 10)
                    .fill(.white.opacity(0.25))
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(borderColor, lineWidth: borderWidth)
        )
        .opacity(isNext ? (pulsePhase ? 1.0 : 0.7) : 1.0)
        .onAppear {
            if isNext {
                startPulse()
            }
        }
        .onChange(of: isNext) { _, newValue in
            if newValue {
                startPulse()
            }
        }
    }

    private var backgroundColor: AnyShapeStyle {
        if isCurrent {
            return AnyShapeStyle(Color.accentColor.opacity(0.4))
        } else if latched {
            return AnyShapeStyle(TFTheme.chipActiveFill)
        } else {
            return AnyShapeStyle(TFTheme.chipFill)
        }
    }

    private var borderColor: Color {
        if isCurrent {
            return .accentColor
        } else if pressed {
            return .white
        } else if latched {
            return .white.opacity(0.85)
        } else {
            return TFTheme.stroke
        }
    }

    private var borderWidth: CGFloat {
        if isCurrent {
            return 2.5
        } else if pressed {
            return 2
        } else if latched {
            return 1.5
        } else {
            return 1
        }
    }

    private func startPulse() {
        withAnimation(.easeInOut(duration: 0.5).repeatForever(autoreverses: true)) {
            pulsePhase = true
        }
    }
}
