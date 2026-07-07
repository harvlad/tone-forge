// MicRecorder.swift
//
// Mic capture for the pad-source pipeline (P3): record ≤8 s, deliver
// 48 kHz mono Float32 for RecordingProcessor → Classifier →
// PadSampleStore. Uses its OWN private AVAudioEngine so the main
// playback graph is never rewired mid-jam; the only shared state is
// the AVAudioSession category, which AudioSessionController flips to
// .playAndRecord for the duration and reverts after.
//
// No input monitoring, ever, in this flow: on the built-in speaker it
// feeds back instantly (the UI shows a warning instead), and on
// headphones the direct acoustic path already beats anything we could
// render. Bluetooth routes get a "timing will be loose" note.
//
// The 8 s compliance cap (StemSlice.maxChopDurationSec) is enforced
// at the tap: accumulation stops mid-buffer at exactly capFrames and
// the recorder auto-stops. Everything the store sees is already
// under the cap.

import Foundation
import AVFoundation
import ToneForgeEngine

@MainActor
public final class MicRecorder: ObservableObject {

    public enum RecorderError: Error, LocalizedError {
        case permissionDenied
        case alreadyRecording
        case engineStartFailed(String)

        public var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "Microphone access is not allowed. Enable it in Settings."
            case .alreadyRecording:
                return "A recording is already in progress"
            case .engineStartFailed(let msg):
                return "Could not start the microphone: \(msg)"
            }
        }
    }

    public enum RouteWarning: Equatable, Sendable {
        /// Playing through the built-in speaker while recording —
        /// the capture will pick up the app's own output.
        case speakerFeedbackRisk
        /// Bluetooth route — expect ~40 ms of extra latency.
        case bluetoothLatency
    }

    public static let maxDurationSec: Double = StemSlice.maxChopDurationSec

    @Published public private(set) var isRecording = false
    @Published public private(set) var elapsedSec: Double = 0
    @Published public private(set) var routeWarning: RouteWarning?

    /// Fired on the main actor when the 8 s cap auto-stops the
    /// recording. Payload = the finished 48 kHz mono capture.
    public var onAutoStop: (([Float]) -> Void)?

    private let session: AudioSessionController
    private var captureEngine: AVAudioEngine?
    private var box: CaptureBox?
    private var elapsedTimer: Timer?
    private var startedAt: Date?

    public init(session: AudioSessionController) {
        self.session = session
    }

    // MARK: - Control

    /// Request permission, flip the session to record mode, and start
    /// accumulating. Throws instead of silently recording nothing.
    public func start() async throws {
        guard !isRecording else { throw RecorderError.alreadyRecording }
        guard await Self.requestPermission() else {
            throw RecorderError.permissionDenied
        }

        session.activateForRecording()
        routeWarning = session.isOutputBuiltInSpeaker
            ? .speakerFeedbackRisk
            : (session.isRouteBluetooth ? .bluetoothLatency : nil)

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        let box = CaptureBox(
            capFrames: Int(Self.maxDurationSec * format.sampleRate)
        )
        input.installTap(onBus: 0, bufferSize: 1024, format: format) {
            [weak self] buffer, _ in
            // Tap thread. Accumulate; on cap, hop to main to finish.
            if box.append(buffer) {
                DispatchQueue.main.async { self?.autoStop() }
            }
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            session.revertToPlayback()
            routeWarning = nil
            throw RecorderError.engineStartFailed(error.localizedDescription)
        }

        self.captureEngine = engine
        self.box = box
        self.startedAt = Date()
        self.elapsedSec = 0
        self.isRecording = true
        self.elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0 / 30.0, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let startedAt = self.startedAt else { return }
                self.elapsedSec = min(
                    Date().timeIntervalSince(startedAt), Self.maxDurationSec
                )
            }
        }
    }

    /// Stop and return the capture as 48 kHz mono. Empty array when
    /// nothing was captured (e.g. instant stop). Nil when not
    /// recording at all.
    @discardableResult
    public func stop() -> [Float]? {
        guard isRecording else { return nil }
        return finish()
    }

    /// Abandon the capture (user cancelled the sheet).
    public func cancel() {
        guard isRecording else { return }
        _ = finish()
    }

    private func autoStop() {
        guard isRecording else { return }  // stop() may have raced us
        let samples = finish() ?? []
        onAutoStop?(samples)
    }

    private func finish() -> [Float]? {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
        isRecording = false
        routeWarning = nil
        startedAt = nil

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil
        session.revertToPlayback()

        guard let box else { return [] }
        self.box = nil
        let (native, nativeRate) = box.drain()
        guard !native.isEmpty else { return [] }
        return Self.resample(
            native, from: nativeRate, to: AudioEngine.canonicalSampleRate
        )
    }

    // MARK: - Permission

    static func requestPermission() async -> Bool {
        #if os(iOS)
        if #available(iOS 17.0, *) {
            return await AVAudioApplication.requestRecordPermission()
        }
        return await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission {
                continuation.resume(returning: $0)
            }
        }
        #else
        return true  // macOS test builds never open the mic
        #endif
    }

    // MARK: - Resample (pure, testable)

    /// Mono Float32 rate conversion via AVAudioConverter (mastering
    /// quality). One-shot over the whole capture — ≤8 s is at most
    /// 384 k frames, no need for streaming conversion.
    nonisolated static func resample(
        _ x: [Float], from sourceRate: Double, to targetRate: Double
    ) -> [Float] {
        guard !x.isEmpty else { return [] }
        guard sourceRate != targetRate else { return x }
        guard
            let srcFormat = AVAudioFormat(
                standardFormatWithSampleRate: sourceRate, channels: 1
            ),
            let dstFormat = AVAudioFormat(
                standardFormatWithSampleRate: targetRate, channels: 1
            ),
            let converter = AVAudioConverter(from: srcFormat, to: dstFormat),
            let inBuffer = AVAudioPCMBuffer(
                pcmFormat: srcFormat, frameCapacity: AVAudioFrameCount(x.count)
            )
        else { return x }

        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        inBuffer.frameLength = AVAudioFrameCount(x.count)
        x.withUnsafeBufferPointer { src in
            inBuffer.floatChannelData![0].update(
                from: src.baseAddress!, count: x.count
            )
        }

        let outCapacity = AVAudioFrameCount(
            (Double(x.count) * targetRate / sourceRate).rounded(.up)
        ) + 64
        guard let outBuffer = AVAudioPCMBuffer(
            pcmFormat: dstFormat, frameCapacity: outCapacity
        ) else { return x }

        var fed = false
        var out: [Float] = []
        out.reserveCapacity(Int(outCapacity))
        while true {
            var error: NSError?
            let status = converter.convert(
                to: outBuffer, error: &error
            ) { _, outStatus in
                if fed {
                    outStatus.pointee = .endOfStream
                    return nil
                }
                fed = true
                outStatus.pointee = .haveData
                return inBuffer
            }
            if outBuffer.frameLength > 0 {
                let data = outBuffer.floatChannelData![0]
                out.append(contentsOf: UnsafeBufferPointer(
                    start: data, count: Int(outBuffer.frameLength)
                ))
                outBuffer.frameLength = 0
            }
            // .haveData = full output buffer, more pending; keep
            // pulling. Anything else (endOfStream, inputRanDry after
            // the single feed, error) means we're done.
            guard status == .haveData, error == nil else { break }
        }
        return out
    }
}

// MARK: - CaptureBox

/// Lock-protected accumulation shared between the tap thread and the
/// main actor. Mono-izes by averaging channels and hard-stops at
/// `capFrames` (mid-buffer truncation — the cap is exact).
final class CaptureBox: @unchecked Sendable {

    private let lock = NSLock()
    private var samples: [Float] = []
    private var sampleRate: Double = 0
    private var capped = false
    let capFrames: Int

    init(capFrames: Int) {
        self.capFrames = capFrames
        samples.reserveCapacity(capFrames)
    }

    /// Append a tap buffer. Returns true exactly once: on the append
    /// that hits the cap (caller then auto-stops).
    func append(_ buffer: AVAudioPCMBuffer) -> Bool {
        guard let channels = buffer.floatChannelData else { return false }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return false }

        lock.lock()
        defer { lock.unlock() }
        guard !capped else { return false }
        if sampleRate == 0 { sampleRate = buffer.format.sampleRate }

        let room = capFrames - samples.count
        let take = min(frames, room)
        if channelCount == 1 {
            samples.append(contentsOf: UnsafeBufferPointer(
                start: channels[0], count: take
            ))
        } else {
            for i in 0..<take {
                var sum: Float = 0
                for ch in 0..<channelCount { sum += channels[ch][i] }
                samples.append(sum / Float(channelCount))
            }
        }
        if samples.count >= capFrames {
            capped = true
            return true
        }
        return false
    }

    /// (accumulated mono samples, native sample rate). Rate is 0 when
    /// nothing was ever appended.
    func drain() -> ([Float], Double) {
        lock.lock()
        defer { lock.unlock() }
        return (samples, sampleRate)
    }
}
