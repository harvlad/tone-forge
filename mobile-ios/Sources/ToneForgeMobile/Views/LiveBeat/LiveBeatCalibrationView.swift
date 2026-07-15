// LiveBeatCalibrationView.swift
//
// Step-through calibration flow for Live Beat. Guides the user through
// tapping each drum sound 5+ times to build a profile.

import SwiftUI
import ToneForgeEngine

struct LiveBeatCalibrationView: View {
    @ObservedObject var calibrator: LiveBeatCalibrator
    @ObservedObject var profileStore: LiveBeatProfileStore

    @Environment(\.dismiss) private var dismiss

    @State private var profileName = "New Profile"
    @State private var isStarted = false
    /// Guided tap-along (deterministic, reliable) vs manual free-tapping.
    @State private var useGuided = true

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                if !isStarted {
                    setupView
                } else if useGuided {
                    GuidedFlow(
                        guided: calibrator.guided,
                        engine: calibrator.guided.engine,
                        profileStore: profileStore,
                        profileName: profileName,
                        onRetry: { calibrator.startGuided(profileName: profileName) },
                        onDone: { dismiss() }
                    )
                } else {
                    calibrationView
                }
            }
            .padding()
            .navigationTitle("Calibrate")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        if useGuided { calibrator.cancelGuided() } else { calibrator.cancel() }
                        dismiss()
                    }
                }
            }
        }
    }

    // MARK: - Setup View

    private var setupView: some View {
        VStack(spacing: 24) {
            Image(systemName: "waveform.badge.mic")
                .font(.system(size: 60))
                .foregroundStyle(Color.accentColor)

            Text("Teach Your Sounds")
                .font(.title2.bold())

            Text(useGuided
                ? "Tap along to the beat for each drum sound. The metronome keeps time so your hits land cleanly — no need to watch a meter."
                : "You'll tap each drum sound 5+ times so Live Beat can learn your unique sounds.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            TextField("Profile Name", text: $profileName)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal)

            Toggle("Guided tap-along", isOn: $useGuided)
                .padding(.horizontal)

            VStack(alignment: .leading, spacing: 8) {
                Text("Sounds to calibrate:")
                    .font(.headline)

                ForEach(calibrator.rolesToCalibrate, id: \.self) { role in
                    HStack {
                        Image(systemName: "circle")
                            .foregroundStyle(.secondary)
                        Text(role.displayName)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding()
            .background(Color.gray.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))

            Spacer()

            Button {
                if useGuided {
                    calibrator.startGuided(profileName: profileName)
                } else {
                    calibrator.start(profileName: profileName)
                }
                isStarted = true
            } label: {
                Text("Start Calibration")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.accentColor)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    // MARK: - Calibration View

    @ViewBuilder
    private var calibrationView: some View {
        switch calibrator.step {
        case .idle:
            EmptyView()

        case let .waitingForHits(role, collected, target):
            waitingView(role: role, collected: collected, target: target)

        case .computing:
            ProgressView("Processing...")

        case .complete:
            completeView

        case let .failed(message):
            failedView(message: message)
        }
    }

    private func waitingView(role: DrumRole, collected: Int, target: Int) -> some View {
        VStack(spacing: 24) {
            // Progress
            HStack {
                ForEach(calibrator.rolesToCalibrate, id: \.self) { r in
                    Circle()
                        .fill(stepColor(for: r, current: role))
                        .frame(width: 12, height: 12)
                }
            }

            Spacer()

            // Icon
            Image(systemName: iconForRole(role))
                .font(.system(size: 80))
                .foregroundStyle(colorForRole(role))

            // Instruction
            Text("Tap your \(role.displayName.uppercased())")
                .font(.title.bold())

            Text("Hit \(target - collected) more times")
                .font(.headline)
                .foregroundStyle(.secondary)

            // Meter
            envelopeMeter

            // Hit counter
            HStack(spacing: 4) {
                ForEach(0..<target, id: \.self) { i in
                    Circle()
                        .fill(i < collected ? colorForRole(role) : Color.gray.opacity(0.3))
                        .frame(width: 16, height: 16)
                }
            }

            Spacer()

            // Skip button
            HStack {
                Button("Skip") {
                    calibrator.skipCurrentRole()
                }
                .buttonStyle(.bordered)

                if collected >= target {
                    Button("Next") {
                        calibrator.advanceToNextRole()
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
    }

    private var envelopeMeter: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.gray.opacity(0.2))

                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.green)
                    .frame(width: geo.size.width * CGFloat(min(1, calibrator.envelopeLevel * 3)))
                    .animation(.linear(duration: 0.05), value: calibrator.envelopeLevel)
            }
        }
        .frame(height: 16)
        .padding(.horizontal)
    }

    private var completeView: some View {
        VStack(spacing: 24) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.green)

            Text("Calibration Complete!")
                .font(.title2.bold())

            if let profile = calibrator.profile {
                Text("\(profile.templates.count) sounds calibrated")
                    .foregroundStyle(.secondary)
            }

            Spacer()

            Button {
                if let profile = calibrator.finalize() {
                    profileStore.save(profile)
                    profileStore.setActive(id: profile.id)
                }
                dismiss()
            } label: {
                Text("Save Profile")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.green)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private func failedView(message: String) -> some View {
        VStack(spacing: 24) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.red)

            Text("Calibration Failed")
                .font(.title2.bold())

            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Spacer()

            Button("Try Again") {
                calibrator.start(profileName: profileName)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    // MARK: - Helpers

    private func stepColor(for role: DrumRole, current: DrumRole) -> Color {
        let roles = calibrator.rolesToCalibrate
        guard let currentIndex = roles.firstIndex(of: current),
              let roleIndex = roles.firstIndex(of: role)
        else { return .gray }

        if roleIndex < currentIndex {
            return .green  // completed
        } else if roleIndex == currentIndex {
            return colorForRole(role)  // current
        } else {
            return .gray.opacity(0.3)  // pending
        }
    }

    private func iconForRole(_ role: DrumRole) -> String {
        switch role {
        case .kick: return "circle.fill"
        case .snare: return "square.fill"
        case .closedHat: return "triangle.fill"
        case .openHat: return "triangle"
        case .clap: return "hands.clap.fill"
        case .rim: return "bolt.fill"
        case .perc: return "star.fill"
        }
    }

    private func colorForRole(_ role: DrumRole) -> Color {
        switch role {
        case .kick: return .red
        case .snare: return .orange
        case .closedHat: return .yellow
        case .openHat: return .yellow
        case .clap: return .purple
        case .rim: return .blue
        case .perc: return .gray
        }
    }
}

// MARK: - Guided (tap-along) flow

/// Visual-metronome calibration. A pulsing beat keeps time; the user taps
/// along; the take is segmented deterministically by the known beat times.
/// Observes both the driver (beat/role) and its engine (step/profile).
private struct GuidedFlow: View {
    @ObservedObject var guided: LiveBeatGuidedSession
    @ObservedObject var engine: LiveBeatCalibrationEngine
    @ObservedObject var profileStore: LiveBeatProfileStore

    let profileName: String
    let onRetry: () -> Void
    let onDone: () -> Void

    var body: some View {
        switch engine.step {
        case .complete:
            completeView
        case let .failed(message):
            failedView(message)
        default:
            tapAlongView
        }
    }

    // MARK: Tap-along

    private var tapAlongView: some View {
        let role = guided.currentRole ?? .kick
        let counting = guided.beat < 0
        return VStack(spacing: 24) {
            roleProgress(current: role)

            Spacer()

            // Pulsing beat marker. Scales up on each beat tick.
            ZStack {
                Circle()
                    .fill(colorForRole(role).opacity(counting ? 0.25 : 0.9))
                    .frame(width: 160, height: 160)
                    .scaleEffect(counting ? 1.0 : 1.18)
                    .animation(.spring(response: 0.18, dampingFraction: 0.5), value: guided.beat)
                Image(systemName: iconForRole(role))
                    .font(.system(size: 64))
                    .foregroundStyle(.white)
            }

            Text(counting ? "Get ready…" : "Tap your \(role.displayName.uppercased())")
                .font(.title2.bold())

            Text(counting
                ? "Follow the beat"
                : "Tap on every pulse")
                .font(.subheadline)
                .foregroundStyle(.secondary)

            // Beat progress dots.
            if guided.beatsPerRole > 0 {
                HStack(spacing: 6) {
                    ForEach(0..<guided.beatsPerRole, id: \.self) { i in
                        Circle()
                            .fill(!counting && i <= guided.beat
                                ? colorForRole(role)
                                : Color.gray.opacity(0.3))
                            .frame(width: 12, height: 12)
                    }
                }
            }

            Spacer()
        }
    }

    private func roleProgress(current: DrumRole) -> some View {
        HStack {
            ForEach(engine.rolesToCalibrate, id: \.self) { r in
                Circle()
                    .fill(stepColor(for: r, current: current))
                    .frame(width: 12, height: 12)
            }
        }
    }

    // MARK: Complete / failed

    private var completeView: some View {
        VStack(spacing: 24) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.green)
            Text("Calibration Complete!")
                .font(.title2.bold())
            if let profile = engine.profile {
                Text("\(profile.templates.count) sounds calibrated")
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                if let profile = guided.finalize() {
                    profileStore.save(profile)
                    profileStore.setActive(id: profile.id)
                }
                onDone()
            } label: {
                Text("Save Profile")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding()
                    .background(Color.green)
                    .foregroundStyle(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
            }
        }
    }

    private func failedView(_ message: String) -> some View {
        VStack(spacing: 24) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 60))
                .foregroundStyle(.red)
            Text("Didn't catch enough taps")
                .font(.title2.bold())
            Text(message)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
            Spacer()
            Button("Try Again", action: onRetry)
                .buttonStyle(.borderedProminent)
        }
    }

    // MARK: Helpers

    private func stepColor(for role: DrumRole, current: DrumRole) -> Color {
        let roles = engine.rolesToCalibrate
        guard let ci = roles.firstIndex(of: current),
              let ri = roles.firstIndex(of: role) else { return .gray }
        if ri < ci { return .green }
        if ri == ci { return colorForRole(role) }
        return .gray.opacity(0.3)
    }

    private func iconForRole(_ role: DrumRole) -> String {
        switch role {
        case .kick: return "circle.fill"
        case .snare: return "square.fill"
        case .closedHat: return "triangle.fill"
        case .openHat: return "triangle"
        case .clap: return "hands.clap.fill"
        case .rim: return "bolt.fill"
        case .perc: return "star.fill"
        }
    }

    private func colorForRole(_ role: DrumRole) -> Color {
        switch role {
        case .kick: return .red
        case .snare: return .orange
        case .closedHat, .openHat: return .yellow
        case .clap: return .purple
        case .rim: return .blue
        case .perc: return .gray
        }
    }
}
