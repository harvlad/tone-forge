// StudioModels.swift
//
// Wire models for the Studio results inspector (Phase 1), decoding
// GET /api/history/{id} — the entry metadata plus the legacy
// AnalysisResult blob under `result`. Shapes verified against a live
// jamn.app entry (2026-07); everything but `id` is optional because
// legacy entries omit keys freely and per-type analyses (guitar vs
// synth vs bass) carry different subtrees.
//
// The tone descriptor lives at result.descriptor OR nested under the
// detected type (result.guitar.descriptor etc.) — studio.html:2773
// resolves that fallback chain; here StudioModel does.

import Foundation

// MARK: - /api/history/{id} entry

public struct StudioHistoryDetail: Decodable, Sendable {
    public let id: String
    public let timestamp: String?
    public let name: String?
    public let filename: String?
    public let detectedType: String?
    public let summary: String?
    public let duration: Double?
    public let deepAnalysis: Bool?
    public let artist: String?
    public let license: String?
    public let attribution: String?
    public let result: StudioResult?

    enum CodingKeys: String, CodingKey {
        case id, timestamp, name, filename, summary, duration
        case detectedType = "detected_type"
        case deepAnalysis = "deep_analysis"
        case artist, license, attribution, result
    }

    public init(
        id: String, timestamp: String? = nil, name: String? = nil,
        filename: String? = nil, detectedType: String? = nil,
        summary: String? = nil, duration: Double? = nil,
        deepAnalysis: Bool? = nil, artist: String? = nil,
        license: String? = nil, attribution: String? = nil,
        result: StudioResult? = nil
    ) {
        self.id = id
        self.timestamp = timestamp
        self.name = name
        self.filename = filename
        self.detectedType = detectedType
        self.summary = summary
        self.duration = duration
        self.deepAnalysis = deepAnalysis
        self.artist = artist
        self.license = license
        self.attribution = attribution
        self.result = result
    }
}

// MARK: - result blob (legacy AnalysisResult projection)

public struct StudioResult: Decodable, Sendable {
    public let filename: String?
    public let durationSec: Double?
    public let sampleRate: Int?
    public let tempoBpm: Double?
    public let detectedKey: String?
    public let tuningOffsetCents: Double?
    public let detectedType: String?
    public let descriptor: ToneDescriptor?
    /// Type-nested wrappers; each may carry its own descriptor.
    public let guitar: InstrumentWrapper?
    public let bass: InstrumentWrapper?
    public let synth: InstrumentWrapper?
    public let drums: InstrumentWrapper?
    public let stemsPaths: [String: String]?
    public let midiStems: [String: MidiStemInfo]?
    public let profiling: StudioProfiling?
    public let totalTimeSec: Double?
    public let processingTime: Double?

    enum CodingKeys: String, CodingKey {
        case filename, descriptor, guitar, bass, synth, drums, profiling
        case durationSec = "duration_sec"
        case sampleRate = "sample_rate"
        case tempoBpm = "tempo_bpm"
        case detectedKey = "detected_key"
        case tuningOffsetCents = "tuning_offset_cents"
        case detectedType = "detected_type"
        case stemsPaths = "stems_paths"
        case midiStems = "midi_stems"
        case totalTimeSec = "total_time_sec"
        case processingTime = "processing_time"
    }

    public init(
        filename: String? = nil, durationSec: Double? = nil,
        sampleRate: Int? = nil, tempoBpm: Double? = nil,
        detectedKey: String? = nil, tuningOffsetCents: Double? = nil,
        detectedType: String? = nil, descriptor: ToneDescriptor? = nil,
        guitar: InstrumentWrapper? = nil, bass: InstrumentWrapper? = nil,
        synth: InstrumentWrapper? = nil, drums: InstrumentWrapper? = nil,
        stemsPaths: [String: String]? = nil,
        midiStems: [String: MidiStemInfo]? = nil,
        profiling: StudioProfiling? = nil, totalTimeSec: Double? = nil,
        processingTime: Double? = nil
    ) {
        self.filename = filename
        self.durationSec = durationSec
        self.sampleRate = sampleRate
        self.tempoBpm = tempoBpm
        self.detectedKey = detectedKey
        self.tuningOffsetCents = tuningOffsetCents
        self.detectedType = detectedType
        self.descriptor = descriptor
        self.guitar = guitar
        self.bass = bass
        self.synth = synth
        self.drums = drums
        self.stemsPaths = stemsPaths
        self.midiStems = midiStems
        self.profiling = profiling
        self.totalTimeSec = totalTimeSec
        self.processingTime = processingTime
    }
}

public struct InstrumentWrapper: Decodable, Sendable {
    public let descriptor: ToneDescriptor?

    public init(descriptor: ToneDescriptor? = nil) {
        self.descriptor = descriptor
    }
}

// MARK: - tone descriptor

public struct ToneDescriptor: Decodable, Sendable {
    public let amp: AmpDescriptor?
    public let cab: CabDescriptor?
    public let effects: EffectsDescriptor?
    public let guitar: GuitarDescriptor?
    public let confidence: ToneConfidence?
    public let version: String?

    public init(
        amp: AmpDescriptor? = nil, cab: CabDescriptor? = nil,
        effects: EffectsDescriptor? = nil, guitar: GuitarDescriptor? = nil,
        confidence: ToneConfidence? = nil, version: String? = nil
    ) {
        self.amp = amp
        self.cab = cab
        self.effects = effects
        self.guitar = guitar
        self.confidence = confidence
        self.version = version
    }
}

public struct AmpDescriptor: Decodable, Sendable {
    public let family: String?
    /// 0–1; the web renders knobs as value × 10, one decimal.
    public let gain: Double?
    public let voicing: AmpVoicing?
    public let alternates: [AmpAlternate]?

    public init(
        family: String? = nil, gain: Double? = nil,
        voicing: AmpVoicing? = nil, alternates: [AmpAlternate]? = nil
    ) {
        self.family = family
        self.gain = gain
        self.voicing = voicing
        self.alternates = alternates
    }
}

public struct AmpVoicing: Decodable, Sendable {
    public let bass: Double?
    public let mid: Double?
    public let treble: Double?
    public let presence: Double?
    public let midScoop: Double?

    enum CodingKeys: String, CodingKey {
        case bass, mid, treble, presence
        case midScoop = "mid_scoop"
    }

    public init(
        bass: Double? = nil, mid: Double? = nil, treble: Double? = nil,
        presence: Double? = nil, midScoop: Double? = nil
    ) {
        self.bass = bass
        self.mid = mid
        self.treble = treble
        self.presence = presence
        self.midScoop = midScoop
    }
}

public struct AmpAlternate: Decodable, Sendable {
    public let family: String?
    public let score: Double?

    public init(family: String? = nil, score: Double? = nil) {
        self.family = family
        self.score = score
    }
}

public struct CabDescriptor: Decodable, Sendable {
    public let configuration: String?
    public let speakerCharacter: String?
    public let micPosition: String?

    enum CodingKeys: String, CodingKey {
        case configuration
        case speakerCharacter = "speaker_character"
        case micPosition = "mic_position"
    }

    public init(
        configuration: String? = nil, speakerCharacter: String? = nil,
        micPosition: String? = nil
    ) {
        self.configuration = configuration
        self.speakerCharacter = speakerCharacter
        self.micPosition = micPosition
    }
}

/// Wire key is `overdrive_pedal` (the web's `.overdrive` read is a
/// dead path — verified against a live entry).
public struct EffectsDescriptor: Decodable, Sendable {
    public let overdrivePedal: OverdriveEffect?
    public let compressor: CompressorEffect?
    public let modulation: ModulationEffect?
    public let delay: DelayEffect?
    public let reverb: ReverbEffect?

    enum CodingKeys: String, CodingKey {
        case compressor, modulation, delay, reverb
        case overdrivePedal = "overdrive_pedal"
    }

    public init(
        overdrivePedal: OverdriveEffect? = nil,
        compressor: CompressorEffect? = nil,
        modulation: ModulationEffect? = nil, delay: DelayEffect? = nil,
        reverb: ReverbEffect? = nil
    ) {
        self.overdrivePedal = overdrivePedal
        self.compressor = compressor
        self.modulation = modulation
        self.delay = delay
        self.reverb = reverb
    }
}

public struct OverdriveEffect: Decodable, Sendable {
    public let style: String?
    public let drive: Double?

    public init(style: String? = nil, drive: Double? = nil) {
        self.style = style
        self.drive = drive
    }
}

public struct CompressorEffect: Decodable, Sendable {
    public let amount: Double?
    public let character: String?

    public init(amount: Double? = nil, character: String? = nil) {
        self.amount = amount
        self.character = character
    }
}

public struct ModulationEffect: Decodable, Sendable {
    public let type: String?
    public let rate: Double?
    public let depth: Double?

    public init(type: String? = nil, rate: Double? = nil, depth: Double? = nil) {
        self.type = type
        self.rate = rate
        self.depth = depth
    }
}

public struct DelayEffect: Decodable, Sendable {
    public let type: String?
    public let timeMs: Double?
    public let feedback: Double?
    public let mix: Double?

    enum CodingKeys: String, CodingKey {
        case type, feedback, mix
        case timeMs = "time_ms"
    }

    public init(
        type: String? = nil, timeMs: Double? = nil,
        feedback: Double? = nil, mix: Double? = nil
    ) {
        self.type = type
        self.timeMs = timeMs
        self.feedback = feedback
        self.mix = mix
    }
}

public struct ReverbEffect: Decodable, Sendable {
    public let type: String?
    public let size: Double?
    public let mix: Double?

    public init(type: String? = nil, size: Double? = nil, mix: Double? = nil) {
        self.type = type
        self.size = size
        self.mix = mix
    }
}

public struct GuitarDescriptor: Decodable, Sendable {
    public let pickupBrightness: Double?
    public let playingStyle: String?
    public let estimatedTuning: String?

    enum CodingKeys: String, CodingKey {
        case pickupBrightness = "pickup_brightness"
        case playingStyle = "playing_style"
        case estimatedTuning = "estimated_tuning"
    }

    public init(
        pickupBrightness: Double? = nil, playingStyle: String? = nil,
        estimatedTuning: String? = nil
    ) {
        self.pickupBrightness = pickupBrightness
        self.playingStyle = playingStyle
        self.estimatedTuning = estimatedTuning
    }
}

public struct ToneConfidence: Decodable, Sendable {
    public let ampFamily: Double?
    public let gain: Double?
    public let cab: Double?
    public let effects: Double?

    enum CodingKeys: String, CodingKey {
        case gain, cab, effects
        case ampFamily = "amp_family"
    }

    public init(
        ampFamily: Double? = nil, gain: Double? = nil,
        cab: Double? = nil, effects: Double? = nil
    ) {
        self.ampFamily = ampFamily
        self.gain = gain
        self.cab = cab
        self.effects = effects
    }
}

// MARK: - MIDI stems

/// Per-stem MIDI extraction stats. `content` (base64 .mid) and the
/// raw note list are deliberately not decoded — Phase 1 shows stats
/// only.
public struct MidiStemInfo: Decodable, Sendable {
    public let filename: String?
    public let noteCount: Int?
    public let durationSeconds: Double?
    public let extractionTempoBpm: Double?
    public let method: String?
    public let notesPerSecond: Double?

    enum CodingKeys: String, CodingKey {
        case filename, method
        case noteCount = "note_count"
        case durationSeconds = "duration_seconds"
        case extractionTempoBpm = "extraction_tempo_bpm"
        case notesPerSecond = "notes_per_second"
    }

    public init(
        filename: String? = nil, noteCount: Int? = nil,
        durationSeconds: Double? = nil, extractionTempoBpm: Double? = nil,
        method: String? = nil, notesPerSecond: Double? = nil
    ) {
        self.filename = filename
        self.noteCount = noteCount
        self.durationSeconds = durationSeconds
        self.extractionTempoBpm = extractionTempoBpm
        self.method = method
        self.notesPerSecond = notesPerSecond
    }
}

// MARK: - profiling

/// Pipeline timing. Stage keys are dynamic ("midi_extraction.bass",
/// "stem_separation", …) so stages decode as a dictionary.
public struct StudioProfiling: Decodable, Sendable {
    public let totalMs: Double?
    public let audioDurationSec: Double?
    public let processingRatio: Double?
    public let stages: [String: ProfilingStage]?

    enum CodingKeys: String, CodingKey {
        case stages
        case totalMs = "total_ms"
        case audioDurationSec = "audio_duration_sec"
        case processingRatio = "processing_ratio"
    }

    public init(
        totalMs: Double? = nil, audioDurationSec: Double? = nil,
        processingRatio: Double? = nil,
        stages: [String: ProfilingStage]? = nil
    ) {
        self.totalMs = totalMs
        self.audioDurationSec = audioDurationSec
        self.processingRatio = processingRatio
        self.stages = stages
    }
}

public struct ProfilingStage: Decodable, Sendable {
    public let startedMs: Double?
    public let finishedMs: Double?
    public let durationMs: Double?
    public let gpuUsed: Bool?
    public let error: String?
    public let skipped: Bool?

    enum CodingKeys: String, CodingKey {
        case error, skipped
        case startedMs = "started_ms"
        case finishedMs = "finished_ms"
        case durationMs = "duration_ms"
        case gpuUsed = "gpu_used"
    }

    public init(
        startedMs: Double? = nil, finishedMs: Double? = nil,
        durationMs: Double? = nil, gpuUsed: Bool? = nil,
        error: String? = nil, skipped: Bool? = nil
    ) {
        self.startedMs = startedMs
        self.finishedMs = finishedMs
        self.durationMs = durationMs
        self.gpuUsed = gpuUsed
        self.error = error
        self.skipped = skipped
    }
}
