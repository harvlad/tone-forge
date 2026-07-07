// TransformCache.swift
//
// LRU cache for transformed audio channels. Keys: content hash (FNV-1a
// over the raw Float bits), chain hash (FNV-1a over sorted-keys JSON),
// and tempo bucket (quarter-BPM rounding so tempo nudges hit the same
// entry). Not thread-safe; callers are @MainActor. Eviction is
// least-recently-USED (reads refresh recency). Entries larger than
// maxBytes are not cached at all.

import Foundation

// MARK: - TransformCacheKey

public struct TransformCacheKey: Hashable, Sendable {
    public var contentHash: UInt64
    public var chainHash: UInt64
    public var tempoBucket: Int

    public init(
        contentHash: UInt64, chainHash: UInt64, tempoBucket: Int
    ) {
        self.contentHash = contentHash
        self.chainHash = chainHash
        self.tempoBucket = tempoBucket
    }
}

// MARK: - TransformHashing

public enum TransformHashing {

    /// FNV-1a 64-bit hash over the raw Float bit patterns, with channel
    /// count and per-channel lengths mixed in.
    public static func contentHash(_ channels: [[Float]]) -> UInt64 {
        var h: UInt64 = 0xcbf2_9ce4_8422_2325
        let prime: UInt64 = 0x0000_0100_0000_01b3

        // Mix in channel count
        h ^= UInt64(channels.count)
        h = h &* prime

        for ch in channels {
            // Mix in this channel's length
            h ^= UInt64(ch.count)
            h = h &* prime

            // Mix in every Float's raw bit pattern
            for sample in ch {
                let bits = sample.bitPattern
                h ^= UInt64(bits)
                h = h &* prime
            }
        }
        return h
    }

    /// FNV-1a 64-bit hash over the sorted-keys JSON encoding of the
    /// transform chain (deterministic across runs).
    public static func chainHash(_ chain: [PadTransform]) -> UInt64 {
        let enc = JSONEncoder()
        enc.outputFormatting = .sortedKeys
        guard let data = try? enc.encode(chain) else { return 0 }

        var h: UInt64 = 0xcbf2_9ce4_8422_2325
        let prime: UInt64 = 0x0000_0100_0000_01b3
        for byte in data {
            h ^= UInt64(byte)
            h = h &* prime
        }
        return h
    }

    /// Quarter-BPM bucket: Int((bpm × 4).rounded()).
    public static func tempoBucket(_ bpm: Double) -> Int {
        return Int((bpm * 4).rounded())
    }
}

// MARK: - TransformCache

/// LRU cache for transformed channels. Not thread-safe; callers must
/// synchronise (typically @MainActor). Evicts the least-recently-USED
/// entry when storage exceeds maxBytes; reads refresh recency.
public final class TransformCache {

    public private(set) var totalBytes: Int = 0

    private let maxBytes: Int
    private var storage: [TransformCacheKey: Entry] = [:]
    private var useCounter: UInt64 = 0

    private struct Entry {
        var channels: [[Float]]
        var lastUse: UInt64
    }

    public init(maxBytes: Int = 64 << 20) {
        self.maxBytes = maxBytes
    }

    // MARK: - Public API

    /// Lookup cached channels for `key`, marking them recently-used.
    public func channels(for key: TransformCacheKey) -> [[Float]]? {
        guard var entry = storage[key] else { return nil }
        useCounter &+= 1
        entry.lastUse = useCounter
        storage[key] = entry
        return entry.channels
    }

    /// Store `channels` for `key`, evicting LRU entries until under
    /// maxBytes. If this entry alone exceeds maxBytes it is not cached.
    public func store(
        _ channels: [[Float]], for key: TransformCacheKey
    ) {
        let size = sizeOf(channels)
        guard size <= maxBytes else { return }

        // Evict LRU until there's room (if key already exists its old
        // footprint is still in totalBytes; after eviction we'll
        // replace it and the delta is small).
        while totalBytes + size > maxBytes && !storage.isEmpty {
            evictLRU()
        }

        // Remove old entry if present
        if let old = storage[key] {
            totalBytes -= sizeOf(old.channels)
        }

        // Insert new entry
        useCounter &+= 1
        storage[key] = Entry(channels: channels, lastUse: useCounter)
        totalBytes += size
    }

    /// Clear all cached entries and reset totalBytes to 0.
    public func removeAll() {
        storage.removeAll()
        totalBytes = 0
    }

    // MARK: - Internal

    private func sizeOf(_ channels: [[Float]]) -> Int {
        var bytes = 0
        for ch in channels {
            bytes += ch.count * MemoryLayout<Float>.size
        }
        return bytes
    }

    private func evictLRU() {
        guard let victim = storage.min(by: {
            $0.value.lastUse < $1.value.lastUse
        }) else { return }
        totalBytes -= sizeOf(victim.value.channels)
        storage.removeValue(forKey: victim.key)
    }
}
