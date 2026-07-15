// LiveBeatOnsetEngine.swift
//
// Platform-independent onset DSP for Live Beat, shared by every tap
// (iOS ToneForgeMobile, macOS JamDesktopAudio). Each platform owns only
// the AVAudioEngine / audio-session glue; the onset state machine, ring
// buffer, deferred-body capture, and event type live here so a change to
// the detection physics touches ONE file, not a forked copy per target.
//
// On onset the capture is deferred by one audio buffer so the emitted
// window holds the sound's *body* (post-attack), which carries the
// low-frequency energy that separates a kick-thump from a snare-snap.

import Accelerate
import Foundation

/// Event emitted when an onset's body window is ready for classification.
public struct LiveBeatOnsetEvent: Sendable {
    /// Host time when the onset (attack) was detected.
    public let hostTime: UInt64

    /// Captured body window following the onset (`captureWindowSize` samples).
    public let samples: [Float]

    /// RMS level at the attack (for velocity).
    public let rmsLevel: Float

    /// Sample rate of the captured audio.
    public let sampleRate: Double

    public init(hostTime: UInt64, samples: [Float], rmsLevel: Float, sampleRate: Double) {
        self.hostTime = hostTime
        self.samples = samples
        self.rmsLevel = rmsLevel
        self.sampleRate = sampleRate
    }
}

/// Per-platform tuning for the onset detector. The state-machine logic is
/// identical across platforms; only these constants differ.
public struct LiveBeatOnsetConfig: Sendable {
    /// Attack coefficient for the envelope follower (higher = smoother).
    public var attackCoeff: Float
    /// Release coefficient (higher = slower decay).
    public var releaseCoeff: Float
    /// Envelope level required to trigger an onset.
    public var onThreshold: Float
    /// Envelope level below which the detector re-arms (hysteresis).
    public var offThreshold: Float
    /// Minimum samples between onsets (refractory period).
    public var minIntervalSamples: Int

    public init(
        attackCoeff: Float,
        releaseCoeff: Float,
        onThreshold: Float,
        offThreshold: Float,
        minIntervalSamples: Int
    ) {
        self.attackCoeff = attackCoeff
        self.releaseCoeff = releaseCoeff
        self.onThreshold = onThreshold
        self.offThreshold = offThreshold
        self.minIntervalSamples = minIntervalSamples
    }

    /// iOS tap tuning: fast attack/decay, ~100 ms refractory. Larger
    /// interval tolerates the long acoustic decay of chest/desk taps.
    public static let mobile = LiveBeatOnsetConfig(
        attackCoeff: 0.3,
        releaseCoeff: 0.5,
        onThreshold: 0.04,
        offThreshold: 0.01,
        minIntervalSamples: 4800
    )

    /// macOS tap tuning: smoother envelope, ~50 ms refractory. Desktop
    /// mics tend to be closer and cleaner than a handheld phone.
    public static let desktop = LiveBeatOnsetConfig(
        attackCoeff: 0.8,
        releaseCoeff: 0.995,
        onThreshold: 0.025,
        offThreshold: 0.008,
        minIntervalSamples: 2400
    )
}

/// Onset detection state machine with hysteresis. Value type; the owning
/// processor holds the single mutable instance and drives it from the
/// (serial) audio thread.
public struct LiveBeatOnsetDetector: Sendable {
    private let attackCoeff: Float
    private let releaseCoeff: Float
    private let minIntervalSamples: Int

    /// Threshold to trigger onset (scaled by sensitivity).
    public var onThreshold: Float
    /// Threshold to re-arm (hysteresis).
    public var offThreshold: Float

    /// Current envelope level.
    public private(set) var envelope: Float = 0

    private var isArmed: Bool = true
    private var samplesSinceLastOnset: Int

    public init(config: LiveBeatOnsetConfig) {
        self.attackCoeff = config.attackCoeff
        self.releaseCoeff = config.releaseCoeff
        self.minIntervalSamples = config.minIntervalSamples
        self.onThreshold = config.onThreshold
        self.offThreshold = config.offThreshold
        self.samplesSinceLastOnset = config.minIntervalSamples
    }

    /// Process an RMS level and detect an onset.
    /// Returns true exactly once per onset.
    public mutating func process(rms: Float, sampleCount: Int) -> Bool {
        // Envelope follower.
        if rms > envelope {
            envelope = attackCoeff * envelope + (1 - attackCoeff) * rms
        } else {
            envelope = releaseCoeff * envelope + (1 - releaseCoeff) * rms
        }

        samplesSinceLastOnset += sampleCount

        // Re-arm on either a level drop OR the refractory elapsing. The
        // time-based path handles sounds with long acoustic decay (chest
        // taps) whose envelope never falls below offThreshold in time.
        if !isArmed && (envelope < offThreshold || samplesSinceLastOnset >= minIntervalSamples) {
            isArmed = true
        }

        // Trigger only when armed, above threshold, and the refractory
        // has elapsed. The refractory guarantees a prior deferred body
        // capture is consumed before a second onset arrives.
        if isArmed && envelope >= onThreshold && samplesSinceLastOnset >= minIntervalSamples {
            isArmed = false
            samplesSinceLastOnset = 0
            return true
        }

        return false
    }

    /// Reset detector state.
    public mutating func reset() {
        envelope = 0
        isArmed = true
        samplesSinceLastOnset = minIntervalSamples
    }
}

/// Fixed-capacity ring buffer for capturing samples around an onset.
public final class LiveBeatRingBuffer: @unchecked Sendable {
    private let capacity: Int
    private var buffer: [Float]
    private var writeIndex: Int = 0
    private let lock = NSLock()

    public init(capacity: Int = 2048) {
        self.capacity = capacity
        self.buffer = [Float](repeating: 0, count: capacity)
    }

    public func write(_ samples: UnsafePointer<Float>, count: Int) {
        lock.lock()
        defer { lock.unlock() }

        for i in 0..<count {
            buffer[writeIndex] = samples[i]
            writeIndex = (writeIndex + 1) % capacity
        }
    }

    /// Read the most recent N samples (oldest first).
    public func read(count: Int) -> [Float] {
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

/// Drives onset detection over an audio callback. Owns the detector, ring
/// buffer, and deferred-body-capture bookkeeping. All calls are expected
/// from a single (serial) audio thread; `@unchecked Sendable` reflects
/// that the platform tap guarantees serial access.
public final class LiveBeatOnsetProcessor: @unchecked Sendable {
    /// Window size for feature extraction. Must match
    /// `LiveBeatFeatures.windowSize` so the FFT sees a full window.
    public let captureWindowSize: Int

    private var detector: LiveBeatOnsetDetector
    private let ringBuffer: LiveBeatRingBuffer

    // Deferred-capture state. An onset's attack click carries no
    // low-frequency information; we arm this flag and read the *next*
    // buffer, which holds the sound's body. The onset refractory
    // guarantees the pending slot is consumed before a second onset.
    private var pendingBodyCapture = false
    private var pendingHostTime: UInt64 = 0
    private var pendingRMS: Float = 0

    public init(
        config: LiveBeatOnsetConfig,
        captureWindowSize: Int = LiveBeatFeatures.windowSize,
        ringCapacity: Int = 2048
    ) {
        self.detector = LiveBeatOnsetDetector(config: config)
        self.captureWindowSize = captureWindowSize
        self.ringBuffer = LiveBeatRingBuffer(capacity: ringCapacity)
    }

    /// Result of processing one audio buffer.
    public struct Result {
        /// Body window from a prior onset, ready to classify (or nil).
        public let event: LiveBeatOnsetEvent?
        /// Current envelope level (for the UI meter).
        public let envelope: Float
    }

    /// Update onset thresholds (e.g. from a sensitivity change).
    public func setThresholds(on: Float, off: Float) {
        detector.onThreshold = on
        detector.offThreshold = off
    }

    /// Reset detector + deferred-capture state.
    public func reset() {
        detector.reset()
        pendingBodyCapture = false
    }

    /// Process one buffer of mono samples. Computes RMS, feeds the onset
    /// detector, and returns the deferred body window from the *previous*
    /// onset (if any) plus the current envelope.
    public func process(
        samples: UnsafePointer<Float>,
        frameCount: Int,
        hostTime: UInt64,
        sampleRate: Double
    ) -> Result {
        var rms: Float = 0
        vDSP_rmsqv(samples, 1, &rms, vDSP_Length(frameCount))

        ringBuffer.write(samples, count: frameCount)

        let onsetDetected = detector.process(rms: rms, sampleCount: frameCount)

        // If an onset fired on the PREVIOUS buffer, its body has now been
        // written into the ring — read the window and emit it. Velocity /
        // host-time come from the original attack, not the body.
        var event: LiveBeatOnsetEvent?
        if pendingBodyCapture {
            event = LiveBeatOnsetEvent(
                hostTime: pendingHostTime,
                samples: ringBuffer.read(count: captureWindowSize),
                rmsLevel: pendingRMS,
                sampleRate: sampleRate
            )
            pendingBodyCapture = false
        }

        // Arm a deferred body capture for a fresh onset.
        if onsetDetected {
            pendingBodyCapture = true
            pendingHostTime = hostTime
            pendingRMS = rms
        }

        return Result(event: event, envelope: detector.envelope)
    }
}
