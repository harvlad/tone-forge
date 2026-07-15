// LiveBeatTap.swift
//
// Real-time audio tap for Live Beat mode. Installs on the main engine's
// inputNode and detects percussive onsets using amplitude threshold with
// hysteresis. On onset detection, captures a small sample window and
// dispatches to the main actor for feature extraction and classification.
//
// Target latency: ~10ms (onset detected mid-buffer).

import Accelerate
import AVFoundation
import Foundation
import ToneForgeEngine

/// Event dispatched when an onset is detected.
public struct LiveBeatOnsetEvent: Sendable {
    /// Host time when the onset was detected.
    public let hostTime: UInt64

    /// Captured samples around the onset (128 samples).
    public let samples: [Float]

    /// RMS level at detection (for velocity).
    public let rmsLevel: Float

    /// Sample rate of the captured audio.
    public let sampleRate: Double
}

/// Onset detection state machine with hysteresis.
private struct OnsetDetector: Sendable {
    /// Attack coefficient for envelope follower (fast attack for transients).
    private let attackCoeff: Float = 0.3

    /// Release coefficient (very fast decay so re-arm happens quickly).
    private let releaseCoeff: Float = 0.5

    /// Current envelope level.
    private(set) var envelope: Float = 0

    /// Threshold to trigger onset (needs to be above ambient noise).
    var onThreshold: Float = 0.04

    /// Threshold to re-arm (well below trigger to avoid chatter).
    var offThreshold: Float = 0.01

    /// Whether detector is armed for next onset.
    private var isArmed: Bool = true

    /// Minimum samples between onsets (~100ms @ 48kHz = max 10 hits/sec).
    private let minIntervalSamples: Int = 4800

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

        // Re-arm: either level drops below threshold OR enough time passed
        // Time-based re-arm handles sounds with long acoustic decay (chest taps)
        if !isArmed {
            if envelope < offThreshold || samplesSinceLastOnset >= minIntervalSamples {
                isArmed = true
            }
        }

        // Trigger if armed and above threshold
        if isArmed && envelope >= onThreshold {
            isArmed = false
            samplesSinceLastOnset = 0
            return true
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
    /// Audio thread access - onset state machine.
    private nonisolated(unsafe) var detector = OnsetDetector()
    private let ringBuffer = SampleRingBuffer(capacity: 256)
    /// Audio thread access - sample rate for events.
    private nonisolated(unsafe) var sampleRate: Double = 48000

    /// Window size for feature extraction.
    private let captureWindowSize = 128

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
        sampleRate = format.sampleRate

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
            detector.reset()
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

        // Capture data before hopping to main actor
        let capturedSamples = onsetDetected ? ringBuffer.read(count: captureWindowSize) : []
        let hostTime = time.hostTime
        let sampleRate = self.sampleRate
        let envelope = localDetector.envelope

        Task { @MainActor [weak self] in
            guard let self else { return }

            // Update state on main actor
            self.detector = localDetector
            self.envelopeLevel = envelope

            // Dispatch onset event
            if onsetDetected && !capturedSamples.isEmpty {
                let event = LiveBeatOnsetEvent(
                    hostTime: hostTime,
                    samples: capturedSamples,
                    rmsLevel: rms,
                    sampleRate: sampleRate
                )
                self.onOnset?(event)
            }
        }
    }
}
