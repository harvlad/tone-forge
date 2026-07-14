// RecordToggle.swift
//
// Compact recorder pill on the Play tab. Arms the P6
// SessionCaptureRecorder (D-015 — the legacy layer recorder is
// frozen read-only). Works in both contexts: song loaded (session
// keyed to the bundle's analysisId) and song-less sketch (synthetic
// tempo grid, optional 1-bar count-in). Shows one of four states:
//
//   idle       →  ● Record          (accented dot, tap to arm)
//   armed      →  ○ Ready…          (outlined, waiting for first event)
//   count-in   →  ○ Count-in…       (sketch only: lead bar clicking)
//   recording  →  ● Rec 12 events   (pulsing dot + live count)
//
// Tap semantics:
//   idle → armSessionRecording() (context resolved inside AppState)
//   armed / recording → stopAndSaveSessionRecording()
//   long-press while armed/recording → cancelSessionRecording()

import SwiftUI
import ToneForgeEngine

struct RecordToggle: View {
    @EnvironmentObject private var appState: AppState
    @State private var pulse: Bool = false

    /// When false, arming does not start the song transport. The
    /// sequencer sets this — its own clock drives playback, so
    /// pressing Record there should not kick off the song.
    var startsTransport: Bool = true

    /// Compact layout: no greedy `Spacer`, no inline error text, and
    /// the label collapses to just the dot. Used inside crowded
    /// transport rows (sequencer) where the full-width pill overflows.
    var compact: Bool = false

    /// Fired when the recorder arms (idle → armed). The sequencer uses
    /// this to start its own clock so the pattern is audible + captured.
    var onArm: (() -> Void)? = nil

    /// Fired when the recorder stops or cancels (→ idle). Sequencer
    /// stops its clock.
    var onStop: (() -> Void)? = nil

    var body: some View {
        HStack(spacing: 10) {
            dot
            if !(compact && appState.sessionRecorder.state == .idle) {
                Text(label)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(labelColor)
                    .lineLimit(1)
                    .fixedSize(horizontal: true, vertical: false)
            }
            if !compact {
                Spacer()
                if let error = appState.layerError {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(.orange)
                        .lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(Color(.sRGB, white: 0.10, opacity: 1))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(borderColor, lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onTapGesture { handleTap() }
        .onLongPressGesture(minimumDuration: 0.6) { handleLongPress() }
        .onAppear { pulse = true }
        .animation(
            appState.sessionRecorder.state == .recording
                ? .easeInOut(duration: 0.6).repeatForever(autoreverses: true)
                : .default,
            value: pulse
        )
    }

    // MARK: - Sub-views

    @ViewBuilder
    private var dot: some View {
        let color = dotColor
        switch appState.sessionRecorder.state {
        case .idle:
            Circle().fill(color).frame(width: 12, height: 12)
        case .armed:
            Circle().strokeBorder(color, lineWidth: 2).frame(width: 12, height: 12)
        case .recording:
            Circle().fill(color)
                .frame(width: 12, height: 12)
                .opacity(pulse ? 0.35 : 1.0)
        }
    }

    // MARK: - Derived state

    private var hasBundle: Bool { appState.currentBundle != nil }

    /// Sketch count-in window: transport is running the negative lead
    /// bar of an armed take. Uses the published `songSeconds` mirror
    /// (30 fps tick) so the label live-updates.
    private var isCountingIn: Bool {
        !hasBundle && appState.sessionRecorder.state != .idle
            && appState.songSeconds < 0
    }

    private var label: String {
        if isCountingIn { return "Count-in…" }
        switch appState.sessionRecorder.state {
        case .idle:      return "Record"
        case .armed:     return "Ready — start playing"
        case .recording: return "Rec · \(appState.sessionRecorder.eventCount) events"
        }
    }

    private var labelColor: Color {
        switch appState.sessionRecorder.state {
        case .idle:      return .primary
        case .armed:     return .secondary
        case .recording: return Color.red
        }
    }

    private var dotColor: Color {
        appState.sessionRecorder.state == .idle ? Color.red.opacity(0.85) : Color.red
    }

    private var borderColor: Color {
        switch appState.sessionRecorder.state {
        case .idle:      return Color.white.opacity(0.08)
        case .armed:     return Color.orange.opacity(0.55)
        case .recording: return Color.red.opacity(0.85)
        }
    }

    // MARK: - Actions

    private func handleTap() {
        switch appState.sessionRecorder.state {
        case .idle:
            appState.armSessionRecording(startTransport: startsTransport)
            onArm?()
        case .armed, .recording:
            appState.stopAndSaveSessionRecording()
            onStop?()
        }
    }

    private func handleLongPress() {
        guard appState.sessionRecorder.state != .idle else { return }
        appState.cancelSessionRecording()
        onStop?()
    }
}
