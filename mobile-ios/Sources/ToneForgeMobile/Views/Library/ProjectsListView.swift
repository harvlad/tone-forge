// ProjectsListView.swift
//
// The Library tab's Projects segment: saved per-song pad workspaces
// (ProjectStore / ProjectCoordinator), newest-updated first. Tap a
// row to load its base song and restore the workspace over it; the
// row menu offers Rename / Duplicate / Reset to song / Delete —
// the same list/inline-rename/ellipsis-Menu chrome as
// RecordingsListView.

import SwiftUI
import ToneForgeEngine

struct ProjectsListView: View {
    @EnvironmentObject private var appState: AppState

    @State private var renamingProjectId: UUID? = nil
    @State private var renameText: String = ""

    /// Coordinator changes forward through appState.objectWillChange,
    /// so reading it in body re-renders on save/delete/rename.
    private var projects: [Project] { appState.projects.savedProjects }

    var body: some View {
        List {
            Section {
                if projects.isEmpty {
                    Text("No projects yet. Set up the pads for a song, "
                        + "then use \u{201C}Save workspace\u{201D} in the "
                        + "Jam settings to keep it. Your in-progress "
                        + "workspace is auto-saved per song either way.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(projects) { project in
                        projectRow(project)
                            .tfLibraryRowChrome()
                    }
                }
            } header: {
                Text("Projects")
            } footer: {
                if !projects.isEmpty {
                    Text("Tap a project to load its song with the saved "
                        + "pads, effects, sequences and borrowed loops.")
                }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(TFTheme.background)
    }

    @ViewBuilder
    private func projectRow(_ project: Project) -> some View {
        let isLoaded =
            appState.currentBundle?.analysisId == project.baseSongId
        HStack(spacing: 12) {
            Button {
                appState.projects.load(project)
            } label: {
                RoundedRectangle(cornerRadius: 10)
                    .fill(tileGradient(for: project.id.uuidString))
                    .frame(width: 52, height: 52)
                    .overlay(
                        Image(systemName: "square.grid.4x3.fill")
                            .font(.title3)
                            .foregroundStyle(.white)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(TFTheme.stroke, lineWidth: 1)
                    )
            }
            .buttonStyle(.borderless)

            VStack(alignment: .leading, spacing: 2) {
                if renamingProjectId == project.id {
                    TextField("Project name", text: $renameText)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { commitRename(project) }
                } else {
                    Text(project.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                        .lineLimit(1)
                }
                Text(subtitle(for: project))
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            Menu {
                Button(renamingProjectId == project.id
                       ? "Save name" : "Rename") {
                    if renamingProjectId == project.id {
                        commitRename(project)
                    } else {
                        renamingProjectId = project.id
                        renameText = project.name
                    }
                }
                Button {
                    appState.projects.duplicate(project)
                } label: {
                    Label("Duplicate", systemImage: "plus.square.on.square")
                }
                Button {
                    appState.projects.resetProjectToSong(project)
                } label: {
                    Label("Reset to song", systemImage: "arrow.uturn.backward")
                }
                Button("Delete", role: .destructive) {
                    appState.projects.delete(project)
                    if renamingProjectId == project.id {
                        renamingProjectId = nil
                    }
                }
            } label: {
                Image(systemName: "ellipsis")
                    .rotationEffect(.degrees(90))
                    .foregroundStyle(TFTheme.textSecondary)
                    .frame(width: 30, height: 30)
                    .contentShape(Rectangle())
            }
        }
        .tfLibraryCard(active: isLoaded)
        .contentShape(Rectangle())
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
            appState.projects.rename(project, to: trimmed)
        }
        renamingProjectId = nil
        renameText = ""
    }

    /// Stable per-project hue (RecordingsListView.tileGradient twin).
    private func tileGradient(for id: String) -> LinearGradient {
        var seed = 0
        for scalar in id.unicodeScalars {
            seed = (seed &* 31) &+ Int(scalar.value)
        }
        let hue = Double(abs(seed) % 360) / 360.0
        return LinearGradient(
            colors: [
                Color(hue: hue, saturation: 0.55, brightness: 0.45),
                Color(hue: hue, saturation: 0.65, brightness: 0.20),
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}
