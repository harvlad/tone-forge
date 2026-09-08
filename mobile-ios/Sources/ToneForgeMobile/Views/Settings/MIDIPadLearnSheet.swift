// MIDIPadLearnSheet.swift
//
// MIDI-Learn flow for controllers whose pads are NOT one contiguous
// note run — the Pioneer DJM-S7's two 8-pad decks, Teenage Engineering
// boxes, custom LPD8 programs. The user taps their hardware pads in
// grid order (1..16); each incoming note-on is captured via the
// transport's onLearnNote tap and assigned to the next slot. The
// finished map persists in SampleSettingsStore.midiPadNoteMap and
// beats the contiguous note-36 default in AppState.noteRouting.
//
// Duplicate notes are ignored (a re-pressed pad must not eat two
// slots); "Done" saves a PARTIAL map on purpose — a 12-pad controller
// (TE) maps 12 slots and stops.

import SwiftUI

struct MIDIPadLearnSheet: View {
    @ObservedObject var settings: SampleSettingsStore
    let transport: MIDIKeyboardTransport?
    @Environment(\.dismiss) private var dismiss

    /// slot index -> captured note, in capture order.
    @State private var captured: [(slot: Int, note: Int)] = []

    private var nextSlot: Int { captured.count }
    private var complete: Bool { captured.count >= 16 }

    var body: some View {
        NavigationStack {
            VStack(spacing: 18) {
                if complete {
                    Label("All 16 pads mapped", systemImage: "checkmark.circle.fill")
                        .font(.title3.bold())
                        .foregroundStyle(.green)
                } else {
                    Text("Press pad \(nextSlot + 1) of 16 on your controller")
                        .font(.title3.bold())
                        .multilineTextAlignment(.center)
                    Text("Tap the hardware pads in the order you want them "
                         + "on the 4×4 grid (left→right, top→bottom). "
                         + "12-pad controllers: tap Done after pad 12.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }

                // 4x4 capture progress grid.
                LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 4),
                          spacing: 8) {
                    ForEach(0..<16, id: \.self) { slot in
                        let note = captured.first(where: { $0.slot == slot })?.note
                        ZStack {
                            RoundedRectangle(cornerRadius: 8)
                                .fill(note != nil ? Color.green.opacity(0.35)
                                      : slot == nextSlot ? Color.accentColor.opacity(0.35)
                                      : Color.secondary.opacity(0.15))
                            Text(note.map { "n\($0)" } ?? "\(slot + 1)")
                                .font(.caption.monospacedDigit())
                        }
                        .frame(height: 44)
                    }
                }
                .padding(.horizontal)

                if !settings.midiPadNoteMap.isEmpty && captured.isEmpty {
                    Button(role: .destructive) {
                        settings.midiPadNoteMap = [:]
                    } label: {
                        Text("Clear saved mapping (\(settings.midiPadNoteMap.count) pads)")
                    }
                }

                Spacer()
            }
            .padding(.top, 24)
            .navigationTitle("Map controller pads")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(complete ? "Save" : "Done") { saveAndClose() }
                        .disabled(captured.isEmpty)
                }
                ToolbarItem(placement: .automatic) {
                    Button("Restart") { captured = [] }
                        .disabled(captured.isEmpty)
                }
            }
            .onAppear { armLearnTap() }
            .onDisappear { transport?.onLearnNote = nil }
        }
    }

    private func armLearnTap() {
        transport?.onLearnNote = { note in
            guard !complete else { return }
            guard !captured.contains(where: { $0.note == note }) else { return }
            captured.append((slot: nextSlot, note: note))
        }
    }

    private func saveAndClose() {
        var map: [Int: Int] = [:]
        for entry in captured {
            map[entry.note] = entry.slot
        }
        settings.midiPadNoteMap = map
        dismiss()
    }
}
