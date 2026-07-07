// HelpSheets.swift
//
// In-app help (P7): one "How Tone Forge works" sheet reachable from
// Settings. Same scroll-of-sections scaffold as LegalSheets, minus
// the placeholder disclaimer — this copy ships. Kept deliberately
// short: each section is a two-sentence orientation, not a manual;
// the deep material lives in the repo docs.

import SwiftUI

struct HelpSheet: View {
    @Environment(\.dismiss) private var dismiss

    private let sections: [(heading: String, body: String)] = [
        (
            "Play the grid",
            "Sample mode gives you 64 pads of chops and your own "
            + "captures. Hybrid mode keeps samples on the top rows and "
            + "turns the bottom rows into synth notes in the song's "
            + "key — chord tones light up bright."
        ),
        (
            "Load a song",
            "Import a track you own from the Library tab. It's "
            + "analysed into stems, sections and sample chops, and the "
            + "grid re-tunes itself to the song."
        ),
        (
            "Capture your own samples",
            "Record up to 8 seconds from the mic, or sing through the "
            + "vocoder for harmonised captures. These samples stay on "
            + "this device and are never uploaded."
        ),
        (
            "Record a take",
            "The Record pill captures your performance as events — "
            + "which pads you hit and when — not audio, so takes are "
            + "tiny. With no song loaded you record a sketch against "
            + "the metronome grid, count-in included."
        ),
        (
            "Replay and bounce",
            "Find your takes under Settings → Storage → Sessions. "
            + "Replay one over the transport, or bounce it offline to "
            + "a WAV or M4A file you can share. The same take always "
            + "bounces to the same audio."
        ),
        (
            "Storage",
            "Samples, sessions and bounces all live on this device. "
            + "Browse and delete them any time under Settings → "
            + "Storage."
        ),
        (
            "Launchpad",
            "Plug in a Novation Launchpad over USB and the grid maps "
            + "onto it, lights and all. If a banner says the device is "
            + "underpowered, use a powered USB hub."
        ),
    ]

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
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
            .navigationTitle("How Tone Forge works")
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
