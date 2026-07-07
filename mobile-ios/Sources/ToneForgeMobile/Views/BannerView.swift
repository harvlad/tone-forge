// BannerView.swift
//
// Dismissible top-of-screen warning banner (P7). One consumer today:
// the Launchpad underpower heuristic (≥3 connection flaps in 10 s or
// a SysEx send failure while online → probably an unpowered hub).
// Kept generic — icon/title/message injected — so future warnings
// (route changes, storage pressure) reuse the same chrome.
//
// Dismiss clears the underlying flag; the transport re-raises it on
// the next flap, so a genuinely bad hub keeps nagging.

import SwiftUI

struct BannerView: View {
    let icon: String
    let title: String
    let message: String
    let onDismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .font(.title3)
                .foregroundStyle(.yellow)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Button {
                onDismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(.secondary)
                    .padding(6)
            }
            .accessibilityLabel("Dismiss")
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(.thinMaterial)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .strokeBorder(.yellow.opacity(0.4))
                )
        )
        .padding(.horizontal, 12)
        .padding(.top, 4)
        .transition(.move(edge: .top).combined(with: .opacity))
        .accessibilityIdentifier("banner-underpower")
    }
}
