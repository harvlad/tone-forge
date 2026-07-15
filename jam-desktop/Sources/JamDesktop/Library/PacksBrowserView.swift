// PacksBrowserView.swift
//
// Curated sample-pack browser (iOS parity P5): catalog list with
// download progress + cache state, and a 4×4 trigger grid for the
// active pack. Pads fire one-shot through the chop voice pool
// (SessionController.triggerPackPad), colored by the pad's manifest
// hint or its family palette (iOS ModeCoordinator colors).

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct PacksBrowserView: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    private var packs: PacksModel { session.packs }

    var body: some View {
        VStack(spacing: 0) {
            // Close button row
            HStack {
                Spacer()
                Button { dismiss() } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.title3)
                        .foregroundStyle(.secondary)
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.escape, modifiers: [])
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)

            HSplitView {
                catalogList
                    .frame(minWidth: 280, idealWidth: 320)
                activePane
                    .frame(minWidth: 320)
            }
        }
        .frame(minWidth: 660, minHeight: 480)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .task {
            await packs.loadCatalog(baseURL: model.backendBaseURL)
        }
    }

    // MARK: - Catalog

    private var catalogList: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Sample Packs")
                    .font(.title3.bold())
                Spacer()
                if packs.isLoading {
                    ProgressView().controlSize(.small)
                }
            }
            .padding([.top, .horizontal], 16)

            if let error = packs.errorMessage {
                Text(error)
                    .font(.caption)
                    .foregroundStyle(JamTheme.error)
                    .padding(.horizontal, 16)
            }

            List(packs.entries) { entry in
                catalogRow(entry)
                    .listRowBackground(Color.clear)
            }
            .scrollContentBackground(.hidden)
        }
    }

    @ViewBuilder
    private func catalogRow(_ entry: SamplePackCatalogEntry) -> some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 4)
                .fill(familyColor(entry.family))
                .frame(width: 10, height: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.name)
                    .font(.callout.weight(.medium))
                Text("\(entry.family.rawValue.capitalized) · \(entry.padCount) pads")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            rowAction(entry)
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
        .onTapGesture {
            handleRowTap(entry)
        }
    }

    private func handleRowTap(_ entry: SamplePackCatalogEntry) {
        // Skip if downloading
        guard packs.downloading[entry.packId] == nil else { return }

        if packs.isCached(entry.packId) {
            packs.activate(packId: entry.packId)
        } else {
            packs.download(baseURL: model.backendBaseURL, packId: entry.packId)
        }
    }

    @ViewBuilder
    private func rowAction(_ entry: SamplePackCatalogEntry) -> some View {
        if let progress = packs.downloading[entry.packId] {
            ProgressView(
                value: Double(progress.padsCompleted),
                total: Double(max(1, progress.padsTotal))
            )
            .frame(width: 70)
        } else if packs.isCached(entry.packId) {
            Button(
                packs.activePack?.pack.packId == entry.packId
                    ? "Active" : "Load"
            ) {
                packs.activate(packId: entry.packId)
            }
            .disabled(packs.activePack?.pack.packId == entry.packId)
        } else {
            Button {
                packs.download(
                    baseURL: model.backendBaseURL, packId: entry.packId)
            } label: {
                Image(systemName: "arrow.down.circle")
            }
            .buttonStyle(.plain)
            .help("Download pack")
        }
    }

    // MARK: - Active pack

    @ViewBuilder
    private var activePane: some View {
        if let resolved = packs.activePack {
            VStack(spacing: 12) {
                HStack {
                    Text(resolved.pack.name)
                        .font(.title3.bold())
                    Spacer()
                    Text(resolved.pack.family.rawValue.capitalized)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                triggerGrid(resolved)
                    .aspectRatio(1, contentMode: .fit)
                Spacer(minLength: 0)
            }
            .padding(16)
        } else {
            VStack(spacing: 8) {
                Image(systemName: "square.grid.2x2")
                    .font(.largeTitle)
                    .foregroundStyle(.secondary)
                Text("Download and load a pack to play its pads.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private func triggerGrid(_ resolved: ResolvedSamplePack) -> some View {
        GeometryReader { geo in
            let spacing: CGFloat = 8
            let side = (min(geo.size.width, geo.size.height) - spacing * 3) / 4
            VStack(spacing: spacing) {
                // padIdx 0 = bottom-left, iOS grid convention.
                ForEach((0..<4).reversed(), id: \.self) { row in
                    HStack(spacing: spacing) {
                        ForEach(0..<4, id: \.self) { col in
                            let idx = row * 4 + col
                            PackPadTile(
                                pad: resolved.pack.pads
                                    .first(where: { $0.padIdx == idx }),
                                playable: resolved.padFileURLs[idx] != nil,
                                onDown: {
                                    session.triggerPackPad(
                                        packId: resolved.pack.packId,
                                        padIdx: idx
                                    )
                                }
                            )
                            .frame(width: side, height: side)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }
}

/// iOS ModeCoordinator family palette.
func familyColor(_ family: SampleFamily) -> Color {
    let hex: UInt32
    switch family {
    case .pads: hex = 0xA855F7
    case .percussion: hex = 0xF97316
    case .textures: hex = 0x14B8A6
    case .stabs: hex = 0xEC4899
    case .bass: hex = 0x3B82F6
    case .fx: hex = 0xEAB308
    case .vocals: hex = 0x22C55E
    case .mixed: hex = 0x9CA3AF
    }
    return Color(
        red: Double((hex >> 16) & 0xFF) / 255.0,
        green: Double((hex >> 8) & 0xFF) / 255.0,
        blue: Double(hex & 0xFF) / 255.0
    )
}

/// One trigger pad: manifest colorHint (or family color), name label,
/// press flash. One-shot — no release action in v1.
private struct PackPadTile: View {
    let pad: SamplePad?
    let playable: Bool
    let onDown: () -> Void

    @State private var flashing = false

    var body: some View {
        RoundedRectangle(cornerRadius: 8)
            .fill(fill)
            .overlay(
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.white.opacity(0.08), lineWidth: 1)
            )
            .overlay(alignment: .bottomLeading) {
                if let pad {
                    Text(pad.name)
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.white.opacity(0.85))
                        .padding(4)
                        .lineLimit(2)
                }
            }
            .overlay {
                if flashing {
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.white.opacity(0.3))
                }
            }
            .onTapGesture {
                guard playable else { return }
                onDown()
                flashing = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) {
                    flashing = false
                }
            }
    }

    private var fill: Color {
        guard let pad, playable else { return Color(white: 0.14) }
        if let hintHex = pad.colorHint,
           let hex = UInt32(
               hintHex.trimmingCharacters(in: CharacterSet(charactersIn: "#")),
               radix: 16
           ) {
            return Color(
                red: Double((hex >> 16) & 0xFF) / 255.0,
                green: Double((hex >> 8) & 0xFF) / 255.0,
                blue: Double(hex & 0xFF) / 255.0
            ).opacity(0.7)
        }
        return familyColor(pad.family).opacity(0.6)
    }
}
