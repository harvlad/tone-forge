// CorpusTabView.swift
//
// Ground-truth corpus evaluation: aggregate stats, confusion matrix
// with heat coloring, per-song status table. Mirrors debug.js corpus
// tab; "View" jumps to the Inspector.

import SwiftUI
import JamDesktopCore

struct CorpusTabView: View {
    let model: DebugCorpusModel
    let openInInspector: (String) -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if model.isLoading {
                    ProgressView("Loading corpus…").controlSize(.small)
                }
                if let error = model.error {
                    Text(error).font(.callout).foregroundStyle(JamTheme.error)
                }
                if !model.rows.isEmpty {
                    statsRow
                    matrixGrid
                    songTable
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    // MARK: aggregate stats

    private var statsRow: some View {
        HStack(spacing: 20) {
            stat("Accuracy", model.matrix.total > 0
                ? "\(model.matrix.correct)/\(model.matrix.total) (\(Int((model.matrix.accuracy * 100).rounded()))%)"
                : "—")
            stat("Macro-F1", model.matrix.total > 0
                ? String(format: "%.3f", model.matrix.macroF1) : "—")
            stat("Songs analyzed", "\(model.analyzedCount)/\(model.rows.count)")
            Spacer()
        }
    }

    private func stat(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label).font(.caption).foregroundStyle(JamTheme.textSecondary)
            Text(value).font(.title3.monospacedDigit().bold())
        }
        .padding(10)
        .jamTile()
    }

    // MARK: confusion matrix

    private var matrixGrid: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Confusion matrix (rows = predicted, columns = actual)")
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
            Grid(horizontalSpacing: 4, verticalSpacing: 4) {
                GridRow {
                    Text("")
                    ForEach(ConfusionMatrix.modes, id: \.self) { actual in
                        Text(actual)
                            .font(.caption.bold())
                            .foregroundStyle(JamTheme.guidanceColor(actual))
                            .frame(width: 72)
                    }
                }
                ForEach(ConfusionMatrix.modes, id: \.self) { predicted in
                    GridRow {
                        Text(predicted)
                            .font(.caption.bold())
                            .foregroundStyle(JamTheme.guidanceColor(predicted))
                            .frame(width: 72, alignment: .trailing)
                        ForEach(ConfusionMatrix.modes, id: \.self) { actual in
                            matrixCell(predicted: predicted, actual: actual)
                        }
                    }
                }
            }
            .padding(10)
            .jamTile()
        }
    }

    private func matrixCell(predicted: String, actual: String) -> some View {
        let count = model.matrix.count(predicted: predicted, actual: actual)
        let heat = model.matrix.total > 0
            ? Double(count) / Double(model.matrix.total) : 0
        let diagonal = predicted == actual
        return Text("\(count)")
            .font(.callout.monospacedDigit())
            .frame(width: 72, height: 34)
            .background(
                RoundedRectangle(cornerRadius: 5)
                    .fill((diagonal ? Color.green : Color.red)
                        .opacity(count > 0 ? 0.15 + 0.5 * heat : 0.04))
            )
    }

    // MARK: per-song table

    private var songTable: some View {
        VStack(alignment: .leading, spacing: 0) {
            Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                GridRow {
                    Text("Title").font(.caption.bold())
                    Text("Artist").font(.caption.bold())
                    Text("Status").font(.caption.bold())
                    Text("GT sections").font(.caption.bold())
                    Text("")
                }
                .foregroundStyle(JamTheme.textSecondary)
                ForEach(model.rows) { row in
                    GridRow {
                        Text(row.song.title ?? row.song.slug ?? "—")
                            .font(.callout)
                        Text(row.song.artist ?? "—")
                            .font(.callout)
                            .foregroundStyle(JamTheme.textSecondary)
                        statusBadge(row)
                        Text("\(row.song.groundTruthSections?.count ?? 0)")
                            .font(.callout.monospacedDigit())
                        if let session = row.session {
                            Button("View") { openInInspector(session.id) }
                                .buttonStyle(.link)
                                .font(.caption)
                        } else {
                            Text("")
                        }
                    }
                }
            }
            .padding(12)
        }
        .jamCard()
    }

    private func statusBadge(_ row: DebugCorpusModel.Row) -> some View {
        let (text, color): (String, Color)
        if row.bundle != nil {
            (text, color) = row.fuzzy
                ? ("analyzed (fuzzy)", .yellow) : ("analyzed", .green)
        } else if row.session != nil {
            (text, color) = ("no features", .orange)
        } else {
            (text, color) = ("not analyzed", Color(white: 0.5))
        }
        return Text(text)
            .font(.caption)
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.12)))
    }
}
