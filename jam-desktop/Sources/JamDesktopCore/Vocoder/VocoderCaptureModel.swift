// VocoderCaptureModel.swift
//
// UI state for vocoder capture sheet. Manages mode selection,
// recording state, and saving captured samples.

import Foundation
import Combine
import ToneForgeEngine

/// UI state for vocoder capture flow.
@MainActor
public final class VocoderCaptureModel: ObservableObject {

    public enum State: Equatable {
        case idle
        case recording
        case processing
        case saved(PadSampleMetadata)
        case failed(String)
    }

    @Published public private(set) var state: State = .idle
    @Published public var selectedMode: VocoderMode = .classic

    /// Display name for mode picker.
    public static func displayName(for mode: VocoderMode) -> String {
        switch mode {
        case .classic: return "Classic"
        case .song: return "Song"
        case .stem: return "Stem"
        case .harmony: return "Harmony"
        case .texture: return "Texture"
        }
    }

    /// Description blurb for mode.
    public static func blurb(for mode: VocoderMode) -> String {
        switch mode {
        case .classic:
            return "Robot voice over synth drone"
        case .song:
            return "Voice follows song's chord progression"
        case .stem:
            return "Sing through the song's own audio"
        case .harmony:
            return "Chord-aware harmonization (adds 3rds and 5ths)"
        case .texture:
            return "Sing through another sample"
        }
    }

    public init() {}

    /// Start recording. Called by UI when "Record" pressed.
    public func startRecording() {
        state = .recording
    }

    /// Stop recording and begin processing. Called by UI.
    public func stopRecording() {
        state = .processing
    }

    /// Recording saved successfully.
    public func didSave(_ metadata: PadSampleMetadata) {
        state = .saved(metadata)
    }

    /// Recording failed.
    public func didFail(_ error: Error) {
        state = .failed(error.localizedDescription)
    }

    /// Reset to idle state.
    public func reset() {
        state = .idle
    }
}
