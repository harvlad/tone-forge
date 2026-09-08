// MIDIPadMapStore.swift
//
// Persisted MIDI-Learn pad map for generic note controllers (iOS
// parity: SampleSettingsStore.midiPadNoteMap). Learned MIDI-note ->
// sample-pad-index (0..<16) built by the Settings "map controller
// pads" flow, for controllers whose pads are NOT one contiguous note
// run (Pioneer DJM-S7's two 8-pad decks, TE boxes, custom LPD8
// programs).
//
// Sidecar UserDefaults key with the same wire format as mobile
// ([[note, pad], ...] JSON pairs) — no settings-blob migration
// surgery, and a future shared-defaults import stays a straight copy.

import Foundation
import Combine

@MainActor
public final class MIDIPadMapStore: ObservableObject {

    /// Learned note -> pad-index map. Empty = no custom mapping.
    @Published public var map: [Int: Int] {
        didSet {
            let pairs = map.map { [$0.key, $0.value] }
            defaults.set(try? JSONSerialization.data(withJSONObject: pairs),
                         forKey: Self.key)
            onMapChanged?(map)
        }
    }

    /// Fired after every map change (save, clear) — SessionController
    /// re-derives the transport's noteRouting from it.
    public var onMapChanged: (([Int: Int]) -> Void)?

    private let defaults: UserDefaults
    /// Desktop sidecar — distinct from mobile's "toneforge.midiPadNoteMap"
    /// so a shared-defaults future can't silently cross-wire two devices'
    /// controllers.
    static let key = "jamn.midiPadMap.desktop"

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        if let data = defaults.data(forKey: Self.key),
           let pairs = (try? JSONSerialization.jsonObject(with: data)) as? [[Int]] {
            var loaded: [Int: Int] = [:]
            for pair in pairs where pair.count == 2 {
                loaded[pair[0]] = pair[1]
            }
            map = loaded
        } else {
            map = [:]
        }
    }

    /// Persisted map -> transport routing. A learned map wins; empty
    /// keeps the desktop default (notes play the wavetable synth).
    /// Same rule as mobile's AppState.noteRouting with the learn map
    /// beating the contiguous note-36 default — and the mappedPads
    /// case drops unmapped notes rather than falling through to the
    /// synth (mandatory cross-platform semantic, PARITY.yaml).
    public static func noteRouting(
        map: [Int: Int]
    ) -> MIDIKeyboardTransport.NoteRouting {
        map.isEmpty ? .synth : .mappedPads(map: map)
    }
}
