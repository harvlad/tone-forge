// ArrangementBar.swift
//
// Live-capture arrangement UI for the Jam Samples surface — the mobile
// twin of jam-desktop's LaunchpadPanelView.arrangementRow /
// arrangementStrip and the web kit.js control. Split in two so the caller
// can pack chrome tight: ArrangementChips (Rec/Play/Clear, rides the
// shared chrome row) and ArrangementBar (the proportional section strip
// with fills + playhead). Driven by ArrangementModel (which holds the
// shared ArrangementRuntime); gated to Samples mode by the caller.
// Hidden until the song has sections to collapse into blocks.

import SwiftUI
import ToneForgeEngine

struct ArrangementBar: View {
    @ObservedObject var arrangement: ArrangementModel

    var body: some View {
        if !arrangement.blocks.isEmpty {
            strip
                .padding(.horizontal, 12)
        }
    }
    // MARK: - Section strip

    private var strip: some View {
        let blocks = arrangement.blocks
        let span = max((blocks.last?.end ?? 1) - (blocks.first?.start ?? 0), 0.001)
        return GeometryReader { geo in
            ZStack(alignment: .topLeading) {
                HStack(spacing: 2) {
                    ForEach(Array(blocks.enumerated()), id: \.offset) { i, b in
                        let frac = (b.end - b.start) / span
                        let filled = arrangement.filledBlocks.contains(i)
                        let active = arrangement.activeBlock == i
                        RoundedRectangle(cornerRadius: 4)
                            .fill(filled
                                  ? TFTheme.accent.opacity(active ? 0.9 : 0.5)
                                  : Color.white.opacity(active ? 0.25 : 0.10))
                            .overlay(alignment: .leading) {
                                Text(b.label)
                                    .font(.caption2)
                                    .lineLimit(1)
                                    .foregroundStyle(.white.opacity(0.85))
                                    .padding(.horizontal, 5)
                            }
                            .frame(width: max(2, geo.size.width * frac - 2))
                    }
                }
                if let f = arrangement.playheadFrac {
                    Rectangle()
                        .fill(Color.white.opacity(0.9))
                        .frame(width: 2)
                        .offset(x: geo.size.width * f)
                }
            }
        }
        .frame(height: 22)
        .accessibilityLabel("Arrangement sections")
    }
}

/// Rec / Play / Clear chips, label-free — they sit on the Samples
/// surface's single chrome row next to the 16|64 toggle.
struct ArrangementChips: View {
    @ObservedObject var arrangement: ArrangementModel

    var body: some View {
        if !arrangement.blocks.isEmpty {
            HStack(spacing: 8) {
                // Rec: capture which pads you play in each section.
                // Mutually exclusive with Play (the runtime enforces it).
                Button {
                    Haptics.selectionChanged()
                    arrangement.toggleRecording()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "record.circle").font(.caption)
                        Text("Rec").font(TFTheme.chipFont)
                    }
                    .tfChip(active: arrangement.recording)
                    .fixedSize()
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    arrangement.recording ? "Stop recording arrangement"
                                          : "Record arrangement")

                // Play: replay the captured arrangement hands-free.
                Button {
                    Haptics.selectionChanged()
                    arrangement.togglePlaying()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "play.circle").font(.caption)
                        Text("Play").font(TFTheme.chipFont)
                    }
                    .tfChip(active: arrangement.playing)
                    .fixedSize()
                }
                .buttonStyle(.plain)
                .disabled(arrangement.filledBlocks.isEmpty)
                .accessibilityLabel(
                    arrangement.playing ? "Stop arrangement playback"
                                        : "Play captured arrangement")

                // Clear: forget this song's captured arrangement.
                Button {
                    Haptics.selectionChanged()
                    arrangement.clear()
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "trash").font(.caption)
                        Text("Clear").font(TFTheme.chipFont)
                    }
                    .tfChip(active: false)
                    .fixedSize()
                }
                .buttonStyle(.plain)
                .disabled(arrangement.filledBlocks.isEmpty)
                .accessibilityLabel("Clear captured arrangement")
            }
        }
    }
}
