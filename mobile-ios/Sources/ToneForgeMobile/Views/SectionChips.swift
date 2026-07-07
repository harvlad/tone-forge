// SectionChips.swift
//
// Horizontal, scrollable strip of one chip per BundleTimeline section.
// Two interactions:
//   - Tap:        seek transport to section start.
//   - Long-press: toggle the section in the per-song allowlist (the
//                 "Play only in" gate consumed by SampleScheduler).
//
// A chip's colour reflects three states:
//   .accent tint    → currently-active section (contains songSeconds)
//   .primary tint   → allowed by the section gate
//   .secondary tint → gated off (taps produce silence in Samples)

import SwiftUI
import ToneForgeEngine

struct SectionChips: View {
    let sections: [SectionEvent]
    let nowSongSeconds: Double
    /// Allowlist. `nil` = allow all sections; empty set = deny all.
    /// Matches `SectionResolver.isAllowed` semantics.
    let allowedLabels: Set<String>?
    let onSeek: (Double) -> Void
    let onGateToggle: (String) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(Array(sections.enumerated()), id: \.offset) { _, s in
                    chip(for: s)
                }
                if sections.isEmpty {
                    Text("No sections yet")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 8)
                }
            }
            .padding(.horizontal, 12)
        }
    }

    @ViewBuilder
    private func chip(for s: SectionEvent) -> some View {
        let label = s.label ?? "Section"
        let isActive = (s.start <= nowSongSeconds) && (nowSongSeconds < s.end)
        let allowed: Bool = {
            guard let allow = allowedLabels else { return true }
            return allow.contains { $0.caseInsensitiveCompare(label) == .orderedSame }
        }()

        let bg: Color = isActive
            ? Color.accentColor.opacity(0.30)
            : (allowed ? Color.gray.opacity(0.20) : Color.gray.opacity(0.08))
        let fg: Color = allowed ? .primary : .secondary

        VStack(spacing: 2) {
            Text(label).font(.caption.weight(.medium))
            Text(fmt(s.start)).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
        }
        .foregroundStyle(fg)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 8).fill(bg))
        .contentShape(Rectangle())
        .onTapGesture { onSeek(s.start) }
        .onLongPressGesture { onGateToggle(label) }
    }

    private func fmt(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
