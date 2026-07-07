// LayerTimeline.swift
//
// Persisted event-stream schema for a user's recorded contribution
// layer. The mobile app records the STREAM OF EVENTS (padKey ↦
// songTimeOn/Off + params), not the resulting audio, because:
//
//   1. Non-destructive: pack change / mix change / section-gate change
//      all just re-render the same events differently.
//   2. Tiny on disk: a 4-minute performance is ~few kB of JSON.
//   3. Trivial backend sync: it's just JSON.
//   4. Faithful replay: LayerPlayer uses the exact same
//      SampleScheduler path as live triggers, so the recording sounds
//      identical to the performance.
//
// Offline audio-render export (m4a) is deferred to P6 via
// AVAudioEngine.enableManualRenderingMode over this same event stream.
//
// LOCK-IN: this schema is one of the plan's Critical Files. Layers are
// written to disk under
// ~/Library/Application Support/toneforge/layers/{analysisId}/{layerId}.json
// and any change must bump `timelineVersion` and add a
// version-branching decode path — old recordings must remain playable.

import Foundation

// MARK: - Root

/// A recorded contribution layer. Independent of the source song's
/// audio; keyed to the song by `analysisId` so playback can locate the
/// right bundle + stems on replay.
public struct LayerTimeline: Codable, Sendable, Equatable {
    /// Wire version. Bump on any schema-breaking change.
    public let timelineVersion: Int
    /// Stable id for this layer. UUID-string by convention.
    public let layerId: String
    /// The song this layer was performed against. Matches
    /// `SongBundle.analysisId`.
    public let analysisId: String
    /// User-visible name (defaults to timestamp; renameable in
    /// Profile → Layers).
    public var name: String
    /// Seconds since epoch when the recording completed. Used for
    /// sort order in the Layers list.
    public let createdAtEpoch: Double
    /// Total duration in song-time seconds. May be shorter than the
    /// source song if the user stopped recording early.
    public let durationSec: Double
    /// The events, sorted ascending by `songTimeOn`. Sampler + engine
    /// events are interleaved in one stream so the replay iterator is
    /// simple.
    public let events: [LayerEvent]
    /// Which pack was active at record time. Used at replay to
    /// pre-activate the pack (so pad triggers find their samples).
    /// nil = no sample pack was in use during the recording (pure
    /// Instrument-mode layer).
    public let activePackId: String?

    // Sketch metadata (all optional + additive, so v1 song layers and
    // pre-metadata sketch layers decode unchanged — synthesized
    // Codable uses decodeIfPresent for optionals). Populated only for
    // song-less takes recorded against the synthetic tempo grid.

    /// Sketch tempo at record time (BPM). nil for song layers.
    public let sketchTempoBpm: Double?
    /// Sketch time-signature numerator (3, 4 or 6). nil for song layers.
    public let sketchTimeSigNumerator: Int?
    /// Display name of the pack at record time — packIds like
    /// `song-derived:xyz` aren't human-readable in the Profile list.
    public let packName: String?

    public init(
        timelineVersion: Int = 1,
        layerId: String,
        analysisId: String,
        name: String,
        createdAtEpoch: Double,
        durationSec: Double,
        events: [LayerEvent],
        activePackId: String?,
        sketchTempoBpm: Double? = nil,
        sketchTimeSigNumerator: Int? = nil,
        packName: String? = nil
    ) {
        self.timelineVersion = timelineVersion
        self.layerId = layerId
        self.analysisId = analysisId
        self.name = name
        self.createdAtEpoch = createdAtEpoch
        self.durationSec = durationSec
        self.events = events
        self.activePackId = activePackId
        self.sketchTempoBpm = sketchTempoBpm
        self.sketchTimeSigNumerator = sketchTimeSigNumerator
        self.packName = packName
    }
}

// MARK: - Events

/// One recorded event in a layer. Discriminated by `kind`.
///
/// The union of kinds is:
///   - sampleOn/sampleOff — a SamplePad hit (Contribute → Samples).
///   - noteOn/noteOff     — an OpenJamGrid MIDI note (Contribute →
///                          Instrument). MIDI is captured rather than
///                          pad-index so re-transposition survives.
///
/// Params are packed into `params` (a lightweight typed struct) to
/// avoid a bag of Optional at the top level.
public struct LayerEvent: Codable, Sendable, Equatable {
    public enum Kind: String, Codable, Sendable, Equatable {
        case sampleOn
        case sampleOff
        case noteOn
        case noteOff
    }

    /// Discriminator.
    public let kind: Kind
    /// Song-time at which the event was captured. Sorted ascending
    /// within `LayerTimeline.events`.
    public let songTimeSec: Double
    /// Per-kind parameters. Optional fields are populated per the
    /// table below:
    ///
    ///   sampleOn:  padIdx, velocity(?), packIdOverride(?)
    ///   sampleOff: padIdx
    ///   noteOn:    midiNote, velocity(?)
    ///   noteOff:   midiNote
    public let params: Params

    public init(kind: Kind, songTimeSec: Double, params: Params) {
        self.kind = kind
        self.songTimeSec = songTimeSec
        self.params = params
    }

    public struct Params: Codable, Sendable, Equatable {
        /// Sample pad grid index. Populated for sampleOn/sampleOff.
        public let padIdx: Int?
        /// MIDI note. Populated for noteOn/noteOff.
        public let midiNote: Int?
        /// 0..1 velocity. Populated when the input device reported one.
        public let velocity: Double?
        /// Overrides the layer's `activePackId` for this event only —
        /// used when the pack was live-swapped during recording so
        /// each event names the pack it was played on. nil = use the
        /// active pack at replay time.
        public let packIdOverride: String?

        public init(
            padIdx: Int? = nil,
            midiNote: Int? = nil,
            velocity: Double? = nil,
            packIdOverride: String? = nil
        ) {
            self.padIdx = padIdx
            self.midiNote = midiNote
            self.velocity = velocity
            self.packIdOverride = packIdOverride
        }
    }
}

// MARK: - Convenience

extension LayerTimeline {
    /// Empty timeline for `analysisId`, seeded with a fresh UUID and
    /// current wall-clock. Used by `LayerRecorder.arm()`.
    public static func empty(analysisId: String, activePackId: String?) -> LayerTimeline {
        LayerTimeline(
            layerId: UUID().uuidString,
            analysisId: analysisId,
            name: DateFormatter.layerDefaultName.string(from: Date()),
            createdAtEpoch: Date().timeIntervalSince1970,
            durationSec: 0,
            events: [],
            activePackId: activePackId
        )
    }
}

extension DateFormatter {
    /// "Layer 2026-07-06 14:23" style default name.
    fileprivate static let layerDefaultName: DateFormatter = {
        let df = DateFormatter()
        df.dateFormat = "'Layer' yyyy-MM-dd HH:mm"
        return df
    }()
}
