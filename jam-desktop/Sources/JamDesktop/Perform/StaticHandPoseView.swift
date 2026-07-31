// StaticHandPoseView.swift
//
// The ORIENTATION layer of the Perform learning stack. Shows the baked
// fretting-hand reference image for the CURRENT chord so a beginner can compare
// their own hand shape — answering "does my hand roughly look like this?".
//
// No motion, no biomechanics: it updates per chord and nothing more. The
// long-term goal is a properly animated, anatomically convincing hand; for V1
// the existing rendered pose is a perfectly good static reference. Reuses the
// offline-baked HandSprites via ToneForgeEngine.HandPoseLibrary.

import SwiftUI
import ToneForgeEngine

struct StaticHandPoseView: View {
    let symbol: String?

    var body: some View {
        Group {
            if let symbol, let image = HandPoseLibrary.spriteImage(for: symbol) {
                image.resizable().scaledToFit()
                    .accessibilityLabel(Text("Reference hand shape for \(symbol)"))
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "hand.raised")
                        .font(.system(size: 30)).foregroundStyle(.secondary)
                    Text(symbol.map { "No pose for \($0)" } ?? "—")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
