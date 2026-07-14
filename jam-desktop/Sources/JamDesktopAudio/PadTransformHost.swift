// PadTransformHost.swift
//
// Desktop counterpart of iOS PadTransformHost: manages render-on-arm
// transform pipeline. Transform chains render TO A BUFFER when assigned,
// never on the trigger path — keeps ≤8 ms pad-tap budget and disk
// samples untouched (non-destructive).
//
// Fills two ChopPlayer seams:
//   * transformResolver — trigger-time buffer swap: rendered buffer if
//     pad has a chain, else base.
//   * loopResolver — whether chain contains `.loop` (playback flag).
//
// Renders run off-main via Task.detached; per-key generation counter
// drops stale results when user edits chain mid-render. Rendered
// channels are LRU-cached (TransformCache, 64 MB) so re-arming same
// chain or revisiting a mode is instant.

import Foundation
import AVFoundation
import ToneForgeEngine

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
    /// Rendered buffers keyed by pad; consulted by transformResolver.
    private var rendered: [Key: AVAudioPCMBuffer] = [:]

    public init() {}

    // MARK: - ChopPlayer seams

    /// Whether pad's chain contains `.loop`.
    public func loops(packId: String, padIdx: Int) -> Bool {
        loopKeys.contains(Key(packId: packId, padIdx: padIdx))
    }

    /// Trigger-time resolution: rendered buffer if chain armed, else
    /// base. Synchronous — chain still rendering plays base until
    /// render lands.
    public func resolve(
        base: AVAudioPCMBuffer, packId: String, padIdx: Int
    ) -> AVAudioPCMBuffer {
        rendered[Key(packId: packId, padIdx: padIdx)] ?? base
    }

    // MARK: - Chain management

    /// Drop all armed renders + loop flags (mode switch re-syncs from
    /// assignment store). LRU cache kept — re-arming same chain on
    /// same audio is cache hit.
    public func clearAll() {
        loopKeys.removeAll()
        generation.removeAll()
        rendered.removeAll()
    }

    /// Arm `chain` for pad: update loop flag, render audible transforms
    /// into buffer (cache-first, else detached render). Empty chain /
    /// nil base clears pad's render. `chord` feeds harmony voice leading
    /// (MIDI notes; empty = nominal intervals).
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

        // `.loop` is playback flag (identity in TransformEngine);
        // rendering it alone would duplicate base buffer.
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

    /// Render `chain` over `base` synchronously off-main, return mono
    /// (channel average) samples — bake path (render → classify → save
    /// as new local sample).
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

    /// Deinterleave AVAudioPCMBuffer into per-channel Float arrays.
    static func channels(of buffer: AVAudioPCMBuffer) -> [[Float]]? {
        guard let data = buffer.floatChannelData else { return nil }
        let frames = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frames > 0, channelCount > 0 else { return nil }
        return (0..<channelCount).map {
            Array(UnsafeBufferPointer(start: data[$0], count: frames))
        }
    }

    /// Rebuild AVAudioPCMBuffer in `format` from rendered channels.
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
}
