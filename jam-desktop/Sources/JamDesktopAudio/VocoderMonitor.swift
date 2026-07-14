// VocoderMonitor.swift
//
// Live-preview output for the vocoder capture flow:
//
//   VocoderCaptureSession worker → VocoderPreviewRing → sourceNode
//     → musicBus (vocoder monitor mix into master FX chain)
//
// The ring is the ONLY hand-off between the processing worker (a
// serial DispatchQueue) and the render thread. The render callback
// never blocks on DSP: it copies whatever the ring holds and
// zero-fills the rest, bumping the underrun counter — that counter
// is the "vocoder capture zero dropouts" gate.
//
// Feedback safety: on the built-in speaker route the preview is
// MUTED, not disabled — the render thread still consumes the ring at
// the same pace but writes silence, keeping vocoded audio out of mic.
//
// The node attaches once at startup and stays attached; outside a
// capture the ring is inactive and the callback renders silence, so
// the running graph is never rewired mid-jam.
//
// Desktop port of iOS VocoderMonitor.

import Foundation
import AVFoundation

// MARK: - Ring buffer

/// Lock-guarded SPSC ring between the preview worker (producer) and
/// the render callback (consumer). os_unfair_lock with tiny critical
/// sections (a bounded copy) — unfair-lock so the render thread
/// benefits from priority donation if it ever contends with worker.
public final class VocoderPreviewRing: @unchecked Sendable {

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
    public init(capacity: Int = 96_000) {
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
    public func begin(muted: Bool) {
        os_unfair_lock_lock(lock)
        readPos = 0
        writePos = 0
        active = false
        underrunCount = 0
        self.muted = muted
        os_unfair_lock_unlock(lock)
    }

    /// Capture ended: back to silent renders, no underrun counting.
    public func end() {
        os_unfair_lock_lock(lock)
        active = false
        os_unfair_lock_unlock(lock)
    }

    /// Producer side (worker queue). The first write activates the
    /// ring — renders before it are warm-up silence, not underruns.
    /// Overflow drops the NEWEST samples (preview only; the full-take
    /// modulator is accumulated separately).
    public func write(_ samples: [Float]) {
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
    public func read(into out: UnsafeMutablePointer<Float>, count: Int) {
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

    public var underruns: Int {
        os_unfair_lock_lock(lock)
        defer { os_unfair_lock_unlock(lock) }
        return underrunCount
    }
}

// MARK: - Monitor node

/// Hosts the preview source node on the desktop audio graph. Attaches
/// to musicBus so preview is colored by master FX.
@MainActor
public final class VocoderMonitor: ObservableObject {

    public let ring = VocoderPreviewRing()

    private var sourceNode: AVAudioSourceNode?
    private var mixerNode: AVAudioMixerNode?
    private let avEngine: AVAudioEngine

    /// Preview gain (linear). -8 dB = 0.4.
    public static let previewGain: Float = 0.4

    public init(avEngine: AVAudioEngine) {
        self.avEngine = avEngine
    }

    /// Attach the source node to the engine (connect to `outputNode`
    /// which should be musicBus). Idempotent; call before engine start.
    public func attach(outputNode: AVAudioNode) {
        guard sourceNode == nil else { return }

        let format = AVAudioFormat(
            standardFormatWithSampleRate: 48_000, channels: 2
        )!

        let ring = self.ring
        let source = AVAudioSourceNode(format: format) {
            _, _, frameCount, audioBufferList in
            let abl = UnsafeMutableAudioBufferListPointer(audioBufferList)
            let frames = Int(frameCount)
            guard let left = abl[0].mData?.assumingMemoryBound(
                to: Float.self
            ) else { return noErr }
            ring.read(into: left, count: frames)
            // Duplicate to right channel for stereo
            if abl.count > 1,
               let right = abl[1].mData?.assumingMemoryBound(to: Float.self) {
                right.update(from: left, count: frames)
            }
            return noErr
        }

        // Mixer node for gain control
        let mixer = AVAudioMixerNode()
        mixer.outputVolume = Self.previewGain

        avEngine.attach(source)
        avEngine.attach(mixer)
        avEngine.connect(source, to: mixer, format: format)
        avEngine.connect(mixer, to: outputNode, format: format)

        self.sourceNode = source
        self.mixerNode = mixer
    }

    /// Detach and free nodes. Called on scene teardown.
    public func detach() {
        if let source = sourceNode {
            avEngine.detach(source)
            sourceNode = nil
        }
        if let mixer = mixerNode {
            avEngine.detach(mixer)
            mixerNode = nil
        }
    }

    /// Re-wire after device flap (graph rebuild). Node stays attached
    /// but connections drop.
    public func reattach(outputNode: AVAudioNode) {
        guard let source = sourceNode, let mixer = mixerNode else { return }
        let format = AVAudioFormat(
            standardFormatWithSampleRate: 48_000, channels: 2
        )!
        avEngine.connect(source, to: mixer, format: format)
        avEngine.connect(mixer, to: outputNode, format: format)
    }
}
