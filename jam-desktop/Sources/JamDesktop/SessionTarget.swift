// SessionTarget.swift
//
// Optional "Session" key/BPM target for the Launchpad — OFF by default.
//
// Core principle: songs stay TRUE by default. This target ONLY conforms
// ADDED (borrowed) parts to a shared session key/tempo; the loaded song's
// own audio is NEVER repitched or retimed. When off, Borrow requests are
// byte-identical to today (donor loops conform to the host song). When on,
// the Borrow fetches carry ?target_bpm=&target_key= so the backend conforms
// donor loops to the session instead of the host.
//
// Desktop port of the web implementation (kit.js `jamn.session.target`):
// same "G minor" backend key form, same enharmonic fold, same first-enable
// prefill-blanks-only rule. Persistence is desktop-local (UserDefaults);
// the store key namespace is independent of the web localStorage blob.

import Foundation
import Combine

@MainActor
final class SessionTargetModel: ObservableObject {
    /// Chromatic roots offered by the Key picker (sharps; flats fold in).
    static let roots = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    /// Enharmonic fold so a flat key from analysis maps onto a picker root.
    private static let flatToSharp: [String: String] = [
        "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
        "Cb": "B", "Fb": "E", "E#": "F", "B#": "C",
    ]

    @Published var isOn: Bool { didSet { if loaded { persist() } } }
    @Published var root: String { didSet { if loaded { keySet = true; persist() } } }
    @Published var isMinor: Bool { didSet { if loaded { keySet = true; persist() } } }
    /// Session tempo in BPM; 0 means unset (no target_bpm sent).
    @Published var bpm: Int { didSet { if loaded { persist() } } }

    /// Whether a key has ever been committed (user-touched or prefilled). Guards
    /// the first-enable prefill so re-toggling never clobbers a dialed-in key.
    private var keySet: Bool
    /// False during init decode so `didSet` writes don't fire mid-construction.
    private var loaded = false
    private let store: UserDefaults

    private enum Key {
        static let on = "jamn.session.on"
        static let root = "jamn.session.root"
        static let minor = "jamn.session.minor"
        static let bpm = "jamn.session.bpm"
        static let keySet = "jamn.session.keySet"
    }

    init(store: UserDefaults = .standard) {
        self.store = store
        isOn = store.bool(forKey: Key.on)
        let r = store.string(forKey: Key.root) ?? "C"
        root = Self.roots.contains(r) ? r : "C"
        isMinor = store.bool(forKey: Key.minor)
        bpm = store.integer(forKey: Key.bpm)
        keySet = store.bool(forKey: Key.keySet)
        loaded = true
    }

    private func persist() {
        store.set(isOn, forKey: Key.on)
        store.set(root, forKey: Key.root)
        store.set(isMinor, forKey: Key.minor)
        store.set(bpm, forKey: Key.bpm)
        store.set(keySet, forKey: Key.keySet)
    }

    /// Backend `target_key` form ("G minor"), or nil when the session is off.
    var targetKey: String? {
        guard isOn else { return nil }
        return "\(root) \(isMinor ? "minor" : "major")"
    }

    /// Session tempo for `target_bpm`, or nil when off / unset.
    var targetBpm: Double? {
        guard isOn, bpm > 0 else { return nil }
        return Double(bpm)
    }

    /// Toggle the session. Turning ON prefills key + tempo from the loaded
    /// song's own key/tempo — but only fills blanks, so opting in doesn't
    /// conform anything away from what's already playing, and never clobbers a
    /// target the user previously dialed in (mirrors the web first-enable rule).
    func toggle(songKey: String?, songBpm: Double?) {
        if isOn {
            isOn = false
            return
        }
        // Fill the key only if never set. Assigning root/isMinor marks keySet
        // via their didSet, so a prefilled or user-touched key is "set".
        if !keySet {
            if let parsed = Self.parse(songKey) {
                root = parsed.root
                isMinor = parsed.minor
            } else {
                keySet = true   // adopt the current default as the chosen key
            }
        }
        if bpm == 0, let b = songBpm, b > 0 {
            bpm = Int(b.rounded())
        }
        isOn = true   // last, so the final persist() carries the prefilled values
    }

    /// Split a free-form key string ("G minor", "Gm", "Bb major", "C") into a
    /// chromatic picker root + major/minor. Nil for anything unparseable, so the
    /// caller keeps its current default rather than snapping to a wrong key.
    static func parse(_ str: String?) -> (root: String, minor: Bool)? {
        let raw = (str ?? "").trimmingCharacters(in: .whitespaces)
        guard let first = raw.first, first.isLetter,
              ("A"..."G").contains(String(first).uppercased()) else { return nil }
        var root = String(first).uppercased()
        var rest = raw.dropFirst()
        if let acc = rest.first, "#b♯♭".contains(acc) {
            root += (acc == "b" || acc == "♭") ? "b" : "#"
            rest = rest.dropFirst()
        }
        if let mapped = flatToSharp[root] { root = mapped }
        guard roots.contains(root) else { return nil }
        let tail = rest.lowercased().trimmingCharacters(in: .whitespaces)
        let minor = tail.contains("min") || tail == "m"
        return (root, minor)
    }
}
