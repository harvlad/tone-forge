// KitFlowPill.swift
//
// The visual Jam→Perform relationship (PM eval): one shared breadcrumb
// pill rendered on BOTH tabs — [Jam] → [Perform] with the current stage
// lit — so the build→stage pipeline is a picture, not a sentence. Both
// chips are tappable (hop tabs). The kit name rides along, cleaned
// (auto-kits are named "{analysis-hash} — Auto Kit" on the wire; nobody
// wants the hash).

import SwiftUI

struct KitFlowPill: View {
    @EnvironmentObject private var appState: AppState
    /// Which stage this surface IS (lit side of the pill).
    let active: AppTab

    /// Human kit name — auto-kits drop the analysis-id hash prefix.
    private var kitName: String? {
        guard let pack = appState.activeSamplePack?.pack else { return nil }
        return pack.packId.hasPrefix("auto-") ? "Auto Kit" : pack.name
    }

    var body: some View {
        HStack(spacing: 8) {
            chip("Build", icon: "wrench.adjustable.fill", tab: .jam)
            Image(systemName: "arrow.right")
                .font(.caption2.weight(.bold))
                .foregroundStyle(TFTheme.textSecondary)
            chip("Perform", icon: "bolt.fill", tab: .perform)

            if let kitName {
                Text("·")
                    .foregroundStyle(TFTheme.textSecondary)
                Text(kitName)
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, TFTheme.Spacing.md)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            active == .jam
                ? "Jam builds the kit, Perform plays it. You are building."
                : "Jam builds the kit, Perform plays it. You are performing.")
    }

    private func chip(_ title: String, icon: String, tab: AppTab) -> some View {
        let isActive = tab == active
        return Button {
            guard tab != active else { return }
            Haptics.selectionChanged()
            appState.selectedTab = tab
        } label: {
            HStack(spacing: 4) {
                Image(systemName: icon).font(.system(size: 9, weight: .bold))
                Text(title).font(.caption2.weight(.semibold))
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .foregroundStyle(isActive ? TFTheme.textPrimary : TFTheme.textSecondary)
            .background(
                isActive ? TFTheme.accent.opacity(0.35) : TFTheme.chipFill,
                in: Capsule()
            )
            .overlay(
                Capsule().stroke(
                    isActive ? TFTheme.accent : TFTheme.stroke, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}
