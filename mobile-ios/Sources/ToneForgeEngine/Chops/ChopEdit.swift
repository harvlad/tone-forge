// ChopEdit.swift
//
// Chop boundary edits that overlay bundle chops at runtime (D-023).
// The original bundle data stays unchanged; edits are per-analysisId
// in ChopEditStore (UserDefaults). This keeps the bundle immutable
// (re-analysis overwrites cleanly) while letting users refine chop
// boundaries via the waveform editor.
//
// Edit types:
//   - Boundary adjustment: shift startSec/endSec within the original range
//   - Split: create two synthetic chops from one parent
//   - Merge: collapse two adjacent chops into one
//
// The pure `resolvedChops` function applies all edits to produce the
// final chop array the audio engine uses. Callers should cache this
// result and invalidate when edits change.

import Foundation

// MARK: - Boundary Edit

/// A single chop's adjusted boundaries. The original chop data is
/// preserved in `originalStart`/`originalEnd` so the edit can be
/// reset without re-fetching the bundle.
public struct ChopBoundaryEdit: Codable, Equatable, Sendable {
    /// Index into the preset's chops array.
    public let chopIndex: Int
    /// Original boundaries from the bundle (for reset).
    public let originalStart: Double
    public let originalEnd: Double
    /// User-adjusted boundaries.
    public var editedStart: Double
    public var editedEnd: Double

    public init(
        chopIndex: Int,
        originalStart: Double,
        originalEnd: Double,
        editedStart: Double,
        editedEnd: Double
    ) {
        self.chopIndex = chopIndex
        self.originalStart = originalStart
        self.originalEnd = originalEnd
        self.editedStart = editedStart
        self.editedEnd = editedEnd
    }

    /// Create an edit from an existing Chop (no adjustment yet).
    public init(from chop: Chop) {
        self.chopIndex = chop.idx
        self.originalStart = chop.startSec
        self.originalEnd = chop.endSec
        self.editedStart = chop.startSec
        self.editedEnd = chop.endSec
    }

    /// Whether this edit differs from the original boundaries.
    public var isModified: Bool {
        abs(editedStart - originalStart) > 0.001 ||
        abs(editedEnd - originalEnd) > 0.001
    }

    /// Clamp edited boundaries to valid range.
    public mutating func clamp(minStart: Double = 0, maxEnd: Double) {
        editedStart = max(minStart, min(editedStart, editedEnd - 0.01))
        editedEnd = max(editedStart + 0.01, min(editedEnd, maxEnd))
    }
}

// MARK: - Split

/// A split divides one parent chop into two at `splitPoint`.
/// The parent chop is replaced by two synthetic chops:
///   - First: startSec → splitPoint
///   - Second: splitPoint → endSec
public struct ChopSplit: Codable, Equatable, Sendable {
    /// Index of the parent chop being split.
    public let parentIndex: Int
    /// Song time where the split occurs.
    public let splitPoint: Double

    public init(parentIndex: Int, splitPoint: Double) {
        self.parentIndex = parentIndex
        self.splitPoint = splitPoint
    }
}

// MARK: - Merge

/// A merge collapses two adjacent chops into one. The resulting
/// chop spans from the first's startSec to the second's endSec.
public struct ChopMerge: Codable, Equatable, Sendable {
    /// Index of the first chop (lower startSec).
    public let firstIndex: Int
    /// Index of the second chop (higher startSec).
    public let secondIndex: Int

    public init(firstIndex: Int, secondIndex: Int) {
        self.firstIndex = firstIndex
        self.secondIndex = secondIndex
    }
}

// MARK: - Edit Collection

/// All edits for one preset within one song.
public struct ChopEdits: Codable, Equatable, Sendable {
    /// Schema version for future migrations.
    public static let schemaVersion = 1
    public var schemaVersion: Int = ChopEdits.schemaVersion

    /// Preset key (e.g., "harmonic", "sections").
    public let presetKey: String

    /// Boundary edits, keyed by original chop index.
    public var boundaryEdits: [Int: ChopBoundaryEdit] = [:]

    /// Splits in application order.
    public var splits: [ChopSplit] = []

    /// Merges in application order.
    public var merges: [ChopMerge] = []

    public init(presetKey: String) {
        self.presetKey = presetKey
    }

    /// Whether any edits exist.
    public var hasEdits: Bool {
        !boundaryEdits.isEmpty || !splits.isEmpty || !merges.isEmpty
    }

    /// Clear all edits, returning to bundle state.
    public mutating func reset() {
        boundaryEdits.removeAll()
        splits.removeAll()
        merges.removeAll()
    }
}

// MARK: - Resolved Chop

/// A chop with all edits applied. May be synthetic (from a split)
/// or derived from the original bundle data.
public struct ResolvedChop: Equatable, Sendable {
    /// Unique identifier for this resolved chop. For original chops,
    /// this matches the bundle index. For synthetic chops (splits),
    /// it's a generated negative index.
    public let id: Int
    /// Parent index from the bundle (nil for merges).
    public let parentIndex: Int?
    /// Final start time after edits.
    public let startSec: Double
    /// Final end time after edits.
    public let endSec: Double
    /// Duration in seconds.
    public var durationSec: Double { endSec - startSec }
    /// Original chop metadata (kind, root, sectionLabel, etc.).
    /// Nil for fully synthetic chops.
    public let metadata: ChopMetadata?
    /// Whether this chop was created by a split.
    public let isSynthetic: Bool
    /// Whether this chop has boundary edits applied.
    public let isEdited: Bool

    public init(
        id: Int,
        parentIndex: Int?,
        startSec: Double,
        endSec: Double,
        metadata: ChopMetadata?,
        isSynthetic: Bool,
        isEdited: Bool
    ) {
        self.id = id
        self.parentIndex = parentIndex
        self.startSec = startSec
        self.endSec = endSec
        self.metadata = metadata
        self.isSynthetic = isSynthetic
        self.isEdited = isEdited
    }
}

/// Metadata from the original Chop, preserved through edits.
public struct ChopMetadata: Codable, Equatable, Sendable {
    public let kind: String?
    public let root: Int?
    public let sectionLabel: String?
    public let chordSymbol: String?
    public let colorHint: String?

    public init(from chop: Chop) {
        self.kind = chop.kind
        self.root = chop.root
        self.sectionLabel = chop.sectionLabel
        self.chordSymbol = chop.chordSymbol
        self.colorHint = chop.colorHint
    }

    public init(
        kind: String? = nil,
        root: Int? = nil,
        sectionLabel: String? = nil,
        chordSymbol: String? = nil,
        colorHint: String? = nil
    ) {
        self.kind = kind
        self.root = root
        self.sectionLabel = sectionLabel
        self.chordSymbol = chordSymbol
        self.colorHint = colorHint
    }
}

// MARK: - Resolution

/// Apply all edits to a preset's chops array, producing the final
/// resolved chops for playback. This is a pure function — same inputs,
/// same outputs — so results can be cached and invalidated when edits
/// change.
///
/// Resolution order:
///   1. Start with bundle chops
///   2. Apply boundary edits (adjust start/end)
///   3. Apply splits (replace parent with two children)
///   4. Apply merges (collapse two into one)
///   5. Sort by startSec
public func resolvedChops(
    bundleChops: [Chop],
    edits: ChopEdits
) -> [ResolvedChop] {
    // Start with bundle chops, applying boundary edits.
    var working: [ResolvedChop] = bundleChops.map { chop in
        let edit = edits.boundaryEdits[chop.idx]
        let start = edit?.editedStart ?? chop.startSec
        let end = edit?.editedEnd ?? chop.endSec
        return ResolvedChop(
            id: chop.idx,
            parentIndex: chop.idx,
            startSec: start,
            endSec: end,
            metadata: ChopMetadata(from: chop),
            isSynthetic: false,
            isEdited: edit?.isModified ?? false
        )
    }

    // Apply splits. Each split replaces the parent with two children.
    // Process in order; use negative IDs for synthetic chops.
    var nextSyntheticId = -1
    for split in edits.splits {
        guard let parentIdx = working.firstIndex(where: { $0.id == split.parentIndex }) else {
            continue
        }
        let parent = working[parentIdx]
        // Validate split point is within bounds.
        guard split.splitPoint > parent.startSec,
              split.splitPoint < parent.endSec else {
            continue
        }

        let first = ResolvedChop(
            id: nextSyntheticId,
            parentIndex: parent.parentIndex,
            startSec: parent.startSec,
            endSec: split.splitPoint,
            metadata: parent.metadata,
            isSynthetic: true,
            isEdited: parent.isEdited
        )
        nextSyntheticId -= 1

        let second = ResolvedChop(
            id: nextSyntheticId,
            parentIndex: parent.parentIndex,
            startSec: split.splitPoint,
            endSec: parent.endSec,
            metadata: parent.metadata,
            isSynthetic: true,
            isEdited: parent.isEdited
        )
        nextSyntheticId -= 1

        // Replace parent with the two children.
        working.remove(at: parentIdx)
        working.insert(contentsOf: [first, second], at: parentIdx)
    }

    // Apply merges. Each merge collapses two chops into one.
    for merge in edits.merges {
        guard let firstIdx = working.firstIndex(where: { $0.id == merge.firstIndex }),
              let secondIdx = working.firstIndex(where: { $0.id == merge.secondIndex }),
              firstIdx != secondIdx else {
            continue
        }

        let first = working[firstIdx]
        let second = working[secondIdx]

        // Merged chop spans both.
        let merged = ResolvedChop(
            id: nextSyntheticId,
            parentIndex: nil, // Merged from two parents.
            startSec: min(first.startSec, second.startSec),
            endSec: max(first.endSec, second.endSec),
            metadata: first.metadata, // Keep first's metadata.
            isSynthetic: true,
            isEdited: true
        )
        nextSyntheticId -= 1

        // Remove both originals and insert merged.
        // Remove higher index first to avoid shifting.
        let indicesToRemove = [firstIdx, secondIdx].sorted(by: >)
        for idx in indicesToRemove {
            working.remove(at: idx)
        }
        working.append(merged)
    }

    // Sort by start time.
    return working.sorted { $0.startSec < $1.startSec }
}

// MARK: - Convenience

extension ResolvedChop {
    /// Convert back to a Chop for APIs that expect the bundle type.
    /// Uses the resolved ID as idx.
    public func toChop() -> Chop {
        Chop(
            idx: id,
            startSec: startSec,
            endSec: endSec,
            durationSec: durationSec,
            kind: metadata?.kind,
            root: metadata?.root,
            sectionLabel: metadata?.sectionLabel,
            chordSymbol: metadata?.chordSymbol,
            colorHint: metadata?.colorHint
        )
    }
}
