// LiveBeatTap.swift
//
// Real-time audio tap for Live Beat mode on macOS. Installs on a
// dedicated AVAudioEngine's inputNode and detects percussive onsets
// using amplitude threshold with hysteresis. On onset detection the
// capture is deferred by one audio buffer so the window holds the
// sound's *body* (post-attack), which carries the low-frequency energy
// that separates a kick-thump from a snare-snap. The buffered window is
// then dispatched to the main actor for feature extraction.
//
// Desktop port of iOS LiveBeatTap - uses AVAudioEngine directly
// instead of AudioSessionController.
//
// Target latency: ~one buffer of body (~21ms @ 48kHz) after the attack.

import Accelerate
import AVFoundation
import Foundation
import JamDesktopCore
import ToneForgeEngine

/// Onset detection state machine with hysteresis.
private struct OnsetDetector: Sendable {
    /// Attack coefficient for envelope follower (~10ms @ 48kHz / 1024 hop).
    private let attackCoeff: Float = 0.8

    /// Release coefficient (~50ms decay).
    private let releaseCoeff: Float = 0.995

    /// Current envelope level.
    private(set) var envelope: Float = 0

    /// Threshold to trigger onset.
    var onThreshold: Float = 0.025

    /// Threshold to re-arm (hysteresis).
    var offThreshold: Float = 0.008

    /// Whether detector is armed for next onset.
    private var isArmed: Bool = true

    /// Minimum samples between onsets (~50ms @ 48kHz).
    private let minIntervalSamples: Int = 2400

    /// Samples since last onset.
    private var samplesSinceLastOnset: Int = 2400

    /// Process RMS level and detect onset.
    /// Returns true exactly once per onset.
    mutating func process(rms: Float, sampleCount: Int) -> Bool {
        // Envelope follower
        if rms > envelope {
            envelope = attackCoeff * envelope + (1 - attackCoeff) * rms
        } else {
            envelope = releaseCoeff * envelope + (1 - releaseCoeff) * rms
        }

        samplesSinceLastOnset += sampleCount

        // Hysteresis state machine
        if isArmed && envelope >= onThreshold && samplesSinceLastOnset >= minIntervalSamples {
            isArmed = false
            samplesSinceLastOnset = 0
            return true
        }

        if !isArmed && envelope < offThreshold {
            isArmed = true
        }

        return false
    }

    /// Reset detector state.
    mutating func reset() {
        envelope = 0
        isArmed = true
        samplesSinceLastOnset = minIntervalSamples
    }
}

/// Ring buffer for capturing samples around onset.
private final class SampleRingBuffer: @unchecked Sendable {
    private let capacity: Int
    private var buffer: [Float]
    private var writeIndex: Int = 0
    private let lock = NSLock()

    init(capacity: Int = 256) {
        self.capacity = capacity
        self.buffer = [Float](repeating: 0, count: capacity)
    }

    func write(_ samples: UnsafePointer<Float>, count: Int) {
        lock.lock()
        defer { lock.unlock() }

        for i in 0..<count {
            buffer[writeIndex] = samples[i]
            writeIndex = (writeIndex + 1) % capacity
        }
    }

    /// Read the most recent N samples (oldest first).
    func read(count: Int) -> [Float] {
        lock.lock()
        defer { lock.unlock() }

        let n = min(count, capacity)
        var result = [Float](repeating: 0, count: n)
        let startIdx = (writeIndex - n + capacity) % capacity

        for i in 0..<n {
            result[i] = buffer[(startIdx + i) % capacity]
        }

        return result
    }
}

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

    /// Base threshold (before sensitivity scaling).
    public var baseThreshold: Float = 0.08 {
        didSet { updateThresholds() }
    }

    /// Dedicated capture engine (not the main playback engine).
    private var captureEngine: AVAudioEngine?
    /// Audio thread access - onset state machine.
    private nonisolated(unsafe) var detector = OnsetDetector()
    /// Holds at least two capture windows so the deferred body read
    /// always has a full post-onset window available.
    private let ringBuffer = SampleRingBuffer(capacity: 2048)
    /// Audio thread access - sample rate for events.
    private nonisolated(unsafe) var sampleRate: Double = 48000

    /// Window size for feature extraction. Must match
    /// `LiveBeatFeatures.windowSize` so the FFT sees a full window.
    private let captureWindowSize = 1024

    // Deferred-capture state (audio thread only). An onset's attack click
    // carries no low-frequency information; we arm this flag and read the
    // *next* buffer, which holds the sound's body. The onset refractory
    // guarantees the pending slot is consumed before a second onset.
    private nonisolated(unsafe) var pendingBodyCapture = false
    private nonisolated(unsafe) var pendingHostTime: UInt64 = 0
    private nonisolated(unsafe) var pendingRMS: Float = 0

    public init() {}

    /// Install the tap and start capturing.
    public func install() async throws {
        guard !isRunning else { return }

        // Request microphone permission
        let authorized = await AVCaptureDevice.requestAccess(for: .audio)
        guard authorized else {
            throw LiveBeatError.permissionDenied
        }

        // Create dedicated capture engine
        let engine = AVAudioEngine()
        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)

        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw LiveBeatError.noInputAvailable
        }

        sampleRate = format.sampleRate

        // Mono conversion format
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
            detector.reset()
            pendingBodyCapture = false
        } catch {
            inputNode.removeTap(onBus: 0)
            throw LiveBeatError.engineStartFailed(error.localizedDescription)
        }
    }

    /// Remove the tap and stop capturing.
    public func remove() {
        guard isRunning else { return }

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil
        isRunning = false
    }

    // MARK: - Private

    private func updateThresholds() {
        // Higher sensitivity = lower threshold
        let adjustedThreshold = baseThreshold / sensitivity
        detector.onThreshold = adjustedThreshold
        detector.offThreshold = adjustedThreshold * 0.25
    }

    private nonisolated func processTapBuffer(_ buffer: AVAudioPCMBuffer, time: AVAudioTime) {
        guard let channelData = buffer.floatChannelData?[0] else { return }
        let frameCount = Int(buffer.frameLength)

        // Compute RMS
        var rms: Float = 0
        vDSP_rmsqv(channelData, 1, &rms, vDSP_Length(frameCount))

        // Write to ring buffer
        ringBuffer.write(channelData, count: frameCount)

        // Detect onset
        var localDetector = detector
        let onsetDetected = localDetector.process(rms: rms, sampleCount: frameCount)

        // If an onset fired on the PREVIOUS buffer, its body has now been
        // written into the ring — read the window and dispatch it. The
        // velocity/host-time come from the original attack, not the body.
        var bodySamples: [Float] = []
        var bodyHostTime: UInt64 = 0
        var bodyRMS: Float = 0
        if pendingBodyCapture {
            bodySamples = ringBuffer.read(count: captureWindowSize)
            bodyHostTime = pendingHostTime
            bodyRMS = pendingRMS
            pendingBodyCapture = false
        }

        // Arm a deferred body capture for a fresh onset. The onset
        // refractory guarantees the pending slot is consumed first.
        if onsetDetected {
            pendingBodyCapture = true
            pendingHostTime = time.hostTime
            pendingRMS = rms
        }

        let sampleRate = self.sampleRate
        let envelope = localDetector.envelope

        Task { @MainActor [weak self] in
            guard let self else { return }

            // Update state on main actor
            self.detector = localDetector
            self.envelopeLevel = envelope

            // Dispatch the buffered body window from the prior onset.
            if !bodySamples.isEmpty {
                let event = LiveBeatOnsetEvent(
                    hostTime: bodyHostTime,
                    samples: bodySamples,
                    rmsLevel: bodyRMS,
                    sampleRate: sampleRate
                )
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
