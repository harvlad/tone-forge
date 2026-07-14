// RehearsalView.swift
//
// Section practice: sidebar with the deduped section list + goal
// timer, main pane with the selected section's chord progression,
// and a rehearsal transport (play, loop toggle, speed presets, next
// section). Selecting a section loops it and seeks there — same
// semantics as jam.js _selectRehearsalSection.
//
// Learn mode: pressing "Practice" on a section starts chord practice
// with scoring. Interactive pads record presses; loop wraps score
// the pass and persist progress.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

struct RehearsalView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var rehearsal = RehearsalModel()
    /// Previous position for loop-wrap detection.
    @State private var lastPosition: Double = 0

    var body: some View {
        Group {
            if let loaded = model.session {
                content
                    .task(id: loaded.bundle.analysisId) {
                        await session.attach(loaded)
                        rehearsal.load(bundle: loaded.bundle)
                        applySelection(seek: false)
                    }
            } else {
                ContentUnavailableView(
                    "No song loaded",
                    systemImage: "music.note",
                    description: Text("Pick a song from the Intake view first.")
                )
            }
        }
        .onAppear { rehearsal.enterView() }
        .onDisappear {
            rehearsal.leaveView()
            session.learn.stopPractice()
        }
    }

    private var content: some View {
        HSplitView {
            sidebar
                .frame(minWidth: 220, maxWidth: 300)

            VStack(spacing: 0) {
                mainPane
                Divider()
                RehearsalTransportBar(rehearsal: rehearsal) {
                    applySelection(seek: true)
                }
                .padding(12)
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Parts to learn")
                .font(.headline)
                .padding(.horizontal, 12)
                .padding(.top, 12)

            SectionPracticeGrid(rehearsal: rehearsal, learn: session.learn) { item in
                // Stop any active practice when switching sections
                if session.learn.phase == .practicing {
                    session.learn.stopPractice()
                }
                rehearsal.select(sectionIndex: item.sectionIndex)
                applySelection(seek: true)
            }

            Spacer()

            // Progress summary
            if session.learn.totalSections > 0 {
                learnProgressSummary
                    .padding(12)
            }

            GoalTimerView(rehearsal: rehearsal)
                .padding(12)
        }
    }

    private var learnProgressSummary: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Progress")
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            HStack {
                Text("\(session.learn.learnedCount)/\(session.learn.totalSections) sections")
                    .font(.subheadline)
                Spacer()
                Text("\(Int(session.learn.percentComplete * 100))%")
                    .font(.subheadline.monospacedDigit())
            }
            ProgressView(value: session.learn.percentComplete)
                .tint(.green)
        }
    }

    @ViewBuilder
    private var mainPane: some View {
        if let item = rehearsal.selectedItem {
            let isPracticing = session.learn.phase == .practicing
                && session.learn.activeSection?.start == item.section.start

            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .firstTextBaseline) {
                    Text(item.label)
                        .font(.largeTitle.bold())
                    if item.recurrenceCount > 1 {
                        Text("appears \(item.recurrenceCount)×")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                    practiceButton(section: item.section, isPracticing: isPracticing)
                }

                if isPracticing {
                    practiceContent(item)
                } else {
                    chordTiles(item)
                }

                Spacer()
            }
            .padding(20)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            // Loop-wrap detection for pass scoring
            .onChange(of: session.transport.positionSeconds) { oldPos, newPos in
                detectLoopWrap(oldPos: oldPos, newPos: newPos)
            }
        } else {
            ContentUnavailableView(
                "No sections detected",
                systemImage: "rectangle.split.3x1",
                description: Text("This song's analysis has no section map.")
            )
        }
    }

    @ViewBuilder
    private func practiceButton(section: SectionEvent, isPracticing: Bool) -> some View {
        if isPracticing {
            Button("Stop") {
                session.learn.stopPractice()
                session.transport.pause()
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        } else {
            Button("Practice") {
                startPractice(section: section)
            }
            .buttonStyle(.borderedProminent)
        }
    }

    @ViewBuilder
    private func practiceContent(_ item: RehearsalSectionItem) -> some View {
        let currentPos = session.transport.positionSeconds
        let currentChord = session.ribbon?.currentChord(at: currentPos)
        VStack(spacing: 16) {
            // Countdown bar
            ChordCountdownBar(
                prediction: session.learn.prediction(
                    atTime: currentPos,
                    currentChord: currentChord
                )
            )

            // Interactive chord grid (all song chords)
            ChordPracticeGridView(
                chords: session.learn.songChords,
                currentChordSymbol: currentChord?.symbol,
                prediction: session.learn.prediction(
                    atTime: currentPos,
                    currentChord: currentChord
                ),
                lastPressHit: session.learn.lastPressHit
            )

            // Score overlay
            PracticeScoreOverlay(
                passHits: session.learn.passHits,
                passMisses: session.learn.passMisses,
                currentStreak: session.learn.currentStreak,
                lastPassResult: session.learn.lastPassResult
            )
        }
    }

    private func chordTiles(_ item: RehearsalSectionItem) -> some View {
        let columns = [GridItem(.adaptive(minimum: 90), spacing: 10)]
        return LazyVGrid(columns: columns, alignment: .leading, spacing: 10) {
            ForEach(Array(item.chords.enumerated()), id: \.offset) { _, symbol in
                Text(symbol)
                    .font(.title2.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 18)
                    .jamTile(cornerRadius: 10)
            }
        }
    }

    /// Push the current selection into the transport: loop the
    /// section (or clear), optionally seek to its start.
    private func applySelection(seek: Bool) {
        if let loop = rehearsal.activeLoop {
            session.transport.setLoop(loop)
        } else {
            session.transport.clearLoop()
        }
        if seek, let item = rehearsal.selectedItem {
            session.transport.seek(to: item.section.start)
        }
    }

    private func startPractice(section: SectionEvent) {
        session.learn.startSection(section)
        if let loop = session.learn.loopRegion {
            session.transport.setLoop(loop)
        }
        session.transport.seek(to: section.start)
        session.transport.play()
        lastPosition = section.start
    }

    /// Detect when the playhead wraps back to loop start (pass complete).
    private func detectLoopWrap(oldPos: Double, newPos: Double) {
        guard session.learn.phase == .practicing,
              let loop = session.transport.loop,
              session.transport.isPlaying else { return }
        // Wrap detected: position jumped backward significantly
        // (more than 1 second backward within the loop region)
        if oldPos > newPos + 1.0 && newPos < loop.inSeconds + 1.0 {
            session.learn.passCompleted()
        }
    }
}
