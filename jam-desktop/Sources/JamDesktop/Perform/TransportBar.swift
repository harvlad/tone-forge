// TransportBar.swift
//
// Play/pause + scrub + tempo + click + loop, driving the Core
// TransportController. Scrubbing holds a local value while dragging
// and seeks once on release so the audio layer isn't spammed with
// per-pixel seeks (same behavior as the web slider).

import SwiftUI
import JamDesktopCore

struct TransportBar: View {
    @EnvironmentObject private var session: SessionController

    @State private var isScrubbing = false
    @State private var scrubSeconds: Double = 0

    private var transport: TransportController { session.transport }

    var body: some View {
        VStack(spacing: 8) {
            positionRow
            controlsRow
        }
        .padding(12)
        .jamCard()
    }

    // MARK: - Position

    private var positionRow: some View {
        HStack(spacing: 8) {
            Text(timeString(displayedSeconds))
                .font(.caption.monospacedDigit())
                .frame(width: 44, alignment: .trailing)

            Slider(
                value: Binding(
                    get: { displayedSeconds },
                    set: { scrubSeconds = $0 }
                ),
                in: 0...max(1, transport.durationSeconds),
                onEditingChanged: { editing in
                    if editing {
                        scrubSeconds = transport.positionSeconds
                        isScrubbing = true
                    } else {
                        isScrubbing = false
                        transport.seek(to: scrubSeconds)
                    }
                }
            )

            Text(timeString(transport.durationSeconds))
                .font(.caption.monospacedDigit())
                .frame(width: 44, alignment: .leading)
        }
    }

    private var displayedSeconds: Double {
        isScrubbing ? scrubSeconds : transport.positionSeconds
    }

    // MARK: - Controls

    private var controlsRow: some View {
        HStack(spacing: 16) {
            Button {
                transport.togglePlay()
            } label: {
                Image(systemName: transport.isPlaying ? "pause.fill" : "play.fill")
                    .font(.title2)
                    .frame(width: 32)
            }
            .keyboardShortcut(.space, modifiers: [])
            .help(transport.isPlaying ? "Pause" : "Play")

            Divider().frame(height: 20)

            tempoControls

            Divider().frame(height: 20)

            Toggle(isOn: Binding(
                get: { session.clickEnabled },
                set: { session.clickEnabled = $0 }
            )) {
                Image(systemName: "metronome")
            }
            .toggleStyle(.button)
            .help("Click track")

            Divider().frame(height: 20)

            loopControls

            Divider().frame(height: 20)

            RecordToggle(recorder: session.recording.recorder)

            Spacer()
        }
    }

    private var tempoControls: some View {
        HStack(spacing: 6) {
            Image(systemName: "tortoise")
                .foregroundStyle(.secondary)
            Slider(
                value: Binding(
                    get: { transport.tempoPct },
                    set: { transport.setTempo($0) }
                ),
                in: TransportController.tempoRange
            )
            .frame(width: 120)
            Text("\(Int((transport.tempoPct * 100).rounded()))%")
                .font(.caption.monospacedDigit())
                .frame(width: 36, alignment: .leading)
        }
        .help("Practice speed (pitch-preserving)")
    }

    private var loopControls: some View {
        HStack(spacing: 6) {
            Button("Loop In") {
                let inPoint = transport.positionSeconds
                let outPoint = transport.loop?.outSeconds
                    ?? max(inPoint + 1, transport.durationSeconds)
                transport.setLoop(LoopRegion(inSeconds: inPoint, outSeconds: outPoint))
            }

            Button("Loop Out") {
                let outPoint = transport.positionSeconds
                let inPoint = transport.loop?.inSeconds ?? 0
                transport.setLoop(LoopRegion(inSeconds: inPoint, outSeconds: outPoint))
            }

            if let loop = transport.loop {
                Text("\(timeString(loop.inSeconds))–\(timeString(loop.outSeconds))")
                    .font(.caption.monospacedDigit())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.accentColor.opacity(0.2), in: Capsule())

                Button {
                    transport.clearLoop()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                }
                .buttonStyle(.plain)
                .help("Clear loop")
            }
        }
    }

    private func timeString(_ s: Double) -> String {
        let total = max(0, Int(s.rounded(.down)))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}
