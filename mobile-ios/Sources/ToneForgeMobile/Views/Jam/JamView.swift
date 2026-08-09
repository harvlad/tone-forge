// JamView.swift
//
// The JAM IN KEY surface (D-022 Phase 5 mockup): key + scale header,
// a [Pads | Chords] pad-mode toggle, 7 diatonic degree pads (pads
// mode), current-chord panel with two suggested follow-ups, the big
// pad grid — 12 in-key performance pads OR the 4×4 diatonic chord
// grid (the former standalone Chord Pads surface, folded in here) —
// and a controls row (quantize / metronome / section loop / octave /
// settings).
//
// Degree pads and chord pads voice directly on the PadSynth (D-019
// bus bypass); 12-pad presses flow through the normal
// ContributionEventBus via JamPadGrid12Mapping so capture/replay and
// Launchpad mirroring keep working.
//
// The Hold chip (pads mode) is visual: it keeps pads pressed on
// screen and on the Launchpad by swallowing touch pad-ups. Jam-mode
// pad-up routes no audio (PadSynth voices auto-release), so there is
// no voice to latch or cut.

import SwiftUI
import ToneForgeEngine

struct JamView: View {
    @ObservedObject var coordinator: ModeCoordinator
    @ObservedObject var jamSettings: JamSettingsStore
    @ObservedObject var controller: JamInKeyController
    @ObservedObject var chordPadController: ChordPadController
    @EnvironmentObject private var appState: AppState

    @State private var showSettingsSheet = false
    @State private var showMetronomeSheet = false
    @State private var showChordSheet = false
    /// Progressive disclosure L3: long-pressing a Samples pad opens the
    /// deeper instrument-construction workspace (the ex-Contribute
    /// tools) — reached contextually from Jam, no permanent button.
    @State private var showInstrumentEditor = false

    /// Live performance-FX gesture state (PERFORM_PARITY spec 1). Held
    /// pads engage momentarily; pushed to the engine on every change.
    @State private var perfFX = PerfFXState.idle

    /// Progressive disclosure: the DJ FX row is hidden until the
    /// performer reveals it from the harmonic bar. Jam is for building —
    /// live FX stay one tap away, not always on screen (Perform makes
    /// them primary).
    @State private var showFX = false


    var body: some View {
        VStack(spacing: TFTheme.Spacing.sm) {
            // Jam→Perform pipeline breadcrumb (shared with Perform): the
            // build→stage relationship as a picture, current stage lit.
            KitFlowPill(active: .jam)

            sectionStrip

            // Key editing + harmonic detail moved off the primary
            // surface (gear sheet / chord-detail sheet) — the grid gets
            // the vertical room instead. The two compact affordances
            // (chord detail, FX reveal) ride on the mode row.
            padModeRow

            if jamSettings.padMode == .pads {
                DegreePadRow(controller: controller)
            }

            // Chord follow countdown strip (shown when follow mode is on)
            if jamSettings.followEnabled {
                ChordFollowStrip()
            }

            padGrid
                .padding(.horizontal, TFTheme.Spacing.md)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            if showFX {
                fxRow
            }

            controlsRow
        }
        .frame(maxWidth: .infinity)
        .onAppear { Haptics.prepare() }
        .sheet(isPresented: $showSettingsSheet) {
            JamSettingsSheet(
                controller: controller,
                jamSettings: jamSettings,
                chordPadController: chordPadController
            )
        }
        .sheet(isPresented: $showMetronomeSheet) {
            JamMetronomeSheet(controller: controller, jamSettings: jamSettings)
        }
        .sheet(isPresented: $showChordSheet) {
            ChordDisplaySheet(controller: controller)
        }
        .fullScreenCover(isPresented: $showInstrumentEditor) {
            InstrumentEditorView()
        }
    }

    // MARK: - Section strip

    /// Shared A/B/C… section selector (compact). Seeks the transport to
    /// a section's start on tap. Hidden when the song has no sections.
    @ViewBuilder
    private var sectionStrip: some View {
        if let sections = appState.currentBundle?.timeline.sections,
           !sections.isEmpty {
            SectionSelector(
                sections: sections,
                currentIndex: currentSectionIndex(sections),
                style: .compact,
                onSelect: { appState.seek(to: $0.start) }
            )
        }
    }

    private func currentSectionIndex(_ sections: [SectionEvent]) -> Int? {
        let t = appState.songSeconds
        return sections.firstIndex { $0.start <= t && t < $0.end }
    }

    // MARK: - Compact affordances (chord detail + FX reveal)

    /// The two icons that survived the key-bar removal — everything
    /// else (key edit, scale variant, chord context) lives in the gear
    /// sheet / chord-detail sheet now so the grid gets the room.
    @ViewBuilder
    private var compactAffordances: some View {
        Button {
            showChordSheet = true
        } label: {
            Image(systemName: "music.note.list")
                .font(.callout)
                .foregroundStyle(TFTheme.textSecondary)
                .frame(width: 36, height: 32)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Chord detail")

        Button {
            Haptics.selectionChanged()
            showFX.toggle()
        } label: {
            Image(systemName: "slider.horizontal.3")
                .font(.callout)
                .foregroundStyle(showFX ? TFTheme.accent : TFTheme.textSecondary)
                .frame(width: 36, height: 32)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(showFX ? "Hide performance effects" : "Show performance effects")
    }


    // MARK: - Pad mode row

    /// [Pads | Chords] surface toggle, plus the per-mode trigger
    /// control: Hold (pads) or Momentary/Latch (chords).
    private var padModeRow: some View {
        HStack(spacing: 8) {
            ForEach(JamPadMode.allCases, id: \.rawValue) { mode in
                Button {
                    setPadMode(mode)
                } label: {
                    Text(mode.displayName)
                        .tfChip(active: jamSettings.padMode == mode)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(mode.displayName) pad surface")
            }

            Spacer()

            switch jamSettings.padMode {
            case .pads:
                holdChip
            case .chords:
                triggerModeToggle
            case .samples:
                Button {
                    jamSettings.sampleLatch.toggle()
                } label: {
                    Text(jamSettings.sampleLatch ? "Latch" : "Tap")
                        .tfChip(active: jamSettings.sampleLatch)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Sample trigger mode: \(jamSettings.sampleLatch ? "Latch" : "Tap"), tap to toggle")
            }

            compactAffordances
        }
        .padding(.horizontal, 12)
    }

    private func setPadMode(_ mode: JamPadMode) {
        guard jamSettings.padMode != mode else { return }
        jamSettings.padMode = mode
        // Entering Samples: preload the song's primary chop pack so the
        // grid's pads have audio — buffers only, so it does NOT hijack
        // the Contribute active pack / tabs.
        if mode == .samples {
            appState.preloadAllSongDnaPacks()
        }
        if mode == .pads {
            // Latched chord visuals make no sense off-surface.
            chordPadController.clearLatches()
        }
    }

    private var holdChip: some View {
        Button {
            jamSettings.holdEnabled.toggle()
        } label: {
            Text("Hold")
                .tfChip(active: jamSettings.holdEnabled)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(
            jamSettings.holdEnabled ? "Hold on" : "Hold off"
        )
    }

    /// Single toggle for the chord trigger mode: Tap (momentary) vs
    /// Latch. Active styling = latched; tapping flips between the two.
    private var triggerModeToggle: some View {
        let isLatch = chordPadController.triggerMode == .latch
        return Button {
            let new: ChordPadController.TriggerMode = isLatch ? .momentary : .latch
            chordPadController.triggerMode = new
            if new == .momentary {
                // Latched visuals make no sense in tap mode.
                chordPadController.clearLatches()
            }
        } label: {
            Text(isLatch ? "Latch" : "Tap")
                .tfChip(active: isLatch)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Chord trigger mode: \(isLatch ? "Latch" : "Tap"), tap to toggle")
    }

    // MARK: - Pad grid

    @ViewBuilder
    private var padGrid: some View {
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
            // The familiar Launchpad: the active pack (auto-built Auto Kit)
            // as a 4×4 rack with the hold→radial menu (Add Sound / Chop /
            // Loop / Effects / Sequence / Delete). Empty pads show "+" and
            // fill from the radial, so this is where you ADD/SWAP pads —
            // driven by the same contribution bus, so audio + loops work
            // unchanged. Tiles color by the pad's category colorHint.
            VStack(spacing: 6) {
                // Shared loop-cycle strip: makes the invisible 8 s lock grid
                // VISIBLE — a sweep of the current cycle with a countdown to
                // the next boundary, so a locked pad's wait reads as musical
                // timing instead of a bug.
                LoopCycleStrip()
                SamplePadGrid4x4(coordinator: coordinator)
            }
        }
    }

    // MARK: - Performance FX row (PERFORM_PARITY spec 1)

    /// DJ-style momentary FX: a Filter XY pad plus four hold pads
    /// (Gater / Stopper / Flanger / Throw). Beat-synced effects need the
    /// song's tempo to sound (BeatClock) but the row is always shown —
    /// Filter works without timing.
    private var fxRow: some View {
        HStack(spacing: 6) {
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
            .frame(width: 88)

            fxHoldPad("Gater", system: "square.grid.4x3.fill",
                      engaged: perfFX.gater) { perfFX.gater = $0 }
            // Same name as Perform's FX pad — "Stopper" vs "Stop" was two
            // words for one effect across the two surfaces.
            fxHoldPad("Brake", system: "stop.circle",
                      engaged: perfFX.stopper) { perfFX.stopper = $0 }
            fxHoldPad("Flanger", system: "wind",
                      engaged: perfFX.flanger) { perfFX.flanger = $0 }
            fxHoldPad("Throw", system: "arrow.uturn.right",
                      engaged: perfFX.delayThrow) { perfFX.delayThrow = $0 }
        }
        .frame(height: 56)
        .padding(.horizontal, 12)
    }

    /// A momentary FX pad: engaged while held, released on lift.
    private func fxHoldPad(
        _ title: String,
        system: String,
        engaged: Bool,
        set: @escaping (Bool) -> Void
    ) -> some View {
        VStack(spacing: 2) {
            Image(systemName: system).font(.callout)
            Text(title).font(.caption2)
        }
        .foregroundStyle(engaged ? TFTheme.textPrimary : TFTheme.textSecondary)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            (engaged ? TFTheme.faderTint.opacity(0.8) : TFTheme.surface),
            in: RoundedRectangle(cornerRadius: 10)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(engaged ? TFTheme.faderTint : TFTheme.stroke, lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !engaged { set(true); applyPerfFX() }
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

    // MARK: - Controls row

    private var controlsRow: some View {
        HStack(spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    // Always visible in Samples mode — these are the core
                    // performance controls; gating them on an Auto Kit hid
                    // them from anyone who hadn't visited Library first.
                    if jamSettings.padMode == .samples {
                        instantGrooveChip
                        stopAllChip
                        loopLockChip
                    }
                    quantizeChip
                    metronomeChip
                    loopSectionChip
                    followChip
                }
            }
            Spacer(minLength: 0)
            Button {
                showSettingsSheet = true
            } label: {
                Image(systemName: "gearshape")
                    .font(.title3)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .accessibilityLabel("Jam settings")
        }
        .padding(.horizontal, TFTheme.Spacing.md)
    }

    /// One-tap groove: fire the best loop of each core category. Loops
    /// are bar-synced, so a single press yields a locked, playable bed.
    private var instantGrooveChip: some View {
        Button {
            Haptics.padTrigger()
            appState.instantGroove()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "wand.and.stars").font(.caption)
                Text("Groove").font(TFTheme.chipFont)
            }
            .tfChip(active: true)
            .fixedSize()
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Instant Groove: play the best loop of every category")
    }

    /// Loop lock: hold triggered loops until the shared cycle restarts so
    /// every pad phase-locks to one 8 s grid and layers coherently.
    private var loopLockChip: some View {
        Button {
            Haptics.selectionChanged()
            appState.sampleScheduler.loopLock.toggle()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: appState.sampleScheduler.loopLock ? "lock.fill" : "lock.open")
                    .font(.caption)
                Text("Lock").font(TFTheme.chipFont)
            }
            .tfChip(active: appState.sampleScheduler.loopLock)
            .fixedSize()
        }
        .buttonStyle(.plain)
        .accessibilityLabel(appState.sampleScheduler.loopLock ? "Loop lock on" : "Loop lock off")
    }

    /// Global stop for the whole kit — silences every sounding pad.
    private var stopAllChip: some View {
        Button {
            Haptics.selectionChanged()
            appState.stopAllPads()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "stop.fill").font(.caption)
                Text("Stop").font(TFTheme.chipFont)
            }
            .tfChip(active: false)
            .fixedSize()
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Stop all pads")
    }

    private var quantizeChip: some View {
        Menu {
            ForEach(QuantizeMode.allCases, id: \.rawValue) { mode in
                Button {
                    jamSettings.quantizeMode = mode
                } label: {
                    if jamSettings.quantizeMode == mode {
                        Label(mode.rawValue, systemImage: "checkmark")
                    } else {
                        Text(mode.rawValue)
                    }
                }
            }
        } label: {
            HStack(spacing: 4) {
                Image(systemName: "metronome")
                    .font(.caption)
                Text(
                    jamSettings.quantizeMode == .off
                        ? "Quantize"
                        : jamSettings.quantizeMode.rawValue
                )
                .font(TFTheme.chipFont)
            }
            .tfChip(active: jamSettings.quantizeMode != .off)
            .fixedSize()
        }
        .accessibilityLabel(
            "Quantize: \(jamSettings.quantizeMode.rawValue)"
        )
    }

    private var metronomeChip: some View {
        Button {
            showMetronomeSheet = true
        } label: {
            Image(systemName: "circle.grid.cross")
                .font(.caption)
                .padding(.horizontal, 2)
                .tfChip(active: jamSettings.metronomeEnabled)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Metronome")
    }

    /// Toggle an A/B loop over the section the playhead is in.
    /// Hidden song-less (no sections to loop).
    @ViewBuilder
    private var loopSectionChip: some View {
        if let sections = appState.currentBundle?.timeline.sections,
           !sections.isEmpty {
            let active = appState.loopRegion != nil
            Button {
                if active {
                    appState.setLoop(nil)
                } else if let section = sections.first(where: {
                    $0.start <= appState.songSeconds
                        && appState.songSeconds < $0.end
                }) ?? sections.first {
                    appState.setLoop(
                        LoopRegion(startSec: section.start, endSec: section.end)
                    )
                }
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "repeat")
                        .font(.caption)
                    Text("Loop")
                        .font(TFTheme.chipFont)
                }
                .tfChip(active: active)
                .fixedSize()
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                active ? "Stop looping section" : "Loop current section"
            )
        }
    }

    /// Follow toggle: highlights current/next chord pads and shows
    /// countdown strip. Only shown when a song with chords is loaded.
    @ViewBuilder
    private var followChip: some View {
        if appState.currentBundle?.timeline.chords.isEmpty == false {
            Button {
                jamSettings.followEnabled.toggle()
            } label: {
                HStack(spacing: 4) {
                    Image(systemName: "eye")
                        .font(.caption)
                    Text("Follow")
                        .font(TFTheme.chipFont)
                }
                .tfChip(active: jamSettings.followEnabled)
                .fixedSize()
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                jamSettings.followEnabled ? "Follow mode on" : "Follow mode off"
            )
        }
    }

}

// MARK: - Degree pads

/// The 7 diatonic degree pads: note name over roman numeral, tinted
/// by the launchpad degree palette. Press-and-release triggers the
/// chord on the PadSynth.
struct DegreePadRow: View {
    @ObservedObject var controller: JamInKeyController

    var body: some View {
        let pads = controller.degreePads
        if pads.isEmpty {
            Text("Load a song or pick a key to jam")
                .font(.caption)
                .foregroundStyle(TFTheme.textSecondary)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
        } else {
            HStack(spacing: 6) {
                ForEach(pads) { pad in
                    DegreePadButton(pad: pad, controller: controller)
                }
            }
            .padding(.horizontal, 12)
        }
    }
}

private struct DegreePadButton: View {
    let pad: JamDegreePad
    @ObservedObject var controller: JamInKeyController

    var body: some View {
        let pressed = controller.heldDegree == pad.degree
        let tint = Self.color(Palette.openJamDegreeBase(degree: pad.degree))
        VStack(spacing: 2) {
            Text(pad.noteName)
                .font(.subheadline.weight(.bold))
                .foregroundStyle(TFTheme.textPrimary)
            Text(pad.romanNumeral)
                .font(.caption2)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(
            tint.opacity(pressed ? 0.9 : 0.35),
            in: RoundedRectangle(cornerRadius: 10)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(pressed ? tint : TFTheme.stroke, lineWidth: 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if controller.heldDegree != pad.degree {
                        controller.padDown(degree: pad.degree)
                    }
                }
                .onEnded { _ in
                    controller.padUp(degree: pad.degree)
                }
        )
        .accessibilityLabel("\(pad.symbol) chord, degree \(pad.romanNumeral)")
    }

    /// PadColor (Novation 0…127 scale) → SwiftUI color.
    static func color(_ c: PadColor) -> Color {
        Color(
            red: Double(c.r) / 127.0,
            green: Double(c.g) / 127.0,
            blue: Double(c.b) / 127.0
        )
    }
}

// MARK: - Jam Samples grid

/// The loaded song's own chops as a trigger grid (PERFORM_PARITY) —
/// ALL stems in one grid, each pad labeled by stem + chop. Tapping fires
/// the chop through SampleScheduler (bar-quantized). Order matches
/// AppState.jamSampleFlatPads so on-screen, Launchpad, and LED align.
struct JamSamplesGrid: View {
    let pads: [AppState.JamSamplePad]
    /// Observed so pads repaint when a chop starts/stops ringing.
    @ObservedObject var voicePool: SampleVoicePool
    let onTrigger: (Int, String) -> Void
    let onRelease: (Int, String) -> Void
    /// Long-press on any pad opens the deeper instrument editor
    /// (progressive disclosure L3). Nil disables it.
    var onLongPress: (() -> Void)? = nil

    @State private var pressed: Set<String> = []

    private let columns = Array(repeating: GridItem(.flexible(), spacing: 6), count: 4)

    var body: some View {
        if pads.isEmpty {
            VStack(spacing: 6) {
                Text("No song samples")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(TFTheme.textPrimary)
                Text("Load a song with chops to trigger its loops here.")
                    .font(.caption)
                    .foregroundStyle(TFTheme.textSecondary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .padding(24)
        } else {
            ScrollView {
                LazyVGrid(columns: columns, spacing: 6) {
                    ForEach(pads) { pad in
                        padTile(pad)
                    }
                }
                .padding(.horizontal, 12)
                // Breathing room so the last row scrolls fully clear of
                // the controls strip instead of sitting clipped under it.
                .padding(.bottom, TFTheme.Spacing.xl)
            }
        }
    }

    private func padTile(_ pad: AppState.JamSamplePad) -> some View {
        let isDown = pressed.contains(pad.id)
        let key = SamplePadKey(packId: pad.packId, padIdx: pad.padIdx)
        // Playing = ringing (looping in Latch / held in Tap); Armed =
        // queued for the next downbeat.
        let isPlaying = voicePool.ringingPadKeys.contains(key)
        let isArmed = voicePool.pendingPadKeys.contains(key)
        let state: PadState = isArmed ? .armed
            : (isPlaying ? .looping : (isDown ? .pressed : .idle))
        let tile = PerformancePad(
            title: pad.name,
            family: pad.family,
            icon: Self.familyIcon(pad.family),
            state: state,
            tintOverride: Self.categoryTint(pad.category)
        )
        .frame(height: 72)
        .contentShape(RoundedRectangle(cornerRadius: TFTheme.Radius.large))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !pressed.contains(pad.id) else { return }
                    pressed.insert(pad.id)
                    Haptics.padTrigger()
                    onTrigger(pad.padIdx, pad.packId)
                }
                .onEnded { _ in
                    pressed.remove(pad.id)
                    onRelease(pad.padIdx, pad.packId)
                }
        )
        .accessibilityLabel("\(isPlaying ? "Stop" : "Play") \(pad.stem) \(pad.name)")

        // Long-press → deeper construction (L3), attached only when the
        // host wires it. Runs alongside the tap gesture and releases any
        // ringing chop first so nothing rings under the editor.
        return Group {
            if let onLongPress {
                tile
                    .simultaneousGesture(
                        LongPressGesture(minimumDuration: 0.5)
                            .onEnded { _ in
                                if pressed.remove(pad.id) != nil {
                                    onRelease(pad.padIdx, pad.packId)
                                }
                                onLongPress()
                            }
                    )
                    .accessibilityHint("Long press to edit the instrument")
            } else {
                tile
            }
        }
    }

    /// Musical-category tile tint — mirrors the backend `_CATEGORY_HEX`
    /// map so the Jam rack is grouped/colored exactly like desktop
    /// (drums red, bass green, chords amber, lead orange…). Nil for raw
    /// song-DNA pads (no category), which keep their sound-family tint.
    static func categoryTint(_ category: String?) -> Color? {
        switch category {
        case "DRUMS":   return Color(red: 0.937, green: 0.267, blue: 0.267) // EF4444
        case "BASS":    return Color(red: 0.133, green: 0.773, blue: 0.369) // 22C55E
        case "CHORDS":  return Color(red: 0.961, green: 0.620, blue: 0.043) // F59E0B
        case "LEAD":    return Color(red: 0.976, green: 0.451, blue: 0.086) // F97316
        case "VOCAL":   return Color(red: 0.925, green: 0.286, blue: 0.600) // EC4899
        case "RHYTHM":  return Color(red: 0.231, green: 0.510, blue: 0.965) // 3B82F6
        case "TEXTURE": return Color(red: 0.024, green: 0.714, blue: 0.831) // 06B6D4
        case "FX":      return Color(red: 0.659, green: 0.333, blue: 0.969) // A855F7
        case "STAB":    return Color(red: 0.545, green: 0.361, blue: 0.965) // 8B5CF6
        default:        return nil
        }
    }

    /// Sound-family glyph for the pad tile (top-leading in PerformancePad).
    static func familyIcon(_ family: SampleFamily) -> String {
        switch family {
        case .pads:       return "waveform.path"
        case .percussion: return "metronome"
        case .textures:   return "water.waves"
        case .stabs:      return "pianokeys"
        case .bass:       return "speaker.wave.2"
        case .fx:         return "sparkles"
        case .vocals:     return "mic"
        case .mixed:      return "music.note"
        }
    }
}

// MARK: - Filter XY pad

/// Momentary resonant-filter surface (PERFORM_PARITY spec 1). Touch to
/// engage; X = cutoff, Y = resonance (up = more). A dot tracks the
/// finger while held. Coordinates are normalized 0..1 with Y inverted
/// so dragging upward raises resonance. Shared by Jam's (hidden) FX row
/// and the Perform FX bar.
struct FilterXYPad: View {
    let engaged: Bool
    let x: Double
    let y: Double
    let onChange: (Double, Double) -> Void
    let onEnd: () -> Void

    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width
            let h = geo.size.height
            ZStack(alignment: .topLeading) {
                RoundedRectangle(cornerRadius: 10)
                    .fill(engaged ? TFTheme.faderTint.opacity(0.35) : TFTheme.surface)
                    .overlay(
                        RoundedRectangle(cornerRadius: 10)
                            .stroke(engaged ? TFTheme.faderTint : TFTheme.stroke, lineWidth: 1)
                    )
                if engaged {
                    Circle()
                        .fill(TFTheme.faderTint)
                        .frame(width: 12, height: 12)
                        .position(x: x * w, y: (1 - y) * h)
                }
                Text("Filter")
                    .font(.caption2)
                    .foregroundStyle(engaged ? TFTheme.textPrimary : TFTheme.textSecondary)
                    .padding(4)
            }
            .contentShape(RoundedRectangle(cornerRadius: 10))
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { g in
                        let nx = min(1, max(0, g.location.x / max(1, w)))
                        let ny = 1 - min(1, max(0, g.location.y / max(1, h)))
                        onChange(nx, ny)
                    }
                    .onEnded { _ in onEnd() }
            )
            .accessibilityLabel("Filter pad, drag to sweep cutoff and resonance")
        }
    }
}

// The former CurrentChordPanel is replaced by the shared ChordContext
// strip (Views/Components/ChordContext.swift), wired to the same
// controller.currentChordSymbol + suggestedChords.
