// LiveBeatTap.swift
//
// Real-time audio tap for Live Beat mode. Installs on the main engine's
// inputNode and detects percussive onsets using amplitude threshold with
// hysteresis. On onset detection the capture is deferred by one audio
// buffer so the window holds the sound's *body* (post-attack), not just
// the broadband attack click — the body is what carries the low-frequency
// energy that separates a kick-thump from a snare-snap. The buffered
// window is then dispatched to the main actor for feature extraction and
// classification.
//
// Target latency: ~one buffer of body (~21ms @ 48kHz) after the attack.

import Accelerate
import AVFoundation
import Foundation
import ToneForgeEngine

/// Event dispatched when an onset is detected.
public struct LiveBeatOnsetEvent: Sendable {
    /// Host time when the onset was detected.
    public let hostTime: UInt64

    /// Captured body window following the onset (1024 samples).
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
    /// Holds at least two capture windows so the deferred body read
    /// always has a full post-onset window available.
    private let ringBuffer = SampleRingBuffer(capacity: 2048)
    /// Audio thread access - sample rate for events.
    private nonisolated(unsafe) var sampleRate: Double = 48000

    /// Window size for feature extraction. Must match
    /// `LiveBeatFeatures.windowSize` so the FFT sees a full window.
    private let captureWindowSize = 1024

    // Deferred-capture state (audio thread only). When an onset fires we
    // don't read immediately — the attack click carries no low-frequency
    // information. We arm this flag and read the *next* buffer, which
    // holds the sound's body. The 100ms onset refractory guarantees no
    // second onset arrives before this one is consumed.
    private nonisolated(unsafe) var pendingBodyCapture = false
    private nonisolated(unsafe) var pendingHostTime: UInt64 = 0
    private nonisolated(unsafe) var pendingRMS: Float = 0

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
            pendingBodyCapture = false
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

        // Arm a deferred body capture for a fresh onset. The 100ms onset
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
