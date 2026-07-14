// SidebarView.swift
//
// Left navigation sidebar matching the web jamn.app theme:
// logo + branding, contribute mode buttons, recent songs list.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

/// Contribute mode for the sidebar selection.
enum ContributeMode: String, CaseIterable, Identifiable {
    case voice
    case beat
    case sample

    var id: String { rawValue }

    var label: String {
        switch self {
        case .voice: "Voice"
        case .beat: "Beat"
        case .sample: "Sample"
        }
    }

    var icon: String {
        switch self {
        case .voice: "mic"
        case .beat: "square.grid.2x2"
        case .sample: "waveform"
        }
    }
}

struct SidebarView: View {
    @EnvironmentObject private var history: HistoryModel
    @EnvironmentObject private var model: AppModel
    @Binding var selectedMode: ContributeMode

    /// Called when user taps a song in the recent list.
    var onSongTap: (HistoryEntry) -> Void = { _ in }
    /// Called when Voice mode is tapped.
    var onVoiceTap: () -> Void = {}
    /// Called when Beat mode is tapped.
    var onBeatTap: () -> Void = {}
    /// Called when Sample mode is tapped.
    var onSampleTap: () -> Void = {}
    /// Tool callbacks
    var onLaunchpadTap: () -> Void = {}
    var onSequencerTap: () -> Void = {}
    var onRecordingsTap: () -> Void = {}
    var onJamPadsTap: () -> Void = {}
    var onPacksTap: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Logo and branding
            HStack(spacing: 10) {
                SidebarLogo()
                    .frame(width: 36, height: 36)
                VStack(alignment: .leading, spacing: 2) {
                    Text("JamN")
                        .font(.system(size: 20, weight: .bold))
                        .foregroundStyle(.white)
                    Text("jamn.app")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 20)
            .padding(.bottom, 24)

            // Contribute section
            VStack(alignment: .leading, spacing: 8) {
                Text("CONTRIBUTE")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)

                ForEach(ContributeMode.allCases) { mode in
                    ContributeModeButton(
                        mode: mode,
                        isSelected: selectedMode == mode
                    ) {
                        selectedMode = mode
                        handleModeTap(mode)
                    }
                }
            }
            .padding(.bottom, 24)

            // Tools section
            VStack(alignment: .leading, spacing: 8) {
                Text("TOOLS")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)

                ToolButton(icon: "square.grid.3x3.fill", label: "Launchpad", action: onLaunchpadTap)
                ToolButton(icon: "squares.below.rectangle", label: "Sequencer", action: onSequencerTap)
                ToolButton(icon: "record.circle", label: "Recordings", action: onRecordingsTap)
                ToolButton(icon: "pianokeys", label: "Jam Pads", action: onJamPadsTap)
                ToolButton(icon: "square.grid.2x2", label: "Packs", action: onPacksTap)
            }
            .padding(.bottom, 24)

            // Recent songs section
            VStack(alignment: .leading, spacing: 8) {
                Text("RECENT SONGS")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 16)

                ScrollView {
                    LazyVStack(spacing: 4) {
                        ForEach(history.entries.prefix(6)) { entry in
                            RecentSongRow(entry: entry) {
                                onSongTap(entry)
                            }
                        }
                    }
                }
            }

            Spacer()

            // View all songs link
            if !history.entries.isEmpty {
                Button {
                    // Navigate to library
                } label: {
                    HStack(spacing: 4) {
                        Text("View all songs")
                            .font(.caption)
                        Image(systemName: "chevron.right")
                            .font(.caption2)
                    }
                    .foregroundStyle(JamTheme.accent)
                }
                .buttonStyle(.plain)
                .padding(.horizontal, 16)
                .padding(.bottom, 20)
            }
        }
        .frame(width: 240)
        .background(Color(white: 0.08))
    }

    private func handleModeTap(_ mode: ContributeMode) {
        switch mode {
        case .voice:
            onVoiceTap()
        case .beat:
            onBeatTap()
        case .sample:
            onSampleTap()
        }
    }
}

// MARK: - Sidebar Logo

private struct SidebarLogo: View {
    private let bars: [CGFloat] = [0.5, 0.8, 1.0, 0.7, 0.5]

    var body: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let barWidth = geo.size.width * 0.12
            let spacing = geo.size.width * 0.06
            HStack(alignment: .center, spacing: spacing) {
                ForEach(bars.indices, id: \.self) { i in
                    Capsule()
                        .frame(width: barWidth, height: h * bars[i])
                }
            }
            .frame(width: geo.size.width, height: h, alignment: .center)
            .foregroundStyle(
                LinearGradient(
                    colors: [Color(hex: 0xA855F7), Color(hex: 0x6366F1)],
                    startPoint: .top,
                    endPoint: .bottom
                )
            )
        }
    }
}

// MARK: - Contribute Mode Button

private struct ContributeModeButton: View {
    let mode: ContributeMode
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: mode.icon)
                    .font(.body)
                    .frame(width: 20)
                Text(mode.label)
                    .font(.body)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(
                RoundedRectangle(cornerRadius: 8)
                    .fill(isSelected ? JamTheme.accent.opacity(0.2) : .clear)
            )
            .foregroundStyle(isSelected ? JamTheme.accent : .white)
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
    }
}

// MARK: - Tool Button

private struct ToolButton: View {
    let icon: String
    let label: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.body)
                    .frame(width: 20)
                Text(label)
                    .font(.body)
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .foregroundStyle(.white.opacity(0.8))
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 8)
    }
}

// MARK: - Recent Song Row

private struct RecentSongRow: View {
    let entry: HistoryEntry
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ArtworkImage(
                    analysisId: entry.id,
                    artist: entry.artist,
                    title: entry.name,
                    size: 36
                )

                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.name ?? "Untitled")
                        .font(.caption)
                        .fontWeight(.medium)
                        .foregroundStyle(.white)
                        .lineLimit(1)
                    Text("\(entry.artist ?? "Unknown") • \(formatDuration(entry.duration))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func formatDuration(_ seconds: Double?) -> String {
        guard let s = seconds else { return "--:--" }
        let mins = Int(s) / 60
        let secs = Int(s) % 60
        return String(format: "%d:%02d", mins, secs)
    }
}
