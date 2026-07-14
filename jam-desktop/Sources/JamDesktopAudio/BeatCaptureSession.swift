// BeatCaptureSession.swift
//
// Desktop beat-capture mic flow (D-024): record the mic while the
// user taps / beatboxes / claps a rhythm, mono-ize + resample to
// 48 kHz, and hand back the raw take for the pure BeatCapture engine
// (BeatOnsetExtractor → TempoEstimator → BeatPatternBuilder). Audio
// is analysis-only — never written to a store.
//
// A longer cap (~16 s ≈ 4–8 bars) is allowed because nothing is
// persisted; the 8 s compliance cap only binds PadSampleStore saves.
//
// Threading:
//   mic tap thread → mono-ize copy → serial worker queue → accumulate
//   main actor     → level meter + elapsed timer (30 Hz)
//
// Stripped desktop sibling of VocoderCaptureSession (no live DSP).

import Foundation
import AVFoundation
import ToneForgeEngine
import JamDesktopCore

@MainActor
public final class BeatCaptureSession: ObservableObject {

    /// One finished capture: dry 48 kHz mono take.
    public struct Take: Sendable {
        public let raw: [Float]
    }

    public enum CaptureError: Error, LocalizedError {
        case permissionDenied
        case alreadyCapturing
        case engineStartFailed(String)
        case noInputAvailable

        public var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "Microphone access is not allowed. Enable in System Settings → Privacy & Security → Microphone."
            case .alreadyCapturing:
                return "A beat capture is already in progress"
            case .engineStartFailed(let msg):
                return "Could not start the microphone: \(msg)"
            case .noInputAvailable:
                return "No audio input device available"
            }
        }
    }

    /// ~4–8 bars of rhythm. Analysis-only, so above the 8 s save cap.
    public static let maxDurationSec: Double = 16
    public static let canonicalSampleRate: Double = 48_000

    @Published public private(set) var isCapturing = false
    @Published public private(set) var elapsedSec: Double = 0
    /// Recent peak level [0, 1] for a capture meter.
    @Published public private(set) var level: Float = 0

    /// Fired on the main actor when the duration cap auto-stops.
    public var onAutoStop: ((Take) -> Void)?

    private var captureEngine: AVAudioEngine?
    private var worker: BeatCaptureWorker?
    private var elapsedTimer: Timer?
    private var startedAt: Date?

    public init() {}

    // MARK: - Control

    /// Request permission and start accumulating the mic.
    public func start() async throws {
        guard !isCapturing else { throw CaptureError.alreadyCapturing }

        let authorized = await AVCaptureDevice.requestAccess(for: .audio)
        guard authorized else { throw CaptureError.permissionDenied }

        let worker = BeatCaptureWorker(
            sampleRate: Self.canonicalSampleRate,
            capFrames: Int(Self.maxDurationSec * Self.canonicalSampleRate),
            onCap: { [weak self] in
                DispatchQueue.main.async { self?.autoStop() }
            }
        )

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            throw CaptureError.noInputAvailable
        }

        input.installTap(onBus: 0, bufferSize: 1024, format: format) {
            buffer, _ in
            worker.ingest(buffer)
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            throw CaptureError.engineStartFailed(error.localizedDescription)
        }

        self.captureEngine = engine
        self.worker = worker
        self.startedAt = Date()
        self.elapsedSec = 0
        self.level = 0
        self.isCapturing = true
        self.elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0 / 30.0, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let startedAt = self.startedAt else { return }
                self.elapsedSec = min(
                    Date().timeIntervalSince(startedAt), Self.maxDurationSec
                )
                self.level = self.worker?.peekLevel() ?? 0
            }
        }
    }

    /// Stop and return the dry take. Nil when not capturing.
    public func stop() -> Take? {
        guard isCapturing else { return nil }
        return Take(raw: finishCapture())
    }

    /// Abandon the capture (user cancelled).
    public func cancel() {
        guard isCapturing else { return }
        _ = finishCapture()
    }

    private func autoStop() {
        guard isCapturing else { return }
        let raw = finishCapture()
        onAutoStop?(Take(raw: raw))
    }

    private func finishCapture() -> [Float] {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
        isCapturing = false
        startedAt = nil
        level = 0

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil

        guard let worker else { return [] }
        self.worker = nil
        return worker.drain()
    }
}

// MARK: - Capture worker

/// Serial-queue owner of the accumulating mono buffer + level.
final class BeatCaptureWorker: @unchecked Sendable {

    private let queue = DispatchQueue(
        label: "jamdesktop.beat.capture", qos: .userInitiated
    )
    private let sampleRate: Double
    private let capFrames: Int
    private let onCap: () -> Void

    private var samples: [Float] = []
    private var capped = false
    private let levelLock = NSLock()
    private var recentLevel: Float = 0

    init(sampleRate: Double, capFrames: Int, onCap: @escaping () -> Void) {
        self.sampleRate = sampleRate
        self.capFrames = capFrames
        self.onCap = onCap
        samples.reserveCapacity(capFrames)
    }

    /// Tap-thread entry: mono-ize, compute level, hand off.
    func ingest(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return }

        var mono = [Float](repeating: 0, count: frames)
        if channelCount == 1 {
            mono.withUnsafeMutableBufferPointer {
                $0.baseAddress!.update(from: channels[0], count: frames)
            }
        } else {
            for i in 0..<frames {
                var sum: Float = 0
                for ch in 0..<channelCount { sum += channels[ch][i] }
                mono[i] = sum / Float(channelCount)
            }
        }

        var peak: Float = 0
        for s in mono { peak = max(peak, abs(s)) }
        levelLock.lock()
        recentLevel = peak
        levelLock.unlock()

        let rate = buffer.format.sampleRate
        queue.async { [weak self] in
            self?.append(mono, nativeRate: rate)
        }
    }

    func peekLevel() -> Float {
        levelLock.lock(); defer { levelLock.unlock() }
        return recentLevel
    }

    /// Blocking drain for stop().
    func drain() -> [Float] {
        queue.sync { samples }
    }

    private func append(_ mono: [Float], nativeRate: Double) {
        guard !capped else { return }
        let converted = nativeRate == sampleRate
            ? mono
            : Self.resample(mono, from: nativeRate, to: sampleRate)
        let room = capFrames - samples.count
        samples.append(contentsOf: converted.prefix(room))
        if samples.count >= capFrames {
            capped = true
            onCap()
        }
    }

    /// Simple linear interpolation resampler.
    private static func resample(
        _ samples: [Float], from srcRate: Double, to dstRate: Double
    ) -> [Float] {
        guard srcRate > 0, dstRate > 0, !samples.isEmpty else { return [] }
        let ratio = srcRate / dstRate
        let outLen = Int(Double(samples.count) / ratio)
        var out = [Float](repeating: 0, count: outLen)
        for i in 0..<outLen {
            let srcPos = Double(i) * ratio
            let idx = Int(srcPos)
            let frac = Float(srcPos - Double(idx))
            let s0 = samples[min(idx, samples.count - 1)]
            let s1 = samples[min(idx + 1, samples.count - 1)]
            out[i] = s0 + frac * (s1 - s0)
        }
        return out
    }
}
