// DocumentPickerView.swift
//
// UIDocumentPickerViewController wrapper for the Files import path.
// `asCopy: true` hands us a sandboxed temp copy, so the transcoder can
// read it without holding the picker's security scope open.

#if os(iOS)

import SwiftUI
import UniformTypeIdentifiers

struct DocumentPickerView: UIViewControllerRepresentable {
    /// Accepted audio types: mp3, wav, aac, m4a, flac.
    static let audioTypes: [UTType] = [
        .mp3,
        .wav,
        .mpeg4Audio,
        UTType(filenameExtension: "aac"),
        UTType(filenameExtension: "flac"),
    ].compactMap { $0 }

    let onPick: (URL) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let picker = UIDocumentPickerViewController(
            forOpeningContentTypes: Self.audioTypes, asCopy: true
        )
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ controller: UIDocumentPickerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL) -> Void

        init(onPick: @escaping (URL) -> Void) {
            self.onPick = onPick
        }

        func documentPicker(
            _ controller: UIDocumentPickerViewController,
            didPickDocumentsAt urls: [URL]
        ) {
            guard let url = urls.first else { return }
            onPick(url)
        }
    }
}

#endif
