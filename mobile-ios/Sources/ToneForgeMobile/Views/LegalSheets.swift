// LegalSheets.swift
//
// Placeholder Terms of Service and Privacy Policy sheets, plus the
// DMCA/takedown contact details. Copy is intentionally provisional —
// counsel-reviewed text replaces the body strings before App Store
// submission; the structure (designated agent contact, retention
// window, personal-practice scope) stays.

import SwiftUI

struct TermsOfServiceSheet: View {
    var body: some View {
        LegalDocumentSheet(
            title: "Terms of Service",
            sections: [
                (
                    "Personal practice only",
                    "Tone Forge analyses audio you own or are licensed to use, "
                    + "for your own practice and tone exploration. You may not "
                    + "upload audio you do not have rights to."
                ),
                (
                    "Your attestation",
                    "Before importing, you confirm that you own each file or "
                    + "hold rights to use it for personal practice. You are "
                    + "responsible for the content you upload."
                ),
                (
                    "Server retention",
                    "Uploaded audio and analysis artifacts are retained on our "
                    + "servers for at most 7 days, after which they are deleted "
                    + "automatically. You can delete them at any time from "
                    + "Settings."
                ),
                (
                    "Copyright complaints",
                    "We respond to copyright takedown notices sent to our "
                    + "designated agent at \(AppConfig.takedownEmail). Include "
                    + "the work, the material complained of, and your contact "
                    + "details."
                ),
            ]
        )
    }
}

struct PrivacyPolicySheet: View {
    var body: some View {
        LegalDocumentSheet(
            title: "Privacy Policy",
            sections: [
                (
                    "What we process",
                    "Audio files you choose to import are uploaded for "
                    + "analysis. We derive stems, chord and section data, and "
                    + "sample chops from them."
                ),
                (
                    "Retention",
                    "Uploads and derived artifacts are deleted from our "
                    + "servers automatically after at most 7 days, or "
                    + "immediately when you delete them in the app."
                ),
                (
                    "No sale or sharing",
                    "We do not sell your audio or share it with third "
                    + "parties. It is used solely to produce your analysis "
                    + "results."
                ),
                (
                    "Contact",
                    "Questions and requests: \(AppConfig.takedownEmail)."
                ),
            ]
        )
    }
}

/// Shared scaffold for the placeholder legal documents.
private struct LegalDocumentSheet: View {
    @Environment(\.dismiss) private var dismiss
    let title: String
    let sections: [(heading: String, body: String)]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Placeholder — final text pending legal review.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    ForEach(sections, id: \.heading) { section in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(section.heading).font(.headline)
                            Text(section.body).font(.body)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding()
            }
            .navigationTitle(title)
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
