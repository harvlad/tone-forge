// SamplePack.swift
//
// Codable manifest schema for a sample pack — the unit of user-facing
// sound content in the mobile contribution instrument.
//
// A pack is a bag of up to 16 pads (matching the mockup's 4×4 grid),
// each backed by a short audio file (typically m4a). Packs come from
// three sources, all sharing this schema:
//
//   1. Bundled StarterPack — shipped in App/Resources/Samples/StarterPack/
//      and always available offline.
//   2. Song-derived — synthesised at bundle-load time by translating
//      SongBundle.presets (see SongBundle.swift) into a virtual pack;
//      also fetched from /api/song/{id}/chops for other sliceModes.
//   3. Curated remote — downloaded from /api/sample-packs/{id} into
//      ~/Library/Caches/toneforge/packs/{packId}/.
//
// Wire shape (manifestVersion = 1):
//
//   {
//     "manifestVersion": 1,
//     "packId": "starter",
//     "name": "Starter",
//     "family": "mixed",
//     "paletteHint": "purple",
//     "pads": [
//       {
//         "padIdx": 0,
//         "name": "Shimmer Pad",
//         "family": "pads",
//         "colorHint": "purple",
//         "filename": "00_shimmer_pad.m4a",
//         "chokeGroup": null,
//         "loopPointSec": null,
//         "gainDb": 0,
//         "defaultQuantize": "1/4"
//       }
//     ]
//   }
//
// LOCK-IN: this schema is one of the plan's Critical Files. Any change
// must bump `manifestVersion` and add a version-branching decode path.
// The mobile client caches downloaded manifests to disk and needs to
// be able to read older ones.

import Foundation

// MARK: - Root

/// A sample pack manifest. Decoded from the pack's `manifest.json` —
/// bundled inside the app, embedded in a song bundle response, or
/// downloaded from the curated pack catalog.
public struct SamplePack: Codable, Sendable, Equatable {
    /// Wire version. Bump when the schema changes; the decoder should
    /// branch on this to keep old cached manifests readable.
    public let manifestVersion: Int
    /// Stable pack identifier. Used as the on-disk cache directory
    /// name and in `LayerEvent.packId` so recorded layers can be
    /// replayed against the correct pack.
    public let packId: String
    /// Human-readable name (shown in the PackPicker).
    public let name: String
    /// Dominant sound family for the pack — used to pick a palette
    /// tint when the pack is displayed in Browse Packs.
    public let family: SampleFamily
    /// Optional color hint the UI can use to tint the pack card.
    /// Falls back to `family`'s default palette when nil.
    public let paletteHint: String?
    /// The pads. May be sparse (padIdx values need not be contiguous
    /// or start at 0). The 4×4 grid indexes 0..15; higher indices are
    /// allowed by the schema for future 8×8 layouts but the current
    /// SamplePadGrid clamps to 0..15.
    public let pads: [SamplePad]
    /// Optional starter groove shipped with the pack (manifestVersion
    /// 2+). Its `id` is generated deterministically from the packId on
    /// the backend so re-activating the pack re-saves the same pattern
    /// (idempotent by id) instead of accumulating duplicates. Tracks
    /// reference `.packPad(packId, padIdx)` into this same pack.
    /// Optional key — v1 manifests decode with this nil (Swift
    /// synthesizes `decodeIfPresent` for Optional properties).
    public let defaultSequence: SequencerPattern?
    /// Rights statement for the pack's audio (e.g. "Proprietary — ©
    /// ToneForge …"). Optional/additive: older manifests decode nil.
    public let license: String?
    /// Machine-readable origin trail (e.g. "Synthesized in-house by
    /// scripts/generate_sample_packs.py …"). Optional/additive.
    public let provenance: String?

    public init(
        manifestVersion: Int = 1,
        packId: String,
        name: String,
        family: SampleFamily,
        paletteHint: String? = nil,
        pads: [SamplePad],
        defaultSequence: SequencerPattern? = nil,
        license: String? = nil,
        provenance: String? = nil
    ) {
        self.manifestVersion = manifestVersion
        self.packId = packId
        self.name = name
        self.family = family
        self.paletteHint = paletteHint
        self.pads = pads
        self.defaultSequence = defaultSequence
        self.license = license
        self.provenance = provenance
    }
}

// MARK: - Pads

/// One tile in a SamplePack. `filename` is resolved against the pack's
/// base directory by `SampleBank`:
///   - bundled pack:  Bundle.main URL for App/Resources/Samples/{packId}/pads/
///   - cached pack:   ~/Library/Caches/toneforge/packs/{packId}/pads/
///   - song-derived:  the parent stem file + [startSec, endSec] window
///                    (see `SamplePad.songDerivedSlice` for the
///                    virtual-pad convenience initializer).
public struct SamplePad: Codable, Sendable, Equatable {
    /// Grid position — 0-based, row-major. The mockup uses 4×4 so
    /// 0..15 covers the visible grid.
    public let padIdx: Int
    /// Display name (short — shown under the pad tile).
    public let name: String
    /// Sound family for this specific pad. Drives the pad's tint in
    /// `SamplePadGrid` and its default choke behaviour.
    public let family: SampleFamily
    /// Optional color hint overriding the family default.
    public let colorHint: String?
    /// Basename relative to the pack's `pads/` directory. Non-nil for
    /// bundled + curated packs; nil for song-derived pads where the
    /// audio comes from a stem-slice window instead (see `stemSlice`).
    public let filename: String?
    /// Choke group. Pads sharing a group cancel each other's active
    /// voice on new trigger — used for hi-hat open/close style pairs.
    /// nil = free-running (no choke).
    public let chokeGroup: Int?
    /// If set, held/toggled playback loops from `loopPointSec` back to
    /// the end of the file. nil = one-shot only.
    public let loopPointSec: Double?
    /// Per-pad gain in dB, applied at the voice's per-slot mixer.
    /// Defaults to 0 when absent in the JSON.
    public let gainDb: Double
    /// Pad-specific quantize default. Overrides the pack- and
    /// user-level defaults. nil = inherit.
    public let defaultQuantize: QuantizeMode?
    /// Song-derived pads only: parent stem role ("vocals", "other", …)
    /// and window [startSec, endSec] into that stem. Nil for
    /// file-backed pads. See `SampleBank.loadSongDerived`.
    public let stemSlice: StemSlice?
    /// Pack-defined baseline for per-pad delay + filter effects. Most
    /// packs leave this nil so pads render dry; user overrides
    /// (persisted in SampleSettingsStore.padEffectsByKey) take
    /// precedence at trigger time. See SamplePadEffects.swift for the
    /// three-tier resolution rule.
    public let effects: SamplePadEffects?

    public init(
        padIdx: Int,
        name: String,
        family: SampleFamily,
        colorHint: String? = nil,
        filename: String? = nil,
        chokeGroup: Int? = nil,
        loopPointSec: Double? = nil,
        gainDb: Double = 0,
        defaultQuantize: QuantizeMode? = nil,
        stemSlice: StemSlice? = nil,
        effects: SamplePadEffects? = nil
    ) {
        self.padIdx = padIdx
        self.name = name
        self.family = family
        self.colorHint = colorHint
        self.filename = filename
        self.chokeGroup = chokeGroup
        self.loopPointSec = loopPointSec
        self.gainDb = gainDb
        self.defaultQuantize = defaultQuantize
        self.stemSlice = stemSlice
        self.effects = effects
    }

    // Custom decoding to default `gainDb` when the key is absent.
    // (Swift's synthesized Codable requires the key to be present
    // unless the type is Optional; a plain `Double = 0` init default
    // doesn't apply during decode.)
    private enum CodingKeys: String, CodingKey {
        case padIdx, name, family, colorHint, filename, chokeGroup,
             loopPointSec, gainDb, defaultQuantize, stemSlice, effects
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.padIdx = try c.decode(Int.self, forKey: .padIdx)
        self.name = try c.decode(String.self, forKey: .name)
        self.family = try c.decode(SampleFamily.self, forKey: .family)
        self.colorHint = try c.decodeIfPresent(String.self, forKey: .colorHint)
        self.filename = try c.decodeIfPresent(String.self, forKey: .filename)
        self.chokeGroup = try c.decodeIfPresent(Int.self, forKey: .chokeGroup)
        self.loopPointSec = try c.decodeIfPresent(Double.self, forKey: .loopPointSec)
        self.gainDb = try c.decodeIfPresent(Double.self, forKey: .gainDb) ?? 0
        self.defaultQuantize = try c.decodeIfPresent(QuantizeMode.self, forKey: .defaultQuantize)
        self.stemSlice = try c.decodeIfPresent(StemSlice.self, forKey: .stemSlice)
        self.effects = try c.decodeIfPresent(SamplePadEffects.self, forKey: .effects)
    }
}

/// Song-derived pad backing: a slice of a stem file. `SampleBank`
/// materialises these into `AVAudioPCMBuffer` at pack-activate time.
public struct StemSlice: Codable, Sendable, Equatable {
    /// Compliance cap: no chop may exceed this duration. Enforced at
    /// the single production construction site (`SampleBank.songDerived`)
    /// so every path — bundle presets, ad-hoc chops, scheduler preload,
    /// offline render — plays at most this many seconds of source audio.
    public static let maxChopDurationSec: Double = 8.0

    /// Stem role — must match one of `SongBundle.stems[].role`.
    public let stemRole: String
    /// Window into the stem, in seconds from the stem's origin.
    public let startSec: Double
    public let endSec: Double

    public init(stemRole: String, startSec: Double, endSec: Double) {
        self.stemRole = stemRole
        self.startSec = startSec
        self.endSec = endSec
    }

    /// The slice, clamped so its duration never exceeds `maxDuration`.
    /// Keeps `startSec` (the musically-placed onset) and pulls in
    /// `endSec`; degenerate slices (end ≤ start) clamp to zero length
    /// rather than going negative. Never drops the slice.
    public func clamped(maxDuration: Double = StemSlice.maxChopDurationSec) -> StemSlice {
        let duration = max(0, endSec - startSec)
        guard duration > maxDuration else { return self }
        return StemSlice(stemRole: stemRole, startSec: startSec, endSec: startSec + maxDuration)
    }

    /// Slice length in seconds (never negative).
    public var durationSec: Double { max(0, endSec - startSec) }
}

// MARK: - Family

/// Coarse sonic category — drives palette tint + default choke
/// behaviour. String rawValues match backend + bundled manifest JSON.
public enum SampleFamily: String, Codable, Sendable, CaseIterable {
    /// Sustained harmonic beds (shimmer, string pad, …).
    case pads
    /// Short rhythmic hits (kick, snare, hats, perc).
    case percussion
    /// Atmospheric drones + noise beds.
    case textures
    /// Short melodic/chordal hits (piano stab, guitar stab).
    case stabs
    /// Bass hits + sub drops.
    case bass
    /// FX tails, risers, impacts, reverse swells.
    case fx
    /// Vocal chops (song-derived vocals).
    case vocals
    /// Pack contains a mix of families (used at pack level, not pad).
    case mixed
}

// MARK: - Quantize

/// Trigger-time snapping policy. The rawValue strings are the exact
/// tokens that appear in manifest JSON and in persisted user
/// settings, so bumping/renaming a case is a schema break.
public enum QuantizeMode: String, Codable, Sendable, CaseIterable, Equatable {
    /// Play immediately on tap. No snap.
    case off       = "off"
    /// Snap to next 1/8-note boundary.
    case eighth    = "1/8"
    /// Snap to next 1/4-note (beat) boundary.
    case quarter   = "1/4"
    /// Snap to next 1/2-note boundary.
    case half      = "1/2"
    /// Snap to next downbeat (bar).
    case bar       = "1 bar"
    /// Snap to next section boundary (chorus/verse/etc.).
    case phrase    = "phrase"
}

// MARK: - Hold / Beat-Bar modes (persisted with pack settings)

/// Behaviour when a pad is triggered.
public enum HoldMode: String, Codable, Sendable, CaseIterable {
    /// Voice plays while pad is held; release fades out on touch-up.
    case hold
    /// First tap starts (loops if pad has a loop point), second stops.
    case toggle
}

/// How the quantize grid is interpreted at the "1" divisor.
public enum BeatBarMode: String, Codable, Sendable, CaseIterable {
    /// "1" means one beat.
    case beat
    /// "1" means one bar (downbeat-to-downbeat).
    case bar
}
