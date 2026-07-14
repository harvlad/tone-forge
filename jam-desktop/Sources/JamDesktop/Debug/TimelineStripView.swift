// TimelineStripView.swift
//
// Section timeline (clickable) + chord strip, mirroring the debug.js
// SVG strips. Sections are plain SwiftUI shapes so hit-testing is
// free; the chord strip is a Canvas (many small pills, no clicks).

import SwiftUI
import JamDesktopCore

struct TimelineStripView: View {
    let bundle: DebugBundle
    let tagRows: [SectionTagDetector.SectionTagRow]
    @Binding var selectedIndex: Int?
    let matchesFilter: (Int) -> Bool

    private var duration: Double { max(bundle.duration, 0.001) }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            sectionStrip
                .frame(height: 56)
            chordStrip
                .frame(height: 32)
        }
        .padding(10)
        .jamCard()
    }

    // MARK: sections

    private var sectionStrip: some View {
        GeometryReader { geo in
            let w = geo.size.width
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.25))
                ForEach(Array(bundle.sections.enumerated()), id: \.offset) { i, sec in
                    sectionBlock(i, sec, totalWidth: w)
                }
            }
        }
    }

    private func sectionBlock(
        _ index: Int, _ sec: DebugSection, totalWidth: CGFloat
    ) -> some View {
        let start = CGFloat((sec.startS ?? 0) / duration) * totalWidth
        let end = CGFloat((sec.endS ?? 0) / duration) * totalWidth
        let width = max(2, end - start)
        let conf = sec.guidanceConfidence ?? 0.5
        let passes = matchesFilter(index)
        let opacity = passes ? 0.3 + 0.7 * conf : 0.08
        let selected = selectedIndex == index
        let tags = index < tagRows.count ? tagRows[index].tags : []

        return ZStack(alignment: .bottom) {
            Rectangle()
                .fill(JamTheme.guidanceColor(sec.guidanceMode).opacity(opacity))
            if width > 40, let label = sec.label {
                Text(label)
                    .font(.system(size: 10))
                    .foregroundStyle(.white.opacity(passes ? 0.9 : 0.3))
                    .lineLimit(1)
                    .frame(maxHeight: .infinity)
            }
            if passes && width > 12 && !tags.isEmpty {
                HStack(spacing: 3) {
                    ForEach(tags) { tag in
                        Circle()
                            .fill(JamTheme.tagColor(tag.id))
                            .frame(width: 7, height: 7)
                    }
                }
                .padding(.bottom, 3)
            }
        }
        .frame(width: width, height: 56)
        .overlay(
            Rectangle().strokeBorder(
                selected ? Color.white : Color.black.opacity(0.4),
                lineWidth: selected ? 1.5 : 0.5)
        )
        .offset(x: start)
        .contentShape(Rectangle())
        .onTapGesture { selectedIndex = index }
        .help(sectionTooltip(sec, tags: tags))
    }

    private func sectionTooltip(_ sec: DebugSection, tags: [SectionTag]) -> String {
        var parts: [String] = []
        if let label = sec.label { parts.append(label) }
        if let mode = sec.guidanceMode { parts.append(mode) }
        if let conf = sec.guidanceConfidence {
            parts.append(String(format: "conf %.3f", conf))
        }
        if !tags.isEmpty {
            parts.append(tags.map { $0.label }.joined(separator: ", "))
        }
        return parts.joined(separator: " · ")
    }

    // MARK: chords

    private var chordStrip: some View {
        Canvas { context, size in
            for chord in bundle.chords {
                guard let cs = chord.startS, let ce = chord.endS else { continue }
                let x = CGFloat(cs / duration) * size.width
                let w = max(1, CGFloat((ce - cs) / duration) * size.width - 1)
                let rect = CGRect(x: x, y: 2, width: w, height: size.height - 4)
                context.fill(
                    Path(roundedRect: rect, cornerRadius: 3),
                    with: .color(JamTheme.surfaceElevated))
                context.stroke(
                    Path(roundedRect: rect, cornerRadius: 3),
                    with: .color(.white.opacity(0.08)))
                if w > 28, let symbol = chord.symbol {
                    context.draw(
                        Text(symbol)
                            .font(.system(size: 10))
                            .foregroundStyle(.white.opacity(0.8)),
                        at: CGPoint(x: rect.midX, y: rect.midY))
                }
            }
        }
    }
}
