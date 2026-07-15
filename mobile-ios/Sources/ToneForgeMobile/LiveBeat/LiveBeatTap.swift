// LiveBeatTap.swift
//
// iOS audio-session glue for Live Beat. Installs a tap on a dedicated
// capture engine and forwards each buffer to the shared
// `LiveBeatOnsetProcessor` (ToneForgeEngine), which owns the onset state
// machine, ring buffer, and deferred-body capture. Detection physics live
// in the engine so a change propagates to macOS for free; this file is
// only the platform seam.
//
// Target latency: ~one buffer of body (~21ms @ 48kHz) after the attack.

import AVFoundation
import Foundation
import ToneForgeEngine

/// Audio tap for real-time onset detection.
/// Uses its own dedicated AVAudioEngine (like MicRecorder) to avoid
/// conflicts with the main playback engine.
@MainActor
public final class LiveBeatTap: ObservableObject {
    /// Whether the tap is currently installed and running.
    @Published public private(set) var isRunning = false

    /// Current envelope level (for UI meter).
    @Published public private(set) var envelopeLevel: Float = 0

    /// Callback when onset is detected.
    public var onOnset: ((LiveBeatOnsetEvent) -> Void)?

    /// Sensitivity multiplier (from profile).
    public var sensitivity: Float = 1.0 {
        didSet { updateThresholds() }
    }

    /// Base threshold (before sensitivity scaling).
    public var baseThreshold: Float = 0.04 {
        didSet { updateThresholds() }
    }

    private let session: AudioSessionController
    /// Dedicated capture engine (not the main playback engine).
    private var captureEngine: AVAudioEngine?
    /// Shared onset DSP. Audio-thread access is serial (one tap callback).
    private let processor = LiveBeatOnsetProcessor(config: .mobile)

    public init(session: AudioSessionController) {
        self.session = session
    }

    /// Install the tap and start capturing.
    public func install() {
        guard !isRunning else { return }

        // Activate audio session for recording
        session.activateForRecording()

        // Create dedicated capture engine
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)

        // Mono conversion if needed
        let monoFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: format.sampleRate,
            channels: 1,
            interleaved: false
        )!

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: monoFormat) { [weak self] buffer, time in
            self?.processTapBuffer(buffer, time: time)
        }

        engine.prepare()
        do {
            try engine.start()
            self.captureEngine = engine
            isRunning = true
            processor.reset()
            updateThresholds()
        } catch {
            inputNode.removeTap(onBus: 0)
            session.revertToPlayback()
            print("[LiveBeatTap] Engine start failed: \(error)")
        }
    }

    /// Remove the tap and stop capturing.
    public func remove() {
        guard isRunning else { return }

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil
        session.revertToPlayback()
        isRunning = false
    }

    // MARK: - Private

    private func updateThresholds() {
        // Higher sensitivity = lower threshold
        let adjustedThreshold = baseThreshold / sensitivity
        processor.setThresholds(on: adjustedThreshold, off: adjustedThreshold * 0.25)
    }

    private nonisolated func processTapBuffer(_ buffer: AVAudioPCMBuffer, time: AVAudioTime) {
        guard let channelData = buffer.floatChannelData?[0] else { return }
        let frameCount = Int(buffer.frameLength)
        let sampleRate = buffer.format.sampleRate

        let result = processor.process(
            samples: channelData,
            frameCount: frameCount,
            hostTime: time.hostTime,
            sampleRate: sampleRate
        )

        let envelope = result.envelope
        let event = result.event

        Task { @MainActor [weak self] in
            guard let self else { return }
            self.envelopeLevel = envelope
            if let event {
                self.onOnset?(event)
            }
        }
    }
}
