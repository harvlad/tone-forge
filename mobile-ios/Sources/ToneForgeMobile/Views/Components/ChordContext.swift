// ChordContext.swift
//
// Shared harmonic-context strip for Jam + Perform. Same musical state
// (`AppState.currentChord` + in-key next choices), adapts by screen:
// Jam exposes harmonic choices, Perform exposes immediate next-chord
// performance. The `NEXT` chords are real playable choices — wired by the
// host to actual chord behaviour (never faked). Optional latch state is
// shown because it's directly relevant while performing.

import SwiftUI

struct ChordContext: View {
    /// Current sounding chord symbol (nil → "—").
    let current: String?
    /// In-key next-chord choices (e.g. ["F", "G"]).
    var next: [String] = []
    /// Fired when a NEXT choice is tapped. Nil → choices are display-only.
    var onSelectNext: ((String) -> Void)? = nil
    /// Latch state to surface (Perform). Nil hides the latch pill.
    var latchOn: Bool? = nil
    var onToggleLatch: (() -> Void)? = nil

    var body: some View {
        HStack(alignment: .center, spacing: TFTheme.Spacing.md) {
            VStack(alignment: .leading, spacing: 2) {
                Text("CURRENT CHORD")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                Text(current ?? "—")
                    .font(TFTheme.primaryValue)
                    .foregroundStyle(TFTheme.textPrimary)
                    .minimumScaleFactor(0.6)
                    .lineLimit(1)
            }

            if !next.isEmpty {
                Image(systemName: "arrow.right")
                    .font(.headline)
                    .foregroundStyle(TFTheme.textSecondary)

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: TFTheme.Spacing.sm) {
                        ForEach(next, id: \.self) { sym in
                            nextChip(sym)
                        }
                    }
                    Text("NEXT")
                        .font(.caption2)
                        .foregroundStyle(TFTheme.textSecondary)
                }
            }

            Spacer(minLength: 0)

            if let latchOn {
                Button {
                    Haptics.toggle()
                    onToggleLatch?()
                } label: {
                    VStack(spacing: 1) {
                        // "Latch", not a bare lock icon — the padlock read as
                        // the (different) loop Lock. One word per concept.
                        Image(systemName: latchOn ? "pin.fill" : "pin")
                        Text("Latch").font(.caption2)
                    }
                    .foregroundStyle(latchOn ? TFTheme.accent : TFTheme.textSecondary)
                    .padding(.horizontal, TFTheme.Spacing.sm)
                    .padding(.vertical, TFTheme.Spacing.xs)
                    .background(
                        latchOn ? TFTheme.accent.opacity(0.20) : TFTheme.surface2,
                        in: RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
                    )
                }
                .buttonStyle(.plain)
                .accessibilityLabel(Text("Latch \(latchOn ? "on" : "off")"))
            }
        }
        .padding(.horizontal, TFTheme.Spacing.lg)
        .padding(.vertical, TFTheme.Spacing.md)
    }

    private func nextChip(_ sym: String) -> some View {
        let tappable = onSelectNext != nil
        return Button {
            if tappable { onSelectNext?(sym) }
        } label: {
            Text(sym)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(TFTheme.textPrimary)
                .padding(.horizontal, TFTheme.Spacing.md)
                .padding(.vertical, 6)
                .background(TFTheme.surface2, in: Capsule())
                .overlay(Capsule().stroke(TFTheme.accent.opacity(0.5), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(!tappable)
        .accessibilityLabel(Text("Next chord \(sym)"))
    }
}
