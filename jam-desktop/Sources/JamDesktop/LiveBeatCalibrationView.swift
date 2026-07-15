// LiveBeatCalibrationView.swift
//
// Desktop calibration flow for Live Beat. Guides the user through tapping
// each drum sound 5+ times to build a profile. Ported from the iOS view;
// drives the shared `LiveBeatCalibrationEngine` via the desktop
// `LiveBeatCalibrator` glue. Also hosts `LiveBeatProfilePicker`.

import SwiftUI
import ToneForgeEngine
import JamDesktopAudio

struct LiveBeatCalibrationView: View {
    @ObservedObject var calibrator: LiveBeatCalibrator
    @ObservedObject var profileStore: LiveBeatProfileStore

    @Environment(\.dismiss) private var dismiss

    @State private var profileName = "New Profile"
    @State private var isStarted = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .frame(minWidth: 420, minHeight: 480)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
    }

    private var header: some View {
        HStack {
            Text("Calibrate").font(.headline)
            Spacer()
            Button {
                calibrator.cancel()
                dismiss()
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }

    @ViewBuilder
    private var content: some View {
        if !isStarted {
            setupView
        } else {
            calibrationView
        }
    }

    // MARK: - Setup View

    private var setupView: some View {
        VStack(spacing: 20) {
            Image(systemName: "waveform.badge.mic")
                .font(.system(size: 52))
                .foregroundStyle(JamTheme.accent)

            Text("Teach Your Sounds")
                .font(.title2.bold())

            Text("You'll tap each drum sound 5+ times so Live Beat can learn your unique sounds.")
                .font(.subheadline)
                .foregroundStyle(JamTheme.textSecondary)
                .multilineTextAlignment(.center)

            TextField("Profile Name", text: $profileName)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 280)

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
            .background(JamTheme.surface, in: RoundedRectangle(cornerRadius: 12))

            Spacer()

            Button {
                calibrator.start(profileName: profileName)
                isStarted = true
            } label: {
                Text("Start Calibration")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(.top, 12)
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
        VStack(spacing: 20) {
            // Progress dots per role
            HStack {
                ForEach(calibrator.rolesToCalibrate, id: \.self) { r in
                    Circle()
                        .fill(stepColor(for: r, current: role))
                        .frame(width: 12, height: 12)
                }
            }

            if let error = calibrator.installError {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.orange)
                    .multilineTextAlignment(.center)
            }

            Spacer()

            Image(systemName: iconForRole(role))
                .font(.system(size: 72))
                .foregroundStyle(colorForRole(role))

            Text("Tap your \(role.displayName.uppercased())")
                .font(.title.bold())

            Text("Hit \(target - collected) more times")
                .font(.headline)
                .foregroundStyle(JamTheme.textSecondary)

            envelopeMeter

            HStack(spacing: 4) {
                ForEach(0..<target, id: \.self) { i in
                    Circle()
                        .fill(i < collected ? colorForRole(role) : JamTheme.surface)
                        .frame(width: 16, height: 16)
                }
            }

            Spacer()

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
                    .fill(JamTheme.surface)

                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.green)
                    .frame(width: geo.size.width * CGFloat(min(1, calibrator.envelopeLevel * 10)))
                    .animation(.linear(duration: 0.05), value: calibrator.envelopeLevel)
            }
        }
        .frame(height: 16)
        .frame(maxWidth: 280)
    }

    private var completeView: some View {
        VStack(spacing: 20) {
            Image(systemName: "checkmark.circle.fill")
                .font(.system(size: 56))
                .foregroundStyle(.green)

            Text("Calibration Complete!")
                .font(.title2.bold())

            if let profile = calibrator.profile {
                Text("\(profile.templates.count) sounds calibrated")
                    .foregroundStyle(JamTheme.textSecondary)
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
            }
            .buttonStyle(.borderedProminent)
            .tint(.green)
        }
    }

    private func failedView(message: String) -> some View {
        VStack(spacing: 20) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 56))
                .foregroundStyle(.orange)

            Text("Calibration Failed")
                .font(.title2.bold())

            Text(message)
                .foregroundStyle(JamTheme.textSecondary)
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
            return .green
        } else if roleIndex == currentIndex {
            return colorForRole(role)
        } else {
            return .gray.opacity(0.3)
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

// MARK: - Profile Picker

/// Sheet for choosing / creating / deleting Live Beat calibration profiles.
struct LiveBeatProfilePicker: View {
    @ObservedObject var store: LiveBeatProfileStore
    var onSelect: (LiveBeatProfile?) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var showNewProfile = false
    @State private var newProfileName = ""

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            List {
                Section {
                    Button {
                        store.setActive(id: nil)
                        onSelect(nil)
                        dismiss()
                    } label: {
                        HStack {
                            Text("Default (Heuristic)")
                            Spacer()
                            if store.activeProfileId == nil {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(JamTheme.accent)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }

                Section("Saved Profiles") {
                    ForEach(store.all()) { profile in
                        Button {
                            store.setActive(id: profile.id)
                            onSelect(profile)
                            dismiss()
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(profile.name)
                                    Text("\(profile.templates.count) sounds")
                                        .font(.caption)
                                        .foregroundStyle(JamTheme.textSecondary)
                                }
                                Spacer()
                                if store.activeProfileId == profile.id {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(JamTheme.accent)
                                }
                            }
                        }
                        .buttonStyle(.plain)
                    }
                    .onDelete { indices in
                        let all = store.all()
                        for index in indices where all.indices.contains(index) {
                            store.delete(id: all[index].id)
                        }
                    }
                }
            }
        }
        .frame(minWidth: 360, minHeight: 360)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .alert("New Profile", isPresented: $showNewProfile) {
            TextField("Profile Name", text: $newProfileName)
            Button("Create") {
                let profile = store.createProfile(name: newProfileName)
                store.setActive(id: profile.id)
                onSelect(profile)
                newProfileName = ""
                dismiss()
            }
            Button("Cancel", role: .cancel) {
                newProfileName = ""
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Select Profile").font(.headline)
            Spacer()
            Button {
                showNewProfile = true
            } label: {
                Image(systemName: "plus")
            }
            .buttonStyle(.plain)
            Button { dismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }
}
