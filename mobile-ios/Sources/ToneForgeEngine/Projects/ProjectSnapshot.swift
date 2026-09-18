// ProjectSnapshot.swift
//
// Projects/Workspaces v1 — the cross-surface wire contract for a saved
// per-song pad workspace. PURE Codable value types: no app-target
// imports, no @MainActor, no UI. This file is what jam-desktop (and,
// later, web/plugin) compile against when their stores go per-song, so
// every field's coordinate space and tri-state is spelled out here, not
// in the store that happens to write it.
//
// A Project = { id, name, createdAt, updatedAt, baseSongId, snapshot }.
// baseSongId is the analysisId and is REQUIRED in v1 — a workspace is
// always anchored to one analyzed song; song-less (sketch) workspaces
// are a later schema bump.
//
// Versioning: `schemaVersion` starts at 1. Readers must tolerate
// unknown ADDITIVE fields (decodeIfPresent) and reject only a major
// bump they don't know. All new fields must be optional or defaulted.

import Foundation

// MARK: - Borrow reference (content-addressed)

/// A CONTENT-ADDRESSED reference to one borrowed loop pad.
///
/// The audit's hard requirement: NEVER persist the borrow response's
/// `padIdx` as identity. That index is renumbered per response (host
/// block then donor block) and the donor's curated-kit selection drifts
/// with pad-usage feedback — a saved padIdx silently points at a
/// different loop tomorrow. Identity is the donor-timeline loop span
/// (the same span that keys the backend's render cache) plus, when the
/// donor kit pad carries one, its stable graph `assetId`.
///
/// Restore re-requests the donor's borrow (same donor + stem) and
/// matches the returned pads by `(loopStartSec, loopEndSec)` /
/// `assetId` — never by response index. `targetPadIdx` is PLACEMENT
/// only (where the pad sat in this workspace's arranged grid), not
/// identity, and restore may re-derive placement when the donor's kit
/// has drifted.
public struct BorrowRef: Codable, Equatable, Sendable {
    /// The donor song's analysisId — which song's borrow to re-request.
    public let donorSongId: String
    /// CONCRETE stem role in the donor ("drums", "bass",
    /// "guitar_center", "vocals", …) — the borrow pad's own `stemRole`
    /// wire field, not the logical family.
    public let stemRole: String
    /// Donor-timeline loop span in seconds — the borrow response's
    /// `sourceLoopStartSec`/`sourceLoopEndSec` fields (deliberately NOT
    /// the client-facing `loopStartSec`/`loopEndSec` window keys; see
    /// backend/tone_forge/performance/borrow.py render_kit_loops).
    public let loopStartSec: Double
    public let loopEndSec: Double
    /// Signed semitone transpose the render applied when this loop was
    /// captured (0 for pitchless drums and for the host's own pads).
    public let transposeSemis: Int
    /// PLACEMENT (not identity): the arranged pack slot (0-based,
    /// row-major as laid out by `SampleBank.arrangeBorrowLayout`) the
    /// pad occupied in this workspace. Restore prefers it but may
    /// re-place when the re-fetched kit differs.
    public let targetPadIdx: Int
    /// The donor kit pad's stable graph-asset id, when the borrow
    /// response carried one. A secondary match key: a donor loop whose
    /// span was re-cut but whose graph asset survived still resolves.
    /// Additive/optional (absent on refs saved before the backend
    /// exposed it).
    public let assetId: String?
    /// Display-only: the donor song's title at capture time, so a
    /// failed restore can say "needs <donor>" without a history
    /// lookup. Never used for matching. Additive/optional.
    public let donorName: String?

    public init(
        donorSongId: String,
        stemRole: String,
        loopStartSec: Double,
        loopEndSec: Double,
        transposeSemis: Int,
        targetPadIdx: Int,
        assetId: String? = nil,
        donorName: String? = nil
    ) {
        self.donorSongId = donorSongId
        self.stemRole = stemRole
        self.loopStartSec = loopStartSec
        self.loopEndSec = loopEndSec
        self.transposeSemis = transposeSemis
        self.targetPadIdx = targetPadIdx
        self.assetId = assetId
        self.donorName = donorName
    }

    /// Build a ref from a mounted borrow pad. Returns nil for pads that
    /// carry no content address (host "initial" pads, pads from a
    /// pre-span backend, non-borrow pads) — those are NOT persistable
    /// as borrows and must never fall back to padIdx identity.
    public init?(pad: SamplePad, donorSongId: String, donorName: String? = nil) {
        guard pad.source == "donor",
              let a = pad.sourceLoopStartSec,
              let b = pad.sourceLoopEndSec, b > a
        else { return nil }
        self.init(
            donorSongId: donorSongId,
            stemRole: pad.stemRole ?? "",
            loopStartSec: a,
            loopEndSec: b,
            transposeSemis: pad.transposeSemis ?? 0,
            targetPadIdx: pad.padIdx,
            assetId: pad.assetId,
            donorName: donorName
        )
    }

    /// The identity key restore matches on — span (rounded to the same
    /// 1 ms grain the backend rounds its cache key to) within the
    /// donor+stem. `targetPadIdx` is deliberately excluded: two refs
    /// that differ only in placement are the SAME loop.
    public var contentKey: String {
        let a = (loopStartSec * 1000).rounded() / 1000
        let b = (loopEndSec * 1000).rounded() / 1000
        return "\(donorSongId)|\(stemRole)|\(a)|\(b)"
    }

    /// Whether `pad` (from a fresh borrow response) IS this loop —
    /// span match (±2 ms, the backend rounds spans to 1 ms) on the
    /// donor's pads. assetId, when both sides carry one, is accepted
    /// as an alternative match so a re-cut span that kept its graph
    /// asset still resolves.
    public func matches(pad: SamplePad) -> Bool {
        guard pad.source == "donor" else { return false }
        if let mine = assetId, let theirs = pad.assetId, mine == theirs {
            return true
        }
        if let a = pad.sourceLoopStartSec, let b = pad.sourceLoopEndSec,
           abs(a - loopStartSec) < 0.002, abs(b - loopEndSec) < 0.002,
           (pad.stemRole ?? "") == stemRole {
            return true
        }
        return false
    }
}

// MARK: - Launchpad settings

/// Launchpad surface settings captured with the workspace. Raw values
/// only — engine types must NOT reference app-target enums (iOS's
/// `SampleTriggerMode` lives in ToneForgeMobile), so the trigger mode
/// travels as its String rawValue ("oneShot" | "follow" | "latch");
/// readers map unknown values to their default.
public struct LaunchpadSnapshot: Codable, Equatable, Sendable {
    /// Grid layout: 16 (4×4) or 64 (8×8). Readers clamp anything else
    /// to 16.
    public var padCount: Int
    /// `SampleTriggerMode.rawValue` — see above.
    public var sampleTriggerMode: String

    public init(padCount: Int, sampleTriggerMode: String) {
        self.padCount = padCount
        self.sampleTriggerMode = sampleTriggerMode
    }
}

// MARK: - Snapshot

/// Everything a per-song pad workspace is, as one Codable value.
///
/// COORDINATE SPACE (padAssignments): outer keys are `AppMode.rawValue`
/// strings ("sample" | "hybrid" | "jamInKey" | …); inner keys are
/// STRINGIFIED iOS `PadIndex` rawValues — `row * 10 + col` with row and
/// col in 1..8 and **row 1 = the BOTTOM row**, so valid keys run
/// "11".."88" (bottom-left = "11", top-right = "88"). This matches the
/// Launchpad Programmer-Mode addressing that launchpad.js and
/// PadTypes.swift already share. String keys because JSON objects
/// require them (and Swift's JSONEncoder would otherwise emit an
/// Int-keyed dictionary as a flat array).
///
/// The slot value is the EXISTING frozen `PadSlot`/`PadSampleReference`
/// wire encoding (type-discriminated: "packPad" | "localSample" |
/// "sequence"). NATIVE-ONLY refs: `.localSample(UUID)` (a device-local
/// recorded/baked sample) and `.sequence(patternId)` (a saved sequencer
/// pattern) reference on-device stores that web/desktop don't have —
/// their contract is PRESERVE-ON-ROUND-TRIP: a surface that can't
/// resolve them must keep the slot bytes intact when re-saving and
/// render the pad inert (visibly unavailable, never dropped).
public struct ProjectSnapshot: Codable, Equatable, Sendable {
    public static let currentSchemaVersion = 1

    public var schemaVersion: Int

    /// AppMode.rawValue → String(PadIndex.rawValue 11..88) → PadSlot.
    /// See the type doc above for the coordinate-space contract.
    public var padAssignments: [String: [String: PadSlot]]

    /// Per-pad FX overrides, keyed "packId#padIdx" (the pack pad's own
    /// index, NOT a grid PadIndex).
    public var padFX: [String: SamplePadEffects]

    /// Hidden pack pads, keyed "packId#padIdx" (same key space as
    /// `padFX`).
    public var hiddenPads: Set<String>

    /// Section allowlist — TRI-STATE, and the distinction is
    /// load-bearing (mirrors `SectionResolver.isAllowed`):
    ///   * nil / absent key  → allow ALL sections (user never gated)
    ///   * empty array       → deny ALL sections
    ///   * non-empty         → allow only the listed labels
    /// Encoders must OMIT the key when nil (encodeIfPresent) — writing
    /// `[]` for "not gated" silently mutes every section on restore.
    public var sectionGates: [String]?

    /// Sequencer patterns referenced by this workspace's pads
    /// (`.sequence(patternId)` slots). The native `SequencerPattern`
    /// wire format IS the cross-platform format (Remix/groove wire) —
    /// canonical here too. Restore upserts them by id (idempotent).
    public var sequencerPatterns: [SequencerPattern]

    /// Chop-boundary edits for the base song, keyed by presetKey
    /// ("harmonic", "sections", …) — the same per-song shape
    /// jam-desktop's ChopEditStore persists (`analysisId → presetKey →
    /// ChopEdits`), minus the analysisId (the Project's baseSongId IS
    /// the song). Without these, pads restore with the ANALYZER's
    /// boundaries instead of the user's edited ones. nil = no edits.
    /// (iOS currently has no chop-edit store — its ChopEditorSheet is
    /// preview-only — so iOS captures nil today; the field is live for
    /// desktop and future iOS persistence.)
    public var chopEdits: [String: ChopEdits]?

    /// Live-capture arrangement: String(blockIndex) → [grid padIdx],
    /// the `{blockIndex: [padIdx]}` shape all surfaces share
    /// (Arrangement.serialize / kit.js / ArrangementStore). String
    /// keys for the same JSON-object reason as `padAssignments`.
    public var arrangement: [String: [Int]]?

    /// Launchpad surface settings (padCount, trigger mode rawValue).
    public var launchpad: LaunchpadSnapshot?

    /// Content-addressed borrowed-loop refs. See `BorrowRef` — never
    /// response padIdx identity.
    public var borrows: [BorrowRef]

    public init(
        schemaVersion: Int = ProjectSnapshot.currentSchemaVersion,
        padAssignments: [String: [String: PadSlot]] = [:],
        padFX: [String: SamplePadEffects] = [:],
        hiddenPads: Set<String> = [],
        sectionGates: [String]? = nil,
        sequencerPatterns: [SequencerPattern] = [],
        chopEdits: [String: ChopEdits]? = nil,
        arrangement: [String: [Int]]? = nil,
        launchpad: LaunchpadSnapshot? = nil,
        borrows: [BorrowRef] = []
    ) {
        self.schemaVersion = schemaVersion
        self.padAssignments = padAssignments
        self.padFX = padFX
        self.hiddenPads = hiddenPads
        self.sectionGates = sectionGates
        self.sequencerPatterns = sequencerPatterns
        self.chopEdits = chopEdits
        self.arrangement = arrangement
        self.launchpad = launchpad
        self.borrows = borrows
    }

    // Custom Codable: every field except schemaVersion decodes-if-
    // present so additive schema growth never bricks an older snapshot,
    // and the tri-state optionals (sectionGates!) encode-if-present so
    // nil stays ABSENT on the wire, never null/[].
    private enum CodingKeys: String, CodingKey {
        case schemaVersion, padAssignments, padFX, hiddenPads,
             sectionGates, sequencerPatterns, chopEdits, arrangement,
             launchpad, borrows
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try c.decode(Int.self, forKey: .schemaVersion)
        self.padAssignments = try c.decodeIfPresent(
            [String: [String: PadSlot]].self, forKey: .padAssignments) ?? [:]
        self.padFX = try c.decodeIfPresent(
            [String: SamplePadEffects].self, forKey: .padFX) ?? [:]
        self.hiddenPads = Set(try c.decodeIfPresent(
            [String].self, forKey: .hiddenPads) ?? [])
        self.sectionGates = try c.decodeIfPresent(
            [String].self, forKey: .sectionGates)
        self.sequencerPatterns = try c.decodeIfPresent(
            [SequencerPattern].self, forKey: .sequencerPatterns) ?? []
        self.chopEdits = try c.decodeIfPresent(
            [String: ChopEdits].self, forKey: .chopEdits)
        self.arrangement = try c.decodeIfPresent(
            [String: [Int]].self, forKey: .arrangement)
        self.launchpad = try c.decodeIfPresent(
            LaunchpadSnapshot.self, forKey: .launchpad)
        self.borrows = try c.decodeIfPresent(
            [BorrowRef].self, forKey: .borrows) ?? []
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(schemaVersion, forKey: .schemaVersion)
        try c.encode(padAssignments, forKey: .padAssignments)
        try c.encode(padFX, forKey: .padFX)
        // Sorted for stable JSON output (Set order is random).
        try c.encode(hiddenPads.sorted(), forKey: .hiddenPads)
        // TRI-STATE: nil must be ABSENT (allow all), [] must be present
        // (deny all). encodeIfPresent gives exactly that.
        try c.encodeIfPresent(sectionGates, forKey: .sectionGates)
        try c.encode(sequencerPatterns, forKey: .sequencerPatterns)
        try c.encodeIfPresent(chopEdits, forKey: .chopEdits)
        try c.encodeIfPresent(arrangement, forKey: .arrangement)
        try c.encodeIfPresent(launchpad, forKey: .launchpad)
        try c.encode(borrows, forKey: .borrows)
    }
}

// MARK: - Project (metadata + snapshot)

/// A saved workspace: metadata plus the snapshot payload. One JSON
/// file per project on every surface that persists these.
public struct Project: Codable, Equatable, Sendable, Identifiable {
    public let id: UUID
    public var name: String
    public let createdAt: Date
    public var updatedAt: Date
    /// The base song's analysisId. REQUIRED in v1 — loading a project
    /// loads this song first, then restores the snapshot over it.
    public let baseSongId: String
    /// Display-only: the base song's title at save time, so lists can
    /// show it without a history lookup. Additive/optional.
    public var baseSongTitle: String?
    public var snapshot: ProjectSnapshot

    public init(
        id: UUID = UUID(),
        name: String,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        baseSongId: String,
        baseSongTitle: String? = nil,
        snapshot: ProjectSnapshot
    ) {
        self.id = id
        self.name = name
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.baseSongId = baseSongId
        self.baseSongTitle = baseSongTitle
        self.snapshot = snapshot
    }

    /// A duplicate with a fresh identity and name; timestamps reset so
    /// the copy sorts as newly created.
    public func duplicated(name: String, now: Date = Date()) -> Project {
        Project(
            id: UUID(),
            name: name,
            createdAt: now,
            updatedAt: now,
            baseSongId: baseSongId,
            baseSongTitle: baseSongTitle,
            snapshot: snapshot
        )
    }
}
