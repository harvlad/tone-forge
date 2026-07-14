// VocoderCaptureSession.swift
//
// Desktop vocoder capture flow: record mic ≤8 s while a low-latency
// PREVIEW plays through VocoderMonitor, then on stop run the FULL
// take through the offline kernel at full quality and hand back
// (raw, processed). The processed take goes to Classifier →
// PadSampleStore (source .vocoded, neverUpload) → pad assignment.
//
// Threading:
//   mic tap thread  → mono-ize copy → serial worker queue
//   worker queue    → accumulate modulator → block-vocode
//                     (4096-sample blocks + 2048 history against
//                     the pre-built carrier) → ring
//   render thread   → VocoderPreviewRing.read (never blocks on DSP;
//                     shortage = underrun)
//
// Preview fidelity: each ~85 ms block is vocoded with one FFT-size
// of history so band envelopes are warm at block seams. M4 (harmony)
// previews the DRY mic (PSOLA too heavy for block preview) and
// harmonizes on stop.
//
// Desktop port of iOS VocoderCaptureSession.

import Foundation
import AVFoundation
import ToneForgeEngine
import JamDesktopCore

@MainActor
public final class VocoderCaptureSession: ObservableObject {

    /// One finished capture: the dry mic take and the processed
    /// (vocoded / harmonized) version, both 48 kHz mono.
    public struct Take: Sendable {
        public let raw: [Float]
        public let processed: [Float]
        public let mode: VocoderMode
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
                return "A vocoder capture is already in progress"
            case .engineStartFailed(let msg):
                return "Could not start the microphone: \(msg)"
            case .noInputAvailable:
                return "No audio input device available"
            }
        }
    }

    public static let maxDurationSec: Double = StemSlice.maxChopDurationSec
    public static let canonicalSampleRate: Double = 48_000

    @Published public private(set) var isCapturing = false
    @Published public private(set) var elapsedSec: Double = 0
    @Published public private(set) var underrunCount = 0

    /// Fired on the main actor when the 8 s cap auto-stops the
    /// capture, after full-quality processing.
    public var onAutoStop: ((Take) -> Void)?

    private let monitor: VocoderMonitor
    private var captureEngine: AVAudioEngine?
    private var worker: VocoderPreviewWorker?
    private var program: VocoderProgram?
    private var elapsedTimer: Timer?
    private var startedAt: Date?

    public init(monitor: VocoderMonitor) {
        self.monitor = monitor
    }

    // MARK: - Control

    /// Request permission, arm the preview ring, and start capturing
    /// against `program`.
    public func start(program: VocoderProgram) async throws {
        guard !isCapturing else { throw CaptureError.alreadyCapturing }

        // Check microphone permission
        let authorized = await AVCaptureDevice.requestAccess(for: .audio)
        guard authorized else {
            throw CaptureError.permissionDenied
        }

        monitor.ring.begin(muted: false)

        let worker = VocoderPreviewWorker(
            program: program,
            ring: monitor.ring,
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
            monitor.ring.end()
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
            monitor.ring.end()
            throw CaptureError.engineStartFailed(error.localizedDescription)
        }

        self.captureEngine = engine
        self.worker = worker
        self.program = program
        self.startedAt = Date()
        self.elapsedSec = 0
        self.underrunCount = 0
        self.isCapturing = true
        self.elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0 / 30.0, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let startedAt = self.startedAt else { return }
                self.elapsedSec = min(
                    Date().timeIntervalSince(startedAt), Self.maxDurationSec
                )
                self.underrunCount = self.monitor.ring.underruns
            }
        }
    }

    /// Stop, run the whole take through the offline kernel, and
    /// return it. Nil when not capturing.
    public func stop() async -> Take? {
        guard isCapturing, let program else { return nil }
        let raw = finishCapture()
        let processed = await Self.processFull(
            raw: raw, program: program,
            sampleRate: Self.canonicalSampleRate
        )
        return Take(raw: raw, processed: processed, mode: program.mode)
    }

    /// Abandon the capture (user cancelled).
    public func cancel() {
        guard isCapturing else { return }
        _ = finishCapture()
    }

    private func autoStop() {
        guard isCapturing, let program else { return }
        let raw = finishCapture()
        Task { [weak self] in
            let processed = await Self.processFull(
                raw: raw, program: program,
                sampleRate: Self.canonicalSampleRate
            )
            self?.onAutoStop?(
                Take(raw: raw, processed: processed, mode: program.mode)
            )
        }
    }

    /// Tear down the capture engine + preview and return the dry
    /// 48 kHz mono take.
    private func finishCapture() -> [Float] {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
        isCapturing = false
        startedAt = nil

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil
        underrunCount = monitor.ring.underruns
        monitor.ring.end()

        guard let worker else { return [] }
        self.worker = nil
        self.program = nil
        return worker.drain()
    }

    // MARK: - Full-quality processing (off-main)

    /// Whole-take render: SpectralVocoder for M1–M3/M5, PSOLA
    /// harmonize for M4.
    nonisolated static func processFull(
        raw: [Float], program: VocoderProgram, sampleRate: Double
    ) async -> [Float] {
        guard !raw.isEmpty else { return [] }
        return await Task.detached(priority: .userInitiated) {
            if program.mode == .harmony {
                let spans = program.chordSpans
                return PSOLAHarmonizer.harmonize(
                    raw, sampleRate: sampleRate,
                    chordAt: { t in
                        spans.last(where: { $0.startSec <= t })?
                            .midiNotes ?? []
                    },
                    settings: program.harmonySettings
                )
            }
            return SpectralVocoder.process(
                modulator: raw, carrier: program.carrier,
                config: program.config, sampleRate: sampleRate
            )
        }.value
    }
}

// MARK: - Preview worker

/// Serial-queue owner of the per-capture DSP state.
final class VocoderPreviewWorker: @unchecked Sendable {

    static let blockLen = 4096
    static let historyLen = 2048

    private let queue = DispatchQueue(
        label: "jamdesktop.vocoder.preview", qos: .userInitiated
    )
    private let program: VocoderProgram
    private let ring: VocoderPreviewRing
    private let sampleRate: Double
    private let capFrames: Int
    private let onCap: () -> Void

    private var modulator: [Float] = []
    private var processedFrames = 0
    private var capped = false

    init(
        program: VocoderProgram,
        ring: VocoderPreviewRing,
        sampleRate: Double,
        capFrames: Int,
        onCap: @escaping () -> Void
    ) {
        self.program = program
        self.ring = ring
        self.sampleRate = sampleRate
        self.capFrames = capFrames
        self.onCap = onCap
        modulator.reserveCapacity(capFrames)
    }

    /// Tap-thread entry: mono-ize and hand off.
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
        let rate = buffer.format.sampleRate
        queue.async { [weak self] in
            self?.append(mono, nativeRate: rate)
        }
    }

    /// Blocking drain for stop().
    func drain() -> [Float] {
        queue.sync { modulator }
    }

    private func append(_ mono: [Float], nativeRate: Double) {
        guard !capped else { return }

        let converted = nativeRate == sampleRate
            ? mono
            : Self.resample(mono, from: nativeRate, to: sampleRate)

        let room = capFrames - modulator.count
        modulator.append(contentsOf: converted.prefix(room))
        if modulator.count >= capFrames {
            capped = true
        }

        processReadyBlocks()

        if capped { onCap() }
    }

    private func processReadyBlocks() {
        let blockLen = Self.blockLen
        while modulator.count - processedFrames >= blockLen {
            let histStart = max(0, processedFrames - Self.historyLen)
            let end = processedFrames + blockLen
            let out: [Float]
            if program.mode == .harmony {
                // Dry preview — PSOLA runs once, on stop.
                out = Array(modulator[processedFrames..<end])
            } else {
                let seg = Array(modulator[histStart..<end])
                let vocoded = SpectralVocoder.process(
                    modulator: seg,
                    carrier: carrierSlice(from: histStart, count: seg.count),
                    config: program.config,
                    sampleRate: sampleRate
                )
                out = Array(vocoded.suffix(blockLen))
            }
            ring.write(out)
            processedFrames += blockLen
        }
    }

    private func carrierSlice(from start: Int, count: Int) -> [Float] {
        let carrier = program.carrier
        guard !carrier.isEmpty else { return [] }
        var slice = [Float](repeating: 0, count: count)
        for i in 0..<count {
            slice[i] = carrier[(start + i) % carrier.count]
        }
        return slice
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
