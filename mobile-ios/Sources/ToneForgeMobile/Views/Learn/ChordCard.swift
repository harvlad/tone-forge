// ChordCard.swift
//
// One chord of the Learn tab's NOW / NEXT pair (D-022): role label,
// chord symbol with its roman-numeral function in the song key, and
// a fretboard diagram. Falls back to a symbol-only card when
// GuitarVoicing can't produce a shape (unparseable or chromatic-mess
// symbols).

import SwiftUI
import ToneForgeEngine

struct ChordCard: View {
    /// "NOW" / "NEXT".
    let role: String
    /// Chord symbol ("Am"); nil renders the empty placeholder.
    let symbol: String?
    /// Song key for the roman-numeral label; nil hides it.
    let key: MusicalKey?
    /// Highlighted treatment for the active (NOW) card.
    var emphasized: Bool = false

    /// Fretting-hand silhouette overlay (Learn setting, shared by both
    /// cards). The hand animates between chord shapes.
    @AppStorage("learn.showHand") private var showHand = true

    var body: some View {
        VStack(spacing: 6) {
            Text(role)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(TFTheme.textSecondary)

            if let symbol {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(symbol)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(
                            emphasized ? Color.accentColor : TFTheme.textPrimary)
                    if let numeral = RomanNumeral.label(symbol: symbol, key: key) {
                        Text(numeral)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(TFTheme.textSecondary)
                    }
                }
                .lineLimit(1)
                .minimumScaleFactor(0.6)

                if let shape = GuitarVoicing.shape(symbol: symbol) {
                    // Keep chord-chart proportions: the flexible card
                    // can be much taller than the diagram wants —
                    // unconstrained it stretched into spaghetti.
                    diagram(shape: shape)
                        .aspectRatio(0.85, contentMode: .fit)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .frame(minHeight: 96)
                } else {
                    Spacer(minLength: 0)
                }
            } else {
                Text("—")
                    .font(.title2.weight(.bold))
                    .foregroundStyle(TFTheme.textSecondary)
                Spacer(minLength: 0)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(.vertical, 10)
        .padding(.horizontal, 8)
        .frame(minHeight: 160)
        .tfCard()
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(
                    emphasized ? Color.accentColor.opacity(0.5) : .clear,
                    lineWidth: 1.5
                )
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(role): \(symbol ?? "no chord")")
    }

    /// Board → hand silhouette → dots, so finger numbers stay readable
    /// on top of the hand. The hand animates to the new chord shape;
    /// the diagram itself swaps instantly.
    @ViewBuilder
    private func diagram(shape: GuitarChordShape) -> some View {
        if showHand {
            GeometryReader { geo in
                ZStack {
                    FretboardDiagram(shape: shape, layer: .board)
                    HandSilhouetteView(
                        plan: HandPlan.plan(shape: shape, size: geo.size)
                    )
                    .animation(.easeInOut(duration: 0.45),
                               value: HandPlan.plan(shape: shape, size: geo.size))
                    FretboardDiagram(shape: shape, layer: .dots)
                }
            }
        } else {
            FretboardDiagram(shape: shape)
        }
    }
}
