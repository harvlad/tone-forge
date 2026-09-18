// RecordToggle.swift
//
// The recorder pill, in one of two modes (the mode picks WHAT gets
// recorded, not how the pill looks):
//
//   .sessionEvents (default) — arms the P6 SessionCaptureRecorder
//     (D-015; the legacy layer recorder is frozen read-only). Captures
//     replayable ContributionEvents, no audio. Used by the sequencer
//     and Contribute sketches. States: idle / armed / count-in /
//     recording (live event count). Tap: arm → stop-and-save; long-
//     press: discard.
//
//   .audioOutput — drives the OutputRecorder, which taps the engine's
//     master bus and writes the actual mixed SOUND (song + pads + FX)
//     to an .m4a in Library → Recordings. The bottom transport uses
//     this. States: idle / recording (live elapsed). Tap: start →
//     stop-and-save. There is no "arm" or "discard" — the file is the
//     take, kept the moment you stop.
//
// The two are deliberately independent: repointing the transport pill
// to audio must not disturb the event recorder the sequencer relies on.

import SwiftUI
import ToneForgeEngine

struct RecordToggle: View {
    @EnvironmentObject private var appState: AppState
    @State private var pulse: Bool = false

    /// What this pill records. The bottom transport captures audio; the
    /// sequencer (and any other instance) captures replayable events.
    enum RecordMode { case sessionEvents, audioOutput }
    var mode: RecordMode = .sessionEvents

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
            // Audio mode is glanceable, not wordy: idle = just the red
            // dot; recording = a live level meter + elapsed, no "Record"
            // label (user: "the record red dot is enough").
            if mode == .audioOutput {
                if phase == .recording {
                    liveLevelMeter
                    Text(Self.timeLabel(appState.outputRecorder.elapsedSec))
                        .font(.caption.weight(.medium).monospacedDigit())
                        .foregroundStyle(Color.red)
                }
            } else if !(compact && phase == .idle) {
                Text(label)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(labelColor)
                    .lineLimit(1)
                    // Shrink before overflowing — a fixed-size label
                    // pushed the whole transport row off-screen when
                    // the armed copy got long.
                    .minimumScaleFactor(0.6)
            }
            if !compact && mode != .audioOutput {
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
        .accessibilityElement(children: .ignore)
        .accessibilityAddTraits(.isButton)
        .accessibilityLabel(accessibilityLabelText)
        .accessibilityHint(accessibilityHintText)
        .onAppear { pulse = true }
        .animation(
            phase == .recording
                ? .easeInOut(duration: 0.6).repeatForever(autoreverses: true)
                : .default,
            value: pulse
        )
    }

    // MARK: - Sub-views

    /// Rolling realtime level bars while audio-recording — the visible
    /// proof capture is alive. Fed by OutputRecorder's published `peak`
    /// (per tap buffer); newest sample on the right.
    @State private var levels: [Float] = []

    private var liveLevelMeter: some View {
        HStack(alignment: .center, spacing: 2) {
            ForEach(Array(levels.enumerated()), id: \.offset) { _, level in
                Capsule()
                    .fill(Color.red.opacity(0.85))
                    .frame(width: 3,
                           height: max(3, CGFloat(min(level, 1)) * 22))
            }
        }
        .frame(height: 24)
        .onReceive(appState.outputRecorder.$peak) { peak in
            levels.append(peak)
            if levels.count > 18 { levels.removeFirst(levels.count - 18) }
        }
        .onDisappear { levels = [] }
    }

    @ViewBuilder
    private var dot: some View {
        let color = dotColor
        switch phase {
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

    /// Rendering-only three-state abstraction over whichever recorder
    /// this mode drives. Audio mode has no `.armed` (capture starts
    /// immediately), so it only ever reports idle/recording.
    private enum Phase { case idle, armed, recording }

    private var phase: Phase {
        switch mode {
        case .audioOutput:
            return appState.outputRecorder.state == .recording ? .recording : .idle
        case .sessionEvents:
            switch appState.sessionRecorder.state {
            case .idle:      return .idle
            case .armed:     return .armed
            case .recording: return .recording
            }
        }
    }

    private var hasBundle: Bool { appState.currentBundle != nil }

    /// Sketch count-in window: transport is running the negative lead
    /// bar of an armed take. Uses the published `songSeconds` mirror
    /// (30 fps tick) so the label live-updates. Event mode only — audio
    /// capture has no count-in.
    private var isCountingIn: Bool {
        mode == .sessionEvents && !hasBundle
            && appState.sessionRecorder.state != .idle
            && appState.songSeconds < 0
    }

    private var accessibilityLabelText: String {
        if mode == .audioOutput {
            switch appState.outputRecorder.state {
            case .idle: return "Record session audio"
            case .recording:
                return "Recording session audio, "
                    + "\(Int(appState.outputRecorder.elapsedSec)) seconds"
            }
        }
        if isCountingIn { return "Recording count-in" }
        switch appState.sessionRecorder.state {
        case .idle:      return "Record"
        case .armed:     return "Recorder armed, waiting for first note"
        case .recording:
            return "Recording, \(appState.sessionRecorder.eventCount) events"
        }
    }

    private var accessibilityHintText: String {
        if mode == .audioOutput {
            return appState.outputRecorder.state == .idle
                ? "Records the session's audio to your Library"
                : "Stops and saves the recording"
        }
        return appState.sessionRecorder.state == .idle
            ? "Arms the recorder"
            : "Stops and saves. Long-press to discard."
    }

    private var label: String {
        if mode == .audioOutput {
            switch appState.outputRecorder.state {
            case .idle:      return "Record"
            case .recording: return "Rec \(Self.timeLabel(appState.outputRecorder.elapsedSec))"
            }
        }
        if isCountingIn { return "Count-in…" }
        switch appState.sessionRecorder.state {
        case .idle:      return "Record"
        case .armed:     return "Ready — start playing"
        case .recording: return "Rec · \(appState.sessionRecorder.eventCount) events"
        }
    }

    private var labelColor: Color {
        switch phase {
        case .idle:      return .primary
        case .armed:     return .secondary
        case .recording: return Color.red
        }
    }

    private var dotColor: Color {
        phase == .idle ? Color.red.opacity(0.85) : Color.red
    }

    private var borderColor: Color {
        switch phase {
        case .idle:      return Color.white.opacity(0.08)
        case .armed:     return Color.orange.opacity(0.55)
        case .recording: return Color.red.opacity(0.85)
        }
    }

    private static func timeLabel(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    // MARK: - Actions

    private func handleTap() {
        if mode == .audioOutput {
            switch appState.outputRecorder.state {
            case .idle:      appState.startOutputRecording()
            case .recording: appState.stopOutputRecording()
            }
            return
        }
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
        // Audio mode has no discard — the file is the take; a long-press
        // does nothing rather than risk dropping a good capture.
        guard mode == .sessionEvents else { return }
        guard appState.sessionRecorder.state != .idle else { return }
        appState.cancelSessionRecording()
        onStop?()
    }
}
