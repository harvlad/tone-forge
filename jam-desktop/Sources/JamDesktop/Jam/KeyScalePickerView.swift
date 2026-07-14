// KeyScalePickerView.swift
//
// Header controls for the Jam in Key surface: key override menu
// (12 roots × major/minor + "Detected" reset), minor scale variant,
// octave shift and the chord-highlight toggle — the same knobs the
// iOS jam settings sheet exposes.

import SwiftUI
import JamDesktopCore

struct KeyScalePickerView: View {
    @EnvironmentObject private var session: SessionController

    private static let roots = [
        "C", "C#", "D", "Eb", "E", "F",
        "F#", "G", "Ab", "A", "Bb", "B",
    ]

    private var jam: JamInKeyModel { session.jam }

    var body: some View {
        HStack(spacing: 12) {
            keyMenu

            if isMinorKey {
                Picker("Scale", selection: variantBinding) {
                    ForEach(JamScaleVariant.allCases, id: \.self) {
                        Text($0.displayName).tag($0)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 240)
            }

            Stepper(
                "Octave \(jam.octaveShift >= 0 ? "+" : "")\(jam.octaveShift)",
                value: octaveBinding, in: -3...3
            )
            .font(.caption)

            Toggle("Chord glow", isOn: highlightBinding)
                .toggleStyle(.switch)
                .controlSize(.small)
                .help("Brighten the pads of the chord currently sounding")
        }
    }

    private var keyMenu: some View {
        Menu {
            if jam.detectedKey != nil {
                Button("Detected (\(jam.detectedKey ?? ""))") {
                    jam.setKeyOverride(nil)
                }
                Divider()
            }
            ForEach(Self.roots, id: \.self) { root in
                Menu(root) {
                    Button("\(root) major") {
                        jam.setKeyOverride("\(root) major")
                    }
                    Button("\(root) minor") {
                        jam.setKeyOverride("\(root) minor")
                    }
                }
            }
            if jam.keyOverride != nil {
                Divider()
                Button("Clear override") { jam.setKeyOverride(nil) }
            }
        } label: {
            Label(jam.keyDisplayName, systemImage: "key")
                .font(.callout.weight(.medium))
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .help("Key the pads snap to — override the detected key")
    }

    private var isMinorKey: Bool {
        // Variant only matters for minor-family keys.
        let raw = (jam.keyOverride ?? jam.detectedKey)?.lowercased() ?? ""
        return raw.contains("minor")
    }

    private var variantBinding: Binding<JamScaleVariant> {
        Binding(get: { jam.scaleVariant }, set: { jam.scaleVariant = $0 })
    }

    private var octaveBinding: Binding<Int> {
        Binding(get: { jam.octaveShift }, set: { jam.octaveShift = $0 })
    }

    private var highlightBinding: Binding<Bool> {
        Binding(
            get: { jam.highlightCurrentChord },
            set: { jam.highlightCurrentChord = $0 }
        )
    }
}
