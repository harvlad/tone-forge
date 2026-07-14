// SectionPracticeGrid.swift
//
// Sidebar list of deduped sections ("parts to learn"): label, chord
// count, recurrence badge; selected row highlighted. Shows checkmark
// for learned sections from the LearnSessionModel.

import SwiftUI
import JamDesktopCore

struct SectionPracticeGrid: View {
    let rehearsal: RehearsalModel
    let learn: LearnSessionModel
    let onSelect: (RehearsalSectionItem) -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 4) {
                ForEach(rehearsal.items) { item in
                    Button {
                        onSelect(item)
                    } label: {
                        row(item)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 8)
        }
    }

    private func row(_ item: RehearsalSectionItem) -> some View {
        let isSelected = rehearsal.selectedIndex == item.sectionIndex
        let isLearned = learn.isLearned(item.section)
        return HStack {
            if isLearned {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .font(.body)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(item.label)
                    .font(.body.weight(isSelected ? .semibold : .regular))
                Text(subtitle(item))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if item.recurrenceCount > 1 {
                Text("\(item.recurrenceCount)×")
                    .font(.caption2.monospacedDigit())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
            }
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(
            isSelected ? Color.accentColor.opacity(0.18) : Color.clear,
            in: RoundedRectangle(cornerRadius: 8)
        )
        .contentShape(Rectangle())
    }

    private func subtitle(_ item: RehearsalSectionItem) -> String {
        let chords = item.chords.count
        let seconds = Int((item.section.end - item.section.start).rounded())
        return "\(chords) chord\(chords == 1 ? "" : "s") · \(seconds)s"
    }
}
