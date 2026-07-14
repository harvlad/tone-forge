// ArrangementTimelineView.swift
//
// Studio Phase 3: arrangement from /api/detect-sections — energy
// curve over the file plus clickable section pills (guidance-mode
// colored, like the Debug timeline). Clicking a section runs
// /api/analyze-region; results render in RegionInspectorView below.

import SwiftUI
import JamDesktopCore

struct ArrangementTimelineView: View {
    let arrangement: ArrangementAnalysisDTO
    let selectedSectionID: Double?
    let onSelect: (ArrangementSection) -> Void

    private var duration: Double {
        arrangement.duration
            ?? arrangement.sections.map(\.endTime).max()
            ?? 1
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("Arrangement").font(.headline)
                if let tempo = arrangement.tempoBpm {
                    chip(String(format: "%.1f BPM", tempo))
                }
                if let key = arrangement.key {
                    chip(key)
                }
                chip("\(arrangement.sections.count) sections")
                Spacer()
                Text("Click a section to analyze the region")
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
            energyCurve
            sectionStrip
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func chip(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(JamTheme.surfaceElevated))
            .overlay(Capsule().strokeBorder(JamTheme.stroke))
    }

    // MARK: - energy curve

    @ViewBuilder
    private var energyCurve: some View {
        if let curve = arrangement.energyCurve, curve.count > 1 {
            Canvas { context, size in
                let peak = max(curve.max() ?? 1, 0.0001)
                var path = Path()
                for (i, value) in curve.enumerated() {
                    let x = size.width * CGFloat(i) / CGFloat(curve.count - 1)
                    let y = size.height
                        - size.height * CGFloat(min(1, value / peak))
                    if i == 0 {
                        path.move(to: CGPoint(x: x, y: y))
                    } else {
                        path.addLine(to: CGPoint(x: x, y: y))
                    }
                }
                context.stroke(
                    path, with: .color(JamTheme.accent.opacity(0.8)),
                    lineWidth: 1.5)
            }
            .frame(height: 44)
            .background(
                RoundedRectangle(cornerRadius: 4)
                    .fill(Color.black.opacity(0.25)))
        }
    }

    // MARK: - section pills

    private var sectionStrip: some View {
        GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                ForEach(arrangement.sections) { section in
                    sectionPill(section, totalWidth: geo.size.width)
                }
            }
        }
        .frame(height: 40)
    }

    private func sectionPill(
        _ section: ArrangementSection, totalWidth: CGFloat
    ) -> some View {
        let x = totalWidth * CGFloat(section.startTime / duration)
        let width = max(
            2, totalWidth * CGFloat(section.duration / duration) - 1)
        let selected = section.id == selectedSectionID
        return Button {
            onSelect(section)
        } label: {
            ZStack {
                RoundedRectangle(cornerRadius: 4)
                    .fill(JamTheme.guidanceColor(section.guidanceMode)
                        .opacity(selected ? 0.95 : 0.6))
                if width > 44 {
                    Text(section.type?.capitalized ?? "—")
                        .font(.system(size: 10))
                        .foregroundStyle(.white.opacity(0.95))
                        .lineLimit(1)
                        .padding(.horizontal, 2)
                }
            }
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .strokeBorder(
                        selected ? Color.white : Color.clear,
                        lineWidth: 1.5))
        }
        .buttonStyle(.plain)
        .frame(width: width, height: 40)
        .offset(x: x)
        .help(String(
            format: "%@  %.1fs – %.1fs",
            section.type ?? "section",
            section.startTime, section.endTime))
    }
}

// MARK: - region inspector

struct RegionInspectorView: View {
    let region: RegionAnalysisDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Text("Region analysis").font(.headline)
                if let type = region.sectionType {
                    Text(type.capitalized)
                        .font(.caption.bold())
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(Capsule().fill(
                            JamTheme.accent.opacity(0.25)))
                }
                if let bounds = region.bounds,
                   let start = bounds.start, let end = bounds.end {
                    Text(String(format: "%.1fs – %.1fs", start, end))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(JamTheme.textSecondary)
                }
                Spacer()
                if let count = region.noteCount ?? region.notes?.count {
                    Text("\(count) notes")
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(JamTheme.textSecondary)
                }
            }
            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 240), spacing: 12)],
                alignment: .leading, spacing: 12
            ) {
                if let confidence = region.confidence {
                    confidenceCard(confidence)
                }
                if let features = region.audioFeatures {
                    featuresCard(features)
                }
                if let provenance = region.provenance {
                    provenanceCard(provenance)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func confidenceCard(_ c: RegionConfidence) -> some View {
        card("Confidence") {
            metric("Overall", c.overall)
            metric("Notes", c.noteConfidence)
            metric("Timing", c.timingConfidence)
            metric("Pitch", c.pitchConfidence)
            metric("Velocity", c.velocityConfidence)
            if c.needsCleanup == true {
                Text("Needs cleanup"
                     + ((c.suggestedPasses?.isEmpty == false)
                        ? ": " + c.suggestedPasses!.joined(separator: ", ")
                        : ""))
                    .font(.caption)
                    .foregroundStyle(JamTheme.error)
            }
        }
    }

    private func featuresCard(_ f: RegionAudioFeatures) -> some View {
        card("Audio features") {
            metric("Energy mean", f.energyMean)
            metric("Energy peak", f.energyPeak)
            row("Spectral centroid", f.spectralCentroid.map {
                String(format: "%.0f Hz", $0)
            })
            row("Local tempo", f.tempoLocal.map {
                String(format: "%.1f BPM", $0)
            })
        }
    }

    private func provenanceCard(_ p: RegionProvenance) -> some View {
        card("Provenance") {
            if let contributions = p.detectorContributions,
               !contributions.isEmpty {
                ForEach(contributions.sorted(by: { $0.value > $1.value }),
                        id: \.key) { name, value in
                    metric(name.replacingOccurrences(of: "_", with: " "),
                           value)
                }
            }
            if let passes = p.cleanupPassesApplied, !passes.isEmpty {
                row("Cleanup passes", passes.joined(separator: ", "))
            }
            row("Corrections", p.correctionsMade.map(String.init))
            metric("FP risk", p.fpRisk)
            metric("FN risk", p.fnRisk)
        }
    }

    // MARK: - card helpers

    private func card(
        _ title: String, @ViewBuilder content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption.bold())
                .foregroundStyle(JamTheme.textSecondary)
            content()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 6)
                .fill(JamTheme.surfaceElevated))
    }

    @ViewBuilder
    private func metric(_ label: String, _ value: Double?) -> some View {
        if let value {
            HStack(spacing: 8) {
                Text(label)
                    .font(.caption)
                    .frame(width: 110, alignment: .leading)
                    .foregroundStyle(JamTheme.textSecondary)
                ProgressView(value: min(1, max(0, value)))
                    .controlSize(.small)
                Text(String(format: "%.0f%%", value * 100))
                    .font(.caption.monospacedDigit())
                    .frame(width: 40, alignment: .trailing)
            }
        }
    }

    @ViewBuilder
    private func row(_ label: String, _ value: String?) -> some View {
        if let value {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(label)
                    .font(.caption)
                    .frame(width: 110, alignment: .leading)
                    .foregroundStyle(JamTheme.textSecondary)
                Text(value).font(.caption)
            }
        }
    }
}
