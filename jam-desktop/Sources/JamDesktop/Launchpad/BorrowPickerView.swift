// BorrowPickerView.swift
//
// "Add from another song" — the DJ cross-song sampling picker, promoted
// to a first-class action ON the Launchpad surface (it used to be buried
// in the Remix sheet's Borrow section). Pick a part (Beat / Bass / Chords
// / Melody), pick one of YOUR other analyzed songs, and its real loops —
// tempo-matched, and key-compatible for melodic parts — mount straight
// onto the Launchpad pads.
//
// Not a second code path: the part list drives the SAME
// SessionController.borrowCandidates / loadBorrowLoops the Remix sheet
// already used, so ranking, download and the pad-mount are shared. The
// only new part is Melody = stem "vocals" (harmonic-matched toplines),
// which the backend already serves.

import SwiftUI
import ToneForgeEngine

/// One selectable Borrow part. `stem` is the server's stem key; the title
/// is the musician-facing name (web parity: Beat/Bass/Chords/Melody).
struct BorrowPart: Identifiable, Equatable, Hashable {
    let title: String
    let stem: String
    var id: String { stem }

    /// Melodic parts get key + harmonic hints; drums are tempo-only.
    var isMelodic: Bool { stem != "drums" }

    static let all: [BorrowPart] = [
        .init(title: "Beat", stem: "drums"),
        .init(title: "Bass", stem: "bass"),
        .init(title: "Chords", stem: "other"),
        .init(title: "Melody", stem: "vocals"),
    ]
}

struct BorrowPickerView: View {
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @State private var part: BorrowPart = BorrowPart.all[0]
    @State private var candidates: [BorrowCandidate] = []
    @State private var loaded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label("Add from another song", systemImage: "square.stack.3d.up")
                    .font(.title3.weight(.semibold))
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(16)

            Divider()

            // Part selector — Beat / Bass / Chords / Melody (web parity).
            Picker("Part", selection: $part) {
                ForEach(BorrowPart.all) { p in
                    Text(p.title).tag(p)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .onChange(of: part) { _ in
                loaded = false
                Task { await load() }
            }

            candidateList
                .frame(minHeight: 260)

            if let msg = session.remixApplied {
                Divider()
                Text(msg)
                    .font(.callout)
                    .foregroundStyle(JamTheme.accent)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
            }
            if let err = session.remixError {
                Text(err)
                    .font(.caption)
                    .foregroundStyle(JamTheme.error)
                    .padding(.horizontal, 16)
                    .padding(.bottom, 8)
            }
        }
        .frame(width: 440, height: 520)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .task { await load() }
    }

    @ViewBuilder
    private var candidateList: some View {
        List {
            Section {
                if !loaded {
                    HStack {
                        ProgressView().controlSize(.small)
                        Text("Finding compatible loops…")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                } else if candidates.isEmpty {
                    Text(part.stem == "drums"
                         ? "Analyze more songs to borrow beats."
                         : "No harmonically compatible songs yet.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                ForEach(candidates) { c in
                    candidateRow(c)
                }
            } header: {
                Text(part.isMelodic
                     ? "Songs whose \(part.title.lowercased()) fits this song's key"
                     : "Songs whose beat matches this tempo")
            }
        }
        .listStyle(.inset)
    }

    private func candidateRow(_ c: BorrowCandidate) -> some View {
        Button {
            Task {
                await session.loadBorrowLoops(donorId: c.entryId, stem: part.stem)
                // Success drops the loops on the pads behind this sheet;
                // close so the Launchpad grid is immediately visible.
                if session.remixError == nil { dismiss() }
            }
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(c.name).lineLimit(1)
                    Text(part.isMelodic
                         ? "\(c.key ?? "?") · \(Int(c.tempo)) bpm"
                         : "\(Int(c.tempo)) bpm")
                        .font(.caption2).foregroundStyle(.secondary)
                }
                Spacer()
                if session.borrowBusyDonor == c.entryId {
                    ProgressView().controlSize(.small)
                } else if part.isMelodic, c.harmonic >= 0.9 {
                    Text("harmonizes").font(.caption2)
                        .foregroundStyle(JamTheme.accent)
                } else if part.isMelodic, c.harmonic >= 0.75 {
                    Text("fits").font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(session.borrowBusyDonor != nil)
    }

    private func load() async {
        candidates = await session.borrowCandidates(stem: part.stem)
        loaded = true
    }
}
