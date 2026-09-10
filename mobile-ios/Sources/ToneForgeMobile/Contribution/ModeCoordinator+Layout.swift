// ModeCoordinator+Layout.swift
//
// Grid painting: rebuilds the immutable layout snapshot the router
// consumes, computes the 64 PadVisuals, and owns the pad-binding map
// (grid index → scheduler key) as a byproduct of painting the sample
// quadrant. Split from ModeCoordinator.swift — same class, same
// behavior; see that file's header for the coordinator's contract.

import Foundation
import ToneForgeEngine

extension ModeCoordinator {

    // MARK: - Layout

    /// Rebuild the layout snapshot + pad bindings + visuals. Call on
    /// mode change, pack activation, and bundle load/clear. Also
    /// re-arms transform renders: pack activation makes new base
    /// buffers resident and bundle load/clear changes the tempo the
    /// synced transforms (stutter/gate) rendered against.
    public func refreshLayout() {
        rebuildLayout()
        syncTransforms()
    }

    /// Chord advanced (hybrid + jam brighten the sounding chord's
    /// tones). Cheap no-op in sample mode.
    public func chordChanged() {
        guard appMode == .hybrid || appMode == .jamInKey else { return }
        rebuildLayout()
    }

    func rebuildLayout() {
        let content = sampleQuadrantContent()
        // Jam/Perform in SAMPLES mode always shows the active pack's
        // labeled quadrant, whatever the underlying appMode — this was
        // racing with refreshJamSamplesLEDs (an unlabeled LED overwrite),
        // flip-flopping the grid between all-"+" and label-less tiles.
        if app.jamSettings.padMode == .samples {
            layout = SampleModeLayout(content: content)
            var visuals = [PadVisual](repeating: .off, count: 64)
            for row in 1...8 {
                for col in 1...8 {
                    visuals[(row - 1) * 8 + (col - 1)] =
                        layout.visual(at: PadIndex.at(row: row, col: col))
                }
            }
            // Hardware Launchpad: light the 48 non-quadrant cells with
            // the song's overflow chops (jamSampleAt maps them by the
            // same reading order) so the full 8×8 plays the whole song
            // instead of leaving dead unlit cells. The on-screen 4×4
            // only reads quadrant cells, so this is LED/hardware-only.
            let overflow = app.jamOverflowPads
            if !overflow.isEmpty {
                for row in 1...8 {
                    for col in 1...8 {
                        guard let idx = Self.jamOverflowIndex(row: row, col: col),
                              idx < overflow.count
                        else { continue }
                        let pad = overflow[idx]
                        visuals[(row - 1) * 8 + (col - 1)] = PadVisual(
                            colorHint: Self.familyColor(pad.family),
                            label: pad.name
                        )
                    }
                }
            }
            padVisuals = visuals
            return
        }
        switch appMode {
        case .sample:
            layout = SampleModeLayout(content: content)
        case .hybrid:
            layout = HybridModeLayout(
                keyLabel: app.currentBundle?.meta.detectedKey,
                chordPitchClasses: currentChordPitchClasses(),
                sampleContent: content
            )
        case .jamInKey:
            layout = JamInKeyLayout(
                key: app.jamSettings.effectiveKey(
                    detectedKey: app.currentBundle?.meta.detectedKey,
                    analysisId: app.currentBundle?.analysisId
                ),
                chordPitchClasses: app.jamSettings.highlightCurrentChord
                    ? currentChordPitchClasses() : [],
                octaveShift: app.jamSettings.octaveShift
            )
        default:
            layout = EmptyLayout()
        }
        var visuals = [PadVisual](repeating: .off, count: 64)
        for row in 1...8 {
            for col in 1...8 {
                visuals[(row - 1) * 8 + (col - 1)] =
                    layout.visual(at: PadIndex.at(row: row, col: col))
            }
        }
        padVisuals = visuals
    }

    /// Grid pads (PadIndex rawValues) whose bound sample pad has a
    /// ringing looping voice — drives the on-screen "still sounding"
    /// outline. Only the active pack's quadrant is bound, so ringing
    /// voices from *other* packs don't light the grid; the Play tab's
    /// stop-all button covers those.
    func ringingGridPads(from keys: Set<SamplePadKey>) -> Set<Int> {
        guard !keys.isEmpty else { return [] }
        var out: Set<Int> = []
        for (raw, binding) in padBindings
        where keys.contains(
            SamplePadKey(packId: binding.packId, padIdx: binding.padIdx)
        ) {
            out.insert(raw)
        }
        return out
    }

    /// Active pack's 16 pads mapped into the top-left 4×4 quadrant:
    /// pack padIdx p (row-major from the pack's top row) → grid
    /// row 8 - p/4, col p%4 + 1. Local sample assignments (P3) then
    /// overlay the pack content — matching the audio path, where a
    /// local buffer shadows the pack pad at the same grid index.
    /// Also refreshes `padBindings`.
    private func sampleQuadrantContent() -> [Int: PadContent] {
        padBindings = [:]
        var content: [Int: PadContent] = [:]
        if let active = app.activeSamplePack {
            let packId = active.pack.packId
            // Borrow manifests carry BOTH songs' loops tagged `source`
            // ("initial"/"donor"). AppState.loadBorrowLoops runs them through
            // SampleBank.arrangeBorrowLayout, so their padIdx is already the
            // web-parity 64-grid arrangement (current song top rows, a blank
            // divider row, donor below — row-major, 8 wide). Paint the FULL
            // 8×8 with a TOP-origin mapping so the on-screen grid matches web
            // (pad 0 = top-left); non-borrow packs keep the 4×4 bottom-left
            // quadrant with the hardware-launchpad bottom-origin orientation.
            let isBorrow = active.pack.pads.contains {
                $0.source == "donor" || $0.source == "initial"
            }
            let padRange = isBorrow ? 0..<64 : 0..<16
            for pad in active.pack.pads where padRange.contains(pad.padIdx) {
                // Skip hidden pads
                if app.sampleSettings.isPadHidden(packId: packId, padIdx: pad.padIdx) {
                    continue
                }
                let grid: PadIndex = isBorrow
                    // Web-parity: padIdx 0 = top-left, rows fill downward,
                    // 8 wide. PadIndex rows run BOTTOM-up, so row = 8 - p/8.
                    ? PadIndex.at(row: 8 - pad.padIdx / 8, col: pad.padIdx % 8 + 1)
                    : PadIndex.at(
                        // Hardware-launchpad orientation (matches the jamn
                        // Kit plugin): pad 0 lands BOTTOM-left, rows climb.
                        row: 5 + pad.padIdx / 4,
                        col: pad.padIdx % 4 + 1
                    )
                padBindings[grid.rawValue] = (packId: packId, padIdx: pad.padIdx)
                // Show edited badge if user has modified effects from baseline
                let hasEffectsOverride = app.sampleSettings
                    .padEffectsOverride(packId: packId, padIdx: pad.padIdx) != nil
                // Loop state: radial per-pad override wins; else manifest
                // (loopPointSec or the kit's loopable flag).
                let loopKey = SamplePadKey(packId: packId, padIdx: pad.padIdx)
                let loops = app.sampleScheduler.padLoopOverrides[loopKey]
                    ?? (pad.loopPointSec != nil || (pad.loopable ?? false))
                content[grid.rawValue] = PadContent(
                    label: pad.name,
                    colorHint: Self.hexColorHint(pad.colorHint)
                        ?? Self.familyColor(pad.family),
                    badge: hasEffectsOverride ? .edited : nil,
                    loops: loops,
                    // Borrow: the small source-song line under the tint
                    // (arrangeBorrowLayout stamped it; nil on other packs).
                    sourceLabel: isBorrow ? pad.sourceName : nil
                )
            }
        }
        for (gridRaw, slot) in app.padAssignmentStore.assignments(for: appMode) {
            switch slot.ref {
            case .localSample(let id):
                guard let meta = app.padSampleStore.metadata(id: id)
                else { continue }
                // Local pads bind to the scheduler's synthetic pack
                // under their GRID index — the same key
                // setLocalBuffer used.
                padBindings[gridRaw] = (
                    packId: SampleScheduler.localPackId, padIdx: gridRaw
                )
                content[gridRaw] = PadContent(
                    label: Self.classLabel(meta.effectiveClass),
                    colorHint: meta.colorHint != 0
                        ? meta.colorHint
                        : Self.localColor(meta.source),
                    badge: Self.transformBadge(slot.transforms)
                        ?? Self.localBadge(meta.source),
                    loops: slot.transforms.contains(.loop)
                )

            case .packPad(let packId, let padIdx):
                if let binding = padBindings[gridRaw],
                   binding.packId == packId, binding.padIdx == padIdx {
                    // The active pack already painted this exact pad
                    // here — overlay the transform-chain badge only (P4).
                    guard let badge = Self.transformBadge(slot.transforms),
                          var existing = content[gridRaw]
                    else { continue }
                    existing.badge = badge
                    existing.loops =
                        existing.loops || slot.transforms.contains(.loop)
                    content[gridRaw] = existing
                } else {
                    // A pad from a pack other than the fronted one,
                    // pinned to this single grid cell. Bind + paint it
                    // from its own pack manifest so it triggers and shows.
                    guard let info = app.packPadInfo(packId: packId, padIdx: padIdx)
                    else { continue }
                    app.ensurePackLoaded(packId: packId)
                    padBindings[gridRaw] = (packId: packId, padIdx: padIdx)
                    content[gridRaw] = PadContent(
                        label: info.name,
                        colorHint: Self.familyColor(info.family),
                        badge: Self.transformBadge(slot.transforms),
                        loops: info.loops || slot.transforms.contains(.loop)
                    )
                }

            case .sequence(let patternId):
                // A saved sequence assigned to this pad. No scheduler
                // buffer — playback is handled by SequencePadManager on
                // touch. Show a labeled tile so the pad isn't "empty".
                let name = app.sequencerPatternStore
                    .pattern(id: patternId)?.name ?? "Sequence"
                padBindings[gridRaw] = nil
                content[gridRaw] = PadContent(
                    label: name,
                    colorHint: Self.sequenceColor,
                    badge: .loop,
                    loops: true
                )
            }
        }
        return content
    }

    // MARK: - Palette + labels

    /// Tile color (0xRRGGBB) for pads holding a saved sequence.
    static let sequenceColor: UInt32 = 0x30D5C8

    /// Grid badge for a pad carrying a transform chain: `.loop` when
    /// the chain loops, `.transformed` otherwise, nil for no chain.
    static func transformBadge(_ chain: [PadTransform]) -> PadBadge? {
        guard !chain.isEmpty else { return nil }
        return chain.contains(.loop) ? .loop : .transformed
    }

    /// Provenance color (0xRRGGBB) for local samples whose metadata
    /// carries no colorHint. Mic = warm orange, vocoded = purple.
    static func localColor(_ source: PadSampleMetadata.Source) -> UInt32 {
        switch source {
        case .mic:      return 0xFF8C3A
        case .vocoded:  return 0x9B4DFF
        case .songChop: return 0x9CA3AF
        }
    }

    static func localBadge(_ source: PadSampleMetadata.Source) -> PadBadge {
        switch source {
        case .mic:      return .mic
        case .vocoded:  return .vocoded
        case .songChop: return .transformed
        }
    }

    /// Short pad label for a classified local sample.
    static func classLabel(_ cls: SampleClass) -> String {
        switch cls {
        case .vocalChop:     return "Vocal"
        case .percussion:    return "Perc"
        case .sustainedNote: return "Note"
        case .texture:       return "Texture"
        case .phrase:        return "Phrase"
        case .speechWord:    return "Word"
        case .unknown:       return "Sample"
        }
    }

    /// Family → 0xRRGGBB grid color. Pack manifests carry a *named*
    /// colorHint string ("purple") aimed at the web UI; the grid keys
    /// off family instead so all packs get a consistent palette.
    static func familyColor(_ family: SampleFamily) -> UInt32 {
        switch family {
        case .pads:       return 0xA855F7
        case .percussion: return 0xF97316
        case .textures:   return 0x14B8A6
        case .stabs:      return 0xEC4899
        case .bass:       return 0x3B82F6
        case .fx:         return 0xEAB308
        case .vocals:     return 0x22C55E
        case .mixed:      return 0x9CA3AF
        }
    }

    /// An EXPLICIT per-pad colorHint from the manifest, as 0xRRGGBB — or nil
    /// when it's a named color ("purple") or unparseable, so normal packs keep
    /// falling back to the family palette above. Borrow pads carry a real hex
    /// per SOURCE (this song = blue, the borrowed song = amber); without this
    /// both collapsed to family `.mixed` grey and you couldn't tell which song
    /// a pad came from (web/desktop already honour the hex).
    nonisolated static func hexColorHint(_ s: String?) -> UInt32? {
        guard var h = s?.trimmingCharacters(in: .whitespaces), !h.isEmpty
        else { return nil }
        if h.hasPrefix("#") { h.removeFirst() }
        guard h.count == 6, let v = UInt32(h, radix: 16) else { return nil }
        return v
    }

    // MARK: - Chord context

    /// Pitch classes of the currently sounding chord — hybrid mode
    /// brightens these pads.
    func currentChordPitchClasses() -> Set<Int> {
        guard let sym = app.currentChord?.symbol else { return [] }
        return Self.pitchClasses(for: sym)
    }

    /// Chord symbol → chord-tone pitch classes. Delegates to the
    /// engine's shared voicing table (redesign Phase 4) so grid
    /// highlights, Jam pads and Chord Pads all agree on chord
    /// spelling. Richer than the old inline table for maj7/min7
    /// (adds the 7th) and sus (4th instead of 3rd); identical for
    /// maj/min/dom7/dim/aug/other. Empty for unparseable symbols.
    static func pitchClasses(for symbol: String) -> Set<Int> {
        ChordVoicing.pitchClassSet(symbol: symbol)
    }
}
