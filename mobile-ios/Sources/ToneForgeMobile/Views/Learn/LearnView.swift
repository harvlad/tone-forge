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
    /// Phase 2: the chord-transition practice sheet (tap the chord
    /// area to open).
    @State private var showTransitions = false
    /// Fretting-hand silhouette on the chord cards (shared with
    /// ChordCard via AppStorage).
    @AppStorage("learn.showHand") private var showHand = true

    var body: some View {
        Group {
            if appState.currentBundle == nil {
                JamWelcomeView()
            } else if controller.phase == .practicing {
                PracticeOverlay(controller: controller)
            } else {
                overview
            }
        }
        // The three branches are full-screen surfaces of different
        // heights; without this an ancestor's implicit animation makes
        // the swap (open a song, or Stop → phase .idle) scale/zoom.
        .animation(nil, value: appState.currentBundle?.analysisId)
        .animation(nil, value: controller.phase)
        .sheet(isPresented: $showOverview) {
            SectionOverviewSheet(controller: controller)
        }
        .sheet(isPresented: $showTransitions) {
            ChordTransitionSheet(
                pairs: ChordTransitionSheet.pairs(
                    from: appState.currentBundle?.timeline.resolvedChords.map(\.symbol) ?? []),
                onPlayChord: { appState.jamController.trigger(symbol: $0) }
            )
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

    /// The chord the hero surfaces as NOW: the sounding chord, falling
    /// FORWARD to the first upcoming chord when the playhead sits
    /// before the first chord event or in a gap. Mirrors web's
    /// prime-at-index-0 (jam.js `_onActiveChordChanged(0)` after
    /// analysis) and desktop's ChordRibbonModel.window(at:) fall-
    /// forward — without this `currentChord` is nil until the first
    /// chord boundary and the hero renders an empty board.
    private var displayedChord: ChordEvent? {
        if let current = appState.currentChord { return current }
        let chords = appState.currentBundle?.timeline.resolvedChords ?? []
        return chords.first { $0.start > appState.songSeconds }
    }

    /// First chord that starts after the displayed one.
    private var nextChordSymbol: String? {
        let chords = appState.currentBundle?.timeline.resolvedChords ?? []
        guard let shown = displayedChord else { return nil }
        return chords.first { $0.start > shown.start + 0.01 }?.symbol
    }

    /// Anticipation state for the hero countdown — the same math the
    /// practice overlay uses, but anchored on the DISPLAYED chord so
    /// the bar agrees with the hero during the pre-first-chord prime.
    private var heroPrediction: LearnSessionController.ChordPrediction? {
        LearnSessionController.prediction(
            chords: appState.currentBundle?.timeline.resolvedChords ?? [],
            current: displayedChord,
            now: appState.songSeconds
        )
    }

    /// One-line coaching hint for the NOW → NEXT change (shared
    /// ToneForgeEngine port of web's _computeTransitionHint).
    private var transitionHint: String? {
        ChordTransitionHint.hint(
            from: displayedChord?.symbol, to: nextChordSymbol)
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

    private var currentSectionIndex: Int? {
        let t = appState.songSeconds
        return sections.firstIndex { $0.start <= t && t < $0.end }
    }

    // MARK: - Overview (one screen, no scrolling)
    //
    // Music is the UI: sections, the NOW/NEXT chords + fretboards, and
    // a big Practice button dominate. Mastery stats are demoted to a
    // compact secondary line at the bottom.

    private var overview: some View {
        VStack(spacing: TFTheme.Spacing.md) {
            if !sections.isEmpty {
                SectionSelector(
                    sections: sections,
                    currentIndex: currentSectionIndex,
                    lockedIndices: Set(sections.indices.filter { appState.isSectionLocked(sections[$0]) }),
                    style: .compact,
                    onSelect: { appState.selectSection($0) },
                    onToggleLock: { appState.toggleSectionLock($0) }
                )
            }

            // The chord cards grow to fill the surface so the music
            // (NOW/NEXT + fretboards) dominates instead of leaving a
            // dead gap where the old mastery card sat. Hand mode swaps
            // the charts for ONE horizontal neck with a hand playing
            // the song (sample design).
            Group {
                if showHand {
                    GuitarNeckPlayView(
                        current: displayedChord?.symbol,
                        next: nextChordSymbol,
                        key: songKey
                    )
                } else {
                    chordCards
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(.horizontal, TFTheme.Spacing.md)
            // Tap the chord area → transition practice (Phase 2).
            .contentShape(Rectangle())
            .onTapGesture { showTransitions = true }

            // Web parity: the jam chord panel pairs its NOW/NEXT
            // guidance with a countdown strip and a one-line
            // transition hint; the overview hero gets the same
            // anticipation aids, not just the practice overlay.
            if let prediction = heroPrediction {
                LearnCountdownBar(
                    prediction: prediction,
                    songSeconds: appState.songSeconds
                )
            }
            if let hint = transitionHint {
                Text(hint)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(TFTheme.textSecondary)
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, TFTheme.Spacing.md)
                    .accessibilityLabel("Transition hint: \(hint)")
            }

            practiceButton
                .padding(.horizontal, TFTheme.Spacing.md)

            secondaryControls
                .padding(.horizontal, TFTheme.Spacing.md)

            compactStats
                .padding(.horizontal, TFTheme.Spacing.md)
                .padding(.top, TFTheme.Spacing.xs)
                .padding(.bottom, TFTheme.Spacing.xs)
        }
    }

    private var chordCards: some View {
        HStack(spacing: 10) {
            ChordCard(
                role: "NOW",
                symbol: displayedChord?.symbol,
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

    // MARK: - Practice (the hero action)

    private var practiceButton: some View {
        Button {
            if let target = practiceTarget {
                controller.startSection(target)
            }
        } label: {
            // Stems still downloading: pressing Practice would run the
            // transport against an empty StemPlayer (silence), so gate
            // the button until audio is actually loadable.
            Label(
                appState.isDownloading ? "Loading…" : "Practice",
                systemImage: appState.isDownloading
                    ? "arrow.down.circle" : "play.fill"
            )
            .font(.title3.weight(.semibold))
            .frame(maxWidth: .infinity)
            .padding(.vertical, TFTheme.Spacing.md)
            .background(
                TFTheme.accent,
                in: RoundedRectangle(cornerRadius: TFTheme.Radius.large)
            )
            .foregroundStyle(TFTheme.textPrimary)
            .opacity(practiceTarget == nil || appState.isDownloading ? 0.5 : 1)
        }
        .buttonStyle(.plain)
        .disabled(practiceTarget == nil || appState.isDownloading)
        .accessibilityLabel(
            appState.isDownloading
                ? "Downloading song audio" : "Start practicing")
    }

    // MARK: - Secondary controls (Loop · Sections · Speed)

    private var secondaryControls: some View {
        HStack(spacing: TFTheme.Spacing.sm) {
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
                showOverview = true
            } label: {
                Label("Sections", systemImage: "list.bullet")
                    .tfChip(active: false)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Show song structure")

            Spacer(minLength: 0)

            // Neck (trajectory) view ⇄ chord charts. The neck is the hero
            // now — the hand silhouette is gone (it covered the dots).
            Button {
                showHand.toggle()
            } label: {
                Image(systemName: showHand ? "guitars" : "square.grid.2x2")
                    .padding(.horizontal, 2)
                    .tfChip(active: showHand)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(showHand ? "Switch to chord charts" : "Switch to neck view")

            Button {
                cycleSpeed()
            } label: {
                Label(speedLabel, systemImage: "gauge.with.dots.needle.67percent")
                    .tfChip(active: appState.playbackRate != 1.0)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Practice speed \(speedLabel)")
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

    // MARK: - Compact stats (secondary — stats don't dominate)

    private var compactStats: some View {
        let pct = Int((controller.percentComplete * 100).rounded())
        let acc = Int((controller.overallAccuracy * 100).rounded())
        return HStack(spacing: TFTheme.Spacing.md) {
            // diameter param, NOT an outer .frame — the ring sizes itself
            // internally, so an outer 44pt frame just let the 84pt default
            // overflow onto the transport row.
            ProgressRing(
                value: controller.percentComplete,
                centerText: "\(pct)%",
                caption: "learned",
                diameter: 44,
                lineWidth: 5
            )

            Text("\(controller.learnedCount)/\(controller.totalSections) sections · \(acc)% acc · streak \(controller.longestStreak)")
                .font(TFTheme.metadata)
                .foregroundStyle(TFTheme.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.7)

            Spacer(minLength: 0)

            if let bpm = appState.currentBundle?.meta.tempoBpm {
                Text("\(Int(bpm.rounded())) BPM")
                    .font(TFTheme.metadata)
                    .foregroundStyle(TFTheme.textSecondary)
            }
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
