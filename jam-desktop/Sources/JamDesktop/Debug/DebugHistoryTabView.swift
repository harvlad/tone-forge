// DebugHistoryTabView.swift
//
// History tab: four histograms (tempo/key/section types/guidance
// modes) + the history table. Histogram bars are plain SwiftUI
// shapes — debug.js draws 280×80 SVGs, same idea. Row click jumps to
// the Inspector, like the web.

import SwiftUI
import JamDesktopCore

struct DebugHistoryTabView: View {
    let model: DebugHistoryModel
    let openInInspector: (String) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if model.isLoading {
                    ProgressView("Loading history…").controlSize(.small)
                }
                if let error = model.error {
                    Text(error).font(.callout).foregroundStyle(JamTheme.error)
                }
                if !model.rows.isEmpty {
                    histogramsRow
                    historyTable
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var histogramsRow: some View {
        let pending = model.isFetchingBundles
        return LazyVGrid(
            columns: [GridItem(.adaptive(minimum: 300), alignment: .topLeading)],
            alignment: .leading, spacing: 12
        ) {
            HistogramView(title: "Tempo (BPM)", bins: model.tempoBins, pending: false)
            HistogramView(title: "Detected key", bins: model.keyBins, pending: false)
            HistogramView(
                title: "Section types", bins: model.sectionTypeBins, pending: pending)
            HistogramView(
                title: "Guidance modes", bins: model.guidanceModeBins, pending: pending)
        }
    }

    private var historyTable: some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 7) {
            GridRow {
                Text("Name").font(.caption.bold())
                Text("Date").font(.caption.bold())
                Text("Tempo").font(.caption.bold())
                Text("Key").font(.caption.bold())
                Text("Type").font(.caption.bold())
            }
            .foregroundStyle(JamTheme.textSecondary)
            ForEach(model.rows) { row in
                GridRow {
                    Text(row.name ?? row.filename ?? "untitled")
                        .font(.callout)
                        .lineLimit(1)
                    Text(DebugFormat.timestamp(row.timestamp))
                        .font(.callout)
                        .foregroundStyle(JamTheme.textSecondary)
                    Text(row.tempoBpm.map { String(format: "%.1f", $0) } ?? "")
                        .font(.callout.monospacedDigit())
                    Text(row.detectedKey ?? "")
                        .font(.callout)
                    Text(row.detectedType ?? "")
                        .font(.callout)
                        .foregroundStyle(JamTheme.textSecondary)
                }
                .contentShape(Rectangle())
                .onTapGesture { openInInspector(row.id) }
            }
        }
        .padding(12)
        .jamCard()
    }
}

struct HistogramView: View {
    let title: String
    let bins: [HistogramBin]
    let pending: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(pending ? "\(title)  (loading…)" : title)
                .font(.caption.bold())
                .foregroundStyle(JamTheme.textSecondary)
            if bins.isEmpty {
                Text(pending ? "fetching bundles…" : "no data")
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
                    .frame(height: 80)
            } else {
                bars
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamTile()
    }

    private var bars: some View {
        let maxValue = max(bins.map { $0.value }.max() ?? 1, 1)
        return HStack(alignment: .bottom, spacing: 2) {
            ForEach(Array(bins.enumerated()), id: \.offset) { _, bin in
                VStack(spacing: 2) {
                    Spacer(minLength: 0)
                    RoundedRectangle(cornerRadius: 2)
                        .fill(JamTheme.accent.opacity(0.75))
                        .frame(height: max(
                            1, CGFloat(bin.value) / CGFloat(maxValue) * 62))
                    Text(bin.label)
                        .font(.system(size: 9))
                        .foregroundStyle(JamTheme.textSecondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity)
                .help("\(bin.label): \(bin.value)")
            }
        }
        .frame(height: 80)
    }
}
