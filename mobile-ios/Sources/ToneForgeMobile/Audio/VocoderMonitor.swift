// VocoderMonitor.swift
//
// Live-preview output for the vocoder capture flow (P5):
//
//   VocoderCaptureSession worker → VocoderPreviewRing → sourceNode
//     → engine.vocoderBusInput (vocoderBus → sharedBus, D-013)
//
// The ring is the ONLY hand-off between the processing worker (a
// serial DispatchQueue) and the render thread. The render callback
// never blocks on DSP: it copies whatever the ring holds and
// zero-fills the rest, bumping the underrun counter — that counter is
// the P7 "vocoder capture zero dropouts" gate.
//
// Feedback safety: on the built-in speaker route the preview is
// MUTED, not disabled — the render thread still consumes the ring at
// the same pace (so underrun accounting stays meaningful for the
// probe) but writes silence, keeping vocoded audio out of the mic.
//
// The node attaches once at bootAudio and stays attached; outside a
// capture the ring is inactive and the callback renders silence, so
// the running graph is never rewired mid-jam.

import Foundation
import AVFoundation
import ToneForgeEngine

// MARK: - Ring buffer

/// Lock-guarded SPSC ring between the preview worker (producer) and
/// the render callback (consumer). os_unfair_lock with tiny critical
/// sections (a bounded copy) — same pragmatism as CaptureBox's NSLock,
/// but unfair-lock so the render thread benefits from priority
/// donation if it ever contends with the worker.
final class VocoderPreviewRing: @unchecked Sendable {

    private let lock: UnsafeMutablePointer<os_unfair_lock_s>
    private var buffer: [Float]
    /// Absolute sample counts — the difference is the fill level.
    private var readPos = 0
    private var writePos = 0
    private var active = false
    private var muted = false
    private var underrunCount = 0

    /// Default capacity 2 s at 48 kHz — the worker writes ~85 ms
    /// blocks, so this never fills unless the consumer stalls.
    init(capacity: Int = 96_000) {
        buffer = [Float](repeating: 0, count: capacity)
        lock = UnsafeMutablePointer<os_unfair_lock_s>.allocate(capacity: 1)
        lock.initialize(to: os_unfair_lock_s())
    }

    deinit {
        lock.deinitialize(count: 1)
        lock.deallocate()
    }

    /// Arm for a new capture: clear content, counters, and the mute
    /// state. The ring stays silent until the first `write`.
    func begin(muted: Bool) {
        os_unfair_lock_lock(lock)
        readPos = 0
        writePos = 0
        active = false
        underrunCount = 0
        self.muted = muted
        os_unfair_lock_unlock(lock)
    }

    /// Capture ended: back to silent renders, no underrun counting.
    func end() {
        os_unfair_lock_lock(lock)
        active = false
        os_unfair_lock_unlock(lock)
    }

    /// Producer side (worker queue). The first write activates the
    /// ring — renders before it are warm-up silence, not underruns.
    /// Overflow drops the NEWEST samples (preview only; the full-take
    /// modulator is accumulated separately).
    func write(_ samples: [Float]) {
        os_unfair_lock_lock(lock)
        let capacity = buffer.count
        for s in samples {
            if writePos - readPos >= capacity { break }
            buffer[writePos % capacity] = s
            writePos += 1
        }
        active = true
        os_unfair_lock_unlock(lock)
    }

    /// Consumer side (render thread). Copies up to `count` samples
    /// into `out`, zero-fills any shortage. A shortage while active
    /// is an underrun. Muted rings consume at full pace but emit
    /// silence (speaker-route feedback guard).
    func read(into out: UnsafeMutablePointer<Float>, count: Int) {
        os_unfair_lock_lock(lock)
        if !active {
            os_unfair_lock_unlock(lock)
            out.update(repeating: 0, count: count)
            return
        }
        let capacity = buffer.count
        let take = min(count, writePos - readPos)
        if muted {
            readPos += take
            os_unfair_lock_unlock(lock)
            out.update(repeating: 0, count: count)
            return
        }
        for i in 0..<take {
            out[i] = buffer[(readPos + i) % capacity]
        }
        readPos += take
        if take < count { underrunCount += 1 }
        os_unfair_lock_unlock(lock)
        if take < count {
            (out + take).update(repeating: 0, count: count - take)
        }
    }

    var underruns: Int {
        os_unfair_lock_lock(lock)
        defer { os_unfair_lock_unlock(lock) }
        return underrunCount
    }
}

// MARK: - Monitor node

/// Hosts the preview source node on the main audio graph. Mirrors
/// WavetableSynthNode's attach/detach shape.
@MainActor
public final class VocoderMonitor: ObservableObject {

    let ring = VocoderPreviewRing()

    private var sourceNode: AVAudioSourceNode?
    private weak var engine: AudioEngine?

    public init(engine: AudioEngine) {
        self.engine = engine
    }

    /// Attach the source node to vocoderBus. Idempotent; call from
    /// bootAudio before engine start (graph-validator rule, see
    /// AudioEngine.buildContributionGraph).
    public func attach() {
        guard let engine = engine, sourceNode == nil else { return }
        let format = engine.canonicalFormat

        let ring = self.ring
        let source = AVAudioSourceNode(format: format) {
            _, _, frameCount, audioBufferList in
            let abl = UnsafeMutableAudioBufferListPointer(audioBufferList)
            let frames = Int(frameCount)
            guard let left = abl[0].mData?.assumingMemoryBound(
                to: Float.self
            ) else { return noErr }
            ring.read(into: left, count: frames)
            if abl.count > 1,
               let right = abl[1].mData?.assumingMemoryBound(to: Float.self) {
                right.update(from: left, count: frames)
            }
            return noErr
        }

        engine.engine.attach(source)
        engine.engine.connect(
            source, to: engine.vocoderBusInput, format: format
        )
        self.sourceNode = source
    }

    /// Detach and free the node. Called on scene teardown.
    public func detach() {
        guard let engine = engine, let source = sourceNode else { return }
        engine.engine.detach(source)
        sourceNode = nil
    }
}
