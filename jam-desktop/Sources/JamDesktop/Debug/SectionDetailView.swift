// SectionDetailView.swift
//
// Selected-section deep dive: summary chips, per-stem feature table,
// radar chart, landmark piano roll. Mirrors debug.js renderDetail.

import SwiftUI
import JamDesktopCore

struct SectionDetailView: View {
    let section: DebugSection
    let tags: [SectionTag]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            summaryChips

            if let reason = section.guidanceReason, !reason.isEmpty {
                Text(reason)
                    .font(.callout)
                    .foregroundStyle(JamTheme.textSecondary)
            }

            if let features = section.debugFeatures, !features.isEmpty {
                HStack(alignment: .top, spacing: 16) {
                    featureTable(features)
                    RadarChartView(
                        features: features,
                        dominantStem: section.dominantStem)
                }
            } else {
                Text("No per-stem features — re-analyze to populate.")
                    .font(.callout)
                    .foregroundStyle(JamTheme.textSecondary)
            }

            if let landmarks = section.landmarkNotes, !landmarks.isEmpty {
                Text("Landmark notes")
                    .font(.headline)
                LandmarkRollView(
                    notes: landmarks,
                    startS: section.startS ?? 0,
                    endS: section.endS ?? 0)
                    .frame(height: 200)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    // MARK: summary

    private var summaryChips: some View {
        HStack(spacing: 8) {
            if let mode = section.guidanceMode {
                chipText(mode.uppercased(), color: JamTheme.guidanceColor(mode))
            }
            if let conf = section.guidanceConfidence {
                chipText(String(format: "conf %.3f", conf))
            }
            if let stem = section.dominantStem {
                chipText("stem: \(stem)")
            }
            if let label = section.label {
                chipText(label)
            }
            chipText(String(
                format: "%.1fs → %.1fs", section.startS ?? 0, section.endS ?? 0))
            if let bpm = section.bpm, bpm > 0 {
                chipText("\(Int(bpm.rounded())) bpm")
            }
            ForEach(tags) { tag in
                chipText(tag.label, color: JamTheme.tagColor(tag.id))
            }
            Spacer()
        }
    }

    private func chipText(_ text: String, color: Color? = nil) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(color ?? JamTheme.textPrimary)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill((color ?? JamTheme.surfaceElevated).opacity(
                color == nil ? 1 : 0.18)))
            .overlay(Capsule().strokeBorder(
                (color ?? JamTheme.stroke).opacity(color == nil ? 1 : 0.5)))
    }

    // MARK: per-stem table

    /// Columns shown per stem — section-level fields hidden, matching
    /// debug.js SECTION_LEVEL_FEATURES.
    private static let columns: [(String, (StemDebugFeatures) -> String)] = [
        ("mono", { DebugFormat.num($0.monophonicRatio) }),
        ("repetition", { DebugFormat.num($0.repetitionScore) }),
        ("rep period", { DebugFormat.num($0.repetitionPeriodBeats) }),
        ("polyphony", { DebugFormat.num($0.polyphonyScore) }),
        ("lead", { DebugFormat.num($0.leadActivityScore) }),
        ("pitch div", { DebugFormat.num($0.pitchClassDiversity) }),
        ("voiced", { DebugFormat.num($0.voicedFrameRatio) }),
        ("notes", { DebugFormat.num($0.noteCount) }),
    ]

    private func featureTable(_ features: [StemDebugFeatures]) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 5) {
            GridRow {
                Text("stem").font(.caption.bold())
                ForEach(Self.columns, id: \.0) { col in
                    Text(col.0).font(.caption.bold())
                }
            }
            .foregroundStyle(JamTheme.textSecondary)
            ForEach(Array(features.enumerated()), id: \.offset) { _, stem in
                let dominant = stem.stemName != nil
                    && stem.stemName == section.dominantStem
                GridRow {
                    Text(stem.stemName ?? "—")
                        .font(.caption.bold())
                        .foregroundStyle(
                            dominant ? JamTheme.accent : JamTheme.textPrimary)
                    ForEach(Self.columns, id: \.0) { col in
                        Text(col.1(stem))
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(
                                dominant ? JamTheme.textPrimary : JamTheme.textSecondary)
                    }
                }
            }
        }
        .padding(8)
        .jamTile()
    }
}
