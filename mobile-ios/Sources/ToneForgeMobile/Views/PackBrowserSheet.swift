// PackBrowserSheet.swift
//
// Sheet for browsing available packs and selecting a sound to assign
// to an empty pad. Shows all packs with their pads, each with a preview
// button to audition before selecting.

import SwiftUI
import ToneForgeEngine

struct PackBrowserSheet: View {
    let targetRow: Int
    let targetCol: Int
    let onSelect: (String, Int) -> Void  // packId, padIdx
    let onPreview: (String, Int) -> Void // packId, padIdx

    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var searchText = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 16) {
                    ForEach(filteredPacks, id: \.packId) { pack in
                        PackSection(
                            pack: pack,
                            onSelect: { padIdx in
                                onSelect(pack.packId, padIdx)
                                dismiss()
                            },
                            onPreview: { padIdx in
                                onPreview(pack.packId, padIdx)
                            }
                        )
                    }

                    if filteredPacks.isEmpty {
                        emptyState
                    }
                }
                .padding()
            }
            .background(TFTheme.background)
            .navigationTitle("Add Sound")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
            .searchable(text: $searchText, prompt: "Search sounds")
        }
    }

    // MARK: - Data

    private var allPacks: [PackWithPads] {
        var packs: [PackWithPads] = []

        // Active sample pack
        if let active = appState.activeSamplePack {
            packs.append(PackWithPads(
                packId: active.pack.packId,
                displayName: active.pack.packId.replacingOccurrences(of: "-", with: " ").capitalized,
                pads: active.pack.pads.enumerated().map { idx, pad in
                    PadInfo(padIdx: 51 + idx, name: pad.name, colorHint: colorHintValue(pad.colorHint))
                }
            ))
        }

        // All carousel pages (other packs) - get from active pack since
        // resolvedPack requires PackPage object
        for page in appState.carouselPages {
            // Skip if we already have this pack
            if packs.contains(where: { $0.packId == page.id }) { continue }

            // Get pack data if available
            if let resolved = appState.resolvedPack(for: page) {
                packs.append(PackWithPads(
                    packId: resolved.pack.packId,
                    displayName: page.displayName,
                    pads: resolved.pack.pads.enumerated().map { idx, pad in
                        PadInfo(padIdx: 51 + idx, name: pad.name, colorHint: colorHintValue(pad.colorHint))
                    }
                ))
            }
        }

        return packs
    }

    /// Convert color hint string to UInt32.
    private func colorHintValue(_ hint: String?) -> UInt32 {
        guard let hint = hint,
              hint.hasPrefix("#"),
              let value = UInt32(hint.dropFirst(), radix: 16)
        else { return 0 }
        return value
    }

    private var filteredPacks: [PackWithPads] {
        guard !searchText.isEmpty else { return allPacks }
        return allPacks.compactMap { pack in
            let matchingPads = pack.pads.filter { pad in
                (pad.name ?? "").lowercased().contains(searchText.lowercased())
            }
            if matchingPads.isEmpty && !pack.displayName.lowercased().contains(searchText.lowercased()) {
                return nil
            }
            if matchingPads.isEmpty {
                return pack // Pack name matched
            }
            return PackWithPads(packId: pack.packId, displayName: pack.displayName, pads: matchingPads)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "magnifyingglass")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)

            Text("No sounds found")
                .font(.headline)
                .foregroundStyle(TFTheme.textPrimary)

            Text("Try a different search term")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 60)
    }
}

// MARK: - Pack Section

private struct PackSection: View {
    let pack: PackWithPads
    let onSelect: (Int) -> Void
    let onPreview: (Int) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            // Pack header
            HStack {
                Text(pack.displayName)
                    .font(.headline)
                    .foregroundStyle(TFTheme.textPrimary)

                Spacer()

                Text("\(pack.pads.count) sounds")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
            }

            // Pad list with preview buttons
            LazyVStack(spacing: 8) {
                ForEach(pack.pads, id: \.padIdx) { pad in
                    PadRow(
                        pad: pad,
                        onSelect: { onSelect(pad.padIdx) },
                        onPreview: { onPreview(pad.padIdx) }
                    )
                }
            }
        }
        .padding()
        .background(TFTheme.surface, in: RoundedRectangle(cornerRadius: 12))
    }
}

// MARK: - Pad Row

private struct PadRow: View {
    let pad: PadInfo
    let onSelect: () -> Void
    let onPreview: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            // Color indicator
            RoundedRectangle(cornerRadius: 4)
                .fill(color(fromHex: pad.colorHint))
                .frame(width: 4, height: 36)

            // Preview button
            Button(action: onPreview) {
                Image(systemName: "play.circle.fill")
                    .font(.title2)
                    .foregroundStyle(Color.accentColor)
            }
            .buttonStyle(.plain)

            // Name
            Text(pad.name ?? "Sound \(pad.padIdx)")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textPrimary)
                .lineLimit(1)

            Spacer()

            // Select button
            Button(action: onSelect) {
                Text("Add")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .background(Color.accentColor, in: Capsule())
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(TFTheme.chipFill, in: RoundedRectangle(cornerRadius: 10))
    }

    private func color(fromHex hex: UInt32) -> Color {
        guard hex != 0 else { return .gray }
        return Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

// MARK: - Data Types

private struct PackWithPads {
    let packId: String
    let displayName: String
    let pads: [PadInfo]
}

private struct PadInfo {
    let padIdx: Int
    let name: String?
    let colorHint: UInt32
}
