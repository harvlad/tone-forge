// BrowsePacksSheet.swift
//
// The "Browse Packs" sheet reached from the Contribute tab's
// `PackPicker` and the "What do you want to add?" category cards.
// Thin modal wrapper (D-022): the browsing UI itself lives in
// PacksBrowserView, shared with the Library tab's Packs segment.
// Activating a pack dismisses the sheet; the Contribute tab sees the
// change via `AppState.activeSamplePack` (ModeCoordinator rebinds the
// grid). `initialFamily` seeds the filter when opened from a
// CategoryCards card.

import SwiftUI
import ToneForgeEngine

struct BrowsePacksSheet: View {
    @Environment(\.dismiss) private var dismiss

    private let initialFamily: SampleFamily?

    init(initialFamily: SampleFamily? = nil) {
        self.initialFamily = initialFamily
    }

    var body: some View {
        NavigationStack {
            PacksBrowserView(
                initialFamily: initialFamily,
                onActivated: { dismiss() }
            )
            .navigationTitle("Browse Packs")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }
}
