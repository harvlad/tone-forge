// RemixSheetView.swift
//
// Desktop twin of mobile's RemixSheet: one slide-up home for every
// one-click transform (kits / Flip / Humanize / Re-Drum / Instrument
// Pack) — a single toolbar entry instead of a button per feature on the
// already-dense Launchpad toolbar. Rows call the same SessionController
// remix layer the Launchpad buttons use, so nothing here is a second
// code path.

import SwiftUI
import ToneForgeEngine

struct RemixSheetView: View {
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var candidates: [RedrumCandidate] = []
    @State private var candidatesLoaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Remix", systemImage: "sparkles")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(16)

            Divider()

            List {
                Section("Pads") {
                    kitRow("Auto Kit", icon: "wand.and.stars", kind: "auto",
                           subtitle: "The song's best loops, color-coded")
                    kitRow("Drum Kit", icon: "circle.grid.3x3.fill", kind: "drums",
                           subtitle: "Its kick, snare and hats as clean one-shots")
                    kitRow("Flip", icon: "shuffle", kind: "flip",
                           subtitle: "A new beat built from the song's own DNA")
                }

                Section("Feel") {
                    Button {
                        Task { await session.toggleHumanize() }
                    } label: {
                        row("Humanize", icon: "waveform.path",
                            subtitle: "Sequences swing with this song's own timing",
                            busy: session.remixBusy == "humanize",
                            checked: session.remixHumanizeOn)
                    }
                    .buttonStyle(.plain)
                    .disabled(session.remixBusy == "humanize")
                }

                Section {
                    if session.redrumActiveKit != nil {
                        Button {
                            Task { await session.clearRedrum() }
                        } label: {
                            Label("Original drums",
                                  systemImage: "arrow.uturn.backward")
                        }
                        .buttonStyle(.plain)
                        .disabled(session.remixBusy == "redrum")
                    }
                    redrumRow("Tightened (own kit)", kit: "self")
                    if !candidatesLoaded {
                        HStack {
                            ProgressView().controlSize(.small)
                            Text("Finding kit donors…")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } else if candidates.isEmpty {
                        Text("Analyze more songs to unlock cross-song kits.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    ForEach(candidates.prefix(6)) { c in
                        redrumRow(c.name, kit: "song:\(c.entryId)")
                    }
                } header: {
                    Text("Re-Drum — keep the groove, swap the kit")
                }

                Section("Export") {
                    Button {
                        session.openInstrumentPack()
                    } label: {
                        row("Instrument Pack (.sfz)", icon: "pianokeys",
                            subtitle: "Drums on keys, bass + stab chromatic — downloads via browser",
                            busy: false, checked: false)
                    }
                    .buttonStyle(.plain)
                }

                if let err = session.remixError {
                    Text(err).font(.caption).foregroundStyle(JamTheme.error)
                }
            }
            .listStyle(.inset)
        }
        .frame(width: 440, height: 520)
        .task {
            candidates = await session.redrumCandidates()
            candidatesLoaded = true
        }
    }

    // MARK: - Rows

    private func row(_ title: String, icon: String, subtitle: String,
                     busy: Bool, checked: Bool) -> some View {
        HStack {
            Label {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                    Text(subtitle).font(.caption2).foregroundStyle(.secondary)
                }
            } icon: {
                Image(systemName: icon)
            }
            Spacer()
            if busy {
                ProgressView().controlSize(.small)
            } else if checked {
                Image(systemName: "checkmark").foregroundStyle(JamTheme.accent)
            }
        }
        .contentShape(Rectangle())
    }

    private func kitRow(_ title: String, icon: String, kind: String,
                        subtitle: String) -> some View {
        Button {
            Task { await session.loadAutoKit(kind: kind) }
        } label: {
            row(title, icon: icon, subtitle: subtitle,
                busy: session.autoKitLoading,
                checked: session.lastKitKind == kind && !session.autoKitLoading)
        }
        .buttonStyle(.plain)
        .disabled(session.autoKitLoading)
    }

    private func redrumRow(_ title: String, kit: String) -> some View {
        Button {
            Task { await session.applyRedrum(kit: kit) }
        } label: {
            row(title, icon: "arrow.triangle.2.circlepath",
                subtitle: kit == "self"
                    ? "Re-trigger this song's own cleaned kit"
                    : "This song's groove on that song's drums",
                busy: session.remixBusy == "redrum",
                checked: session.redrumActiveKit == kit)
        }
        .buttonStyle(.plain)
        .disabled(session.remixBusy == "redrum")
    }
}
