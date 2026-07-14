// InspectorTabView.swift
//
// Session picker + tag filter chips + timeline/chord strips + section
// detail. Mirrors the debug.js inspector layout.

import SwiftUI
import JamDesktopCore

struct InspectorTabView: View {
    @Bindable var model: DebugInspectorModel
    let baseURL: URL

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                pickerRow

                if let error = model.error {
                    Text(error)
                        .font(.callout)
                        .foregroundStyle(JamTheme.error)
                }

                if let bundle = model.currentBundle {
                    tagFilterChips
                    TimelineStripView(
                        bundle: bundle,
                        tagRows: model.tagRows,
                        selectedIndex: $model.selectedSectionIndex,
                        matchesFilter: { model.sectionMatchesFilter($0) })
                    if let index = model.selectedSectionIndex,
                       index < bundle.sections.count {
                        SectionDetailView(
                            section: bundle.sections[index],
                            tags: index < model.tagRows.count
                                ? model.tagRows[index].tags : [])
                    } else {
                        Text("Click a section to inspect it.")
                            .font(.callout)
                            .foregroundStyle(JamTheme.textSecondary)
                    }
                } else if model.isLoading {
                    ProgressView().controlSize(.small)
                } else {
                    Text("Pick a session to inspect.")
                        .font(.callout)
                        .foregroundStyle(JamTheme.textSecondary)
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var pickerRow: some View {
        HStack(spacing: 10) {
            Picker("Session", selection: Binding(
                get: { model.currentSessionId ?? "" },
                set: { id in
                    guard !id.isEmpty else { return }
                    Task { await model.loadBundle(baseURL: baseURL, id: id) }
                }
            )) {
                Text("— pick a session —").tag("")
                ForEach(model.sessions) { s in
                    Text(sessionLabel(s)).tag(s.id)
                }
            }
            .frame(maxWidth: 460)

            Button {
                Task { await model.loadSessions(baseURL: baseURL) }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .help("Reload session list")

            if model.isLoading {
                ProgressView().controlSize(.small)
            }
            Spacer()
        }
    }

    private func sessionLabel(_ s: DebugSessionSummary) -> String {
        var label = s.name
        let ts = DebugFormat.timestamp(s.timestamp)
        if !ts.isEmpty { label += " · \(ts)" }
        if s.hasDebugFeatures != true { label += " [legacy]" }
        return label
    }

    private var tagFilterChips: some View {
        HStack(spacing: 8) {
            chip(nil, label: "All", count: nil)
            ForEach(SectionTagDetector.allTags) { tag in
                chip(tag.id, label: tag.label, count: model.tagCounts[tag.id] ?? 0)
            }
            Spacer()
        }
    }

    private func chip(_ id: String?, label: String, count: Int?) -> some View {
        let active = model.tagFilter == id
        return Button {
            model.tagFilter = id
        } label: {
            HStack(spacing: 4) {
                if let id {
                    Circle()
                        .fill(JamTheme.tagColor(id))
                        .frame(width: 7, height: 7)
                }
                Text(count.map { "\(label) (\($0))" } ?? label)
                    .font(.caption)
            }
            .padding(.horizontal, 9)
            .padding(.vertical, 4)
            .background(
                Capsule().fill(
                    active ? JamTheme.accent.opacity(0.35) : JamTheme.surfaceElevated)
            )
            .overlay(Capsule().strokeBorder(
                active ? JamTheme.accent : JamTheme.stroke))
        }
        .buttonStyle(.plain)
        .foregroundStyle(JamTheme.textPrimary)
    }
}
