// TrackRowView.swift
//
// A single track row in the pattern editor (D-023 Phase 4). Shows:
//   - Chop preview thumbnail + name (tappable for selection)
//   - Mute/solo buttons
//   - Step cells (tap to toggle, vertical drag for velocity)
//
// The row uses HStack with fixed-width label and flexible step grid.
// Step cells highlight on the current playhead position.

import SwiftUI
import ToneForgeEngine

struct TrackRowView: View {
    let track: SequencerTrack
    let trackIndex: Int
    let currentStep: Int
    let isPlaying: Bool
    let onToggleStep: (Int) -> Void
    let onSetVelocity: (Int, Float) -> Void
    let onToggleMute: () -> Void
    let onToggleSolo: () -> Void
    let onPreview: () -> Void
    /// Reassign this track's drum role (Beat Capture correction). Only
    /// offered when the track resolves to a kit pad.
    var onSetRole: ((DrumRole) -> Void)? = nil

    private let labelWidth: CGFloat = 80
    private let controlsWidth: CGFloat = 50

    var body: some View {
        HStack(spacing: 8) {
            // Track label + controls
            trackLabel

            // Step grid
            stepGrid
        }
        .padding(.vertical, 4)
    }

    // MARK: - Track Label

    private var trackLabel: some View {
        HStack(spacing: 6) {
            // Preview button / chop indicator
            Button(action: onPreview) {
                ZStack {
                    RoundedRectangle(cornerRadius: 6)
                        .fill(trackColor.opacity(0.2))
                        .frame(width: 32, height: 32)

                    Image(systemName: track.chopRef.iconName)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(trackColor)
                }
            }

            // Track name
            VStack(alignment: .leading, spacing: 2) {
                Text(track.name ?? track.chopRef.displayLabel)
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(1)

                // Mute/Solo indicators
                HStack(spacing: 4) {
                    muteButton
                    soloButton
                }
            }
        }
        .frame(width: labelWidth, alignment: .leading)
        .contentShape(Rectangle())
        .modifier(RoleCorrectionMenu(role: currentRole, onSetRole: onSetRole))
    }

    /// Drum role this track resolves to, when it's a kit pad. Drives the
    /// long-press correction menu.
    private var currentRole: DrumRole? {
        onSetRole == nil ? nil : BeatKit.role(for: track.chopRef)
    }

    private var muteButton: some View {
        Button(action: onToggleMute) {
            Text("M")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(track.isMuted ? .red : TFTheme.textSecondary)
                .frame(width: 18, height: 18)
                .background(
                    track.isMuted
                        ? Color.red.opacity(0.2)
                        : TFTheme.chipFill,
                    in: RoundedRectangle(cornerRadius: 4)
                )
        }
    }

    private var soloButton: some View {
        Button(action: onToggleSolo) {
            Text("S")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(track.isSoloed ? .yellow : TFTheme.textSecondary)
                .frame(width: 18, height: 18)
                .background(
                    track.isSoloed
                        ? Color.yellow.opacity(0.2)
                        : TFTheme.chipFill,
                    in: RoundedRectangle(cornerRadius: 4)
                )
        }
    }

    // MARK: - Step Grid

    private var stepGrid: some View {
        HStack(spacing: 2) {
            ForEach(0..<track.steps.count, id: \.self) { stepIndex in
                StepCell(
                    step: track.steps[stepIndex],
                    isPlayhead: isPlaying && stepIndex == currentStep,
                    isDownbeat: stepIndex % 4 == 0,
                    color: trackColor,
                    onTap: { onToggleStep(stepIndex) },
                    onVelocityChange: { velocity in
                        onSetVelocity(stepIndex, velocity)
                    }
                )
            }
        }
    }

    // MARK: - Helpers

    private var trackColor: Color {
        // Use accent color for now; could be based on chop type
        switch track.chopRef {
        case .bundleChop:
            return .orange
        case .packPad:
            return .accentColor
        case .localSample:
            return .green
        case .customURL:
            return .purple
        case .sequence:
            return .teal
        case .synthChord:
            return .pink
        }
    }
}

// MARK: - Role Correction Menu

/// Long-press context menu that reassigns a Beat Capture track's drum
/// role. A no-op passthrough when the track isn't a kit pad (`role` nil)
/// or no handler is wired, so non-drum tracks get no spurious menu.
private struct RoleCorrectionMenu: ViewModifier {
    let role: DrumRole?
    let onSetRole: ((DrumRole) -> Void)?

    func body(content: Content) -> some View {
        if let role, let onSetRole {
            content.contextMenu {
                Section("Drum role") {
                    ForEach(DrumRole.allCases, id: \.self) { candidate in
                        Button {
                            onSetRole(candidate)
                        } label: {
                            if candidate == role {
                                Label(candidate.displayName, systemImage: "checkmark")
                            } else {
                                Text(candidate.displayName)
                            }
                        }
                    }
                }
            }
        } else {
            content
        }
    }
}

// MARK: - Step Cell

private struct StepCell: View {
    let step: SequencerStep
    let isPlayhead: Bool
    let isDownbeat: Bool
    let color: Color
    let onTap: () -> Void
    let onVelocityChange: (Float) -> Void

    @State private var dragOffset: CGFloat = 0

    var body: some View {
        ZStack {
            // Background
            RoundedRectangle(cornerRadius: 4)
                .fill(backgroundColor)

            // Velocity indicator (bar from bottom)
            if step.isActive {
                GeometryReader { geo in
                    RoundedRectangle(cornerRadius: 2)
                        .fill(color)
                        .frame(
                            width: geo.size.width - 4,
                            height: max(4, geo.size.height * CGFloat(step.velocity) - 4)
                        )
                        .position(
                            x: geo.size.width / 2,
                            y: geo.size.height - (geo.size.height * CGFloat(step.velocity) / 2)
                        )
                }
            }

            // Playhead highlight
            if isPlayhead {
                RoundedRectangle(cornerRadius: 4)
                    .stroke(Color.white, lineWidth: 2)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .aspectRatio(1, contentMode: .fit)
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
        .gesture(velocityDrag)
    }

    private var backgroundColor: Color {
        if step.isActive {
            return color.opacity(0.3)
        }
        if isDownbeat {
            return TFTheme.surfaceElevated
        }
        return TFTheme.chipFill
    }

    private var velocityDrag: some Gesture {
        DragGesture(minimumDistance: 5)
            .onChanged { value in
                // Vertical drag: up increases velocity, down decreases
                let delta = -value.translation.height / 100
                let newVelocity = max(0.1, min(1.0, Float(step.velocity) + Float(delta)))
                onVelocityChange(newVelocity)
            }
    }
}

// MARK: - ChopReference Icon Extension

extension ChopReference {
    var iconName: String {
        switch self {
        case .bundleChop:
            return "waveform"
        case .packPad:
            return "square.grid.2x2"
        case .localSample:
            return "mic.fill"
        case .customURL:
            return "doc.fill"
        case .sequence:
            return "square.grid.3x3.fill"
        case .synthChord:
            return "pianokeys"
        }
    }
}

// MARK: - Preview

#if DEBUG
struct TrackRowView_Previews: PreviewProvider {
    static var previews: some View {
        VStack(spacing: 8) {
            TrackRowView(
                track: SequencerTrack(
                    chopRef: .packPad(packId: "starter", padIdx: 51),
                    stepCount: 16,
                    name: "Kick"
                ),
                trackIndex: 0,
                currentStep: 4,
                isPlaying: true,
                onToggleStep: { _ in },
                onSetVelocity: { _, _ in },
                onToggleMute: {},
                onToggleSolo: {},
                onPreview: {}
            )

            TrackRowView(
                track: SequencerTrack(
                    chopRef: .bundleChop(presetKey: "harmonic", chopIndex: 2, resolvedId: nil),
                    stepCount: 16,
                    isMuted: true,
                    name: "Chord Stab"
                ),
                trackIndex: 1,
                currentStep: 4,
                isPlaying: true,
                onToggleStep: { _ in },
                onSetVelocity: { _, _ in },
                onToggleMute: {},
                onToggleSolo: {},
                onPreview: {}
            )
        }
        .padding()
        .frame(height: 150)
        .background(TFTheme.background)
        .preferredColorScheme(.dark)
    }
}
#endif
