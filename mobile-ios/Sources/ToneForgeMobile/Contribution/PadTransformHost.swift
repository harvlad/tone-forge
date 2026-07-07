// PadTransformHost.swift
//
// The mobile-side owner of the P4 transform pipeline. Transform
// chains are rendered TO A BUFFER when they're assigned ("render on
// arm"), never on the trigger path — the voice pool and the ≤8 ms
// pad-tap budget stay untouched, and the disk sample never changes
// (non-destructive).
//
// The host fills the two SampleScheduler seams:
//   * transformResolver — trigger-time buffer swap: rendered buffer
//     if this (packId, padIdx) has a chain, else the base.
//   * loopResolver — whether the chain contains `.loop` (a playback
//     flag, identity in TransformEngine).
//
// Renders run off-main via Task.detached (WSOLA/granular/PSOLA can
// take hundreds of ms for an 8 s sample); a per-key generation
// counter drops stale results when the user edits the chain again
// mid-render. Rendered channels are LRU-cached (TransformCache,
// 64 MB) so re-assigning a previous chain or revisiting a mode is
// instant.

import Foundation
import ToneForgeEngine
#if canImport(AVFoundation)
import AVFoundation
#endif

@MainActor
public final class PadTransformHost {

    public struct Key: Hashable, Sendable {
        public let packId: String
        public let padIdx: Int
        public init(packId: String, padIdx: Int) {
            self.packId = packId
            self.padIdx = padIdx
        }
    }

    private let cache = TransformCache()
    /// Pads whose chain contains `.loop` (playback flag).
    private var loopKeys: Set<Key> = []
    /// Monotonic per-key edit counter; async renders that finish
    /// after a newer setChain are discarded.
    private var generation: [Key: UInt64] = [:]

    #if canImport(AVFoundation)
    /// Rendered buffers keyed by pad; consulted by transformResolver.
    private var rendered: [Key: AVAudioPCMBuffer] = [:]
    #endif

    public init() {}

    // MARK: - Scheduler seams

    /// Whether the pad's chain contains `.loop`.
    public func loops(packId: String, padIdx: Int) -> Bool {
        loopKeys.contains(Key(packId: packId, padIdx: padIdx))
    }

    #if canImport(AVFoundation)
    /// Trigger-time resolution: the rendered buffer when a chain is
    /// armed for this pad, else the base. Synchronous — a chain still
    /// rendering plays the base until the render lands.
    public func resolve(
        base: AVAudioPCMBuffer, packId: String, padIdx: Int
    ) -> AVAudioPCMBuffer {
        rendered[Key(packId: packId, padIdx: padIdx)] ?? base
    }
    #endif

    // MARK: - Chain management

    /// Drop every armed render + loop flag (mode switch re-syncs from
    /// the assignment store). The LRU cache is kept — re-arming the
    /// same chain on the same audio is a cache hit.
    public func clearAll() {
        loopKeys.removeAll()
        generation.removeAll()
        #if canImport(AVFoundation)
        rendered.removeAll()
        #endif
    }

    #if canImport(AVFoundation)
    /// Arm `chain` for the pad: update the loop flag and render the
    /// audible transforms into a buffer (cache-first, else detached
    /// render). Empty chain / nil base clears the pad's render.
    /// `chord` feeds the harmony transform's voice leading (MIDI
    /// notes; empty = nominal intervals).
    public func setChain(
        _ chain: [PadTransform],
        packId: String,
        padIdx: Int,
        base: AVAudioPCMBuffer?,
        tempoBpm: Double,
        chord: [Int]
    ) {
        let key = Key(packId: packId, padIdx: padIdx)
        let gen = (generation[key] ?? 0) &+ 1
        generation[key] = gen

        if chain.contains(.loop) {
            loopKeys.insert(key)
        } else {
            loopKeys.remove(key)
        }

        // `.loop` is a playback flag (identity in TransformEngine);
        // rendering it alone would just duplicate the base buffer.
        let audible = chain.filter { $0 != .loop }
        guard !audible.isEmpty, let base,
              let baseChannels = Self.channels(of: base)
        else {
            rendered.removeValue(forKey: key)
            return
        }

        let cacheKey = TransformCacheKey(
            contentHash: TransformHashing.contentHash(baseChannels),
            chainHash: TransformHashing.chainHash(audible),
            tempoBucket: TransformHashing.tempoBucket(tempoBpm)
        )
        if let hit = cache.channels(for: cacheKey),
           let buffer = Self.makeBuffer(channels: hit, format: base.format) {
            rendered[key] = buffer
            return
        }

        let format = base.format
        Task.detached(priority: .userInitiated) {
            let out = baseChannels.map { channel in
                TransformEngine.render(
                    channel,
                    chain: audible,
                    tempoBpm: tempoBpm,
                    sampleRate: format.sampleRate,
                    chordAt: { _ in chord }
                )
            }
            await MainActor.run { [weak self] in
                guard let self, self.generation[key] == gen else { return }
                self.cache.store(out, for: cacheKey)
                if let buffer = Self.makeBuffer(channels: out, format: format) {
                    self.rendered[key] = buffer
                }
            }
        }
    }

    /// Render `chain` over `base` synchronously off-main and return
    /// mono (channel average) samples — the bake path (render →
    /// classify → save as a new local sample).
    public static func renderMono(
        _ chain: [PadTransform],
        base: AVAudioPCMBuffer,
        tempoBpm: Double,
        chord: [Int]
    ) async -> [Float] {
        guard let baseChannels = channels(of: base) else { return [] }
        let audible = chain.filter { $0 != .loop }
        let sampleRate = base.format.sampleRate
        return await Task.detached(priority: .userInitiated) {
            let out = baseChannels.map { channel in
                TransformEngine.render(
                    channel,
                    chain: audible,
                    tempoBpm: tempoBpm,
                    sampleRate: sampleRate,
                    chordAt: { _ in chord }
                )
            }
            guard let first = out.first, !first.isEmpty else { return [] }
            guard out.count > 1 else { return first }
            var mono = first
            let scale = 1.0 / Float(out.count)
            for ch in out.dropFirst() {
                let n = min(mono.count, ch.count)
                for i in 0..<n { mono[i] += ch[i] }
            }
            for i in 0..<mono.count { mono[i] *= scale }
            return mono
        }.value
    }

    // MARK: - Buffer <-> channel plumbing

    /// Deinterleave an AVAudioPCMBuffer into per-channel Float arrays.
    static func channels(of buffer: AVAudioPCMBuffer) -> [[Float]]? {
        guard let data = buffer.floatChannelData else { return nil }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return nil }
        return (0..<channelCount).map {
            Array(UnsafeBufferPointer(start: data[$0], count: frames))
        }
    }

    /// Rebuild an AVAudioPCMBuffer in `format` from rendered channels.
    /// Transforms keep channel counts (same chain applied per channel),
    /// but a defensive mono→stereo duplicate keeps a mismatch from
    /// scheduling a half-silent buffer.
    static func makeBuffer(
        channels: [[Float]], format: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        guard let first = channels.first, !first.isEmpty else { return nil }
        let frames = AVAudioFrameCount(first.count)
        guard let buffer = AVAudioPCMBuffer(
            pcmFormat: format, frameCapacity: frames
        ), let data = buffer.floatChannelData else { return nil }
        buffer.frameLength = frames
        for ch in 0..<Int(format.channelCount) {
            let source = ch < channels.count ? channels[ch] : first
            source.withUnsafeBufferPointer { src in
                data[ch].update(
                    from: src.baseAddress!,
                    count: min(src.count, Int(frames))
                )
            }
        }
        return buffer
    }
    #endif
}
