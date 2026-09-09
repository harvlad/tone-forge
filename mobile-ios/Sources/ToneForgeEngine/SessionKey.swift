// SessionKey.swift  (ToneForgeEngine)
//
// Key parsing/formatting for the optional Session key/BPM target that lets
// ADDED (borrowed) parts conform to a chosen key/tempo without ever touching
// the loaded song. Direct Swift port of web kit.js's parseKey / formatKey
// (the "Session key/BPM target" block) — the port-parity rule wants the same
// enharmonic folding and the same "<root> <quality>" backend form on every
// surface, so a "Bb major" key from analysis maps to the same picker state
// and the same target_key string here as it does in the browser. Shared so
// jam-desktop can drive the same Session UI off one implementation.

import Foundation

public enum SessionKey {
    /// The 12 chromatic roots the picker offers (sharps, matching kit.js).
    public static let roots: [String] =
        ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    /// Enharmonic fold so a flat key from analysis maps onto a chromatic root.
    private static let flatToSharp: [String: String] = [
        "Db": "C#", "Eb": "D#", "Gb": "F#", "Ab": "G#", "Bb": "A#",
        "Cb": "B", "Fb": "E", "E#": "F", "B#": "C",
    ]

    public enum Quality: String, Sendable { case major, minor }

    /// Split a free-form key ("G minor", "Gm", "Bb major", "C") into a
    /// chromatic root + major/minor quality; safe defaults for anything odd.
    /// Byte-for-byte the same rules as kit.js parseKey.
    public static func parse(_ str: String) -> (root: String, quality: Quality) {
        let raw = str.trimmingCharacters(in: .whitespacesAndNewlines)
        if raw.isEmpty { return ("C", .major) }
        // ^([A-Ga-g])([#b♯♭]?)\s*(.*)$
        guard let letter = raw.first,
              letter.isLetter,
              "ABCDEFGabcdefg".contains(letter) else {
            return ("C", .major)
        }
        var rest = String(raw.dropFirst())
        var acc = ""
        if let a = rest.first, "#b♯♭".contains(a) {
            acc = (a == "b" || a == "♭") ? "b" : "#"
            rest = String(rest.dropFirst())
        }
        var root = letter.uppercased() + acc
        if let folded = flatToSharp[root] { root = folded }
        if !roots.contains(root) { root = "C" }
        let tail = rest.trimmingCharacters(in: .whitespaces).lowercased()
        let quality: Quality =
            (tail.contains("min") || tail == "m") ? .minor : .major
        return (root, quality)
    }

    /// Backend target_key form is "<root> <quality>", e.g. "G minor".
    public static func format(root: String, quality: Quality) -> String {
        "\(root) \(quality.rawValue)"
    }

    /// Parse-then-format: normalise any input to the canonical backend form.
    public static func canonical(_ str: String) -> String {
        let p = parse(str)
        return format(root: p.root, quality: p.quality)
    }
}
