// LayerSlotToggle.swift
//
// Compact A/B slot toggle for the Contribute surface (D-022 Phase 7).
// Shows the active slot label (A or B) with two content dots indicating
// which slots have takes assigned. Tap cycles the active slot. Disabled
// while recording.

import SwiftUI
import ToneForgeEngine

struct LayerSlotToggle: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        Button {
            appState.toggleActiveSlot()
        } label: {
            HStack(spacing: 6) {
                // Active slot label
                Text(appState.layerSlots.active.rawValue)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(isRecording ? .secondary : .primary)

                // Content dots showing which slots have takes
                HStack(spacing: 4) {
                    slotDot(slot: .a)
                    slotDot(slot: .b)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(TFTheme.chipFill)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(borderColor, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(isRecording)
        .accessibilityLabel("Layer slot \(appState.layerSlots.active.rawValue)")
        .accessibilityHint("Double tap to switch between layer A and B")
    }

    // MARK: - Helpers

    private var isRecording: Bool {
        appState.sessionRecorder.state != .idle
    }

    private var borderColor: Color {
        Color.white.opacity(0.08)
    }

    @ViewBuilder
    private func slotDot(slot: RecordingSlot) -> some View {
        let hasTake = appState.layerSlots.hasTake(slot)
        let isActive = appState.layerSlots.active == slot
        Circle()
            .fill(hasTake ? Color.accentColor : Color.secondary.opacity(0.3))
            .frame(width: 6, height: 6)
            .overlay(
                Circle()
                    .stroke(isActive ? Color.accentColor : .clear, lineWidth: 1.5)
                    .frame(width: 10, height: 10)
            )
    }
}
