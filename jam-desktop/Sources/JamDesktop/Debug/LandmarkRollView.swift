// LandmarkRollView.swift
//
// Canvas piano roll for a section's landmark notes, ported from
// debug.js renderLandmarkRoll: pitch range snapped to whole octaves
// (min one), black-key zebra rows, C gridlines/labels, velocity
// opacity, time labels at 0/0.5/1.

import SwiftUI
import JamDesktopCore

struct LandmarkRollView: View {
    let notes: [LandmarkNote]
    let startS: Double
    let endS: Double

    private static let padL: CGFloat = 40
    private static let padR: CGFloat = 10
    private static let padT: CGFloat = 10
    private static let padB: CGFloat = 22
    private static let noteNames = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ]
    private static let blackKeys: Set<Int> = [1, 3, 6, 8, 10]

    private static func pitchName(_ midi: Int) -> String {
        noteNames[((midi % 12) + 12) % 12] + String(midi / 12 - 1)
    }

    var body: some View {
        Canvas { context, size in
            draw(context: context, size: size)
        }
        .jamTile()
    }

    private func draw(context: GraphicsContext, size: CGSize) {
        let pitches = notes.compactMap { $0.pitch }.filter { $0.isFinite }
        guard let rawLo = pitches.min(), let rawHi = pitches.max() else { return }

        // Snap to whole octaves, at least one.
        var lo = Int(floor(rawLo / 12)) * 12
        var hi = Int(ceil((rawHi + 1) / 12)) * 12
        if hi - lo < 12 { hi = lo + 12 }
        // Guard degenerate equal bounds after snapping.
        if hi <= lo { hi = lo + 12 }
        lo = max(0, lo)

        let plotW = size.width - Self.padL - Self.padR
        let plotH = size.height - Self.padT - Self.padB
        guard plotW > 0, plotH > 0 else { return }
        let rowH = plotH / CGFloat(hi - lo)
        let dur = max(endS - startS, 0.001)

        func x(_ time: Double) -> CGFloat {
            Self.padL + CGFloat((time - startS) / dur) * plotW
        }
        func y(_ midi: Int) -> CGFloat {
            Self.padT + CGFloat(hi - midi) * rowH
        }

        // Zebra stripes on black-key rows (skip when rows too thin).
        if rowH >= 4 {
            for midi in lo..<hi where Self.blackKeys.contains(((midi % 12) + 12) % 12) {
                let rect = CGRect(
                    x: Self.padL, y: y(midi + 1), width: plotW, height: rowH)
                context.fill(Path(rect), with: .color(.white.opacity(0.03)))
            }
        }

        // C gridlines + labels.
        for midi in stride(from: lo, through: hi, by: 12) {
            let lineY = y(midi)
            var line = Path()
            line.move(to: CGPoint(x: Self.padL, y: lineY))
            line.addLine(to: CGPoint(x: size.width - Self.padR, y: lineY))
            context.stroke(line, with: .color(.white.opacity(0.10)))
            context.draw(
                Text(Self.pitchName(midi))
                    .font(.system(size: 9))
                    .foregroundStyle(.white.opacity(0.5)),
                at: CGPoint(x: Self.padL - 16, y: lineY))
        }

        // Notes.
        for note in notes {
            guard let pitch = note.pitch, pitch.isFinite,
                  let start = note.start, let end = note.end else { continue }
            let midi = Int(pitch.rounded())
            let noteX = x(start)
            let noteW = max(3, x(end) - noteX)
            let velocity = Double(note.velocity ?? 80)
            let opacity = max(0.55, min(1, velocity / 127))
            let rect = CGRect(
                x: noteX, y: y(midi + 1), width: noteW, height: max(rowH - 1, 1))
            context.fill(
                Path(roundedRect: rect, cornerRadius: 1.5),
                with: .color(JamTheme.accent.opacity(opacity)))
        }

        // Time labels at 0 / 0.5 / 1.
        for fraction in [0.0, 0.5, 1.0] {
            let time = startS + fraction * dur
            context.draw(
                Text(String(format: "%.1fs", time))
                    .font(.system(size: 9))
                    .foregroundStyle(.white.opacity(0.5)),
                at: CGPoint(
                    x: Self.padL + CGFloat(fraction) * plotW,
                    y: size.height - Self.padB / 2))
        }
    }
}
