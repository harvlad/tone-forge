// VocoderCaptureSession.swift
//
// Capture-only vocoder flow (P5): record the mic ≤8 s while a
// low-latency PREVIEW of the vocoded result plays through
// VocoderMonitor → vocoderBus, then on stop run the FULL take through
// the offline kernel at full quality and hand back (raw, processed).
// The processed take goes on to Classifier → PadSampleStore (source
// .vocoded, neverUpload) → pad assignment; there is no persistent
// live-vocoder path (non-goal).
//
// Threading (R10 — dropouts):
//   mic tap thread  → mono-ize copy → serial worker queue
//   worker queue    → resample to 48 k → accumulate modulator →
//                     block-vocode (4096-sample blocks + 2048 history
//                     against the pre-built carrier) → ring
//   render thread   → VocoderPreviewRing.read (never blocks on DSP;
//                     shortage = underrun, the P7 gate counter)
//
// Preview fidelity: each 85 ms block is vocoded with one FFT-size of
// history so band envelopes are warm at the block seam; the tiny
// residual seam ripple is preview-only — the saved sample is a single
// whole-take render. M4 (harmony) previews the DRY mic (PSOLA is too
// heavy/stateful for block preview) and harmonizes on stop.
//
// Feedback safety: built-in speaker route → preview muted at the ring
// (still consumed, so underrun accounting works) + routeWarning, same
// policy as MicRecorder.

import Foundation
import AVFoundation
import ToneForgeEngine

/// Everything the capture needs to preview and process one take,
/// pre-built by the coordinator before `start` (carrier synthesis is
/// deterministic and cheap, but not render-thread cheap).
public struct VocoderProgram: Sendable {
    public let mode: VocoderMode
    /// 48 kHz mono carrier covering the full 8 s cap (looped/sliced
    /// as needed). Ignored for `.harmony`.
    public let carrier: [Float]
    /// Chord grid for `.harmony`'s voice leading (last span whose
    /// start ≤ t wins; empty → no chord info, nearest-tone fallback).
    public let chordSpans: [VocoderCarriers.ChordSpan]
    public let harmonySettings: HarmonySettings
    public let config: VocoderConfig

    public init(
        mode: VocoderMode,
        carrier: [Float],
        chordSpans: [VocoderCarriers.ChordSpan] = [],
        harmonySettings: HarmonySettings = HarmonySettings(),
        config: VocoderConfig = VocoderConfig()
    ) {
        self.mode = mode
        self.carrier = carrier
        self.chordSpans = chordSpans
        self.harmonySettings = harmonySettings
        self.config = config
    }
}

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

        public var errorDescription: String? {
            switch self {
            case .permissionDenied:
                return "Microphone access is not allowed. Enable it in Settings."
            case .alreadyCapturing:
                return "A vocoder capture is already in progress"
            case .engineStartFailed(let msg):
                return "Could not start the microphone: \(msg)"
            }
        }
    }

    public static let maxDurationSec: Double = StemSlice.maxChopDurationSec

    @Published public private(set) var isCapturing = false
    @Published public private(set) var elapsedSec: Double = 0
    @Published public private(set) var routeWarning: MicRecorder.RouteWarning?
    /// Preview-ring underruns so far this capture — the P7 "zero
    /// dropouts" gate reads this after a scripted 8 s take.
    @Published public private(set) var underrunCount = 0

    /// Fired on the main actor when the 8 s cap auto-stops the
    /// capture, after full-quality processing.
    public var onAutoStop: ((Take) -> Void)?

    private let session: AudioSessionController
    private let monitor: VocoderMonitor
    private var captureEngine: AVAudioEngine?
    private var worker: VocoderPreviewWorker?
    private var program: VocoderProgram?
    private var elapsedTimer: Timer?
    private var startedAt: Date?

    public init(session: AudioSessionController, monitor: VocoderMonitor) {
        self.session = session
        self.monitor = monitor
    }

    // MARK: - Control

    /// Request permission, flip the session to record mode, arm the
    /// preview ring, and start capturing against `program`.
    public func start(program: VocoderProgram) async throws {
        guard !isCapturing else { throw CaptureError.alreadyCapturing }
        guard await MicRecorder.requestPermission() else {
            throw CaptureError.permissionDenied
        }

        session.activateForRecording()
        routeWarning = session.isOutputBuiltInSpeaker
            ? .speakerFeedbackRisk
            : (session.isRouteBluetooth ? .bluetoothLatency : nil)
        monitor.ring.begin(muted: routeWarning == .speakerFeedbackRisk)

        let worker = VocoderPreviewWorker(
            program: program,
            ring: monitor.ring,
            sampleRate: AudioEngine.canonicalSampleRate,
            capFrames: Int(
                Self.maxDurationSec * AudioEngine.canonicalSampleRate
            ),
            onCap: { [weak self] in
                DispatchQueue.main.async { self?.autoStop() }
            }
        )

        let engine = AVAudioEngine()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: format) {
            buffer, _ in
            // Tap thread: copy out and hop to the worker queue.
            worker.ingest(buffer)
        }

        engine.prepare()
        do {
            try engine.start()
        } catch {
            input.removeTap(onBus: 0)
            monitor.ring.end()
            session.revertToPlayback()
            routeWarning = nil
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
            sampleRate: AudioEngine.canonicalSampleRate
        )
        return Take(raw: raw, processed: processed, mode: program.mode)
    }

    /// Abandon the capture (user cancelled the sheet).
    public func cancel() {
        guard isCapturing else { return }
        _ = finishCapture()
    }

    private func autoStop() {
        guard isCapturing, let program else { return }  // stop() raced us
        let raw = finishCapture()
        Task { [weak self] in
            let processed = await Self.processFull(
                raw: raw, program: program,
                sampleRate: AudioEngine.canonicalSampleRate
            )
            self?.onAutoStop?(
                Take(raw: raw, processed: processed, mode: program.mode)
            )
        }
    }

    /// Tear down the capture engine + preview and return the dry
    /// 48 kHz mono take accumulated so far.
    private func finishCapture() -> [Float] {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
        isCapturing = false
        routeWarning = nil
        startedAt = nil

        captureEngine?.inputNode.removeTap(onBus: 0)
        captureEngine?.stop()
        captureEngine = nil
        underrunCount = monitor.ring.underruns
        monitor.ring.end()
        session.revertToPlayback()

        guard let worker else { return [] }
        self.worker = nil
        self.program = nil
        return worker.drain()
    }

    // MARK: - Full-quality processing (off-main)

    /// Whole-take render: SpectralVocoder for M1–M3/M5, PSOLA
    /// harmonize for M4. Detached — an 8 s PSOLA pass is too heavy
    /// for the main actor.
    nonisolated static func processFull(
        raw: [Float], program: VocoderProgram, sampleRate: Double
    ) async -> [Float] {
        guard !raw.isEmpty else { return [] }
        return await Task.detached(priority: .userInitiated) {
            let rate = sampleRate
            if program.mode == .harmony {
                let spans = program.chordSpans
                return PSOLAHarmonizer.harmonize(
                    raw, sampleRate: rate,
                    chordAt: { t in
                        spans.last(where: { $0.startSec <= t })?
                            .midiNotes ?? []
                    },
                    settings: program.harmonySettings
                )
            }
            return SpectralVocoder.process(
                modulator: raw, carrier: program.carrier,
                config: program.config, sampleRate: rate
            )
        }.value
    }
}

// MARK: - Preview worker

/// Serial-queue owner of the per-capture DSP state. The tap thread
/// only copies samples in; everything else (resample, accumulate,
/// block vocode, ring writes) happens on `queue` so the DSP never
/// touches the tap or render threads.
final class VocoderPreviewWorker: @unchecked Sendable {

    /// Preview block: 4096 samples ≈ 85 ms at 48 k.
    static let blockLen = 4096
    /// History fed before each block so the vocoder's band envelopes
    /// are warm at the seam (= its FFT size).
    static let historyLen = 2048

    private let queue = DispatchQueue(
        label: "toneforge.vocoder.preview", qos: .userInitiated
    )
    private let program: VocoderProgram
    private let ring: VocoderPreviewRing
    /// Canonical rate, injected so the nonisolated worker never
    /// touches the main-actor AudioEngine statics.
    private let sampleRate: Double
    private let capFrames: Int
    private let onCap: () -> Void

    // Queue-confined state.
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

    /// Tap-thread entry: mono-ize (channel average) and hand off.
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

    /// Blocking drain for stop(): waits for in-flight blocks, then
    /// returns the accumulated 48 kHz mono modulator.
    func drain() -> [Float] {
        queue.sync { modulator }
    }

    // MARK: - Queue-confined

    private func append(_ mono: [Float], nativeRate: Double) {
        guard !capped else { return }

        // Native mic rate is 48 k in practice (the session prefers
        // it); the stateless per-chunk fallback below only runs on
        // routes that refuse 48 k, where the ~1 kHz chunk-edge
        // artifacts are preview-and-capture tolerable vs. carrying a
        // streaming converter.
        let converted = nativeRate == sampleRate
            ? mono
            : MicRecorder.resample(mono, from: nativeRate, to: sampleRate)

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

    /// Time-aligned carrier window, wrapping if the take outruns the
    /// pre-built carrier (it shouldn't — carriers cover the 8 s cap).
    private func carrierSlice(from start: Int, count: Int) -> [Float] {
        let carrier = program.carrier
        guard !carrier.isEmpty else { return [] }
        if start + count <= carrier.count {
            return Array(carrier[start..<(start + count)])
        }
        var out = [Float](repeating: 0, count: count)
        for i in 0..<count {
            out[i] = carrier[(start + i) % carrier.count]
        }
        return out
    }
}
