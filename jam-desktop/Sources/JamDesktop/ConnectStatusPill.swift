// ConnectStatusPill.swift
//
// Bridge connection indicator — parity with the web jam "paired"
// pill. Lives in the window toolbar so every view shows it.

import SwiftUI
import JamDesktopCore

struct ConnectStatusPill: View {
    let status: BridgeClient.Status

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .background(
            Capsule()
                .fill(JamTheme.surfaceElevated)
                .overlay(Capsule().strokeBorder(JamTheme.stroke))
        )
        .help(help)
    }

    private var color: Color {
        switch status {
        case .idle: return .gray
        case .connecting: return .yellow
        case .connected: return .green
        case .failed: return .red
        }
    }

    private var label: String {
        switch status {
        case .idle: return "Offline"
        case .connecting: return "Connecting…"
        case .connected(let peers):
            return peers > 1 ? "Paired (\(peers))" : "Session"
        case .failed: return "Bridge error"
        }
    }

    private var help: String {
        if case .failed(let message) = status { return message }
        return "Session bridge status"
    }
}
