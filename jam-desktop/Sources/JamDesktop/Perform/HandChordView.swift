// HandChordView.swift
//
// Perform-tab ALTERNATIVE rendering of the current chord state: the
// fretting-hand pose sprite for the current chord symbol, shown in place
// of the chord DIAGRAM when the user toggles to "Hand".
//
// Pure presentation of the current chord (the same `symbol` the diagram
// uses). NO motion, interpolation, trajectory, or biomechanical solve —
// it just picks the pre-baked sprite for the chord that is sounding now,
// and advances chord-by-chord exactly as the diagram already does. Reuses
// the offline-baked HandSprites shipped in ToneForgeEngine
// (HandPoseLibrary.spriteImage); independent of the planner research.

import SwiftUI
import ToneForgeEngine

struct HandChordView: View {
    let symbol: String

    var body: some View {
        if let image = HandPoseLibrary.spriteImage(for: symbol) {
            image
                .resizable()
                .scaledToFit()
                .accessibilityLabel(Text("Fretting hand for \(symbol)"))
        } else {
            // Chord has no baked hand pose (e.g. an extended/altered
            // voicing not in the sprite set) — say so rather than blank.
            VStack(spacing: 8) {
                Image(systemName: "hand.raised.slash")
                    .font(.system(size: 40))
                    .foregroundStyle(.secondary)
                Text("No hand pose for \(symbol)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}
