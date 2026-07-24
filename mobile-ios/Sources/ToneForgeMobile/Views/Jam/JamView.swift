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

    @State private var showKeySheet = false
    @State private var showSettingsSheet = false
    @State private var showMetronomeSheet = false
    @State private var showChordSheet = false

    /// Live performance-FX gesture state (PERFORM_PARITY spec 1). Held
    /// pads engage momentarily; pushed to the engine on every change.
    @State private var perfFX = PerfFXState.idle


    var body: some View {
        VStack(spacing: 8) {
            keyHeader

            padModeRow

            if jamSettings.padMode == .pads {
                DegreePadRow(controller: controller)
            }

            CurrentChordPanel(controller: controller) {
                showChordSheet = true
            }

            // Chord follow countdown strip (shown when follow mode is on)
            if jamSettings.followEnabled {
                ChordFollowStrip()
            }

            padGrid
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            fxRow

            controlsRow
        }
        .frame(maxWidth: .infinity)
        .sheet(isPresented: $showKeySheet) {
            ScaleWheelSheet(controller: controller, jamSettings: jamSettings)
        }
        .sheet(isPresented: $showSettingsSheet) {
            JamSettingsSheet(controller: controller, jamSettings: jamSettings)
        }
        .sheet(isPresented: $showMetronomeSheet) {
            JamMetronomeSheet(controller: controller, jamSettings: jamSettings)
        }
        .sheet(isPresented: $showChordSheet) {
            ChordDisplaySheet(controller: controller)
        }
    }

    // MARK: - Key header

    private var keyHeader: some View {
        HStack(spacing: 8) {
            Button {
                showKeySheet = true
            } label: {
                HStack(spacing: 6) {
                    Text("Key: \(controller.keyDisplayName)")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(TFTheme.textPrimary)
                    Image(systemName: "pencil")
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                }
                .tfChip(active: false)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Edit key")

            Spacer()

            // Minor-family scale variant. Hidden for major/modal keys
            // where the variant has no effect.
            if isMinorFamilyKey {
                Menu {
                    ForEach(JamScaleVariant.allCases, id: \.rawValue) { v in
                        Button {
                            controller.setScaleVariant(v)
                        } label: {
                            if jamSettings.scaleVariant == v {
                                Label(v.displayName, systemImage: "checkmark")
                            } else {
                                Text(v.displayName)
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        Text(jamSettings.scaleVariant.displayName)
                            .font(TFTheme.chipFont)
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption2)
                    }
                    .tfChip(active: false)
                }
                .accessibilityLabel(
                    "Scale: \(jamSettings.scaleVariant.displayName)"
                )
            }
        }
        .padding(.horizontal, 12)
    }

    private var isMinorFamilyKey: Bool {
        switch controller.effectiveKey?.scale {
        case .minor, .harmonicMinor, .melodicMinor: return true
        default: return false
        }
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
            JamSamplesGrid(
                pads: appState.jamSampleFlatPads,
                voicePool: appState.sampleVoicePool,
                onTrigger: { padIdx, packId in
                    coordinator.triggerJamSample(padIdx: padIdx, packId: packId, latch: jamSettings.sampleLatch)
                },
                onRelease: { padIdx, packId in
                    // Tap mode plays while held — release on lift. Latch
                    // stops on the next tap, so no release there.
                    if !jamSettings.sampleLatch { coordinator.releaseJamSample(padIdx: padIdx, packId: packId) }
                }
            )
            .onAppear {
                // All stem packs shown in one grid → load every pack's
                // buffers (songDnaPacks may populate after setPadMode).
                appState.preloadAllSongDnaPacks()
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
            fxHoldPad("Stopper", system: "stop.circle",
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
                    quantizeChip
                    metronomeChip
                    loopSectionChip
                    followChip
                }
            }
            Spacer(minLength: 0)
            octaveStepper
            Button {
                showSettingsSheet = true
            } label: {
                Image(systemName: "gearshape")
                    .font(.title3)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            .accessibilityLabel("Jam settings")
        }
        .padding(.horizontal, 12)
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

    /// The chord grid keeps its own octave shift (ChordPadController,
    /// unpersisted), the note pads use the persisted jam shift —
    /// matching the two former surfaces.
    private var currentOctaveShift: Int {
        jamSettings.padMode == .chords
            ? chordPadController.octaveShift
            : jamSettings.octaveShift
    }

    private func setOctaveShift(_ shift: Int) {
        switch jamSettings.padMode {
        case .pads:
            controller.setOctaveShift(shift)
        case .chords:
            chordPadController.setOctaveShift(shift)
        case .samples:
            break  // Samples are fixed song chops — no octave transpose.
        }
    }

    private var octaveStepper: some View {
        HStack(spacing: 6) {
            Button {
                setOctaveShift(currentOctaveShift - 1)
            } label: {
                Image(systemName: "minus")
                    .font(.caption.weight(.bold))
                    .frame(width: 22, height: 22)
            }
            .disabled(currentOctaveShift <= -3)
            Text("Oct \(currentOctaveShift >= 0 ? "+" : "")\(currentOctaveShift)")
                .font(TFTheme.chipFont)
                .foregroundStyle(TFTheme.textPrimary)
                .frame(minWidth: 48)
            Button {
                setOctaveShift(currentOctaveShift + 1)
            } label: {
                Image(systemName: "plus")
                    .font(.caption.weight(.bold))
                    .frame(width: 22, height: 22)
            }
            .disabled(currentOctaveShift >= 3)
        }
        .foregroundStyle(TFTheme.textSecondary)
        .accessibilityLabel("Octave shift \(currentOctaveShift)")
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
            }
        }
    }

    private func padTile(_ pad: AppState.JamSamplePad) -> some View {
        let tint = TFTheme.familyTint(pad.family)
        let isDown = pressed.contains(pad.id)
        let key = SamplePadKey(packId: pad.packId, padIdx: pad.padIdx)
        // Playing = ringing (looping in Latch / held in Tap); Armed =
        // queued for the next downbeat.
        let isPlaying = voicePool.ringingPadKeys.contains(key)
        let isArmed = voicePool.pendingPadKeys.contains(key)
        let active = isDown || isPlaying
        let borderColor = isArmed ? Color.orange : (active ? tint : TFTheme.stroke)
        return ZStack(alignment: .topTrailing) {
            VStack(spacing: 2) {
                Text(pad.stem.capitalized)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(TFTheme.textSecondary)
                Text(pad.name)
                    .font(TFTheme.padLabel)
                    .foregroundStyle(TFTheme.textPrimary)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            if isArmed {
                Image(systemName: "hourglass")
                    .font(.caption2).foregroundStyle(Color.orange).padding(5)
            } else if isPlaying {
                Image(systemName: "repeat")
                    .font(.caption2).foregroundStyle(TFTheme.textPrimary).padding(5)
            }
        }
        .frame(maxWidth: .infinity)
        .frame(height: 72)
        .padding(6)
        .background(tint.opacity(active ? 0.95 : (isArmed ? 0.6 : 0.4)),
                    in: RoundedRectangle(cornerRadius: 10))
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(borderColor, lineWidth: (isPlaying || isArmed) ? 2 : 1)
        )
        .contentShape(RoundedRectangle(cornerRadius: 10))
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    guard !pressed.contains(pad.id) else { return }
                    pressed.insert(pad.id)
                    onTrigger(pad.padIdx, pad.packId)
                }
                .onEnded { _ in
                    pressed.remove(pad.id)
                    onRelease(pad.padIdx, pad.packId)
                }
        )
        .accessibilityLabel("\(isPlaying ? "Stop" : "Play") \(pad.stem) \(pad.name)")
    }
}

// MARK: - Filter XY pad

/// Momentary resonant-filter surface (PERFORM_PARITY spec 1). Touch to
/// engage; X = cutoff, Y = resonance (up = more). A dot tracks the
/// finger while held. Coordinates are normalized 0..1 with Y inverted
/// so dragging upward raises resonance.
private struct FilterXYPad: View {
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

// MARK: - Current chord panel

///"Current Chord: Dm  —  Suggested: [C] [Bb]" strip from the mockup.
/// Suggested chips are tappable (they voice on the PadSynth); the
/// panel itself opens the chord-progress sheet.
struct CurrentChordPanel: View {
    @ObservedObject var controller: JamInKeyController
    var onOpenDetail: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Button(action: onOpenDetail) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Current Chord")
                        .font(.caption2)
                        .foregroundStyle(TFTheme.textSecondary)
                    Text(controller.currentChordSymbol ?? "—")
                        .font(.title3.weight(.bold))
                        .foregroundStyle(TFTheme.textPrimary)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                "Current chord \(controller.currentChordSymbol ?? "none"), show details"
            )

            Spacer()

            let suggested = controller.suggestedChords
            if !suggested.isEmpty {
                Text("Suggested")
                    .font(.caption2)
                    .foregroundStyle(TFTheme.textSecondary)
                ForEach(suggested, id: \.degree) { chord in
                    Button {
                        controller.trigger(symbol: chord.symbol)
                    } label: {
                        Text(chord.symbol)
                            .font(TFTheme.chipFont)
                            .tfChip(active: false)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Play \(chord.symbol)")
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .tfCard()
        .padding(.horizontal, 12)
    }
}
