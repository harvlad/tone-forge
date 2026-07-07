// AttestationSheet.swift
//
// One-time ownership attestation shown before the first import.
// Accept persists via AttestationStore (never asked again for this
// attestation version); Cancel abandons the import.

import SwiftUI

struct AttestationSheet: View {
    @ObservedObject var store: AttestationStore
    var onAccept: () -> Void
    var onCancel: () -> Void

    @State private var showTerms = false

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 20) {
                Label("Before you import", systemImage: "checkmark.shield")
                    .font(.title2.bold())

                Text(
                    "I own this file or have rights to use it for personal practice."
                )
                .font(.body)

                Text(
                    "Tone Forge only analyses audio you own — purchases, "
                    + "your own recordings, or files you're licensed to use. "
                    + "Uploads are kept on the server for at most 7 days."
                )
                .font(.footnote)
                .foregroundStyle(.secondary)

                Button("View Terms") { showTerms = true }
                    .font(.footnote)

                Spacer()

                VStack(spacing: 10) {
                    Button {
                        store.accept()
                        onAccept()
                    } label: {
                        Text("I Agree")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .accessibilityIdentifier("attestation-accept")

                    Button("Cancel", role: .cancel) { onCancel() }
                        .frame(maxWidth: .infinity)
                        .accessibilityIdentifier("attestation-cancel")
                }
            }
            .padding()
            .navigationTitle("Ownership")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .sheet(isPresented: $showTerms) {
                TermsOfServiceSheet()
            }
        }
        .interactiveDismissDisabled()
        .accessibilityIdentifier("attestation-sheet")
    }
}
