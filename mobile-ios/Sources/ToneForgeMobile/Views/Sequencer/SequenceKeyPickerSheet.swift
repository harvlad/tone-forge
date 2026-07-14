// SequenceKeyPickerSheet.swift
//
// Key picker for the Sequence Builder's Key Chords source. Twelve roots
// + Major/Minor -> a MusicalKey. Used when no song is loaded (or to
// override the detected key) so the diatonic chord grid can be retuned.

import SwiftUI
import ToneForgeEngine

struct SequenceKeyPickerSheet: View {
    /// Currently selected key (preselected on open).
    let key: MusicalKey
    /// Called with the chosen key when the user taps Done.
    let onPick: (MusicalKey) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var root: Int
    @State private var isMajor: Bool

    private static let rootNames = [
        "C", "C#", "D", "D#", "E", "F",
        "F#", "G", "G#", "A", "A#", "B"
    ]
    private static let accent = TFTheme.color(hex: 0x30D5C8)
    private static let columns = Array(repeating: GridItem(.flexible(), spacing: 8), count: 4)

    init(key: MusicalKey, onPick: @escaping (MusicalKey) -> Void) {
        self.key = key
        self.onPick = onPick
        _root = State(initialValue: ((key.root.rawValue % 12) + 12) % 12)
        _isMajor = State(initialValue: key.scale == .major)
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                LazyVGrid(columns: Self.columns, spacing: 8) {
                    ForEach(0..<12, id: \.self) { pc in
                        Button {
                            root = pc
                        } label: {
                            Text(Self.rootNames[pc])
                                .font(.headline)
                                .frame(maxWidth: .infinity, minHeight: 48)
                                .background(
                                    root == pc
                                        ? Self.accent.opacity(0.35)
                                        : TFTheme.chipFill,
                                    in: RoundedRectangle(cornerRadius: 10)
                                )
                                .overlay(
                                    RoundedRectangle(cornerRadius: 10)
                                        .stroke(root == pc ? Self.accent : TFTheme.stroke,
                                                lineWidth: root == pc ? 2 : 1)
                                )
                                .foregroundStyle(TFTheme.textPrimary)
                        }
                        .buttonStyle(.plain)
                    }
                }

                Picker("Scale", selection: $isMajor) {
                    Text("Major").tag(true)
                    Text("Minor").tag(false)
                }
                .pickerStyle(.segmented)

                Spacer()
            }
            .padding(16)
            .background(TFTheme.background.ignoresSafeArea())
            .navigationTitle("Key")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onPick(MusicalKey(
                            root: PitchClass(root),
                            scale: isMajor ? .major : .minor
                        ))
                        dismiss()
                    }
                }
            }
        }
    }
}
