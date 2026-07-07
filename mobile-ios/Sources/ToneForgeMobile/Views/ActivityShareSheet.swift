// ActivityShareSheet.swift
//
// Minimal SwiftUI wrapper around `UIActivityViewController` plus an
// Identifiable URL box for `.sheet(item:)`. `ShareLink` covers URLs
// known at construction time; our offline renders (m4a layer export,
// session bounce) return their URL asynchronously, so those flows
// need the imperative controller. Shared by ProfileView and the
// storage browsers (P7).

import SwiftUI

/// Identifiable URL wrapper for `.sheet(item:)` so a fresh render
/// re-presents the share sheet even when the URL happens to match a
/// prior render's temp file.
struct ShareFileItem: Identifiable {
    let id = UUID()
    let url: URL
}

#if canImport(UIKit)
import UIKit

struct ActivityShareSheet: UIViewControllerRepresentable {
    let activityItems: [Any]

    func makeUIViewController(context: Context) -> UIActivityViewController {
        UIActivityViewController(
            activityItems: activityItems, applicationActivities: nil)
    }

    func updateUIViewController(
        _ uiViewController: UIActivityViewController, context: Context
    ) {}
}
#endif
