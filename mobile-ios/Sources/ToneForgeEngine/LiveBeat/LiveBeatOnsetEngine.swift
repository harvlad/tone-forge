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

    /// macOS tap tuning: fast attack so quiet low-frequency transients
    /// (chest / desk thumps) cross the onset threshold before they decay.
    /// A slow attack smooths short quiet hits away entirely, so it must
    /// track the peak quickly. ~50 ms refractory; desktop mics are closer
    /// than a handheld phone so the threshold sits a touch lower.
    public static let desktop = LiveBeatOnsetConfig(
        attackCoeff: 0.3,
        releaseCoeff: 0.6,
        onThreshold: 0.02,
        offThreshold: 0.006,
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

        // Re-arm ONLY on a genuine level drop below offThreshold. No
        // time-based re-arm: a sound that sustains above threshold (a
        // ringing drum sample bleeding from speakers, held music) must
        // fire exactly once, not retrigger every refractory. The old
        // time-based path caused machine-gun retriggering and, in
        // speaker mode, an acoustic-feedback cascade. Fast release keeps
        // real taps re-arming quickly between hits.
        if !isArmed && envelope < offThreshold {
            isArmed = true
        }

        // Trigger only when armed, above threshold, and the refractory
        // has elapsed. The refractory debounces one attack's wobble and
        // guarantees a prior deferred body capture is consumed first.
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

    /// Linear gain applied to incoming samples before detection. The RMS
    /// onset threshold is absolute, so a quiet-mic platform can raise this
    /// to genuinely lift sensitivity (louder envelope crosses the same
    /// threshold). 1.0 = passthrough.
    public var inputGain: Float = 1.0

    private var detector: LiveBeatOnsetDetector
    private let ringBuffer: LiveBeatRingBuffer
    /// Reusable scratch for the gained buffer (avoids per-call alloc).
    private var scratch: [Float] = []

    // Deferred-capture state. An onset's attack click carries no
    // low-frequency information; we arm this flag and read the *next*
    // buffer, which holds the sound's body. The onset refractory
    // guarantees the pending slot is consumed before a second onset.
    private var pendingBodyCapture = false
    private var pendingHostTime: UInt64 = 0
    private var pendingRMS: Float = 0

    // Self-trigger feedback gate. When the app plays its own drum sample
    // out the speakers, the mic hears it; without a gate that bleed can
    // retrigger the detector (a feedback cascade). The platform calls
    // `suppressDetection` right after it fires a sample; onsets are
    // swallowed until the gate elapses. Envelope still tracks so the
    // detector's hysteresis stays coherent.
    private var suppressRemaining = 0

    // Raw continuous capture for guided ("tap-along") calibration. When
    // armed, every processed buffer is appended so the glue can hand the
    // whole take to `LiveBeatGuidedCapture`. Stores the *gained* samples
    // (same slice the runtime detector/ring sees) so training and
    // inference agree. Lock-guarded: armed/read from the main actor,
    // appended from the serial audio thread.
    private var rawCapture: [Float]?
    private let rawLock = NSLock()

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

    /// Arm the self-trigger feedback gate for `samples` frames. The
    /// platform calls this right after it plays its own drum sample so
    /// the speaker bleed the mic hears can't retrigger the detector.
    /// Onsets are swallowed while the gate is active; the envelope still
    /// tracks so hysteresis stays coherent. Extends (not resets) an
    /// existing gate so rapid hits keep it open.
    public func suppressDetection(samples: Int) {
        suppressRemaining = max(suppressRemaining, samples)
    }

    /// Begin accumulating a continuous raw take (guided calibration).
    public func beginRawCapture() {
        rawLock.lock()
        rawCapture = []
        rawLock.unlock()
    }

    /// Stop accumulating and return the take (empty if none was armed).
    public func endRawCapture() -> [Float] {
        rawLock.lock()
        defer { rawLock.unlock() }
        let take = rawCapture ?? []
        rawCapture = nil
        return take
    }

    /// Reset detector + deferred-capture state.
    public func reset() {
        detector.reset()
        pendingBodyCapture = false
        suppressRemaining = 0
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
        guard inputGain != 1.0 else {
            return processCore(
                samples: samples, frameCount: frameCount,
                hostTime: hostTime, sampleRate: sampleRate
            )
        }
        if scratch.count < frameCount {
            scratch = [Float](repeating: 0, count: frameCount)
        }
        var g = inputGain
        vDSP_vsmul(samples, 1, &g, &scratch, 1, vDSP_Length(frameCount))
        return scratch.withUnsafeBufferPointer { buf in
            processCore(
                samples: buf.baseAddress!, frameCount: frameCount,
                hostTime: hostTime, sampleRate: sampleRate
            )
        }
    }

    private func processCore(
        samples: UnsafePointer<Float>,
        frameCount: Int,
        hostTime: UInt64,
        sampleRate: Double
    ) -> Result {
        var rms: Float = 0
        vDSP_rmsqv(samples, 1, &rms, vDSP_Length(frameCount))

        ringBuffer.write(samples, count: frameCount)

        // Accumulate the raw take when guided calibration armed it.
        rawLock.lock()
        if rawCapture != nil {
            rawCapture!.append(contentsOf: UnsafeBufferPointer(start: samples, count: frameCount))
        }
        rawLock.unlock()

        var onsetDetected = detector.process(rms: rms, sampleCount: frameCount)

        // Self-trigger feedback gate. While armed, swallow onsets so the
        // app's own drum sample (heard by the mic from the speakers) can't
        // retrigger the detector. The envelope already tracked above, so
        // hysteresis stays coherent through the suppressed window.
        if suppressRemaining > 0 {
            suppressRemaining = max(0, suppressRemaining - frameCount)
            onsetDetected = false
        }

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
