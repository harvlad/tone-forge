// SongBundle.swift
//
// Codable models mirroring `/api/song/{id}/bundle` from tone_forge_api.
// This is the single shared boundary between backend and mobile client;
// every downstream engine + UI decision keys off these types.
//
// Wire shape (bundleVersion = 1):
//
//   {
//     "bundleVersion": 1,
//     "analysisId": "…",
//     "meta": { title, artist, sourceUrl, durationSec, tempoBpm, detectedKey },
//     "timeline": {
//       "chords":    [{start, end, symbol}],
//       "sections":  [{start, end, label?}],
//       "beats":     [Double],
//       "downbeats": [Double]
//     },
//     "stems": [{role, url, codec, sampleRateHz}],
//     "presets": {
//       "harmonic": {stem, sliceMode, chops: [Chop]},
//       "sections": {stem, sliceMode, chops: [Chop]}
//     }
//   }
//
// If the wire shape changes, bump `bundleVersion` on the backend AND
// add a version-branching decode path here. Do not silently mutate
// these types — the mobile client persists bundles to disk between
// launches and needs to be able to read old ones.

import Foundation

// MARK: - Root

public struct SongBundle: Codable, Sendable, Equatable {
    public let bundleVersion: Int
    public let analysisId: String
    public let meta: BundleMeta
    public let timeline: BundleTimeline
    public let stems: [BundleStem]
    public let presets: [String: BundlePreset]

    public init(
        bundleVersion: Int,
        analysisId: String,
        meta: BundleMeta,
        timeline: BundleTimeline,
        stems: [BundleStem],
        presets: [String: BundlePreset]
    ) {
        self.bundleVersion = bundleVersion
        self.analysisId = analysisId
        self.meta = meta
        self.timeline = timeline
        self.stems = stems
        self.presets = presets
    }
}

// MARK: - Meta

public struct BundleMeta: Codable, Sendable, Equatable {
    public let title: String
    public let artist: String
    public let sourceUrl: String
    public let durationSec: Double
    public let tempoBpm: Double?
    public let detectedKey: String?

    public init(
        title: String,
        artist: String,
        sourceUrl: String,
        durationSec: Double,
        tempoBpm: Double? = nil,
        detectedKey: String? = nil
    ) {
        self.title = title
        self.artist = artist
        self.sourceUrl = sourceUrl
        self.durationSec = durationSec
        self.tempoBpm = tempoBpm
        self.detectedKey = detectedKey
    }
}

// MARK: - Timeline

public struct BundleTimeline: Codable, Sendable, Equatable {
    public let chords: [ChordEvent]
    public let sections: [SectionEvent]
    public let beats: [Double]
    public let downbeats: [Double]

    public init(
        chords: [ChordEvent] = [],
        sections: [SectionEvent] = [],
        beats: [Double] = [],
        downbeats: [Double] = []
    ) {
        self.chords = chords
        self.sections = sections
        self.beats = beats
        self.downbeats = downbeats
    }
}

public struct ChordEvent: Codable, Sendable, Equatable {
    public let start: Double
    public let end: Double
    public let symbol: String

    public init(start: Double, end: Double, symbol: String) {
        self.start = start
        self.end = end
        self.symbol = symbol
    }
}

public struct SectionEvent: Codable, Sendable, Equatable {
    public let start: Double
    public let end: Double
    public let label: String?

    public init(start: Double, end: Double, label: String?) {
        self.start = start
        self.end = end
        self.label = label
    }
}

// MARK: - Stems

public struct BundleStem: Codable, Sendable, Equatable {
    public let role: String
    public let url: String?
    public let codec: String
    public let sampleRateHz: Int

    public init(role: String, url: String?, codec: String, sampleRateHz: Int) {
        self.role = role
        self.url = url
        self.codec = codec
        self.sampleRateHz = sampleRateHz
    }
}

// MARK: - Presets

public struct BundlePreset: Codable, Sendable, Equatable {
    public let stem: String
    public let sliceMode: String
    public let chops: [Chop]

    public init(stem: String, sliceMode: String, chops: [Chop]) {
        self.stem = stem
        self.sliceMode = sliceMode
        self.chops = chops
    }
}

/// A chop is a slice of one stem, playable by tapping a pad. The
/// engine schedules `AVAudioPlayerNode.scheduleSegment(...)` reading
/// [startSec, endSec] of the stem file for the pad this chop maps to.
///
/// Fields that may be null depend on `kind` — see
/// backend/tone_forge/contribute_chops.py for the source of truth.
public struct Chop: Codable, Sendable, Equatable {
    public let idx: Int
    public let startSec: Double
    public let endSec: Double
    public let durationSec: Double
    public let kind: String?
    /// Pitch-class root (0..11) when `sliceMode == "chord"`; nil for
    /// section-sliced chops. Backend wire type is JSON number (see
    /// backend/tone_forge/contribute_chops.py).
    public let root: Int?
    public let sectionLabel: String?
    public let chordSymbol: String?
    public let colorHint: String?

    public init(
        idx: Int,
        startSec: Double,
        endSec: Double,
        durationSec: Double,
        kind: String? = nil,
        root: Int? = nil,
        sectionLabel: String? = nil,
        chordSymbol: String? = nil,
        colorHint: String? = nil
    ) {
        self.idx = idx
        self.startSec = startSec
        self.endSec = endSec
        self.durationSec = durationSec
        self.kind = kind
        self.root = root
        self.sectionLabel = sectionLabel
        self.chordSymbol = chordSymbol
        self.colorHint = colorHint
    }
}
