// LearnView.swift
//
// The LEARN tab (D-022 redesign): one scroll-free screen. Section
// chips (tap = seek), the NOW / NEXT chord cards with guitar
// fretboards, a progress ring beside the mastery stats, the
// Loop / Practice / Sections controls, and the Tempo / Speed /
// TimeSig stat chips. Speed drives the real playback rate from
// Phase 3 (AppState.setPlaybackRate); the full song structure —
// scrubber, repetition map, section list — lives in
// SectionOverviewSheet. While a practice pass runs the surface
// swaps to PracticeOverlay.
//
// Learn has no grid — practice pads voice chords directly on the
// PadSynth through LearnSessionController (D-019 bus bypass).

import SwiftUI
import ToneForgeEngine

/// Identifiable wrapper so a SectionEvent can drive `.sheet(item:)`.
struct LearnSectionSheetItem: Identifiable {
    let section: SectionEvent
    var id: String { LearnSessionController.sectionKey(for: section) }
}

struct LearnView: View {
    @ObservedObject var controller: LearnSessionController
    @EnvironmentObject private var appState: AppState

    @State private var showOverview = false

    var body: some View {
        Group {
            if appState.currentBundle == nil {
                placeholder
            } else if controller.phase == .practicing {
                PracticeOverlay(controller: controller)
            } else {
                overview
            }
        }
        .sheet(isPresented: $showOverview) {
            SectionOverviewSheet(controller: controller)
        }
    }

    // MARK: - Derived

    private var sections: [SectionEvent] {
        appState.currentBundle?.timeline.sections ?? []
    }

    private var currentSection: SectionEvent? {
        let t = appState.songSeconds
        return sections.first { $0.start <= t && t < $0.end }
    }

    private var songKey: MusicalKey? {
        MusicalKey.parse(appState.currentBundle?.meta.detectedKey)
    }

    /// First chord that starts after the current one (or after the
    /// playhead when nothing is sounding).
    private var nextChordSymbol: String? {
        let chords = appState.currentBundle?.timeline.chords ?? []
        let after = appState.currentChord?.start ?? appState.songSeconds
        return chords.first { $0.start > after + 0.01 }?.symbol
    }

    /// The section Practice starts: the one under the playhead,
    /// falling back to the first unlearned one.
    private var practiceTarget: SectionEvent? {
        currentSection ?? controller.nextUpSection
    }

    private var isLoopingCurrentSection: Bool {
        guard let s = currentSection else { return false }
        return appState.loopRegion?.startSec == s.start
            && appState.loopRegion?.endSec == s.end
    }

    // MARK: - Song-less placeholder

    private var placeholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "graduationcap")
                .font(.largeTitle)
                .foregroundStyle(TFTheme.textSecondary)
            Text("Load a song to learn it")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
            Button {
                appState.selectedTab = .library
            } label: {
                Text("Open Library")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 18)
                    .padding(.vertical, 8)
                    .background(Color.accentColor, in: Capsule())
                    .foregroundStyle(.black)
            }
            .buttonStyle(.plain)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Overview (one screen, no scrolling)

    private var overview: some View {
        VStack(spacing: 10) {
            SectionChips(
                sections: sections,
                nowSongSeconds: appState.songSeconds,
                allowedLabels: nil,
                onSeek: { appState.seek(to: $0) },
                onGateToggle: { _ in }
            )

            chordCards
                .padding(.horizontal, 12)

            masteryRow
                .padding(.horizontal, 12)

            controlsRow
                .padding(.horizontal, 12)

            statChips
                .padding(.horizontal, 12)

            Spacer(minLength: 0)
        }
    }

    private var chordCards: some View {
        HStack(spacing: 10) {
            ChordCard(
                role: "NOW",
                symbol: appState.currentChord?.symbol,
                key: songKey,
                emphasized: true
            )
            ChordCard(
                role: "NEXT",
                symbol: nextChordSymbol,
                key: songKey
            )
        }
    }

    private var masteryRow: some View {
        HStack(spacing: 14) {
            ProgressRing(
                value: controller.percentComplete,
                centerText:
                    "\(Int((controller.percentComplete * 100).rounded()))%",
                caption: "learned"
            )
            VStack(alignment: .leading, spacing: 6) {
                masteryLine(
                    icon: "checkmark.circle",
                    text: "\(controller.learnedCount)/\(controller.totalSections) sections"
                )
                masteryLine(
                    icon: "target",
                    text: "\(Int((controller.overallAccuracy * 100).rounded()))% accuracy"
                )
                masteryLine(
                    icon: "flame",
                    text: "Best streak \(controller.longestStreak)"
                )
            }
            Spacer(minLength: 0)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tfCard()
    }

    private func masteryLine(icon: String, text: String) -> some View {
        HStack(spacing: 6) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundStyle(Color.accentColor)
            Text(text)
                .font(.caption)
                .foregroundStyle(TFTheme.textPrimary)
        }
    }

    // MARK: - Controls

    private var controlsRow: some View {
        HStack(spacing: 8) {
            Button {
                toggleLoop()
            } label: {
                Label("Loop", systemImage: "repeat")
                    .tfChip(active: isLoopingCurrentSection)
            }
            .buttonStyle(.plain)
            .disabled(currentSection == nil)
            .accessibilityLabel(
                isLoopingCurrentSection
                    ? "Stop looping this section" : "Loop this section")

            Button {
                if let target = practiceTarget {
                    controller.startSection(target)
                }
            } label: {
                Label("Practice", systemImage: "play.fill")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 9)
                    .background(
                        Color.accentColor, in: RoundedRectangle(cornerRadius: 10))
                    .foregroundStyle(.black)
            }
            .buttonStyle(.plain)
            .disabled(practiceTarget == nil)
            .accessibilityLabel("Start practicing")

            Button {
                showOverview = true
            } label: {
                Label("Sections", systemImage: "list.bullet")
                    .tfChip(active: false)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Show song structure")
        }
    }

    private func toggleLoop() {
        guard let s = currentSection else { return }
        if isLoopingCurrentSection {
            appState.setLoop(nil)
        } else {
            appState.setLoop(LoopRegion(startSec: s.start, endSec: s.end))
        }
    }

    // MARK: - Stat chips

    private var statChips: some View {
        HStack(spacing: 8) {
            if let bpm = appState.currentBundle?.meta.tempoBpm {
                Text("\(Int(bpm.rounded())) BPM").tfChip(active: false)
            }

            Button {
                cycleSpeed()
            } label: {
                Text(speedLabel).tfChip(active: appState.playbackRate != 1.0)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Practice speed \(speedLabel)")

            if let numerator = TimeSigEstimator.numerator(
                beats: appState.currentBundle?.timeline.beats ?? [],
                downbeats: appState.currentBundle?.timeline.downbeats ?? []
            ) {
                Text("\(numerator)/4").tfChip(active: false)
            }

            Spacer(minLength: 0)
        }
    }

    private var speedLabel: String {
        String(format: "%.2gx", appState.playbackRate)
    }

    /// 1.0x → 0.75x → 0.5x → 1.0x.
    private func cycleSpeed() {
        let steps: [Double] = [1.0, 0.75, 0.5]
        let current = steps.firstIndex {
            abs($0 - appState.playbackRate) < 0.01
        } ?? 0
        appState.setPlaybackRate(steps[(current + 1) % steps.count])
    }
}
