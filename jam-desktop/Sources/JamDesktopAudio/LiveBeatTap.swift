// LiveBeatTap.swift
//
// macOS audio glue for Live Beat. Installs a tap on a dedicated
// AVAudioEngine's inputNode and forwards each buffer to the shared
// `LiveBeatOnsetProcessor` (ToneForgeEngine), which owns the onset state
// machine, ring buffer, and deferred-body capture. Detection physics live
// in the engine so a change propagates to iOS for free; this file is only
// the platform seam (mic permission + AVAudioEngine lifecycle).
//
// Target latency: ~one buffer of body (~21ms @ 48kHz) after the attack.

import AVFoundation
import Foundation
import JamDesktopCore
import ToneForgeEngine

/// Audio tap for real-time onset detection on macOS.
/// Uses its own dedicated AVAudioEngine to avoid conflicts with the
/// main playback engine.
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

    /// Base threshold (before sensitivity scaling). Low: desktop mics read
    /// quiet, and a chest / desk thump's post-gain envelope is small — a
    /// higher gate never fired even though the meter showed movement.
    public var baseThreshold: Float = 0.012 {
        didSet { updateThresholds() }
    }

    /// Linear capture gain. Desktop mics read quieter than a handheld
    /// phone, so boost the signal before onset detection.
    public var inputGain: Float = 3.0 {
        didSet { processor.inputGain = inputGain }
    }

    /// The shared engine's input node, when the host wants Live Beat
    /// serialized on it (avoids a second AVAudioEngine on one mic → 0/0
    /// format). Returns nil to force the private-engine path.
    public var sharedInput: (() -> AVAudioInputNode?)?
    /// Whether the shared engine is running (and holds the mic).
    public var sharedEngineRunning: (() -> Bool)?
    /// Host frees / restores the shared input meter around the tap.
    public var willOpenInput: (() -> Void)?
    public var didCloseInput: (() -> Void)?

    /// Private capture engine — fallback ONLY when the shared engine isn't
    /// running (and therefore isn't holding the mic).
    private var captureEngine: AVAudioEngine?
    /// True when the tap sits on the shared engine's input node.
    private var tappedSharedInput = false
    /// Shared onset DSP. Audio-thread access is serial (one tap callback).
    private let processor = LiveBeatOnsetProcessor(config: .desktop)
    /// Capture sample rate (set on install), for ms→samples conversion.
    private var captureSampleRate: Double = 48_000

    public init() {}

    /// Install the tap and start capturing.
    public func install() async throws {
        guard !isRunning else { return }

        // Request microphone permission
        let authorized = await AVCaptureDevice.requestAccess(for: .audio)
        guard authorized else {
            throw LiveBeatError.permissionDenied
        }

        // Free the shared input meter, then serialize on the shared engine's
        // input (one engine, one tap on the single mic). Only build a private
        // engine when the shared engine isn't running.
        willOpenInput?()

        func monoFormat(for sr: Double) -> AVAudioFormat {
            AVAudioFormat(
                commonFormat: .pcmFormatFloat32,
                sampleRate: sr, channels: 1, interleaved: false
            )!
        }

        if (sharedEngineRunning?() ?? false), let inputNode = sharedInput?() {
            let format = inputNode.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                didCloseInput?()
                throw LiveBeatError.noInputAvailable
            }
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: monoFormat(for: format.sampleRate)) { [weak self] buffer, time in
                self?.processTapBuffer(buffer, time: time)
            }
            self.tappedSharedInput = true
            self.captureSampleRate = format.sampleRate
            isRunning = true
            processor.reset()
            processor.inputGain = inputGain
            updateThresholds()
        } else {
            let engine = AVAudioEngine()
            let inputNode = engine.inputNode
            let format = inputNode.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                didCloseInput?()
                throw LiveBeatError.noInputAvailable
            }
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: monoFormat(for: format.sampleRate)) { [weak self] buffer, time in
                self?.processTapBuffer(buffer, time: time)
            }
            engine.prepare()
            do {
                try engine.start()
                self.captureEngine = engine
                self.captureSampleRate = format.sampleRate
                isRunning = true
                processor.reset()
                processor.inputGain = inputGain
                updateThresholds()
            } catch {
                inputNode.removeTap(onBus: 0)
                didCloseInput?()
                throw LiveBeatError.engineStartFailed(error.localizedDescription)
            }
        }
    }

    /// Remove the tap and stop capturing.
    public func remove() {
        guard isRunning else { return }

        if tappedSharedInput {
            sharedInput?()?.removeTap(onBus: 0)
            tappedSharedInput = false
        } else {
            captureEngine?.inputNode.removeTap(onBus: 0)
            captureEngine?.stop()
            captureEngine = nil
        }
        // Restore the shared input meter.
        didCloseInput?()
        isRunning = false
    }

    /// Arm the self-trigger feedback gate. Call right after the app plays
    /// its own drum sample so the speaker bleed the mic hears can't
    /// retrigger the detector. `ms` is the swallow window (~70 ms covers a
    /// short one-shot's decay without eating the next real tap).
    public func suppressDetection(ms: Double) {
        let samples = Int(ms / 1000 * captureSampleRate)
        processor.suppressDetection(samples: samples)
    }

    /// Sample rate of the running capture (for time→sample math).
    public var sampleRate: Double { captureSampleRate }

    /// Begin a continuous raw take for guided calibration.
    public func beginRawCapture() { processor.beginRawCapture() }

    /// Stop the raw take and return the accumulated mono samples.
    public func endRawCapture() -> [Float] { processor.endRawCapture() }

    // MARK: - Private

    private func updateThresholds() {
        // Higher sensitivity = lower threshold
        let adjustedThreshold = baseThreshold / sensitivity
        // Re-arm at 0.6 x on-threshold. A wider hysteresis band (the old
        // 0.25) put the re-arm level below the post-gain noise floor, so
        // the detector never re-armed between taps and only the first hit
        // registered. 0.6 sits above the floor and below the trigger.
        processor.setThresholds(on: adjustedThreshold, off: adjustedThreshold * 0.6)
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

// MARK: - Errors

public enum LiveBeatError: Error, LocalizedError {
    case permissionDenied
    case noInputAvailable
    case engineStartFailed(String)

    public var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Microphone access is not allowed. Enable in System Settings → Privacy & Security → Microphone."
        case .noInputAvailable:
            return "No audio input device available"
        case .engineStartFailed(let msg):
            return "Could not start microphone: \(msg)"
        }
    }
}
