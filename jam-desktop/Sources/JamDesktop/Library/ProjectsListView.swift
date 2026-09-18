// ProjectsListView.swift
//
// Projects sheet (Library surface): saved per-song pad workspaces
// (ProjectStore / ProjectCoordinator), newest-updated first, plus the
// "Save workspace as project…" action for the attached song. Row menu
// offers Rename / Duplicate / Reset to song / Delete — the same
// list + inline-rename + ellipsis-Menu chrome as the iOS
// ProjectsListView, in RecordingsListView's sheet shell.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

struct ProjectsListView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var saveName = ""
    @State private var renamingId: UUID?
    @State private var renameText = ""

    /// Coordinator changes forward through session.objectWillChange,
    /// so reading it in body re-renders on save/delete/rename.
    private var projects: ProjectCoordinator { session.projects }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            saveRow
            Divider()
            if projects.savedProjects.isEmpty {
                emptyState
            } else {
                list
            }
        }
        .frame(width: 520, height: 420)
        .background(JamTheme.background)
    }

    private var header: some View {
        HStack {
            Text("Projects")
                .font(.headline)
            Spacer()
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }

    private var saveRow: some View {
        HStack(spacing: 8) {
            TextField("Name this workspace…", text: $saveName)
                .textFieldStyle(.roundedBorder)
                .onSubmit(saveCurrent)
            Button("Save workspace as project", action: saveCurrent)
                .disabled(session.attachedAnalysisId == nil)
                .help(session.attachedAnalysisId == nil
                    ? "Load a song first — a project is a per-song workspace"
                    : "Keep the current pads, FX, edits and borrows as a named project")
        }
        .padding(12)
    }

    private func saveCurrent() {
        guard session.attachedAnalysisId != nil else { return }
        projects.saveCurrentAsProject(named: saveName)
        saveName = ""
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "square.grid.4x3.fill")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
            Text("No projects yet")
                .foregroundStyle(.secondary)
            Text("Set up the pads for a song, then save the workspace "
                + "above. Your in-progress workspace is auto-saved per "
                + "song either way.")
                .font(.caption)
                .foregroundStyle(.tertiary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var list: some View {
        List(projects.savedProjects) { project in
            row(project)
        }
        .listStyle(.inset)
        .scrollContentBackground(.hidden)
    }

    private func row(_ project: Project) -> some View {
        let isLoaded = project.baseSongId != nil
            && session.attachedAnalysisId == project.baseSongId
        return HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                if renamingId == project.id {
                    TextField("Project name", text: $renameText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { commitRename(project) }
                } else {
                    Text(project.name)
                        .font(.callout.weight(.semibold))
                        .lineLimit(1)
                }
                Text(subtitle(for: project))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            if isLoaded {
                Text("Loaded")
                    .font(.caption2.weight(.semibold))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(JamTheme.accent.opacity(0.25),
                                in: Capsule())
            }
            Spacer()

            Button {
                projects.load(project, model: model)
                dismiss()
            } label: {
                Image(systemName: "arrow.down.circle.fill")
            }
            .help("Load this project — opens its song with the saved "
                + "pads, effects, sequences and borrowed loops")

            Menu {
                Button(renamingId == project.id ? "Save name" : "Rename") {
                    if renamingId == project.id {
                        commitRename(project)
                    } else {
                        renamingId = project.id
                        renameText = project.name
                    }
                }
                Button {
                    projects.duplicate(project)
                } label: {
                    Label("Duplicate", systemImage: "plus.square.on.square")
                }
                Button {
                    projects.resetProjectToSong(project)
                } label: {
                    Label("Reset to song", systemImage: "arrow.uturn.backward")
                }
                Button("Delete", role: .destructive) {
                    projects.delete(project)
                    if renamingId == project.id { renamingId = nil }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .frame(width: 24, height: 24)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .frame(width: 28)
        }
        .buttonStyle(.borderless)
        .padding(.vertical, 2)
    }

    private func subtitle(for project: Project) -> String {
        var parts: [String] = []
        if let title = project.baseSongTitle, !title.isEmpty {
            parts.append(title)
        }
        parts.append(Self.dateFormatter.string(from: project.updatedAt))
        return parts.joined(separator: " · ")
    }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .short
        return f
    }()

    private func commitRename(_ project: Project) {
        let trimmed = renameText
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty {
            projects.rename(project, to: trimmed)
        }
        renamingId = nil
        renameText = ""
    }
}
