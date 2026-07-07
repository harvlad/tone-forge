// PadSampleMetadata.swift
//
// Sidecar metadata for locally-created pad samples (mic recordings,
// vocoder captures, baked transforms). Persisted by PadSampleStore as
// `Documents/samples/<uuid>.json` next to the `<uuid>.wav` payload.
//
// COMPLIANCE TRIPWIRE: `neverUpload` is ALWAYS true for `.mic` and
// `.vocoded` sources — locally-recorded and vocoder-captured audio
// never leaves the device. The memberwise init enforces it and the
// decoder re-enforces it, so no persisted payload (however edited)
// can flip a mic sample uploadable. ComplianceTests (P7) grep-gate
// the upload paths against `Documents/samples`.
//
// The wire shape is FROZEN as of P3 (`schemaVersion` 1) — see
// PadSampleMetadataCodableTests' frozen-JSON fixture. Additive
// evolution only: new fields decode with `decodeIfPresent` defaults,
// new SampleClass cases degrade to `.unknown` on old decoders.

import Foundation

/// What the classifier thinks a recording is. Drives default pad
/// behaviour (one-shot vs loop hints) and the badge in the grid UI.
/// String rawValues are the frozen JSON wire values.
public enum SampleClass: String, Codable, CaseIterable, Sendable {
    case vocalChop = "vocal_chop"
    case percussion
    case sustainedNote = "sustained_note"
    case texture
    case phrase
    case speechWord = "speech_word"
    case unknown

    /// Forward compatibility: unknown class strings from a newer app
    /// version degrade to `.unknown` rather than failing the decode.
    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = SampleClass(rawValue: raw) ?? .unknown
    }
}

public struct PadSampleMetadata: Codable, Equatable, Sendable {

    /// Where the audio came from. String rawValues are frozen wire
    /// values.
    public enum Source: String, Codable, Sendable {
        /// Recorded from the device microphone (P3). Never uploaded.
        case mic
        /// Captured through the vocoder/harmonizer (P5). Never uploaded.
        case vocoded
        /// Baked from a song-derived chop (P4 "bake" flow). The chop
        /// itself was already licensed content; still device-local.
        case songChop
    }

    /// Wire-format version. 1 = P3 shape.
    public var schemaVersion: Int
    /// Identity — also the basename of the .wav/.json pair on disk.
    public var id: UUID
    public var source: Source
    /// Classifier verdict at save time.
    public var classification: SampleClass
    /// Classifier confidence 0…1 for `classification`.
    public var confidence: Double
    /// User's long-press override; nil = trust the classifier.
    /// `effectiveClass` is the one callers should read.
    public var userClassOverride: SampleClass?
    public var createdAt: Date
    /// Trimmed length in seconds. Compliance cap: never exceeds
    /// `StemSlice.maxChopDurationSec` (8 s) — MicRecorder auto-stops
    /// and PadSampleStore rejects longer payloads.
    public var durationSec: Double
    public var sampleRate: Double
    public var channels: Int
    /// Grid tint. Mic = warm orange 0xFF8C3A, vocoded = purple
    /// 0x9B4DFF (set by the capture flows, not enforced here).
    public var colorHint: UInt32
    /// Vocoder mode 1–5 for `.vocoded` samples; nil otherwise.
    public var vocoderMode: Int?
    /// Backend song id when the carrier/source was song-derived.
    public var sourceSongId: String?
    /// Compliance: true ⇒ NO upload path may touch this sample.
    /// Always true for `.mic`/`.vocoded` (enforced in init + decode).
    public var neverUpload: Bool

    /// User override wins over the classifier.
    public var effectiveClass: SampleClass { userClassOverride ?? classification }

    public init(
        id: UUID = UUID(),
        source: Source,
        classification: SampleClass,
        confidence: Double,
        userClassOverride: SampleClass? = nil,
        createdAt: Date = Date(),
        durationSec: Double,
        sampleRate: Double,
        channels: Int,
        colorHint: UInt32,
        vocoderMode: Int? = nil,
        sourceSongId: String? = nil,
        neverUpload: Bool = true
    ) {
        self.schemaVersion = 1
        self.id = id
        self.source = source
        self.classification = classification
        self.confidence = confidence
        self.userClassOverride = userClassOverride
        self.createdAt = createdAt
        self.durationSec = durationSec
        self.sampleRate = sampleRate
        self.channels = channels
        self.colorHint = colorHint
        self.vocoderMode = vocoderMode
        self.sourceSongId = sourceSongId
        // Tripwire: mic/vocoded audio can NEVER be marked uploadable.
        self.neverUpload = Self.enforcedNeverUpload(neverUpload, source: source)
    }

    private static func enforcedNeverUpload(_ requested: Bool, source: Source) -> Bool {
        switch source {
        case .mic, .vocoded: return true
        case .songChop:      return requested
        }
    }

    // MARK: - Codable (frozen v1 wire shape)

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, id, source, classification, confidence
        case userClassOverride, createdAt, durationSec, sampleRate
        case channels, colorHint, vocoderMode, sourceSongId, neverUpload
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        self.id = try c.decode(UUID.self, forKey: .id)
        self.source = try c.decode(Source.self, forKey: .source)
        self.classification =
            try c.decodeIfPresent(SampleClass.self, forKey: .classification) ?? .unknown
        self.confidence = try c.decodeIfPresent(Double.self, forKey: .confidence) ?? 0
        self.userClassOverride =
            try c.decodeIfPresent(SampleClass.self, forKey: .userClassOverride)
        self.createdAt = try c.decode(Date.self, forKey: .createdAt)
        self.durationSec = try c.decode(Double.self, forKey: .durationSec)
        self.sampleRate = try c.decode(Double.self, forKey: .sampleRate)
        self.channels = try c.decodeIfPresent(Int.self, forKey: .channels) ?? 1
        self.colorHint = try c.decodeIfPresent(UInt32.self, forKey: .colorHint) ?? 0
        self.vocoderMode = try c.decodeIfPresent(Int.self, forKey: .vocoderMode)
        self.sourceSongId = try c.decodeIfPresent(String.self, forKey: .sourceSongId)
        // Tripwire survives hand-edited sidecars: decode, then enforce.
        let decodedNeverUpload = try c.decodeIfPresent(Bool.self, forKey: .neverUpload) ?? true
        self.neverUpload = Self.enforcedNeverUpload(decodedNeverUpload, source: source)
    }
}
