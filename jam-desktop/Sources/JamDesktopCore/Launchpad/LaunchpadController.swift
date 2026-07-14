// LaunchpadController.swift
//
// Musical meaning above the LaunchpadTransport seam: maps a song's
// chops onto the 8x8 grid, quantizes pad presses against the bundle
// timeline, drives pad LEDs (solid = assigned, pulse = sounding) and
// fetches alternate (stem, sliceMode) chop sets via ChopsClient.
//
// Pure logic — no AVFoundation. The audio layer subscribes to
// `onTrigger` / `onRelease` and does the actual sample scheduling
// (ChopPlayer in JamDesktopAudio). The on-screen panel calls
// `padDown`/`padUp` directly; a hardware transport routes through the
// same methods via `attach(transport:)`.
//
// Grid mapping: chops are laid out row-major from the TOP-LEFT pad in
// ascending `idx` order (row 0 = top, matching LaunchpadPad), 64 max.

import Foundation
import Observation
import ToneForgeEngine

/// One pad's chop assignment: which slice, and which stem file it
/// reads from (chops are per-stem slices).
public struct PadAssignment: Equatable, Sendable {
    public let chop: Chop
    public let stem: String

    public init(chop: Chop, stem: String) {
        self.chop = chop
        self.stem = stem
    }
}

/// Seam for chop fetching so controller tests run offline.
public protocol LaunchpadChopsFetching: Sendable {
    func fetchChops(
        baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
    ) async throws -> [Chop]
}

/// Default fetcher: GET /api/song/{id}/chops via ToneForgeEngine.
public struct BackendChopsFetcher: LaunchpadChopsFetching {
    private let client = ChopsClient()

    public init() {}

    public func fetchChops(
        baseURL: URL, analysisId: String, stem: String?, sliceMode: String?
    ) async throws -> [Chop] {
        try await client.fetchChops(
            baseURL: baseURL, analysisId: analysisId,
            stem: stem, sliceMode: sliceMode
        )
    }
}

@MainActor
@Observable
public final class LaunchpadController {

    /// Slice modes the backend serves (contribute_chops.py).
    public static let sliceModes = [
        "chord", "section", "beat", "phrase", "onset", "drum-bundle",
    ]

    // MARK: - Observable state

    public var quantize: QuantizeMode = .off
    public private(set) var assignments: [LaunchpadPad: PadAssignment] = [:]
    /// Pads currently sounding (pressed, or latched until release).
    public private(set) var activePads: Set<LaunchpadPad> = []
    /// Source of the current grid.
    public private(set) var stem: String?
    public private(set) var sliceMode: String?
    /// Bundle preset key the grid came from (nil for fetched
    /// stem/sliceMode grids). Chop edits are keyed on it.
    public private(set) var presetKey: String?
    /// Runtime chop-boundary overlay (ChopEditStore); applied to the
    /// raw chops via resolvedChops before layout.
    public private(set) var edits: ChopEdits?
    public private(set) var isFetching = false
    public var fetchError: String?

    // MARK: - Callbacks (audio layer)

    /// Fire the chop at `fireAtSongSeconds` (>= press time; equals it
    /// when quantize is off or within the grace window). The pad is
    /// included so the recording layer can capture grid coordinates.
    @ObservationIgnored public var onTrigger: ((LaunchpadPad, PadAssignment, _ fireAtSongSeconds: Double) -> Void)?
    @ObservationIgnored public var onRelease: ((LaunchpadPad, PadAssignment) -> Void)?

    /// Fire a pack pad (packId, source pad index within pack).
    @ObservationIgnored public var onPackPadTrigger: ((String, Int) -> Void)?

    // MARK: - Sequence Pad Support

    /// Manager for patterns running on pads (set by SessionController).
    @ObservationIgnored public weak var sequencePadManager: SequencePadManager?

    /// Custom pad assignments (sequences, local samples, etc.).
    @ObservationIgnored public weak var padAssignmentStore: PadAssignmentStore?

    /// Pulse state for sequence pads (for animation and LED feedback).
    public private(set) var sequencePulses: [Int: SequencePulse] = [:]

    // MARK: - Private

    @ObservationIgnored private let nowProvider: () -> Double
    @ObservationIgnored private let fetcher: any LaunchpadChopsFetching
    @ObservationIgnored private var transport: (any LaunchpadTransport)?
    @ObservationIgnored private var timeline: BundleTimeline?
    @ObservationIgnored private var tempoBpm: Double?
    @ObservationIgnored private var analysisId: String?
    /// Grid chops as delivered (pre-edit), so edits re-resolve from
    /// bundle truth instead of compounding.
    @ObservationIgnored private var rawChops: [Chop] = []

    /// Fallback pad color when a chop carries no colorHint (muted blue).
    private static let defaultColorHint: UInt32 = 0x2E6FB8

    public init(
        nowProvider: @escaping () -> Double,
        fetcher: any LaunchpadChopsFetching = BackendChopsFetcher()
    ) {
        self.nowProvider = nowProvider
        self.fetcher = fetcher
    }

    // MARK: - Wiring

    /// Route a hardware transport's pads through this controller and
    /// take over its LEDs.
    public func attach(transport: any LaunchpadTransport) {
        self.transport = transport
        transport.onPadDown = { [weak self] pad in self?.padDown(pad) }
        transport.onPadUp = { [weak self] pad in self?.padUp(pad) }
        repaint()
    }

    /// Adopt a song: timeline for the quantizer, default chop grid
    /// from the bundle's inline presets (preferring the harmonic /
    /// chord-sliced preset, matching the mobile default).
    public func configure(bundle: SongBundle) {
        timeline = bundle.timeline
        tempoBpm = bundle.meta.tempoBpm
        analysisId = bundle.analysisId
        activePads.removeAll()
        fetchError = nil

        let chosen: (key: String, preset: BundlePreset)? =
            bundle.presets["harmonic"].map { ("harmonic", $0) }
            ?? bundle.presets.first(where: { $0.value.sliceMode == "chord" })
                .map { ($0.key, $0.value) }
            ?? bundle.presets.sorted(by: { $0.key < $1.key }).first
                .map { ($0.key, $0.value) }
        if let chosen {
            setChops(
                chosen.preset.chops,
                stem: chosen.preset.stem,
                sliceMode: chosen.preset.sliceMode
            )
            presetKey = chosen.key
        } else {
            setChops([], stem: nil, sliceMode: nil)
        }
    }

    /// Clear everything (song unloaded).
    public func reset() {
        timeline = nil
        tempoBpm = nil
        analysisId = nil
        setChops([], stem: nil, sliceMode: nil)
    }

    // MARK: - Grid

    /// Lay `chops` onto the grid row-major from the top-left in
    /// ascending idx order; 64 max. Resets any edit overlay (a new
    /// grid means new chop identities — the caller re-applies).
    public func setChops(_ chops: [Chop], stem: String?, sliceMode: String?) {
        self.stem = stem
        self.sliceMode = sliceMode
        presetKey = nil
        edits = nil
        rawChops = chops.sorted(by: { $0.idx < $1.idx })
        activePads.removeAll()
        layout()
    }

    /// Overlay chop-boundary edits (ChopEditStore) on the current
    /// grid. Pass nil to restore bundle boundaries. Re-resolves from
    /// the raw chops each time, so edits never compound.
    public func applyEdits(_ edits: ChopEdits?) {
        self.edits = edits
        activePads.removeAll()
        layout()
    }

    /// Grid chops in display order: idx-sorted raw chops, or the
    /// startSec-sorted resolved set when edits overlay them.
    private var displayChops: [Chop] {
        if let edits, edits.hasEdits {
            return resolvedChops(bundleChops: rawChops, edits: edits)
                .map { $0.toChop() }
        }
        return rawChops
    }

    private func layout() {
        var next: [LaunchpadPad: PadAssignment] = [:]
        if let stem {
            for (slot, chop) in displayChops.prefix(64).enumerated() {
                let pad = LaunchpadPad(row: slot / 8, col: slot % 8)
                next[pad] = PadAssignment(chop: chop, stem: stem)
            }
        }
        assignments = next
        repaint()
    }

    /// Fetch and adopt a different (stem, sliceMode) chop set.
    public func loadChops(
        stem: String, sliceMode: String, backend: URL
    ) async {
        guard let analysisId else { return }
        isFetching = true
        fetchError = nil
        defer { isFetching = false }
        do {
            let chops = try await fetcher.fetchChops(
                baseURL: backend, analysisId: analysisId,
                stem: stem, sliceMode: sliceMode
            )
            setChops(chops, stem: stem, sliceMode: sliceMode)
        } catch {
            fetchError = error.localizedDescription
        }
    }

    // MARK: - Pads

    public func padDown(_ pad: LaunchpadPad) {
        // Check for custom pad assignment first
        let padIdx = pad.row * 8 + pad.col
        if let store = padAssignmentStore, let ref = store.slot(padIdx: padIdx) {
            switch ref {
            case .sequence(let patternId):
                handleSequencePadDown(patternId: patternId, padIdx: padIdx, pad: pad)
                return
            case .packPad(let packId, let sourcePadIdx):
                onPackPadTrigger?(packId, sourcePadIdx)
                activePads.insert(pad)
                transport?.setLight(.pulse(colorHint: 0xA855F7), at: pad)
                return
            case .localSample:
                // Future: handle local sample trigger
                return
            }
        }

        // Normal chop trigger
        guard let assignment = assignments[pad] else { return }
        let now = nowProvider()
        let fireAt = Quantizer.nextQuantized(
            songSeconds: now,
            mode: quantize,
            beats: timeline?.beats ?? [],
            downbeats: timeline?.downbeats ?? [],
            sections: timeline?.sections ?? [],
            tempoBpm: tempoBpm
        )
        activePads.insert(pad)
        transport?.setLight(.pulse(colorHint: colorHint(for: assignment)), at: pad)
        onTrigger?(pad, assignment, fireAt)
    }

    public func padUp(_ pad: LaunchpadPad) {
        // Check for custom pad assignment first
        let padIdx = pad.row * 8 + pad.col
        if let store = padAssignmentStore, let ref = store.slot(padIdx: padIdx) {
            switch ref {
            case .sequence:
                // Sequence pads use toggle mode by default, no action on up
                return
            case .packPad:
                // Pack pads are one-shot, just clear active state
                activePads.remove(pad)
                transport?.setLight(.solid(colorHint: 0xA855F7), at: pad)
                return
            case .localSample:
                return
            }
        }

        // Normal chop release
        guard let assignment = assignments[pad] else { return }
        activePads.remove(pad)
        transport?.setLight(.solid(colorHint: colorHint(for: assignment)), at: pad)
        onRelease?(pad, assignment)
    }

    // MARK: - Sequence Pad Handling

    /// Toggle a sequence pattern on a pad (tap to start, tap to stop).
    private func handleSequencePadDown(patternId: UUID, padIdx: Int, pad: LaunchpadPad) {
        guard let manager = sequencePadManager else { return }
        let bpm = tempoBpm ?? 120
        manager.toggle(patternId: patternId, padIdx: padIdx, songBPM: bpm)
    }

    /// Update pulse state for a sequence pad (called by SequencePadManager).
    public func updateSequencePulse(padIdx: Int, pulse: SequencePulse?) {
        if let pulse {
            sequencePulses[padIdx] = pulse
        } else {
            sequencePulses.removeValue(forKey: padIdx)
        }
        // Update hardware LED
        let pad = LaunchpadPad(row: padIdx / 8, col: padIdx % 8)
        if let pulse {
            // Pulse LED based on step
            let color: UInt32 = pulse.isDownbeat ? 0xFFFFFF : 0x9B59B6  // white flash / purple
            transport?.setLight(.solid(colorHint: color), at: pad)
        } else if padAssignmentStore?.slot(padIdx: padIdx) != nil {
            // Assigned but not playing: dim purple
            transport?.setLight(.solid(colorHint: 0x5B2C6F), at: pad)
        } else {
            transport?.setLight(.off, at: pad)
        }
    }

    // MARK: - Lights

    /// The pad's display color, shared by the hardware LEDs and the
    /// on-screen mirror.
    public func colorHint(for assignment: PadAssignment) -> UInt32 {
        Self.parseColorHint(assignment.chop.colorHint) ?? Self.defaultColorHint
    }

    /// "#RRGGBB" / "RRGGBB" → 0xRRGGBB.
    static func parseColorHint(_ hint: String?) -> UInt32? {
        guard var hex = hint?.trimmingCharacters(in: .whitespaces), !hex.isEmpty
        else { return nil }
        if hex.hasPrefix("#") { hex.removeFirst() }
        guard hex.count == 6, let value = UInt32(hex, radix: 16) else { return nil }
        return value
    }

    private func repaint() {
        guard let transport else { return }
        var frame: [LaunchpadPad: LaunchpadLight] = [:]
        for row in 0..<8 {
            for col in 0..<8 {
                let pad = LaunchpadPad(row: row, col: col)
                if let assignment = assignments[pad] {
                    let hint = colorHint(for: assignment)
                    frame[pad] = activePads.contains(pad)
                        ? .pulse(colorHint: hint)
                        : .solid(colorHint: hint)
                } else {
                    frame[pad] = .off
                }
            }
        }
        transport.setLights(frame)
    }
}
