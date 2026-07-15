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

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                if !isStarted {
                    setupView
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
                        calibrator.cancel()
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

            Text("You'll tap each drum sound 5+ times so Live Beat can learn your unique sounds.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            TextField("Profile Name", text: $profileName)
                .textFieldStyle(.roundedBorder)
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
                calibrator.start(profileName: profileName)
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
