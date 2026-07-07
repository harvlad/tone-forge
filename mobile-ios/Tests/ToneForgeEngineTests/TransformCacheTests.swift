// TransformCacheTests.swift
//
// LRU cache contract: store/lookup round-trip, recency refresh (reads
// mark entries recently-used), totalBytes accounting, over-budget entries
// not cached, removeAll clears state. Hashing: chainHash stable across
// encoder invocations, differs for different chains; tempoBucket
// quarter-BPM rounding; contentHash differs when samples change.

import XCTest
@testable import ToneForgeEngine

final class TransformCacheTests: XCTestCase {

    private func makeChannels(
        count: Int, samples: Int
    ) -> [[Float]] {
        var chs: [[Float]] = []
        for i in 0..<count {
            chs.append([Float](repeating: Float(i), count: samples))
        }
        return chs
    }

    // MARK: - Store/lookup

    func testStoreAndLookupRoundTrip() {
        let cache = TransformCache(maxBytes: 1 << 20)
        let key = TransformCacheKey(
            contentHash: 123, chainHash: 456, tempoBucket: 480
        )
        let channels = makeChannels(count: 2, samples: 1000)

        cache.store(channels, for: key)
        let retrieved = cache.channels(for: key)

        XCTAssertNotNil(retrieved)
        XCTAssertEqual(retrieved, channels)
    }

    func testMissingKeyReturnsNil() {
        let cache = TransformCache(maxBytes: 1 << 20)
        let key = TransformCacheKey(
            contentHash: 999, chainHash: 888, tempoBucket: 500
        )
        XCTAssertNil(cache.channels(for: key))
    }

    // MARK: - Recency and eviction

    func testRecencyRefreshOnRead() {
        // Small cache that can hold ~2 entries of 1000 samples each.
        let cache = TransformCache(maxBytes: 10_000)
        let keyA = TransformCacheKey(
            contentHash: 1, chainHash: 1, tempoBucket: 480
        )
        let keyB = TransformCacheKey(
            contentHash: 2, chainHash: 2, tempoBucket: 480
        )
        let keyC = TransformCacheKey(
            contentHash: 3, chainHash: 3, tempoBucket: 480
        )

        let chA = makeChannels(count: 1, samples: 1000)
        let chB = makeChannels(count: 1, samples: 1000)
        let chC = makeChannels(count: 1, samples: 1000)

        cache.store(chA, for: keyA)
        cache.store(chB, for: keyB)

        // Read A to refresh its recency.
        _ = cache.channels(for: keyA)

        // Store C, which should evict B (the LRU), not A.
        cache.store(chC, for: keyC)

        XCTAssertNotNil(cache.channels(for: keyA), "A was refreshed")
        XCTAssertNil(cache.channels(for: keyB), "B should be evicted")
        XCTAssertNotNil(cache.channels(for: keyC))
    }

    func testEvictLRUWhenOverBudget() {
        let cache = TransformCache(maxBytes: 10_000)
        let keyOld = TransformCacheKey(
            contentHash: 1, chainHash: 1, tempoBucket: 480
        )
        let keyNew = TransformCacheKey(
            contentHash: 2, chainHash: 2, tempoBucket: 480
        )

        let chOld = makeChannels(count: 1, samples: 1000)
        let chNew = makeChannels(count: 1, samples: 2000)

        cache.store(chOld, for: keyOld)
        XCTAssertNotNil(cache.channels(for: keyOld))

        cache.store(chNew, for: keyNew)

        // chNew is larger; old should be evicted to make room.
        XCTAssertNil(
            cache.channels(for: keyOld),
            "LRU entry should be evicted"
        )
        XCTAssertNotNil(cache.channels(for: keyNew))
    }

    // MARK: - totalBytes accounting

    func testTotalBytesAccounting() {
        let cache = TransformCache(maxBytes: 100_000)
        XCTAssertEqual(cache.totalBytes, 0)

        let key1 = TransformCacheKey(
            contentHash: 10, chainHash: 10, tempoBucket: 480
        )
        let ch1 = makeChannels(count: 2, samples: 1000)
        cache.store(ch1, for: key1)

        // 2 channels × 1000 samples × 4 bytes/Float = 8000
        XCTAssertEqual(cache.totalBytes, 8000)

        let key2 = TransformCacheKey(
            contentHash: 20, chainHash: 20, tempoBucket: 480
        )
        let ch2 = makeChannels(count: 1, samples: 500)
        cache.store(ch2, for: key2)

        // +2000 = 10000 total
        XCTAssertEqual(cache.totalBytes, 10_000)

        cache.removeAll()
        XCTAssertEqual(cache.totalBytes, 0)
    }

    func testOverBudgetEntryNotCached() {
        let cache = TransformCache(maxBytes: 1000)
        let key = TransformCacheKey(
            contentHash: 99, chainHash: 99, tempoBucket: 480
        )
        // 10_000 samples × 4 bytes = 40_000 bytes > 1000
        let huge = makeChannels(count: 1, samples: 10_000)

        cache.store(huge, for: key)

        XCTAssertNil(
            cache.channels(for: key),
            "entry larger than maxBytes should not be cached"
        )
        XCTAssertEqual(cache.totalBytes, 0)
    }

    // MARK: - removeAll

    func testRemoveAllClearsCache() {
        let cache = TransformCache(maxBytes: 100_000)
        let key = TransformCacheKey(
            contentHash: 42, chainHash: 42, tempoBucket: 480
        )
        let ch = makeChannels(count: 1, samples: 1000)

        cache.store(ch, for: key)
        XCTAssertNotNil(cache.channels(for: key))
        XCTAssertGreaterThan(cache.totalBytes, 0)

        cache.removeAll()
        XCTAssertNil(cache.channels(for: key))
        XCTAssertEqual(cache.totalBytes, 0)
    }

    // MARK: - Hashing: chainHash

    func testChainHashStableAcrossInvocations() {
        let chain: [PadTransform] = [
            .reverse,
            .stutter(.r1_8),
            .octave(-1),
        ]
        let h1 = TransformHashing.chainHash(chain)
        let h2 = TransformHashing.chainHash(chain)
        XCTAssertEqual(h1, h2, "same chain → same hash")
    }

    func testChainHashDiffersForDifferentChains() {
        let c1: [PadTransform] = [.reverse, .stutter(.r1_8)]
        let c2: [PadTransform] = [.reverse, .stutter(.r1_16)]
        let h1 = TransformHashing.chainHash(c1)
        let h2 = TransformHashing.chainHash(c2)
        XCTAssertNotEqual(h1, h2, "different chains → different hash")
    }

    func testChainHashEmptyChain() {
        let empty: [PadTransform] = []
        let h = TransformHashing.chainHash(empty)
        XCTAssertNotEqual(h, 0, "empty chain should still hash")
    }

    // MARK: - Hashing: tempoBucket

    func testTempoBucketQuarterBPM() {
        XCTAssertEqual(TransformHashing.tempoBucket(120.0), 480)
        XCTAssertEqual(TransformHashing.tempoBucket(120.25), 481)
        XCTAssertEqual(TransformHashing.tempoBucket(120.5), 482)
        XCTAssertEqual(TransformHashing.tempoBucket(119.9), 480)
    }

    func testTempoBucketRounding() {
        // 120.1 × 4 = 480.4 → rounds to 480
        XCTAssertEqual(TransformHashing.tempoBucket(120.1), 480)
        // 120.2 × 4 = 480.8 → rounds to 481
        XCTAssertEqual(TransformHashing.tempoBucket(120.2), 481)
    }

    // MARK: - Hashing: contentHash

    func testContentHashDiffersWhenSampleChanges() {
        let ch1 = makeChannels(count: 1, samples: 100)
        var ch2 = makeChannels(count: 1, samples: 100)
        ch2[0][50] = 999.0  // tweak one sample

        let h1 = TransformHashing.contentHash(ch1)
        let h2 = TransformHashing.contentHash(ch2)
        XCTAssertNotEqual(h1, h2, "different samples → different hash")
    }

    func testContentHashDiffersForDifferentChannelCounts() {
        let ch1 = makeChannels(count: 1, samples: 100)
        let ch2 = makeChannels(count: 2, samples: 100)

        let h1 = TransformHashing.contentHash(ch1)
        let h2 = TransformHashing.contentHash(ch2)
        XCTAssertNotEqual(h1, h2, "channel count → different hash")
    }

    func testContentHashDiffersForDifferentLengths() {
        let ch1 = makeChannels(count: 1, samples: 100)
        let ch2 = makeChannels(count: 1, samples: 101)

        let h1 = TransformHashing.contentHash(ch1)
        let h2 = TransformHashing.contentHash(ch2)
        XCTAssertNotEqual(h1, h2, "length difference → different hash")
    }

    func testContentHashEmptyChannels() {
        let empty: [[Float]] = []
        let h = TransformHashing.contentHash(empty)
        XCTAssertNotEqual(h, 0, "empty channels should still hash")
    }
}
