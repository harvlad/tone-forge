// ModeRouter.swift
//
// Pure event → audio-action resolution. Given a ContributionEvent,
// the current AppMode, and the mode's grid layout, decide what the
// audio engine should do. No side effects, no engine references —
// ModeCoordinator (mobile target) executes the returned AudioAction
// against SampleScheduler / WavetableSynthNode.
//
// Routing table:
//   sample  — every pad is a sample: padDown → triggerSample(rawValue),
//             padUp → releaseSample. The scheduler handles empty pads
//             (.padNotFound) and hold/toggle semantics.
//   hybrid  — rows 5–8 samples (as above); rows 1–4 synth notes from
//             layout.meaning(.note(midi:)); chord tones (bright pads)
//             flagged so the synth can accent them.
//   others  — unimplemented in this build: everything resolves .none.
//
//   midiNote events (future keyboard adapter) map straight to synth
//   note on/off in any implemented mode.
//   .gap markers are recorder bookkeeping — always .none.

import Foundation

/// What the executor should do in response to one event.
public enum AudioAction: Sendable, Equatable {
    /// Fire the sample pad at PadIndex rawValue (row*10+col).
    case triggerSample(padIdx: Int)
    /// Release the sample pad (hold-mode touch-up).
    case releaseSample(padIdx: Int)
    case synthNoteOn(midi: Int, velocity: Double, isChordTone: Bool)
    case synthNoteOff(midi: Int)
    case none
}

public struct ModeRouter {

    /// Resolve one event. Pure function — same inputs, same action.
    public static func resolve(
        _ event: ContributionEvent,
        mode: AppMode,
        layout: any GridLayoutProviding
    ) -> AudioAction {
        guard mode.isImplemented else { return .none }

        switch event.kind {
        case .gap:
            return .none

        case .midiNote(let note, let velocity, let on):
            guard (0...127).contains(note) else { return .none }
            if on && velocity > 0 {
                return .synthNoteOn(
                    midi: note,
                    velocity: Double(velocity) / 127.0,
                    isChordTone: false
                )
            }
            return .synthNoteOff(midi: note)

        case .padDown(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            guard pad.isValid else { return .none }
            switch mode {
            case .sample:
                return .triggerSample(padIdx: pad.rawValue)
            case .hybrid:
                if HybridModeLayout.isNoteRow(row) {
                    guard case .note(let midi, _, _) = layout.meaning(at: pad) else {
                        return .none
                    }
                    let isChordTone = layout.visual(at: pad).isBright
                    return .synthNoteOn(
                        midi: midi,
                        velocity: event.velocity,
                        isChordTone: isChordTone
                    )
                }
                return .triggerSample(padIdx: pad.rawValue)
            default:
                return .none
            }

        case .padUp(let row, let col):
            let pad = PadIndex.at(row: row, col: col)
            guard pad.isValid else { return .none }
            switch mode {
            case .sample:
                return .releaseSample(padIdx: pad.rawValue)
            case .hybrid:
                if HybridModeLayout.isNoteRow(row) {
                    guard case .note(let midi, _, _) = layout.meaning(at: pad) else {
                        return .none
                    }
                    return .synthNoteOff(midi: midi)
                }
                return .releaseSample(padIdx: pad.rawValue)
            default:
                return .none
            }
        }
    }
}
