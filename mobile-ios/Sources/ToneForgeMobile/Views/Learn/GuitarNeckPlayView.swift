// GuitarNeckPlayView.swift
//
// The "Show hand" mode of the Learn tab: thin wrapper around the
// shared ToneForgeEngine.GuitarNeckPlaySurface (horizontal neck +
// animated hand) with the app's chord header (current + NEXT pill).

import SwiftUI
import ToneForgeEngine

struct GuitarNeckPlayView: View {
    /// Current chord symbol (nil → open hand, no dots).
    let current: String?
    /// Upcoming chord symbol (text pill only).
    let next: String?
    /// Song key for roman-numeral labels.
    let key: MusicalKey?

    var body: some View {
        VStack(alignment: .leading, spacing: TFTheme.Spacing.sm) {
            header
            // Mock-panel aspect: the tall Learn card must not stretch
            // the hand/forearm to fill it.
            GuitarNeckPlaySurface(current: current)
                .aspectRatio(1.75, contentMode: .fit)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .padding(TFTheme.Spacing.md)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .tfCard()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Guitar neck. Now \(current ?? "no chord")"
            + (next.map { ", next \($0)" } ?? ""))
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: TFTheme.Spacing.sm) {
            Text(current ?? "—")
                .font(.title.weight(.bold))
                .foregroundStyle(Color.accentColor)
            if let current, let numeral = RomanNumeral.label(symbol: current, key: key) {
                Text(numeral)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textSecondary)
            }
            Spacer()
            if let next {
                HStack(spacing: 4) {
                    Text("NEXT")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(TFTheme.textSecondary)
                    Text(next)
                        .font(.headline.weight(.bold))
                        .foregroundStyle(TFTheme.textPrimary)
                }
                .padding(.horizontal, TFTheme.Spacing.md)
                .padding(.vertical, 5)
                .background(TFTheme.surface2, in: Capsule())
                .overlay(Capsule().stroke(TFTheme.accent.opacity(0.5), lineWidth: 1))
            }
        }
    }
}
