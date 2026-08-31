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
    /// Song-derived synth patch (additive; nil on bundles cached before
    /// the server emitted it). Applied to the client synth engines at
    /// bundle activation so Jam pads take the song's synth color.
    public let synthPatch: BundleSynthPatch?
    /// Analyzed guitar tone summary (additive; nil when the song has no
    /// detected guitar). Full descriptor stays server-side.
    public let guitarTone: BundleGuitarTone?

    public init(
        bundleVersion: Int,
        analysisId: String,
        meta: BundleMeta,
        timeline: BundleTimeline,
        stems: [BundleStem],
        presets: [String: BundlePreset],
        synthPatch: BundleSynthPatch? = nil,
        guitarTone: BundleGuitarTone? = nil
    ) {
        self.bundleVersion = bundleVersion
        self.analysisId = analysisId
        self.meta = meta
        self.timeline = timeline
        self.stems = stems
        self.presets = presets
        self.synthPatch = synthPatch
        self.guitarTone = guitarTone
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
    // Attribution (D-024). Optionals so bundles cached before the
    // server emitted these keys still decode.
    public let license: String?
    public let licenseUrl: String?
    public let attribution: String?

    public init(
        title: String,
        artist: String,
        sourceUrl: String,
        durationSec: Double,
        tempoBpm: Double? = nil,
        detectedKey: String? = nil,
        license: String? = nil,
        licenseUrl: String? = nil,
        attribution: String? = nil
    ) {
        self.title = title
        self.artist = artist
        self.sourceUrl = sourceUrl
        self.durationSec = durationSec
        self.tempoBpm = tempoBpm
        self.detectedKey = detectedKey
        self.license = license
        self.licenseUrl = licenseUrl
        self.attribution = attribution
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

// MARK: - Tone transfer (additive, bundleVersion 2)

/// Song-derived synth patch: the backend's SynthDescriptor translated
/// into the two client synth engines (see backend synth_patch.py —
/// key names mirror the Swift param structs 1:1).
public struct BundleSynthPatch: Codable, Sendable, Equatable {
    public let wavetable: BundleWavetablePatch?
    public let pad: BundlePadPatch?
    public let source: String?

    public init(
        wavetable: BundleWavetablePatch? = nil,
        pad: BundlePadPatch? = nil,
        source: String? = nil
    ) {
        self.wavetable = wavetable
        self.pad = pad
        self.source = source
    }
}

/// Mirror of `WavetableSynthParams` minus masterGain (client gain
/// staging is loudness-calibrated and never song-derived).
public struct BundleWavetablePatch: Codable, Sendable, Equatable {
    public let attackSec: Double
    public let decaySec: Double
    public let sustainLevel: Double
    public let releaseSec: Double
    public let cutoffHz: Double
    public let resonance: Double
    public let detuneCents: Double

    public init(
        attackSec: Double, decaySec: Double, sustainLevel: Double,
        releaseSec: Double, cutoffHz: Double, resonance: Double,
        detuneCents: Double
    ) {
        self.attackSec = attackSec
        self.decaySec = decaySec
        self.sustainLevel = sustainLevel
        self.releaseSec = releaseSec
        self.cutoffHz = cutoffHz
        self.resonance = resonance
        self.detuneCents = detuneCents
    }

    /// The patch applied over an engine's current params: sound fields
    /// come from the song, masterGain is preserved from `base`.
    public func synthParams(over base: WavetableSynthParams) -> WavetableSynthParams {
        WavetableSynthParams(
            attackSec: attackSec,
            decaySec: decaySec,
            sustainLevel: sustainLevel,
            releaseSec: releaseSec,
            cutoffHz: cutoffHz,
            resonance: resonance,
            detuneCents: detuneCents,
            masterGain: base.masterGain
        )
    }
}

/// Mirror of the mobile `PadSynthParams` / launchpad.js slider schema
/// minus masterGain (fixed loudness-calibrated trim on the client).
public struct BundlePadPatch: Codable, Sendable, Equatable {
    public let brightness: Double
    public let strumMs: Double
    public let attackMs: Double
    public let releaseSec: Double
    public let sawMix: Double
    public let detuneCents: Double

    public init(
        brightness: Double, strumMs: Double, attackMs: Double,
        releaseSec: Double, sawMix: Double, detuneCents: Double
    ) {
        self.brightness = brightness
        self.strumMs = strumMs
        self.attackMs = attackMs
        self.releaseSec = releaseSec
        self.sawMix = sawMix
        self.detuneCents = detuneCents
    }
}

/// Guitar tone summary. The client only decodes the display-level
/// fields; the full ToneDescriptor/helix chain in the same JSON object
/// is ignored by Codable and stays a server-side concern.
public struct BundleGuitarTone: Codable, Sendable, Equatable {
    public let ampFamily: String?
    public let gain: Double?

    public init(ampFamily: String? = nil, gain: Double? = nil) {
        self.ampFamily = ampFamily
        self.gain = gain
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
    // --- Riley phrase-aware fields (additive; nil on legacy / non-phrase
    // chops). Populated when the chop comes from the Musical Graph: how the
    // pad is meant to be used, its playable-usefulness rank, whether it loops
    // cleanly, and the seam crossfade for gapless looping. ---
    public let contentType: String?
    public let performanceScore: Double?
    public let difficulty: Double?
    public let loopable: Bool?
    public let loopScore: Double?
    public let crossfadeMs: Double?
    public let patternId: String?

    public init(
        idx: Int,
        startSec: Double,
        endSec: Double,
        durationSec: Double,
        kind: String? = nil,
        root: Int? = nil,
        sectionLabel: String? = nil,
        chordSymbol: String? = nil,
        colorHint: String? = nil,
        contentType: String? = nil,
        performanceScore: Double? = nil,
        difficulty: Double? = nil,
        loopable: Bool? = nil,
        loopScore: Double? = nil,
        crossfadeMs: Double? = nil,
        patternId: String? = nil
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
        self.contentType = contentType
        self.performanceScore = performanceScore
        self.difficulty = difficulty
        self.loopable = loopable
        self.loopScore = loopScore
        self.crossfadeMs = crossfadeMs
        self.patternId = patternId
    }
}
