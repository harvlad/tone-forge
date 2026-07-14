// SampleScheduler.swift
//
// Ties every user-visible sample control (pad tap, quantize menu,
// hold/toggle, section gate, layer record) to the audio subsystem.
// This is where song-time — the transport-clock domain the whole app
// thinks in — is translated into AVAudioTime, the domain the audio
// graph schedules against.
//
// Trigger flow (Contribute → Samples pad tap):
//
//   PadGrid → scheduler.trigger(padIdx:)
//     ├─ SectionResolver.isAllowed?  no → drop silently, emit .gated
//     ├─ Quantizer.nextQuantized → targetSongSeconds
//     ├─ target - transport.now → delaySeconds
//     ├─ hostTime = mach_absolute_time() + delaySeconds
//     ├─ pool.trigger(SampleTrigger, buffer, at: AVAudioTime(hostTime:))
//     └─ (optional) recorder.append(LayerEvent(.sampleOn, songTime: target))
//
// Hold vs toggle:
//   .hold   → touch-down triggers; touch-up releases.
//   .toggle → first touch-down starts (looped if pad has loop point);
//             second touch-down releases. Touch-ups are ignored.
//
// Buffer preload:
//   On `setActivePack`, every file-backed pad is loaded into an
//   AVAudioPCMBuffer eagerly. Song-derived pads read their stem slice
//   into a buffer at the same time. This is the ~50 MB budget the
//   plan calls out — 16 pads × ~1 s @ 44 kHz stereo Float32 ≈ 5 MB
//   for the StarterPack, headroom for larger song-derived packs.
//
// This scheduler is one of the plan's Critical Files — the song-time
// → AVAudioTime conversion + section gate + hold/toggle bookkeeping
// live here in a single place so schema/timing risk is contained.

import Foundation
import ToneForgeEngine
#if canImport(AVFoundation)
// @preconcurrency: AVFAudio types (AVAudioPCMBuffer) predate Sendable;
// the converter input block below captures a buffer that is only read
// synchronously — safe, but the SDK annotation can't express that.
@preconcurrency import AVFoundation
#endif

@MainActor
public final class SampleScheduler: ObservableObject {

    /// Result of a `trigger(padIdx:)` call. Consumed by the UI layer
    /// so it can flash the pad, show a "gated" hint, or paint the pad
    /// as latched (toggle-on).
    public enum TriggerResult: Equatable {
        /// Scheduled to fire at the given song time. Includes hold-
        /// mode taps (targetSongTime ≈ now) and quantized taps
        /// (targetSongTime > now).
        case scheduled(atSongTime: Double)
        /// Toggle-mode second tap. The pad's previous voice was
        /// released with the release fade.
        case toggledOff
        /// Section gate rejected the trigger. Silent.
        case gated
        /// Pack has no pad at that index, or no buffer preloaded.
        case padNotFound
    }

    /// Live user settings mirrored from SampleSettingsStore.
    @Published public var quantize: QuantizeMode = .off
    @Published public var holdMode: HoldMode = .hold
    @Published public var beatBarMode: BeatBarMode = .beat
    /// Section-label whitelist. `nil` = allow all; empty set = allow none.
    @Published public var allowedSections: Set<String>? = nil

    // MARK: - Private

    private weak var engine: AudioEngine?
    private let pool: SampleVoicePool
    private let bus: SampleBus

    /// One loaded pack's metadata + preloaded buffers. Buffers are
    /// keyed by the bare padIdx *within* the pack; cross-pack identity
    /// is the `loadedPacks` dictionary key (packId).
    private struct LoadedPack {
        let pack: ResolvedSamplePack
        #if canImport(AVFoundation)
        var buffers: [Int: AVAudioPCMBuffer] = [:]
        #endif
    }

    /// Every pack the user has visited this session, keyed by packId.
    /// Multiple packs stay resident simultaneously so voices from a
    /// previous carousel page keep ringing while the user plays the
    /// next one. A pack loads once (first visit) and is only dropped
    /// via `unloadPack`/`unloadAllPacks`.
    ///
    /// MEMORY: no LRU eviction — Starter/curated packs are ~5 MB
    /// each and song-derived pads are capped at 8 s slices; AppState
    /// unloads stale `song-derived:` packs on bundle change. Add LRU
    /// here if device measurement ever shows pressure.
    private var loadedPacks: [String: LoadedPack] = [:]

    /// Synthetic packId for locally-recorded samples (P3 mic
    /// pipeline). Never appears in `loadedPacks` — local pads live in
    /// `localBuffers`, a parallel path consulted BEFORE pack lookup —
    /// but it keys the voice pool + effects resolver so local voices
    /// are addressable like any other pad.
    // nonisolated: immutable Sendable constant, read from off-main
    // contexts (LayerOfflineRenderer's render path).
    public nonisolated static let localPackId = "local"

    #if canImport(AVFoundation)
    /// Locally-recorded samples assigned to grid pads, keyed by grid
    /// padIdx (row*10+col). A local assignment shadows the active
    /// pack's pad at the same index. ModeCoordinator populates this
    /// from PadAssignmentStore + PadSampleStore. Buffers are stored
    /// pre-converted to the canonical format (D-017 single-resample
    /// rule — conversion happens once in `setLocalBuffer`).
    private var localBuffers: [Int: (meta: PadSampleMetadata, buffer: AVAudioPCMBuffer)] = [:]
    #endif

    /// The pack currently fronted by the UI (visible carousel page).
    /// Triggers without an explicit packId resolve against this.
    public private(set) var activePackId: String?

    /// Bundle context for quantize + gate. Updated when a bundle loads.
    private var beats: [Double] = []
    private var downbeats: [Double] = []
    private var sections: [SectionEvent] = []
    private var tempoBpm: Double? = nil

    /// Callback fired for every trigger + release. AppState wires this
    /// into LayerRecorder in Phase 4. Left as a plain closure so the
    /// scheduler doesn't depend on the recorder.
    public var onEvent: ((LayerEvent) -> Void)?

    /// Injected by AppState to resolve the effective per-pad effect
    /// params for a given (packId, padIdx) — user override > manifest
    /// baseline > `.neutral`. Left as a closure so the scheduler
    /// doesn't depend on SampleSettingsStore. When nil (unwired
    /// tests, boot), effects fall back to the pad's manifest value or
    /// `.neutral`.
    public var effectsResolver: ((_ packId: String, _ padIdx: Int, _ manifest: SamplePadEffects?) -> SamplePadEffects)?

    /// Debug tripwire for the contribution-engine invariant: every
    /// live trigger must arrive via ContributionEventBus →
    /// ModeCoordinator (the executor sets this to return true only
    /// while it is executing a routed AudioAction). nil (tests,
    /// unwired boot) = allowed. `triggerRaw` is exempt — legacy
    /// LayerPlayer replay is the one documented bypass (D-015).
    public var contributionGuard: (() -> Bool)?

    /// P4 seam: per-trigger transform resolution. Given the pad's
    /// preloaded buffer + (packId, padIdx), returns the buffer to
    /// actually play (identity when the pad has no transform chain).
    /// nil = no transform engine wired (P1–P3).
    #if canImport(AVFoundation)
    public var transformResolver: ((AVAudioPCMBuffer, String, Int) -> AVAudioPCMBuffer)?
    #endif

    /// P4 seam: per-pad `.loop` transform flag. Given (packId, padIdx)
    /// returns whether the pad's transform chain contains `.loop` —
    /// looping pads keep ringing after touch-up (hold mode releases
    /// them like pack pads with a loop point). nil = no transform
    /// engine wired.
    public var loopResolver: ((String, Int) -> Bool)?

    public init(engine: AudioEngine, bus: SampleBus, pool: SampleVoicePool) {
        self.engine = engine
        self.bus = bus
        self.pool = pool
    }

    // MARK: - Bundle context

    /// Refresh quantize + gate context from the loaded song. Call
    /// after every successful `AppState.loadBundle`.
    public func updateBundle(timeline: BundleTimeline, meta: BundleMeta) {
        self.beats = timeline.beats
        self.downbeats = timeline.downbeats
        self.sections = timeline.sections
        self.tempoBpm = meta.tempoBpm
    }

    /// Sketch-mode context: no analysed beats/downbeats/sections, so
    /// `Quantizer.nextQuantized` falls through to its synthetic grid
    /// at `tempoBpm`. Pass nil to clear entirely (no bundle, no
    /// tempo — quantize degrades to .off). AppState calls this when
    /// the Sketch tab activates and restores the bundle context via
    /// `updateBundle` when it deactivates.
    public func updateSyntheticContext(tempoBpm: Double?) {
        self.beats = []
        self.downbeats = []
        self.sections = []
        self.tempoBpm = tempoBpm
    }

    // MARK: - Pack activation

    /// Preload every pad's audio buffer for `pack` into the resident
    /// registry, without touching the active pack or any ringing
    /// voices. No-op if the pack is already loaded (revisiting a
    /// carousel page is free). For file-backed pads, `padFileURLs`
    /// supplies the local file; for song-derived pads, `stemFiles`
    /// maps stem role → local file URL (from `BundleStore.cachedStem`).
    /// Silently skips pads whose file is missing so a partially-cached
    /// pack still triggers what it can.
    public func preloadPack(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) throws {
        let packId = pack.pack.packId
        guard loadedPacks[packId] == nil else { return }

        #if canImport(AVFoundation)
        let loaded = Self.decodePackBuffers(
            pack, stemFiles: stemFiles, target: engine?.canonicalFormat
        )
        loadedPacks[packId] = LoadedPack(pack: pack, buffers: loaded)
        #else
        loadedPacks[packId] = LoadedPack(pack: pack)
        #endif
    }

    /// Async twin of `preloadPack`: the WAV decode + resample (up to 16
    /// pads at AVAudioQuality.max, ~1–3 s cold) runs on a detached task
    /// so the main thread never stalls when a pack is first visited.
    /// This is the path the UI takes (tab switch / carousel / pinned
    /// pads); the sync `preloadPack` stays for the offline export + tests
    /// where blocking is harmless. No-op if already resident, and
    /// re-checks after the await in case a concurrent call won the race.
    public func preloadPackAsync(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) async {
        let packId = pack.pack.packId
        guard loadedPacks[packId] == nil else { return }

        #if canImport(AVFoundation)
        let target = engine?.canonicalFormat
        let loaded = await Task.detached(priority: .userInitiated) {
            Self.decodePackBuffers(pack, stemFiles: stemFiles, target: target)
        }.value
        guard loadedPacks[packId] == nil else { return }
        loadedPacks[packId] = LoadedPack(pack: pack, buffers: loaded)
        #else
        loadedPacks[packId] = LoadedPack(pack: pack)
        #endif
    }

    #if canImport(AVFoundation)
    /// Decode + canonical-format-convert every pad buffer for `pack`.
    /// Pure and `nonisolated` so it can run off the main actor (see
    /// `preloadPackAsync`).
    ///
    /// Every voice slot is connected at the canonical 48 kHz stereo
    /// format (see SampleVoicePool.attach). AVAudioPlayerNode requires
    /// the scheduled buffer's format to match that connection format
    /// exactly — otherwise scheduleBuffer throws NSException and SIGABRTs
    /// the app. Sample files ship in a mix of sample rates / channel
    /// counts (StarterPack is mono 44.1 kHz), so we convert everything
    /// here via AVAudioConverter — the SINGLE resample point of the
    /// contribution path (D-017); everything downstream runs at 48 k
    /// SRC-free. File-backed pads read from `padFileURLs`; song-derived
    /// pads slice the shared stem in `stemFiles`. Missing files are
    /// skipped so a partially-cached pack still triggers what it can.
    nonisolated static func decodePackBuffers(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL],
        target: AVAudioFormat?
    ) -> [Int: AVAudioPCMBuffer] {
        var loaded: [Int: AVAudioPCMBuffer] = [:]
        for pad in pack.pack.pads {
            if let url = pack.padFileURLs[pad.padIdx] {
                if let buf = loadBuffer(from: url, slice: nil, target: target) {
                    loaded[pad.padIdx] = buf
                }
            } else if let slice = pad.stemSlice,
                      let stemURL = stemFiles[slice.stemRole] {
                if let buf = loadBuffer(from: stemURL, slice: slice, target: target) {
                    loaded[pad.padIdx] = buf
                }
            }
        }
        return loaded
    }
    #endif

    /// Make `pack` the UI-fronted pack, loading its buffers if this
    /// is the first visit. Deliberately does NOT stop ringing voices —
    /// swiping the carousel layers packs instead of cutting them off
    /// (the voice pool keys slots by composite SamplePadKey, so old
    /// voices stay addressable for toggle-off/release).
    public func setActivePack(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) throws {
        try preloadPack(pack, stemFiles: stemFiles)
        activePackId = pack.pack.packId
    }

    /// Async twin of `setActivePack`: fronts the pack's id immediately
    /// (so the grid + carousel update this run loop) then decodes its
    /// buffers off-main via `preloadPackAsync`. First-visit taps in the
    /// brief decode window degrade to padNotFound silence rather than
    /// freezing the UI (the freeze that surfaced the iOS touch-and-hold
    /// loupe on the Contribute tab).
    public func setActivePackAsync(
        _ pack: ResolvedSamplePack,
        stemFiles: [String: URL]
    ) async {
        activePackId = pack.pack.packId
        await preloadPackAsync(pack, stemFiles: stemFiles)
    }

    /// Whether `packId`'s buffers are resident.
    public func isPackLoaded(packId: String) -> Bool {
        loadedPacks[packId] != nil
    }

    #if canImport(AVFoundation)
    /// The pad's untransformed canonical-format buffer (pack preload
    /// or local sample). P4 transform rendering + bake read the base
    /// here so chains always apply to the original audio, never to a
    /// previously transformed result.
    public func baseBuffer(packId: String, padIdx: Int) -> AVAudioPCMBuffer? {
        if packId == Self.localPackId {
            return localBuffers[padIdx]?.buffer
        }
        return loadedPacks[packId]?.buffers[padIdx]
    }

    /// Playback length (seconds) of a pad's one-shot buffer, for UI
    /// auto-reset after a preview. Returns nil for looping pads (no
    /// natural end — they stop only on manual release) or when the pad
    /// isn't loaded.
    public func oneShotDurationSec(packId: String, padIdx: Int) -> Double? {
        guard let entry = loadedPacks[packId],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx }),
              let buffer = entry.buffers[padIdx]
        else { return nil }
        if pad.loopPointSec != nil || (loopResolver?(packId, padIdx) ?? false) {
            return nil
        }
        let sr = buffer.format.sampleRate
        guard sr > 0 else { return nil }
        return Double(buffer.frameLength) / sr
    }

    /// Downsampled peak envelope + duration for the waveform trimmer.
    /// Reads the same (transform-resolved) buffer `previewTrimmed`
    /// plays, so the drawn waveform matches what auditions. Peaks are
    /// normalized to 0–1 (ChopWaveformView's contract). nil when the
    /// pad's buffer isn't resident.
    public func padWaveform(
        packId: String, padIdx: Int, binCount: Int = 100
    ) -> (peaks: [Float], durationSec: Double)? {
        guard binCount > 0,
              let base = baseBuffer(packId: packId, padIdx: padIdx)
        else { return nil }
        let buffer = transformResolver?(base, packId, padIdx) ?? base
        let frames = Int(buffer.frameLength)
        let sr = buffer.format.sampleRate
        guard frames > 0, sr > 0,
              let channels = buffer.floatChannelData
        else { return nil }

        let channelCount = Int(buffer.format.channelCount)
        let framesPerBin = max(1, frames / binCount)
        var peaks = [Float](repeating: 0, count: binCount)
        for bin in 0..<binCount {
            let start = bin * framesPerBin
            guard start < frames else { break }
            let end = min(frames, start + framesPerBin)
            var peak: Float = 0
            for ch in 0..<channelCount {
                let data = channels[ch]
                for i in start..<end where abs(data[i]) > peak {
                    peak = abs(data[i])
                }
            }
            peaks[bin] = peak
        }
        if let maxPeak = peaks.max(), maxPeak > 0 {
            for i in peaks.indices { peaks[i] /= maxPeak }
        }
        return (peaks, Double(frames) / sr)
    }
    #endif

    /// Release any ringing voices belonging to `packId` and drop its
    /// buffers. Called by AppState for stale song-derived packs when
    /// a different bundle loads.
    public func unloadPack(packId: String) {
        guard let entry = loadedPacks.removeValue(forKey: packId) else { return }
        for pad in entry.pack.pack.pads {
            let key = SamplePadKey(packId: packId, padIdx: pad.padIdx)
            if pool.isActive(padKey: key) {
                pool.release(padKey: key)
            }
        }
        if activePackId == packId {
            activePackId = nil
        }
    }

    /// Drop every loaded pack + free all preloaded buffers. Called on
    /// song unload / tab teardown.
    public func unloadAllPacks() {
        pool.stopAll()
        loadedPacks.removeAll()
        activePackId = nil
    }

    // MARK: - Local samples (P3 mic pipeline)

    #if canImport(AVFoundation)
    /// Assign a locally-recorded sample to a grid pad. Converts to
    /// the canonical connection format here, once — the voice pool
    /// requires an exact format match and must never SRC (D-017).
    /// Replaces any previous local assignment at that index.
    public func setLocalBuffer(
        _ buffer: AVAudioPCMBuffer, meta: PadSampleMetadata, for padIdx: Int
    ) {
        var resolved = buffer
        if let target = engine?.canonicalFormat, !buffer.format.isEqual(target) {
            guard let converted = Self.convert(buffer, to: target) else { return }
            resolved = converted
        }
        // Mic samples arrive already normalized to -1 dBFS by
        // RecordingProcessor; this is a ~no-op for them but gives
        // vocoded/chopped sources the same pack-parity loudness.
        Self.normalizePeak(resolved)
        localBuffers[padIdx] = (meta, resolved)
    }

    /// Remove the local assignment at `padIdx`, releasing its voice
    /// if it is still ringing. The underlying pad (if the active pack
    /// has one at this index) becomes triggerable again.
    public func clearLocalBuffer(for padIdx: Int) {
        guard localBuffers.removeValue(forKey: padIdx) != nil else { return }
        let key = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
        if pool.isActive(padKey: key) {
            pool.release(padKey: key)
        }
    }

    /// Drop every local assignment (mode switch, delete-all).
    public func clearAllLocalBuffers() {
        for padIdx in localBuffers.keys {
            let key = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
            if pool.isActive(padKey: key) {
                pool.release(padKey: key)
            }
        }
        localBuffers.removeAll()
    }

    /// Metadata for the local sample at `padIdx`, if any. The grid
    /// layout uses this for pad visuals (colorHint, class badge).
    public func localMetadata(for padIdx: Int) -> PadSampleMetadata? {
        localBuffers[padIdx]?.meta
    }
    #endif

    // MARK: - Triggering

    /// Handle a pad touch-down. Returns what happened so the UI can
    /// paint / haptic accordingly.
    ///
    /// `packId` names the pack the pad belongs to; the UI passes the
    /// carousel page's own packId explicitly so a mid-press swipe
    /// (which changes the active pack) can't misroute the gesture.
    /// nil falls back to the active pack.
    @discardableResult
    public func trigger(padIdx: Int, packId: String? = nil) -> TriggerResult {
        assert(
            contributionGuard?() ?? true,
            "SampleScheduler.trigger must be reached via ContributionEventBus → ModeCoordinator"
        )
        #if canImport(AVFoundation)
        // Local samples shadow pack pads at the same index: consulted
        // BEFORE pack lookup. One-shot unless the transform chain
        // contains `.loop` (loopResolver).
        if packId == nil || packId == Self.localPackId,
           let local = localBuffers[padIdx] {
            return triggerLocal(padIdx: padIdx, local: local)
        }
        #endif
        guard let pid = packId ?? activePackId,
              let entry = loadedPacks[pid],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx })
        else { return .padNotFound }

        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)

        // Toggle mode: if already playing, second tap stops.
        if holdMode == .toggle, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(padIdx: padIdx, packIdOverride: pid)
            ))
            return .toggledOff
        }

        let nowSong = nowSongSeconds()

        // Section gate.
        if !SectionResolver.isAllowed(t: nowSong, in: sections, allowed: allowedSections) {
            return .gated
        }

        // Quantize the target song-time — but only when the transport
        // is actually running. If the song isn't playing (auditioning
        // pads, no bundle loaded, paused), snapping to "the next beat"
        // uses stale beats/tempo from a previously-loaded song and
        // pushes the trigger seconds into the future, where a
        // subsequent tap will steal the same slot and cancel it. That
        // manifested as StarterPack pads (all of which have a
        // manifest-level `defaultQuantize`) being silent while
        // shoegaze-textures pads (no defaultQuantize) played fine.
        let transportRunning = (engine?.clock.state == .playing)
        // Drum-machine convention: one-shot pads (loopPointSec == nil)
        // always fire immediately, ignoring quantize. Kicks, snares,
        // stabs — anything percussive — feels wrong if there's a
        // 100–250 ms snap wait between finger-down and audio. Only
        // sustained/looping pads (loopPointSec != nil) honour the
        // pad's defaultQuantize so a held pad drops in cleanly on the
        // next beat/bar. This mirrors MPC / Push / Maschine behavior.
        let isOneShot = (pad.loopPointSec == nil)
        let effectiveQuantize: QuantizeMode = (transportRunning && !isOneShot)
            ? (pad.defaultQuantize ?? quantize)
            : .off
        let targetSong = Quantizer.nextQuantized(
            songSeconds: nowSong,
            mode: effectiveQuantize,
            beats: beats,
            downbeats: downbeats,
            sections: sections,
            tempoBpm: tempoBpm
        )

        #if canImport(AVFoundation)
        guard let baseBuffer = entry.buffers[padIdx] else { return .padNotFound }
        let buffer = transformResolver?(baseBuffer, pid, padIdx) ?? baseBuffer

        // Hold mode retrigger: stop any existing voice for this pad before
        // firing a new one. This gives "self-choke" behavior — rapid taps
        // restart the sample instead of stacking voices.
        if holdMode == .hold, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
        }

        let loop: Bool = (holdMode == .toggle) && (pad.loopPointSec != nil || effectiveQuantize == .off)
        // hold-mode always one-shot; toggle-mode loops if the pad has
        // a loop point or if quantize is off (short loops).
        let effects = effectsResolver?(pid, padIdx, pad.effects)
            ?? pad.effects
            ?? .neutral
        let req = SampleTrigger(
            padKey: padKey,
            loop: loop || pad.loopPointSec != nil
                || (loopResolver?(pid, padIdx) ?? false),
            chokeGroup: pad.chokeGroup,
            gainDb: pad.gainDb,
            effects: effects
        )
        let audioTime = audioTime(forSongSeconds: targetSong, nowSong: nowSong)
        pool.trigger(req, buffer: buffer, at: audioTime)
        #endif

        onEvent?(LayerEvent(
            kind: .sampleOn,
            songTimeSec: targetSong,
            params: LayerEvent.Params(
                padIdx: padIdx,
                velocity: 1.0,
                packIdOverride: pid
            )
        ))
        return .scheduled(atSongTime: targetSong)
    }

    #if canImport(AVFoundation)
    /// Local-sample trigger. Local pads fire immediately, ignoring
    /// quantize (drum-machine convention); they loop only when the
    /// transform chain contains `.loop` (loopResolver). The only
    /// shared bookkeeping with the pack path is toggle-off and the
    /// section gate.
    private func triggerLocal(
        padIdx: Int, local: (meta: PadSampleMetadata, buffer: AVAudioPCMBuffer)
    ) -> TriggerResult {
        let padKey = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)

        if holdMode == .toggle, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(
                    padIdx: padIdx, packIdOverride: Self.localPackId
                )
            ))
            return .toggledOff
        }

        let nowSong = nowSongSeconds()
        if !SectionResolver.isAllowed(t: nowSong, in: sections, allowed: allowedSections) {
            return .gated
        }

        let buffer = transformResolver?(local.buffer, Self.localPackId, padIdx)
            ?? local.buffer

        // Hold mode retrigger: stop any existing voice for this pad before
        // firing a new one (self-choke).
        if holdMode == .hold, pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
        }

        let effects = effectsResolver?(Self.localPackId, padIdx, nil) ?? .neutral
        let req = SampleTrigger(
            padKey: padKey,
            loop: loopResolver?(Self.localPackId, padIdx) ?? false,
            chokeGroup: nil,
            gainDb: 0,
            effects: effects
        )
        pool.trigger(req, buffer: buffer, at: nil)

        onEvent?(LayerEvent(
            kind: .sampleOn,
            songTimeSec: nowSong,
            params: LayerEvent.Params(
                padIdx: padIdx,
                velocity: 1.0,
                packIdOverride: Self.localPackId
            )
        ))
        return .scheduled(atSongTime: nowSong)
    }
    #endif

    /// Replay-path trigger: fire the pad's buffer immediately with no
    /// quantize, no section gate, and no `onEvent` emission. Used by
    /// LayerPlayer — the saved timeline already contains the exact
    /// song-times each hit was intended to fire at, so the scheduler
    /// must not re-interpret them. Feeding a replay through the live
    /// `trigger` path caused every event to be snapped to the next
    /// `defaultQuantize` boundary (StarterPack pads have 1/4 or 1/8)
    /// and then to lose its slot to the following event's snap; net
    /// result was a silent replay.
    ///
    /// Also suppresses the `onEvent` callback so a layer being
    /// replayed while another is being recorded doesn't double-record
    /// the same hits into the new take.
    @discardableResult
    public func triggerRaw(
        padIdx: Int, packId: String? = nil, pan: Float = 0
    ) -> TriggerResult {
        #if canImport(AVFoundation)
        // Replayed local pads: best-effort — fires only while the
        // local sample is still assigned at this index.
        if packId == nil || packId == Self.localPackId,
           let local = localBuffers[padIdx] {
            let padKey = SamplePadKey(packId: Self.localPackId, padIdx: padIdx)
            let buffer = transformResolver?(local.buffer, Self.localPackId, padIdx)
                ?? local.buffer
            let effects = effectsResolver?(Self.localPackId, padIdx, nil) ?? .neutral
            let req = SampleTrigger(
                padKey: padKey,
                loop: loopResolver?(Self.localPackId, padIdx) ?? false,
                chokeGroup: nil,
                gainDb: 0,
                pan: pan,
                effects: effects
            )
            pool.trigger(req, buffer: buffer, at: nil)
            return .scheduled(atSongTime: nowSongSeconds())
        }
        #endif
        guard let pid = packId ?? activePackId,
              let entry = loadedPacks[pid],
              let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx })
        else { return .padNotFound }

        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)

        #if canImport(AVFoundation)
        guard let baseBuffer = entry.buffers[padIdx] else { return .padNotFound }
        let buffer = transformResolver?(baseBuffer, pid, padIdx) ?? baseBuffer
        let effects = effectsResolver?(pid, padIdx, pad.effects)
            ?? pad.effects
            ?? .neutral
        let req = SampleTrigger(
            padKey: padKey,
            loop: pad.loopPointSec != nil
                || (loopResolver?(pid, padIdx) ?? false),
            chokeGroup: pad.chokeGroup,
            gainDb: pad.gainDb,
            pan: pan,
            effects: effects
        )
        pool.trigger(req, buffer: buffer, at: nil)
        #endif

        return .scheduled(atSongTime: nowSongSeconds())
    }

    /// Handle a pad touch-up. In `.hold` this stops the pad with the
    /// 20 ms release fade; in `.toggle` it's a no-op (the second tap
    /// stops instead).
    ///
    /// One-shot pads (no `loopPointSec`) intentionally ignore the
    /// release: percussive hits and stabs should play to completion
    /// once triggered, mirroring drum-machine convention ("you can't
    /// un-hit a drum"). The prior behavior interacted badly with
    /// per-pad `defaultQuantize`: a normal ~150 ms tap on a starter
    /// pad with 1/4 quantize (up to 500 ms wait at 120 BPM) would
    /// fire the release fade + player.stop() before the deferred
    /// play() dispatched, leaving the pad silent. Only looping pads
    /// need touch-up-to-release semantics.
    public func release(padIdx: Int, packId: String? = nil) {
        #if canImport(AVFoundation)
        // Local pads are one-shots (play to completion, no touch-up
        // release) UNLESS their transform chain contains `.loop` —
        // looping local pads follow the same hold-to-sustain
        // semantics as pack pads with a loop point. Either way,
        // return here so the fallthrough doesn't release the *pack*
        // pad shadowed at the same index.
        if packId == nil || packId == Self.localPackId,
           localBuffers[padIdx] != nil {
            let localKey = SamplePadKey(
                packId: Self.localPackId, padIdx: padIdx
            )
            if holdMode == .hold,
               loopResolver?(Self.localPackId, padIdx) ?? false,
               pool.isActive(padKey: localKey) {
                pool.release(padKey: localKey)
                onEvent?(LayerEvent(
                    kind: .sampleOff,
                    songTimeSec: nowSongSeconds(),
                    params: LayerEvent.Params(
                        padIdx: padIdx,
                        packIdOverride: Self.localPackId
                    )
                ))
            }
            return
        }
        #endif
        guard holdMode == .hold,
              let pid = packId ?? activePackId,
              let entry = loadedPacks[pid]
        else { return }
        let pad = entry.pack.pack.pads.first(where: { $0.padIdx == padIdx })
        guard pad?.loopPointSec != nil
            || (loopResolver?(pid, padIdx) ?? false)
        else { return }
        let padKey = SamplePadKey(packId: pid, padIdx: padIdx)
        if pool.isActive(padKey: padKey) {
            pool.release(padKey: padKey)
            onEvent?(LayerEvent(
                kind: .sampleOff,
                songTimeSec: nowSongSeconds(),
                params: LayerEvent.Params(padIdx: padIdx, packIdOverride: pid)
            ))
        }
    }

    /// Unconditionally stop every active voice for a pad. Unlike
    /// `release`, this ignores hold-mode + loop gating — used when a pad
    /// is deleted/hidden so a looping (or toggle-latched) voice can't
    /// keep ringing after the pad is gone from the grid.
    public func stopVoices(padIdx: Int, packId: String) {
        #if canImport(AVFoundation)
        let key = SamplePadKey(packId: packId, padIdx: padIdx)
        if pool.isActive(padKey: key) {
            pool.release(padKey: key)
        }
        #endif
    }

    // MARK: - Preview with trim bounds

    /// Preview a pad sample with trim bounds. Used by the waveform trimmer
    /// to audition the selected region. Bypasses quantize and contribution
    /// guard — this is a UI preview, not a contribution event.
    #if canImport(AVFoundation)
    public func previewTrimmed(
        padIdx: Int,
        packId: String,
        startFraction: Double,
        endFraction: Double
    ) {
        guard let entry = loadedPacks[packId],
              let baseBuffer = entry.buffers[padIdx]
        else { return }

        let buffer = transformResolver?(baseBuffer, packId, padIdx) ?? baseBuffer
        let pad = entry.pack.pack.pads.first { $0.padIdx == padIdx }
        let effects = effectsResolver?(packId, padIdx, pad?.effects)
            ?? pad?.effects ?? .neutral

        let req = SampleTrigger(
            padKey: SamplePadKey(packId: packId, padIdx: padIdx),
            loop: false,
            chokeGroup: nil,
            gainDb: pad?.gainDb ?? 0,
            effects: effects
        )
        pool.triggerSegment(
            req,
            buffer: buffer,
            startFraction: startFraction,
            endFraction: endFraction
        )
    }
    #endif

    // MARK: - One-shot file playback (sequencer delegate)

    #if canImport(AVFoundation)
    /// Canonical-format buffers for sequencer one-shots, keyed by a
    /// caller-supplied identity (chop ref, sample id, URL). Sliced
    /// stems and whole local samples are loaded + converted once here
    /// and reused across every step that fires them.
    private var oneShotCache: [String: AVAudioPCMBuffer] = [:]

    /// Play a slice of an audio file as a gated one-shot voice. This is
    /// the audio path behind `SequencerPlayerDelegate` — bundleChop /
    /// localSample / customURL sequencer tracks don't route through the
    /// pad bus, so they land here instead. Loads + caches the
    /// canonical-format buffer on first use (slicing `startSec..endSec`
    /// when given), then fires immediately through the voice pool.
    ///
    /// - Parameters:
    ///   - url: source audio file (stem or sample).
    ///   - startSec/endSec: slice window; pass nil/nil to play the whole
    ///     file. loadBuffer clamps `endSec` to the file length.
    ///   - gainDb: base voice gain.
    ///   - velocity: 0–1 step velocity, folded into gain (0 dB at 1.0).
    ///   - pan: stereo pan (-1…+1) applied to the voice.
    ///   - cacheKey: stable identity so repeated triggers reuse the buffer.
    public func triggerFileOneShot(
        url: URL,
        startSec: Double?,
        endSec: Double?,
        gainDb: Double = 0,
        velocity: Float = 1.0,
        pan: Float = 0,
        cacheKey: String
    ) {
        let buffer: AVAudioPCMBuffer
        if let cached = oneShotCache[cacheKey] {
            buffer = cached
        } else {
            let slice: StemSlice? = (startSec != nil || endSec != nil)
                ? StemSlice(
                    stemRole: "",
                    startSec: startSec ?? 0,
                    endSec: endSec ?? .greatestFiniteMagnitude
                )
                : nil
            guard let loaded = Self.loadBuffer(
                from: url, slice: slice, target: engine?.canonicalFormat
            ) else { return }
            oneShotCache[cacheKey] = loaded
            buffer = loaded
        }

        // Velocity → gain trim (0 dB at full velocity, floored at -60).
        let clampedVel = max(0, min(1, velocity))
        let velDb = clampedVel > 0.001 ? 20.0 * log10(Double(clampedVel)) : -60.0
        let padKey = SamplePadKey(
            packId: "__seq__", padIdx: abs(cacheKey.hashValue % 1_000_000)
        )
        let req = SampleTrigger(
            padKey: padKey,
            loop: false,
            chokeGroup: nil,
            gainDb: gainDb + velDb,
            pan: pan,
            effects: .neutral
        )
        pool.trigger(req, buffer: buffer, at: nil)
    }
    #endif

    // MARK: - Private helpers

    private func nowSongSeconds() -> Double {
        engine?.clock.nowSongSeconds ?? 0
    }

    #if canImport(AVFoundation)
    /// Song-time → AVAudioTime translation. Uses mach hostTime so the
    /// player node can honour the schedule even when the engine's
    /// output clock hasn't started (before first render) — the
    /// alternative (`lastRenderTime.sampleTime`) is nil at that point.
    ///
    /// Returns nil when the delay is negligible (< 1 ms), which cues
    /// the pool to schedule immediately with `at: nil`.
    ///
    /// Delay is scaled by the transport rate (D-022 practice speed):
    /// at 0.5x a 1-beat song-time delta spans twice the wall-clock.
    private func audioTime(forSongSeconds target: Double, nowSong: Double) -> AVAudioTime? {
        let rate = engine?.clock.rate ?? 1.0
        guard let delayTicks = TransportTimeMath.hostDelayTicks(
            targetSong: target, nowSong: nowSong,
            rate: rate, ticksPerSecond: TransportClock.ticksPerSecond()
        ) else { return nil }
        let hostTime = mach_absolute_time() &+ delayTicks
        return AVAudioTime(hostTime: hostTime)
    }

    /// Target peak level for normalized sample buffers, in linear
    /// amplitude. -3 dBFS = 0.708 keeps a safety margin below full
    /// scale so per-voice EQ + delay AU processing can add a couple
    /// of dB of resonance without clipping.
    ///
    /// A flat pre-amp doesn't work here because the StarterPack files
    /// vary by ~13 dB (shimmer pad at -29.8 dBFS vs snare at -17.3
    /// dBFS); one gain either leaves the pads inaudible or clips the
    /// snare. Normalizing each buffer to a fixed peak on load gives
    /// every pad a consistent, healthy listening level regardless of
    /// how loud the source file was rendered.
    ///
    /// Silence-guard: buffers with no signal (peak < 1e-6) are left
    /// untouched — dividing to reach the target would multiply noise
    /// floor by ~700 000×, producing an audible burst on tap.
    ///
    /// Bumped from 0.708 (-3 dBFS) to 0.891 (-1 dBFS). Peak
    /// normalization is a poor proxy for perceived loudness — the
    /// sub-heavy StarterPack pads (Sub Kick, Bass Hit, Kick, Drone)
    /// sit at the same peak as the brighter shoegaze / lo-fi packs
    /// but sound quieter due to Fletcher-Munson. +2 dB gives the
    /// StarterPack more punch while leaving 1 dB inter-sample
    /// headroom before the mixer chain.
    private nonisolated static let normalizeTargetPeak: Float = 0.891  // -1 dBFS

    /// Normalize `buf` in-place so its peak sample sits at
    /// `normalizeTargetPeak`. Skipped for effectively-silent buffers.
    /// Handles planar-Float32 layout — the default for the canonical
    /// `standardFormat` the contribution graph speaks (D-017).
    private nonisolated static func normalizePeak(_ buf: AVAudioPCMBuffer) {
        guard let channels = buf.floatChannelData else { return }
        let frameCount = Int(buf.frameLength)
        let channelCount = Int(buf.format.channelCount)
        guard frameCount > 0, channelCount > 0 else { return }

        // Scan for peak absolute amplitude across all channels.
        var peak: Float = 0
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                let v = abs(ptr[i])
                if v > peak { peak = v }
            }
        }
        guard peak > 1e-6 else { return }
        let gain = normalizeTargetPeak / peak
        // Skip if the file was already near target — avoids wasting
        // cycles multiplying every sample by ~1.0.
        guard abs(gain - 1.0) > 0.01 else { return }
        for c in 0..<channelCount {
            let ptr = channels[c]
            for i in 0..<frameCount {
                ptr[i] *= gain
            }
        }
    }

    /// Load an audio file into an in-memory PCM buffer, optionally
    /// restricted to `slice` (start/end seconds), optionally converted
    /// to `target` so it matches the voice-pool's connection format
    /// (see SampleVoicePool.attach). Returns nil on any I/O or format
    /// error — the caller handles missing buffers as a silent-pad case
    /// rather than a hard failure.
    private nonisolated static func loadBuffer(
        from url: URL,
        slice: StemSlice?,
        target: AVAudioFormat?
    ) -> AVAudioPCMBuffer? {
        do {
            let file = try AVAudioFile(forReading: url)
            let format = file.processingFormat
            let sampleRate = format.sampleRate

            let startFrame: AVAudioFramePosition
            let frameCount: AVAudioFrameCount
            if let slice = slice {
                startFrame = AVAudioFramePosition(max(0, slice.startSec) * sampleRate)
                let endFrame = AVAudioFramePosition(max(slice.startSec, slice.endSec) * sampleRate)
                let requested = max(0, endFrame - startFrame)
                let clipped = min(requested, max(0, file.length - startFrame))
                frameCount = AVAudioFrameCount(clipped)
            } else {
                startFrame = 0
                frameCount = AVAudioFrameCount(file.length)
            }
            guard frameCount > 0,
                  let srcBuf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)
            else { return nil }

            file.framePosition = startFrame
            try file.read(into: srcBuf, frameCount: frameCount)

            // No target ⇒ tests / unwired boot — hand back the raw
            // buffer. On-device we always have a target and any file
            // whose format differs is converted below.
            guard let target = target, !format.isEqual(target) else {
                normalizePeak(srcBuf)
                return srcBuf
            }
            guard let dstBuf = convert(srcBuf, to: target) else { return nil }
            normalizePeak(dstBuf)
            return dstBuf
        } catch {
            return nil
        }
    }

    /// One-shot whole-buffer format conversion (rate + channel
    /// layout). Best SRC quality — this is the single resample point
    /// of the contribution path (D-017), it runs off the audio
    /// thread, once per pack load / local assignment, so the CPU
    /// cost is irrelevant next to the fidelity win.
    private nonisolated static func convert(
        _ srcBuf: AVAudioPCMBuffer, to target: AVAudioFormat
    ) -> AVAudioPCMBuffer? {
        guard let converter = AVAudioConverter(from: srcBuf.format, to: target) else {
            return nil
        }
        converter.sampleRateConverterQuality = AVAudioQuality.max.rawValue
        let ratio = target.sampleRate / srcBuf.format.sampleRate
        // +32 frames of slack for the converter's internal state,
        // same margin the offline renderer uses.
        let outCapacity = AVAudioFrameCount(Double(srcBuf.frameLength) * ratio) + 32
        guard let dstBuf = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: outCapacity)
        else { return nil }
        var provided = false
        var convError: NSError?
        _ = converter.convert(to: dstBuf, error: &convError) { _, outStatus in
            if provided {
                outStatus.pointee = .endOfStream
                return nil
            }
            provided = true
            outStatus.pointee = .haveData
            return srcBuf
        }
        if convError != nil { return nil }
        return dstBuf
    }
    #endif
}
