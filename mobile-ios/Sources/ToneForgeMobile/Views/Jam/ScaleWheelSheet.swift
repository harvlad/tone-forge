// ScaleWheelSheet.swift
//
// Key picker for the Jam in Key surface (redesign Phase 7): a wheel
// of the 12 pitch classes, a Major/Minor toggle, the minor-family
// variant control (Natural / Harmonic / Melodic), and a reset back
// to the song's detected key.
//
// Every tap applies immediately through JamInKeyController (override
// persisted per song in JamSettingsStore, grid rebuilt) — no
// confirm step, so you can audition keys against the song. Override
// strings use the MusicalKey.parse format ("D minor").

import SwiftUI
import ToneForgeEngine

struct ScaleWheelSheet: View {
    @ObservedObject var controller: JamInKeyController
    @ObservedObject var jamSettings: JamSettingsStore
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// Wheel spellings, chosen to match common key names. All parse
    /// through ChordParser's enharmonic table.
    private static let roots: [(name: String, pc: Int)] = [
        ("C", 0), ("Db", 1), ("D", 2), ("Eb", 3), ("E", 4), ("F", 5),
        ("F#", 6), ("G", 7), ("Ab", 8), ("A", 9), ("Bb", 10), ("B", 11),
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                Text(controller.keyDisplayName)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(TFTheme.textPrimary)
                    .padding(.top, 8)

                wheel

                Picker("Scale", selection: majorMinorBinding) {
                    Text("Major").tag(true)
                    Text("Minor").tag(false)
                }
                .pickerStyle(.segmented)
                .padding(.horizontal, 40)

                if !isMajorSelected {
                    Picker("Variant", selection: variantBinding) {
                        ForEach(JamScaleVariant.allCases, id: \.rawValue) { v in
                            Text(v.displayName).tag(v)
                        }
                    }
                    .pickerStyle(.segmented)
                    .padding(.horizontal, 40)
                }

                if hasOverride {
                    Button("Use detected key") {
                        controller.setKeyOverride(nil)
                    }
                    .font(TFTheme.chipFont)
                    .foregroundStyle(TFTheme.textSecondary)
                }

                Spacer()
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(TFTheme.background)
            .navigationTitle("Key")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Wheel

    private var wheel: some View {
        ZStack {
            Circle()
                .stroke(TFTheme.stroke, lineWidth: 1)
                .frame(width: 250, height: 250)
            ForEach(Self.roots, id: \.pc) { root in
                let selected = root.pc == selectedRootPC
                let angle = Double(root.pc) / 12.0 * 2.0 * .pi
                Button {
                    applyKey(rootName: root.name, major: isMajorSelected)
                } label: {
                    Text(root.name)
                        .font(.subheadline.weight(selected ? .bold : .regular))
                        .foregroundStyle(
                            selected ? TFTheme.textPrimary : TFTheme.textSecondary
                        )
                        .frame(width: 44, height: 44)
                        .background(
                            selected ? TFTheme.chipActiveFill : TFTheme.chipFill,
                            in: Circle()
                        )
                        .overlay(
                            Circle().stroke(
                                selected ? Color.accentColor : TFTheme.stroke,
                                lineWidth: 1
                            )
                        )
                }
                .buttonStyle(.plain)
                .offset(x: 125 * sin(angle), y: -125 * cos(angle))
                .accessibilityLabel("Key root \(root.name)")
            }
        }
        .frame(width: 300, height: 300)
    }

    // MARK: - Selection state

    private var selectedRootPC: Int? {
        controller.effectiveKey?.root.rawValue
    }

    /// Major/modal counts as "major" side of the toggle; the wheel
    /// only writes plain major/minor overrides.
    private var isMajorSelected: Bool {
        switch controller.effectiveKey?.scale {
        case .minor, .harmonicMinor, .melodicMinor: return false
        default: return true
        }
    }

    private var majorMinorBinding: Binding<Bool> {
        Binding(
            get: { isMajorSelected },
            set: { major in
                let rootName = Self.roots.first {
                    $0.pc == selectedRootPC
                }?.name ?? "C"
                applyKey(rootName: rootName, major: major)
            }
        )
    }

    private var variantBinding: Binding<JamScaleVariant> {
        Binding(
            get: { jamSettings.scaleVariant },
            set: { controller.setScaleVariant($0) }
        )
    }

    private var hasOverride: Bool {
        jamSettings.keyOverride(
            analysisId: appState.currentBundle?.analysisId
        ) != nil
    }

    private func applyKey(rootName: String, major: Bool) {
        controller.setKeyOverride("\(rootName) \(major ? "major" : "minor")")
    }
}
