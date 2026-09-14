// BorrowSourcesDecode.swift
//
// Pure decode of the per-pad `source`/`stem` sidecar the /borrow manifest
// carries. The shared SamplePack DTO (mobile-ios, not editable from the
// desktop target) drops both fields, so they are read from the SAME response
// bytes into these maps.
//
// This lived inline in SessionController.fetchBorrowRaw — inside the
// non-testable JamDesktop executable target — which is exactly why two
// user-visible regressions shipped:
//
//   • the mount hard-coded stem "drums", so every borrow pad rendered the
//     same color ("all pads red"), and
//   • the `source` tag was dropped, so the 64/divider layout couldn't tell
//     the current song from the donor.
//
// Extracting the decode here (JamDesktopCore) makes it unit-testable
// (BorrowSourcesDecodeTests) so those bugs fail CI, not the user's eyes.
// Additive/optional: a pad missing its `source` reads as `.initial` (the
// current song); a pad missing its `stem` simply has no entry.

import Foundation

/// Per-pad `source` sidecar wire shape — additive to the SamplePack the
/// backend serves in the same payload.
private struct BorrowPadSourceWire: Decodable {
    let padIdx: Int
    let source: String?
    /// Logical stem (drums/bass/other/vocals). Colors the pad by stem
    /// category; a whole borrow used to render one color because the mount
    /// hard-coded stem "drums".
    let stem: String?
}

private struct BorrowSourcesWire: Decodable {
    let pads: [BorrowPadSourceWire]
}

/// Decode the per-pad source + stem sidecar from raw /borrow bytes.
///
///   • `sources[padIdx]` — `.donor` only when the manifest says `"donor"`;
///     anything else (including a missing tag) reads `.initial`.
///   • `stems[padIdx]`   — present only when the manifest carries a stem.
///
/// Never throws: a payload with no `pads` array (or malformed) yields two
/// empty maps, so a borrow still mounts (callers default source `.initial`,
/// stem `"other"`).
public func decodeBorrowSources(
    from data: Data
) -> (sources: [Int: BorrowPadSource], stems: [Int: String]) {
    var sources: [Int: BorrowPadSource] = [:]
    var stems: [Int: String] = [:]
    guard let wire = try? JSONDecoder().decode(BorrowSourcesWire.self, from: data)
    else { return (sources, stems) }
    for p in wire.pads {
        sources[p.padIdx] = p.source == "donor" ? .donor : .initial
        if let s = p.stem { stems[p.padIdx] = s }
    }
    return (sources, stems)
}
