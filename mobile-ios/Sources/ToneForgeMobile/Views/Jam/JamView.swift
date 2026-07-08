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

            padGrid
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, maxHeight: .infinity)

            controlsRow
        }
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
                triggerModeChip(title: "Momentary", mode: .momentary)
                triggerModeChip(title: "Latch", mode: .latch)
            }
        }
        .padding(.horizontal, 12)
    }

    private func setPadMode(_ mode: JamPadMode) {
        guard jamSettings.padMode != mode else { return }
        jamSettings.padMode = mode
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

    private func triggerModeChip(
        title: String, mode: ChordPadController.TriggerMode
    ) -> some View {
        Button {
            chordPadController.triggerMode = mode
            if mode == .momentary {
                // Latched visuals make no sense in momentary mode.
                chordPadController.clearLatches()
            }
        } label: {
            Text(title)
                .tfChip(active: chordPadController.triggerMode == mode)
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title) trigger mode")
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
            ChordPadGridView(controller: chordPadController)
        }
    }

    // MARK: - Controls row

    private var controlsRow: some View {
        HStack(spacing: 8) {
            quantizeChip
            metronomeChip
            loopSectionChip
            Spacer()
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
            }
            .buttonStyle(.plain)
            .accessibilityLabel(
                active ? "Stop looping section" : "Loop current section"
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
