// RehearsalModel.swift
//
// Rehearsal (section practice) state: which section is selected,
// its chord progression, the section-scoped loop handed to
// TransportController, practice speed presets, and the session goal
// timer. Mirrors jam.js's rehearsal view minus mic verification
// (that arrives with MonitorController/MicListener in M4).
//
// The bundle's section list is flat (start/end/label); like the
// mobile Learn surface we dedupe repeated labels for the practice
// list but keep every occurrence for loop targets.

import Foundation
import Observation
import ToneForgeEngine

/// One row of the practice grid: a section plus its collapsed chord
/// progression and how often the part recurs in the song.
public struct RehearsalSectionItem: Identifiable, Equatable, Sendable {
    /// Index into the bundle's sections array (first occurrence).
    public let sectionIndex: Int
    public let section: SectionEvent
    /// Consecutive-repeat-collapsed chord symbols inside the section.
    public let chords: [String]
    /// How many sections in the song share this label ("Appears Nx").
    public let recurrenceCount: Int

    public var id: Int { sectionIndex }

    public var label: String {
        section.label?.isEmpty == false ? section.label! : "Section \(sectionIndex + 1)"
    }
}

@Observable
@MainActor
public final class RehearsalModel {
    /// Web parity: _REHEARSAL_SPEEDS.
    public static let speeds: [Double] = [0.5, 0.75, 1.0]

    public private(set) var items: [RehearsalSectionItem] = []
    public private(set) var selectedIndex: Int?
    /// Loop-at-section-boundaries toggle; web defaults ON.
    public var loopEnabled = true
    public var speed: Double = 1.0

    public private(set) var goalTimer = GoalTimer()

    private var sections: [SectionEvent] = []
    private var chords: [ChordEvent] = []

    public init() {}

    // MARK: - Bundle load

    public func load(bundle: SongBundle) {
        sections = bundle.timeline.sections
        chords = bundle.timeline.chords.sorted { $0.start < $1.start }
        items = Self.buildItems(sections: sections, chords: chords)
        selectedIndex = items.first?.sectionIndex
    }

    /// Dedupe by label (case-insensitive), first occurrence wins;
    /// unlabeled sections are all kept (no shared identity).
    static func buildItems(
        sections: [SectionEvent], chords: [ChordEvent]
    ) -> [RehearsalSectionItem] {
        var counts: [String: Int] = [:]
        for section in sections {
            if let key = labelKey(section) {
                counts[key, default: 0] += 1
            }
        }

        var seen: Set<String> = []
        var items: [RehearsalSectionItem] = []
        for (index, section) in sections.enumerated() {
            let key = labelKey(section)
            if let key {
                guard seen.insert(key).inserted else { continue }
            }
            items.append(RehearsalSectionItem(
                sectionIndex: index,
                section: section,
                chords: progression(in: section, chords: chords),
                recurrenceCount: key.map { counts[$0] ?? 1 } ?? 1
            ))
        }
        return items
    }

    private static func labelKey(_ section: SectionEvent) -> String? {
        guard let label = section.label?.trimmingCharacters(in: .whitespaces),
              !label.isEmpty else { return nil }
        return label.lowercased()
    }

    /// Chords overlapping the section, consecutive repeats collapsed
    /// (mobile progressionChords parity).
    static func progression(in section: SectionEvent, chords: [ChordEvent]) -> [String] {
        var result: [String] = []
        for chord in chords where chord.end > section.start && chord.start < section.end {
            if chord.symbol != result.last {
                result.append(chord.symbol)
            }
        }
        return result
    }

    // MARK: - Selection & loop

    public var selectedItem: RehearsalSectionItem? {
        guard let selectedIndex else { return nil }
        return items.first { $0.sectionIndex == selectedIndex }
    }

    /// The loop region for the current selection when looping is on;
    /// nil clears the transport loop.
    public var activeLoop: LoopRegion? {
        guard loopEnabled, let item = selectedItem else { return nil }
        return LoopRegion(inSeconds: item.section.start, outSeconds: item.section.end)
    }

    public func select(sectionIndex: Int) {
        guard items.contains(where: { $0.sectionIndex == sectionIndex }) else { return }
        selectedIndex = sectionIndex
    }

    /// Next learnable section after the current one (wraps).
    public func selectNext() {
        guard !items.isEmpty else { return }
        guard let selectedIndex,
              let position = items.firstIndex(where: { $0.sectionIndex == selectedIndex })
        else {
            self.selectedIndex = items.first?.sectionIndex
            return
        }
        self.selectedIndex = items[(position + 1) % items.count].sectionIndex
    }

    // MARK: - Goal timer lifecycle

    public func enterView(now: Date = Date()) {
        goalTimer.start(now: now)
    }

    public func leaveView() {
        goalTimer.stop()
    }
}
