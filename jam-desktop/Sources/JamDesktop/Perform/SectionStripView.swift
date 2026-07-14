// SectionStripView.swift
//
// Full-song section overview: proportional blocks (Verse / Chorus /
// …) with the current section highlighted; clicking a block seeks to
// its start — same interaction as the web jam section strip.

import SwiftUI
import ToneForgeEngine

struct SectionStripView: View {
    let sections: [SectionEvent]
    let durationSeconds: Double
    let positionSeconds: Double
    let onSeek: (Double) -> Void

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                ForEach(Array(sections.enumerated()), id: \.offset) { _, section in
                    block(for: section, width: geo.size.width)
                }
                playhead(width: geo.size.width)
            }
        }
        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 6))
    }

    private func block(for section: SectionEvent, width: CGFloat) -> some View {
        let x0 = xPosition(section.start, width: width)
        let x1 = xPosition(section.end, width: width)
        let isCurrent = positionSeconds >= section.start && positionSeconds < section.end

        return RoundedRectangle(cornerRadius: 4)
            .fill(isCurrent ? Color.accentColor.opacity(0.5) : Color.gray.opacity(0.2))
            .overlay {
                Text(section.label ?? "Section")
                    .font(.caption2.weight(isCurrent ? .bold : .regular))
                    .lineLimit(1)
                    .padding(.horizontal, 2)
            }
            .frame(width: max(2, x1 - x0 - 2))
            .frame(maxHeight: .infinity)
            .padding(.vertical, 4)
            .offset(x: x0 + 1)
            .contentShape(Rectangle())
            .onTapGesture { onSeek(section.start) }
    }

    private func playhead(width: CGFloat) -> some View {
        Rectangle()
            .fill(Color.red.opacity(0.8))
            .frame(width: 2)
            .frame(maxHeight: .infinity)
            .offset(x: xPosition(positionSeconds, width: width))
    }

    private func xPosition(_ t: Double, width: CGFloat) -> CGFloat {
        guard durationSeconds > 0 else { return 0 }
        return CGFloat(min(max(0, t / durationSeconds), 1)) * width
    }
}
