// RehearsalTransportBar.swift
//
// Rehearsal-specific transport: play/pause, loop-section toggle,
// speed presets (0.5x / 0.75x / 1x — web _REHEARSAL_SPEEDS), next
// section. Speed presets drive the same pitch-preserving tempo path
// as the Perform slider.

import SwiftUI
import JamDesktopCore

struct RehearsalTransportBar: View {
    @EnvironmentObject private var session: SessionController

    let rehearsal: RehearsalModel
    /// Re-applies loop + seek after selection-affecting actions.
    let onSelectionChanged: () -> Void

    var body: some View {
        HStack(spacing: 16) {
            Button {
                session.transport.togglePlay()
            } label: {
                Image(systemName: session.transport.isPlaying ? "pause.fill" : "play.fill")
                    .font(.title3)
                    .frame(width: 28)
            }
            .keyboardShortcut(.space, modifiers: [])

            Divider().frame(height: 20)

            Toggle(isOn: loopBinding) {
                Label("Loop section", systemImage: "repeat")
            }
            .toggleStyle(.button)

            Divider().frame(height: 20)

            speedPicker

            Spacer()

            Button("Next section") {
                rehearsal.selectNext()
                onSelectionChanged()
            }
        }
    }

    private var speedPicker: some View {
        Picker("Speed", selection: speedBinding) {
            ForEach(RehearsalModel.speeds, id: \.self) { speed in
                Text(speedLabel(speed)).tag(speed)
            }
        }
        .pickerStyle(.segmented)
        .frame(maxWidth: 200)
    }

    private func speedLabel(_ speed: Double) -> String {
        speed == 1.0 ? "1x" : String(format: "%.2gx", speed)
    }

    private var loopBinding: Binding<Bool> {
        Binding(
            get: { rehearsal.loopEnabled },
            set: {
                rehearsal.loopEnabled = $0
                onSelectionChanged()
            }
        )
    }

    private var speedBinding: Binding<Double> {
        Binding(
            get: { rehearsal.speed },
            set: {
                rehearsal.speed = $0
                session.transport.setTempo($0)
            }
        )
    }
}
