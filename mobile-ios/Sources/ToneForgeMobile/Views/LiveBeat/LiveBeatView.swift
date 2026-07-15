// LiveBeatView.swift
//
// Main performance surface for Live Beat mode. Shows envelope meter,
// recent hits, recording controls, and profile selection.

import SwiftUI
import ToneForgeEngine

struct LiveBeatView: View {
    @ObservedObject var controller: LiveBeatController
    @ObservedObject var profileStore: LiveBeatProfileStore

    @EnvironmentObject private var appState: AppState

    @State private var showProfilePicker = false
    @State private var showCalibration = false

    var body: some View {
        VStack(spacing: 24) {
            // Header with profile picker
            header

            // Envelope meter
            envelopeMeter

            // Recent hits display
            recentHitsGrid

            Spacer()

            // Controls
            controlsSection
        }
        .padding()
        .sheet(isPresented: $showProfilePicker) {
            LiveBeatProfilePicker(
                store: profileStore,
                onSelect: { profile in
                    controller.activeProfile = profile
                    showProfilePicker = false
                }
            )
        }
        .sheet(isPresented: $showCalibration) {
            LiveBeatCalibrationView(
                calibrator: appState.liveBeatCalibrator,
                profileStore: profileStore
            )
        }
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 4) {
                Text("Live Beat")
                    .font(.title2.bold())

                Button {
                    showProfilePicker = true
                } label: {
                    HStack(spacing: 6) {
                        Text(controller.activeProfile?.name ?? "Default")
                            .font(.subheadline)
                        Image(systemName: "chevron.down")
                            .font(.caption)
                    }
                    .foregroundStyle(.secondary)
                }
            }

            Spacer()

            // Hit counter
            VStack(alignment: .trailing) {
                Text("\(controller.hitCount)")
                    .font(.system(.title, design: .monospaced).bold())
                    .foregroundStyle(Color.accentColor)
                Text("hits")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    // MARK: - Envelope Meter

    private var envelopeMeter: some View {
        VStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    // Background
                    RoundedRectangle(cornerRadius: 8)
                        .fill(Color.gray.opacity(0.2))

                    // Level
                    RoundedRectangle(cornerRadius: 8)
                        .fill(meterGradient)
                        .frame(width: geo.size.width * CGFloat(min(1, controller.envelopeLevel * 3)))
                        .animation(.linear(duration: 0.05), value: controller.envelopeLevel)
                }
            }
            .frame(height: 24)

            Text("Tap any surface")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var meterGradient: LinearGradient {
        LinearGradient(
            colors: [.green, .yellow, .orange, .red],
            startPoint: .leading,
            endPoint: .trailing
        )
    }

    // MARK: - Recent Hits Grid

    private var recentHitsGrid: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Recent Hits")
                .font(.headline)

            LazyVGrid(columns: [
                GridItem(.flexible()),
                GridItem(.flexible()),
                GridItem(.flexible()),
                GridItem(.flexible())
            ], spacing: 8) {
                ForEach(controller.recentHits.suffix(8).reversed(), id: \.timeSec) { hit in
                    hitCell(hit)
                }
            }
        }
        .padding()
        .background(Color.gray.opacity(0.1), in: RoundedRectangle(cornerRadius: 12))
    }

    private func hitCell(_ hit: LiveBeatHit) -> some View {
        VStack(spacing: 4) {
            Image(systemName: iconForRole(hit.role))
                .font(.title2)
                .foregroundStyle(colorForRole(hit.role))

            Text(hit.role.displayName)
                .font(.caption2)
                .lineLimit(1)

            // Confidence indicator
            RoundedRectangle(cornerRadius: 2)
                .fill(colorForRole(hit.role).opacity(Double(hit.confidence)))
                .frame(height: 3)
        }
        .padding(8)
        .background(Color.gray.opacity(0.15), in: RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Controls

    private var controlsSection: some View {
        VStack(spacing: 16) {
            // Recording toggle
            HStack {
                Toggle("Record Pattern", isOn: $controller.isRecordingEnabled)
                    .toggleStyle(.switch)

                if !controller.recordedHits.isEmpty {
                    Text("\(controller.recordedHits.count) hits")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            // Main action button
            Button {
                if controller.state == .idle {
                    controller.start()
                } else {
                    controller.stop()
                }
            } label: {
                HStack {
                    Image(systemName: controller.state == .idle ? "play.fill" : "stop.fill")
                    Text(controller.state == .idle ? "Start" : "Stop")
                }
                .font(.headline)
                .frame(maxWidth: .infinity)
                .padding()
                .background(controller.state == .idle ? Color.green : Color.red)
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 12))
            }

            // Secondary actions
            HStack(spacing: 12) {
                Button("Calibrate") {
                    showCalibration = true
                }
                .buttonStyle(.bordered)

                if !controller.recordedHits.isEmpty {
                    Button("Open in Sequencer") {
                        // Will be wired to ModeCoordinator
                    }
                    .buttonStyle(.borderedProminent)
                }
            }
        }
    }

    // MARK: - Helpers

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

struct LiveBeatProfilePicker: View {
    @ObservedObject var store: LiveBeatProfileStore
    var onSelect: (LiveBeatProfile?) -> Void

    @State private var showNewProfile = false
    @State private var newProfileName = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        onSelect(nil)
                    } label: {
                        HStack {
                            Text("Default (Heuristic)")
                            Spacer()
                            if store.activeProfileId == nil {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(Color.accentColor)
                            }
                        }
                    }
                }

                Section("Saved Profiles") {
                    ForEach(store.all()) { profile in
                        Button {
                            store.setActive(id: profile.id)
                            onSelect(profile)
                        } label: {
                            HStack {
                                VStack(alignment: .leading) {
                                    Text(profile.name)
                                    Text("\(profile.templates.count) sounds")
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                Spacer()
                                if store.activeProfileId == profile.id {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(Color.accentColor)
                                }
                            }
                        }
                    }
                    .onDelete { indices in
                        for index in indices {
                            let profile = store.all()[index]
                            store.delete(id: profile.id)
                        }
                    }
                }
            }
            .navigationTitle("Select Profile")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button {
                        showNewProfile = true
                    } label: {
                        Image(systemName: "plus")
                    }
                }
            }
            .alert("New Profile", isPresented: $showNewProfile) {
                TextField("Profile Name", text: $newProfileName)
                Button("Create") {
                    let profile = store.createProfile(name: newProfileName)
                    store.setActive(id: profile.id)
                    onSelect(profile)
                    newProfileName = ""
                }
                Button("Cancel", role: .cancel) {
                    newProfileName = ""
                }
            }
        }
    }
}
