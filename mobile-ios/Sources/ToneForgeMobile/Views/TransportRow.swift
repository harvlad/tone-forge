// TransportRow.swift
//
// Shared bottom transport for the performance tabs (D-022): previous
// / play-pause / next plus a position readout. Prev/next skip by
// SECTION when the loaded song has section analysis; without one
// (sketch mode, or songs whose analysis found no sections) they fall
// back to ±5 s. The waveform scrubber moved into the Section
// Overview sheet — section skips + section chips cover in-tab
// navigation.

import SwiftUI
import ToneForgeEngine

struct TransportRow: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        HStack(spacing: 14) {
            Button {
                skipBackward()
            } label: {
                Image(systemName: "backward.end.fill").font(.title3)
            }
            .accessibilityLabel("Previous section")

            Button {
                Self.noAnim { appState.togglePlayPause() }
            } label: {
                Image(systemName: appState.isPlaying
                      ? "pause.circle.fill" : "play.circle.fill")
                    .font(.system(size: 38))
            }
            .accessibilityLabel(appState.isPlaying ? "Pause" : "Play")

            Button {
                skipForward()
            } label: {
                Image(systemName: "forward.end.fill").font(.title3)
            }
            .accessibilityLabel("Next section")

            // Full transport stop (halt + rewind + stop samples)
            stopButton

            // Record toggle
            RecordToggle()
                .fixedSize(horizontal: true, vertical: false)

            Spacer()

            Text(readout)
                .font(TFTheme.readout)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .padding(.horizontal, 16)
    }

    // MARK: - Skip targets

    private var sectionStarts: [Double] {
        appState.currentBundle?.timeline.sections.map(\.start) ?? []
    }

    private func skipForward() {
        let now = appState.songSeconds
        if let next = sectionStarts.first(where: { $0 > now + 0.05 }) {
            appState.seek(to: next)
        } else {
            appState.seek(to: now + 5)
        }
    }

    private func skipBackward() {
        let now = appState.songSeconds
        if sectionStarts.isEmpty {
            appState.seek(to: max(0, now - 5))
        } else {
            // >1 s into a section returns to its start; otherwise
            // jump to the previous section (music-player convention).
            let prior = sectionStarts.filter { $0 < now - 1.0 }
            appState.seek(to: prior.last ?? 0)
        }
    }

    // MARK: - Readout

    private var readout: String {
        let elapsed = Self.formatTime(appState.songSeconds)
        if let dur = appState.currentBundle?.meta.durationSec, dur > 0 {
            return "\(elapsed) / \(Self.formatTime(dur))"
        }
        return elapsed
    }

    private static func formatTime(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    // MARK: - Stop All

    private var stopButton: some View {
        // Full transport Stop (vs the play/pause toggle): halt the song,
        // rewind to the top, and panic-stop any ringing sample voices.
        // Tint red while samples are ringing.
        let hasRinging = !appState.ringingPadKeys.isEmpty
        return Button {
            Self.noAnim {
                appState.stopAllSamplePads()
                if appState.isPlaying { appState.togglePlayPause() }
                appState.seek(to: 0)
            }
        } label: {
            Image(systemName: "stop.fill")
                .font(.title3)
                .foregroundStyle(hasRinging ? Color.red : TFTheme.textSecondary)
        }
        .accessibilityLabel("Stop")
    }

    /// Run a transport mutation with implicit animations suppressed, so
    /// a play/pause/stop state flip can't drive a SwiftUI transition that
    /// scales/zooms the surface (same guard openSong uses on tab entry).
    private static func noAnim(_ body: () -> Void) {
        var tx = Transaction()
        tx.disablesAnimations = true
        withTransaction(tx, body)
    }
}
