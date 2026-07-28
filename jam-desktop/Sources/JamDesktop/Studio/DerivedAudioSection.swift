// DerivedAudioSection.swift
//
// Studio "Derived Audio": play back what the pipeline STORED — per-stem
// transcribed MIDI and the chord timeline — synthesized on the app's
// synth, no original audio. This is the audible-evaluation surface for
// transcription/harmony quality (guitar-less evaluation mode: hear the
// derived part instead of playing along).

import SwiftUI
import JamDesktopCore

struct DerivedAudioSection: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var studio: StudioModel
    @EnvironmentObject private var session: SessionController
    @StateObject private var playback = DerivedPlaybackController()

    @State private var derivedByRole: [String: DerivedAudio] = [:]
    @State private var roles: [String] = []
    @State private var chordCount: Int = 0
    @State private var engine: String = ""
    @State private var isLoading = false
    @State private var error: String?

    private let client = DerivedAudioClient()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Derived Audio").font(.headline)
                Text("synthesized from analysis — no original audio")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                if !engine.isEmpty && engine != "current" {
                    Text(engine.replacingOccurrences(of: "_", with: " "))
                        .font(.caption2).padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(JamTheme.accent.opacity(0.25)))
                }
                if playback.playingMode != nil {
                    Button("Stop") { playback.stop() }
                        .buttonStyle(.borderedProminent)
                }
            }

            if isLoading {
                ProgressView().controlSize(.small)
            } else if let error {
                Text(error).font(.caption).foregroundStyle(JamTheme.error)
            } else if roles.isEmpty && chordCount == 0 {
                Text("No derived music stored for this session.")
                    .font(.caption).foregroundStyle(.secondary)
            } else {
                allRow()
                ForEach(roles, id: \.self) { role in
                    if let d = derivedByRole[role] {
                        stemRow(role: role, derived: d)
                    }
                }
                if chordCount > 0, let any = derivedByRole.values.first {
                    chordRow(derived: any)
                }
            }
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 12).fill(JamTheme.surface))
        .task(id: studio.loadedHistoryID) { await loadAll() }
        .onDisappear { playback.stop() }
        .onAppear { playback.bind(session: session) }
    }

    @ViewBuilder
    private func allRow() -> some View {
        let melodic = derivedByRole.filter {
            DerivedPlaybackController.ensembleRoles.contains($0.key)
            && !$0.value.notes.isEmpty
        }
        if melodic.count >= 2, let any = melodic.values.first {
            HStack {
                Text("All").frame(width: 70, alignment: .leading).bold()
                Text(melodic.keys.sorted().joined(separator: " + "))
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                playButton("Play", active: playback.playingMode == .all) {
                    playback.play(.all, derived: any, ensemble: melodic)
                }
                if chordCount > 0 {
                    playButton("+ Chords", active: playback.playingMode == .allWithChords) {
                        playback.play(.allWithChords, derived: any, ensemble: melodic)
                    }
                }
            }
            Divider()
        }
    }

    private func stemRow(role: String, derived: DerivedAudio) -> some View {
        HStack {
            Text(role.capitalized).frame(width: 70, alignment: .leading)
            Text("\(derived.notes.count) notes")
                .font(.caption).foregroundStyle(.secondary)
            if let m = derived.method, m.hasPrefix("specialist:") {
                Image(systemName: "sparkles").font(.caption2)
                    .foregroundStyle(JamTheme.accent)
                    .help(m)
            }
            Spacer()
            playButton("Play", active: playback.playingMode == .notes(role: role)) {
                playback.play(.notes(role: role), derived: derived)
            }
            playButton("+ Chords", active: playback.playingMode == .both(role: role)) {
                playback.play(.both(role: role), derived: derived)
            }
        }
    }

    private func chordRow(derived: DerivedAudio) -> some View {
        HStack {
            Text("Chords").frame(width: 70, alignment: .leading)
            Text("\(chordCount) changes")
                .font(.caption).foregroundStyle(.secondary)
            Spacer()
            playButton("Play", active: playback.playingMode == .chords) {
                playback.play(.chords, derived: derived)
            }
        }
    }

    private func playButton(_ label: String, active: Bool,
                            action: @escaping () -> Void) -> some View {
        Button(active ? "Playing…" : label) {
            if active { playback.stop() } else { action() }
        }
        .buttonStyle(.bordered)
        .disabled(false)
    }

    private func loadAll() async {
        guard let id = studio.loadedHistoryID, !id.isEmpty else { return }
        isLoading = true
        error = nil
        derivedByRole = [:]
        roles = []
        do {
            // First fetch discovers available roles + chords.
            let first = try await client.fetch(
                baseURL: model.backendBaseURL, historyId: id)
            engine = first.analysisEngine
            chordCount = first.chords.count
            var byRole: [String: DerivedAudio] = [:]
            if let r = first.role { byRole[r] = first }
            for role in first.availableRoles where byRole[role] == nil {
                byRole[role] = try await client.fetch(
                    baseURL: model.backendBaseURL, historyId: id, role: role)
            }
            derivedByRole = byRole
            roles = first.availableRoles.sorted()
        } catch {
            self.error = "Derived audio unavailable: \(error.localizedDescription)"
        }
        isLoading = false
    }
}
