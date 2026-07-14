// UploadDropZone.swift
//
// Drag-and-drop + browse target for local audio files, with the
// D-024 ownership-attestation checkbox gating both paths (the server
// rejects uploads without attested=true anyway; the UI just fails
// earlier and clearer).

import SwiftUI
import UniformTypeIdentifiers
import JamDesktopCore

struct UploadDropZone: View {
    @EnvironmentObject private var intake: IntakeModel

    /// Called with a readable local file URL once the user drops or
    /// picks a file (and only when attested).
    let onFile: (URL) -> Void

    @State private var isTargeted = false
    @State private var showingPicker = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            dropArea
            attestRow
        }
        .fileImporter(
            isPresented: $showingPicker,
            allowedContentTypes: [.audio]
        ) { result in
            if case let .success(url) = result {
                deliver(url)
            }
        }
    }

    private var dropArea: some View {
        VStack(spacing: 12) {
            // Plus icon in circle
            Circle()
                .fill(JamTheme.accent)
                .frame(width: 48, height: 48)
                .overlay {
                    Image(systemName: "plus")
                        .font(.system(size: 20, weight: .semibold))
                        .foregroundStyle(.white)
                }

            Text("Choose File")
                .font(.headline)
                .foregroundStyle(.white)

            Text("or drag & drop")
                .font(.subheadline)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 32)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    isTargeted ? JamTheme.accent : JamTheme.accent.opacity(0.3),
                    style: StrokeStyle(lineWidth: 2, dash: [8, 6])
                )
        )
        .contentShape(Rectangle())
        .onTapGesture {
            if intake.attested {
                showingPicker = true
            }
        }
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard intake.attested, let provider = providers.first else { return false }
            provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier) { item, _ in
                guard let data = item as? Data,
                      let url = URL(dataRepresentation: data, relativeTo: nil)
                else { return }
                Task { @MainActor in
                    deliver(url)
                }
            }
            return true
        }
        .opacity(intake.attested ? 1 : 0.5)
    }

    private var attestRow: some View {
        Toggle(isOn: attestedBinding) {
            Text("I own the rights to this recording, or it's licensed for this use.")
                .font(.caption)
        }
        .toggleStyle(.checkbox)
    }

    private var attestedBinding: Binding<Bool> {
        Binding(get: { intake.attested }, set: { intake.attested = $0 })
    }

    private func deliver(_ url: URL) {
        // No-op outside a sandbox; required inside one. Balanced by
        // the OS at process exit — the upload copies the bytes long
        // before that.
        _ = url.startAccessingSecurityScopedResource()
        onFile(url)
    }
}
