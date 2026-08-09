// PerformView.swift
//
// The PERFORM surface — "take the instrument you built in Jam onto a
// stage." It inherits Jam's current playing mode (Pads / Chords /
// Samples) and the underlying instrument state, but presents a lean
// PERFORMANCE layout with NO construction/editing affordances:
//
//   - prominent sections (with NEXT), the live scene navigator
//   - harmonic context (current chord + latch) where useful
//   - the same playing surface Jam is configured to
//   - four live FX: Filter · Gater · Throw · Stop
//   - transport (owned by TabScaffold)
//
// Deliberately absent vs Jam: mode toggle, key/scale editing, quantize
// / loop / follow chips, octave, the Instrument Editor long-press. This
// is play + manipulate, not configure. Same controllers as Jam, so the
// instrument the performer built is exactly what they perform.

import SwiftUI
import ToneForgeEngine

struct PerformView: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var jamSettings: JamSettingsStore
    @ObservedObject var controller: JamInKeyController
    @ObservedObject var chordPadController: ChordPadController
    @EnvironmentObject private var appState: AppState

    @State private var perfFX = PerfFXState.idle

    var body: some View {
        VStack(spacing: TFTheme.Spacing.md) {
            // The Jam↔Perform contract, stated: Perform PLAYS the instrument
            // Jam builds — same pack, same pads, same modes. Name the kit so
            // edits made in Jam visibly carry over; offer the Jam hop when
            // there's no kit yet.
            HStack(spacing: 6) {
                if let kit = appState.activeSamplePack?.pack.name {
                    Text("Playing your Jam kit — \(kit)")
                } else {
                    Text("No kit yet.")
                    Button("Build one in Jam") { appState.selectedTab = .jam }
                        .font(.caption2.weight(.semibold))
                }
                Spacer(minLength: 0)
            }
            .font(.caption2)
            .foregroundStyle(TFTheme.textSecondary)
            .padding(.horizontal, TFTheme.Spacing.md)

            sectionStrip

            if let latch = latchBinding {
                ChordContext(
                    current: controller.currentChordSymbol,
                    next: controller.suggestedChords.map(\.symbol),
                    onSelectNext: { controller.trigger(symbol: $0) },
                    latchOn: latch.on,
                    onToggleLatch: latch.toggle
                )
            } else {
                ChordContext(
                    current: controller.currentChordSymbol,
                    next: controller.suggestedChords.map(\.symbol),
                    onSelectNext: { controller.trigger(symbol: $0) }
                )
            }

            playSurface
                .padding(.horizontal, TFTheme.Spacing.md)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            fxBar
        }
        .frame(maxWidth: .infinity)
        .onAppear { Haptics.prepare() }
    }

    // MARK: - Sections (prominent, the live scene navigator)

    @ViewBuilder
    private var sectionStrip: some View {
        if let sections = appState.currentBundle?.timeline.sections,
           !sections.isEmpty {
            let cur = currentSectionIndex(sections)
            SectionSelector(
                sections: sections,
                currentIndex: cur,
                nextIndex: cur.map { min($0 + 1, sections.count - 1) },
                style: .prominent,
                onSelect: { appState.seekAndPlay(to: $0.start) },
                showNext: true
            )
        }
    }

    private func currentSectionIndex(_ sections: [SectionEvent]) -> Int? {
        let t = appState.songSeconds
        return sections.firstIndex { $0.start <= t && t < $0.end }
    }

    // MARK: - Harmonic latch (mode-dependent)

    /// Perform surfaces the latch that matters for the active playing
    /// mode: chord latch in Chords, sample latch in Samples. Pads are
    /// momentary, so no latch pill there.
    private var latchBinding: (on: Bool, toggle: () -> Void)? {
        switch jamSettings.padMode {
        case .chords:
            return (chordPadController.triggerMode == .latch, {
                let isLatch = chordPadController.triggerMode == .latch
                chordPadController.triggerMode = isLatch ? .momentary : .latch
                if isLatch { chordPadController.clearLatches() }
            })
        case .samples:
            return (jamSettings.sampleLatch, { jamSettings.sampleLatch.toggle() })
        case .pads:
            return nil
        }
    }

    // MARK: - Playing surface (inherits Jam's mode)

    @ViewBuilder
    private var playSurface: some View {
        switch jamSettings.padMode {
        case .pads:
            JamPadGrid12(
                coordinator: coordinator,
                key: controller.effectiveKey,
                holdEnabled: jamSettings.holdEnabled
            )
        case .chords:
            ChordPadGridView(
                controller: chordPadController,
                currentChordSymbol: appState.currentChord?.symbol,
                nextChordSymbol: appState.nextChord?.symbol,
                followEnabled: jamSettings.followEnabled,
                songChordSymbols: appState.currentBundle?.timeline.chords.map(\.symbol) ?? []
            )
        case .samples:
            // SAME grid as Jam (UX audit fix #4): Perform previously rendered
            // a different flat pad-set under the same "Samples" label, so the
            // instrument you built in Jam wasn't the one you performed. One
            // launchpad, both tabs — plus the shared loop-cycle strip.
            VStack(spacing: 6) {
                LoopCycleStrip()
                // STAGE build of the Jam kit: same pads, play-only — empty
                // slots recede, no edit radial, hotter tiles + ring glow.
                SamplePadGrid4x4(coordinator: coordinator, stage: true)
            }
            .onAppear { appState.preloadAllSongDnaPacks() }
        }
    }

    // MARK: - Live FX bar (Filter · Gater · Throw · Stop)

    private var fxBar: some View {
        HStack(spacing: TFTheme.Spacing.sm) {
            FilterXYPad(
                engaged: perfFX.filter,
                x: perfFX.filterX,
                y: perfFX.filterY,
                onChange: { x, y in
                    perfFX.filter = true
                    perfFX.filterX = x
                    perfFX.filterY = y
                    applyPerfFX()
                },
                onEnd: {
                    perfFX.filter = false
                    applyPerfFX()
                }
            )
            .frame(width: 96)

            fxHoldPad("Gater", system: "square.grid.4x3.fill",
                      engaged: perfFX.gater) { perfFX.gater = $0 }
            fxHoldPad("Throw", system: "arrow.uturn.right",
                      engaged: perfFX.delayThrow) { perfFX.delayThrow = $0 }
            // "Brake" (DJ stop effect) — "Stop" collided with the transport
            // stop one row below; this momentarily halts playback rate.
            fxHoldPad("Brake", system: "stop.fill",
                      engaged: perfFX.stopper) { perfFX.stopper = $0 }
        }
        .frame(height: 68)
        .padding(.horizontal, TFTheme.Spacing.md)
    }

    /// A momentary FX pad: engaged while held, released on lift.
    private func fxHoldPad(
        _ title: String,
        system: String,
        engaged: Bool,
        set: @escaping (Bool) -> Void
    ) -> some View {
        VStack(spacing: 3) {
            Image(systemName: system).font(.title3)
            Text(title).font(TFTheme.controlLabel)
        }
        .foregroundStyle(engaged ? TFTheme.textPrimary : TFTheme.textSecondary)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            engaged ? TFTheme.accent.opacity(0.85) : TFTheme.surface2,
            in: RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
        )
        .overlay(
            RoundedRectangle(cornerRadius: TFTheme.Radius.medium)
                .stroke(engaged ? TFTheme.accent : TFTheme.border, lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: TFTheme.Radius.medium))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !engaged { Haptics.toggle(); set(true); applyPerfFX() }
                }
                .onEnded { _ in
                    set(false); applyPerfFX()
                }
        )
        .accessibilityLabel("\(title) effect, hold to engage")
    }

    private func applyPerfFX() {
        appState.audioEngine.setPerfFXState(perfFX)
    }
}
