// OutputRecorder.swift
//
// Records the SESSION'S AUDIO OUTPUT — the fully-processed mix the
// user actually hears — by tapping the LAST MASTER NODE feeding
// outputNode (AudioEngine.masterTapNode: the brickwall limiter, or the
// deepest built master node). The limiter → outputNode hop is a direct
// connection, so the capture matches the speakers: song stems + pads +
// FX + master processing, nothing pre-fader.
//
// Why not outputNode itself: it REFUSES recording taps —
// installTapOnBus throws an NSException from
// AUGraphNodeBaseV3::CreateRecordingTap (output-only unit), which
// crashed the first live press ("pressing record crashes the app").
// And why not mainMixerNode: it sits BEFORE the master FX inserts, so a
// tap there would record something the user never heard.
//
// This taps the app's OWN output bus, never the microphone input, so
// there is no mic permission and no feedback risk — the signal is
// already inside the graph.
//
// Threading mirrors MicRecorder: the tap block runs on a realtime
// audio thread and writes straight to the AVAudioFile through a
// lock-guarded box; only the meter/elapsed/auto-stop updates hop to
// the main actor. The file is opened at the tap format's native rate
// (typically 48 kHz stereo on this engine) so `write(from:)` never
// hits a format-mismatch — AAC compression is transparent to the
// caller. A 15-minute safety cap auto-stops a runaway capture.

import Foundation
import AVFoundation
import AudioToolbox

@MainActor
public final class OutputRecorder: ObservableObject {

    public enum State: Equatable {
        case idle
        case recording
    }

    @Published public private(set) var state: State = .idle
    @Published public private(set) var elapsedSec: Double = 0
    /// Peak amplitude (0…1) of the most recent tap buffer — drives the
    /// transport pill's level meter. Reset to 0 at start/stop.
    @Published public private(set) var peak: Float = 0

    /// Safety ceiling. A forgotten record button must not fill the disk
    /// with a multi-hour m4a; at the cap the tap auto-stops and the
    /// take is finalized like a manual stop.
    public static let maxDurationSec: Double = 15 * 60

    /// Fired on the main actor when the cap auto-stops the capture.
    /// Payload = the finished file (nil if nothing was written).
    public var onAutoStop: ((URL?) -> Void)?

    /// Weak — the engine is owned by AudioEngine; the recorder only
    /// borrows a node to install a tap.
    private weak var engine: AVAudioEngine?
    /// The node the tap installs on — AudioEngine.masterTapNode, resolved
    /// lazily at start() so it reflects the master chain actually built.
    private let tapNodeProvider: () -> AVAudioNode?
    /// The node currently carrying our tap, so finish() removes the tap
    /// from the SAME node even if the provider would resolve differently.
    private weak var tappedNode: AVAudioNode?
    private var box: OutputWriterBox?
    private var currentURL: URL?
    private var startedAt: Date?
    private var elapsedTimer: Timer?

    public init(engine: AVAudioEngine, tapNode: @escaping () -> AVAudioNode?) {
        self.engine = engine
        self.tapNodeProvider = tapNode
    }

    // MARK: - Control

    /// Install the output tap and begin writing. Returns false (a
    /// no-op) when already recording or when the engine isn't running
    /// — there is nothing to capture from a stopped graph, and the
    /// caller should surface that rather than believe a take started.
    @discardableResult
    public func start() -> Bool {
        guard state == .idle else { return false }
        guard let engine, engine.isRunning else { return false }
        guard let node = tapNodeProvider() else { return false }
        let format = node.outputFormat(forBus: 0)
        // A running engine whose output bus reports a 0 rate hasn't
        // fully negotiated its render format yet — refuse rather than
        // open a file we can't write.
        guard format.sampleRate > 0, format.channelCount > 0 else { return false }

        let url = Self.makeTempURL()
        let file: AVAudioFile
        do {
            file = try AVAudioFile(
                forWriting: url, settings: Self.aacSettings(for: format))
        } catch {
            return false
        }

        // Defensive: a prior crash/leak could leave a stranded tap on
        // this bus, and installTap traps on a double-install. Clearing
        // first makes start() safe to call after an aborted session.
        node.removeTap(onBus: 0)

        let capFrames = AVAudioFramePosition(Self.maxDurationSec * format.sampleRate)
        let box = OutputWriterBox(file: file, capFrames: capFrames)
        node.installTap(onBus: 0, bufferSize: 4096, format: format) {
            [weak self] buffer, _ in
            // Realtime tap thread: write + measure, then hop to main
            // only for the published UI mirrors and the cap check.
            let bufferPeak = Self.peak(of: buffer)
            let hitCap = box.write(buffer)
            DispatchQueue.main.async {
                guard let self, self.state == .recording else { return }
                self.peak = bufferPeak
                if hitCap { self.autoStop() }
            }
        }

        self.tappedNode = node
        self.box = box
        self.currentURL = url
        self.startedAt = Date()
        self.elapsedSec = 0
        self.peak = 0
        self.state = .recording
        self.elapsedTimer = Timer.scheduledTimer(
            withTimeInterval: 1.0 / 30.0, repeats: true
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, let startedAt = self.startedAt else { return }
                self.elapsedSec = min(
                    Date().timeIntervalSince(startedAt),
                    Self.maxDurationSec
                )
            }
        }
        return true
    }

    /// Stop, flush the m4a, and return its URL. Nil when not recording,
    /// or when nothing was written (the empty file is deleted).
    @discardableResult
    public func stop() -> URL? {
        guard state == .recording else { return nil }
        return finish()
    }

    private func autoStop() {
        guard state == .recording else { return }  // stop() may have raced us
        let url = finish()
        onAutoStop?(url)
    }

    private func finish() -> URL? {
        elapsedTimer?.invalidate()
        elapsedTimer = nil
        state = .idle
        peak = 0

        tappedNode?.removeTap(onBus: 0)
        tappedNode = nil
        let frames = box?.close() ?? 0
        box = nil

        let url = currentURL
        currentURL = nil
        startedAt = nil

        guard frames > 0, let url else {
            // Nothing captured (instant stop) — don't leave a 0-byte
            // m4a behind for the store to ingest.
            if let url { try? FileManager.default.removeItem(at: url) }
            return nil
        }
        return url
    }

    // MARK: - Pure helpers (testable without a live engine)

    /// Peak absolute amplitude across ALL channels of a tap buffer
    /// (0…1). Unlike MicRecorder.peak (channel 0 only) the output tap
    /// is stereo and the meter should reflect the louder side.
    nonisolated static func peak(of buffer: AVAudioPCMBuffer) -> Float {
        guard let channels = buffer.floatChannelData, buffer.frameLength > 0 else {
            return 0
        }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        var maxAbs: Float = 0
        for c in 0..<channelCount {
            let data = channels[c]
            for i in 0..<frames {
                let a = abs(data[i])
                if a > maxAbs { maxAbs = a }
            }
        }
        return min(maxAbs, 1)
    }

    /// AAC .m4a encoder settings derived from the tap format. Rate +
    /// channel count come from the output bus so the file's
    /// processingFormat matches the tap buffers exactly — writing
    /// mismatched formats to an AVAudioFile throws.
    static func aacSettings(for format: AVAudioFormat) -> [String: Any] {
        [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: format.channelCount,
            AVEncoderBitRateKey: 192_000,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
    }

    private static func makeTempURL() -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("session-out-\(UUID().uuidString).m4a")
    }
}

// MARK: - OutputWriterBox

/// Lock-guarded bridge between the realtime tap thread (writes) and
/// the main actor (stop/close). AVAudioFile is not thread-safe, so
/// every touch of it goes through the lock; `close()` nils the file,
/// whose deallocation finalizes the AAC container.
final class OutputWriterBox: @unchecked Sendable {

    private let lock = NSLock()
    private var file: AVAudioFile?
    private var written: AVAudioFramePosition = 0
    private var capped = false
    let capFrames: AVAudioFramePosition

    init(file: AVAudioFile, capFrames: AVAudioFramePosition) {
        self.file = file
        self.capFrames = capFrames
    }

    /// Write a tap buffer. Returns true exactly once — on the write
    /// that reaches the cap — so the caller auto-stops. The final
    /// buffer is written whole (≤1 buffer of overrun at a 15-min cap
    /// is irrelevant, and AAC frames don't truncate cleanly mid-block).
    func write(_ buffer: AVAudioPCMBuffer) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard let file, !capped else { return false }
        do {
            try file.write(from: buffer)
            written += AVAudioFramePosition(buffer.frameLength)
        } catch {
            // A write failure (disk full, encoder hiccup) shouldn't spin
            // the tap; freeze the box so we stop appending and let the
            // main actor finalize whatever landed.
            capped = true
            return true
        }
        if written >= capFrames {
            capped = true
            return true
        }
        return false
    }

    /// Close the file (finalizing the m4a) and return total frames
    /// written. Idempotent.
    func close() -> AVAudioFramePosition {
        lock.lock()
        defer { lock.unlock() }
        file = nil
        return written
    }
}
