// SessionIntegrationTests.swift
//
// D-015 glue on a headless AppState: the Record pill's arm/stop/
// cancel surface, transport gap hooks, the count-in capture
// suppression, replay routing through the session's padMapping
// overlay (triggerRaw, never the quantizing live path), and the
// bounce glue's attestation error surface.
//
// Hermetic: the session store roots in a temp dir (injected through
// AppState.init) and packs are runtime-synthesized tone files — no
// engine start needed, so everything runs on audio-less hosts.

import XCTest
import AVFoundation
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class SessionIntegrationTests: XCTestCase {

    private var app: AppState!
    private var tmpDir: URL!
    private var savedCountIn: Bool = false

    override func setUp() async throws {
        try await super.setUp()
        tmpDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("session-glue-\(UUID().uuidString)")
        try FileManager.default.createDirectory(
            at: tmpDir, withIntermediateDirectories: true
        )
        app = AppState(sessionStoreRoot: tmpDir)
        app.modeCoordinator.setMode(.sample)
        // Wire bus → executor (bootAudio does this in production;
        // idempotent, no engine start).
        app.modeCoordinator.start()
        // Sketch settings persist to real UserDefaults — pin the
        // count-in off for determinism and restore after.
        savedCountIn = app.sketchSettings.countInEnabled
        app.sketchSettings.countInEnabled = false
    }

    override func tearDown() async throws {
        app.cancelSessionRecording()
        app.stopSessionReplay()
        app.pause()
        app.sketchSettings.countInEnabled = savedCountIn
        if let dir = tmpDir { try? FileManager.default.removeItem(at: dir) }
        app = nil
        try await super.tearDown()
    }

    // MARK: - Fixtures (same hermetic runtime-tone packs as
    // ModeCoordinatorRingingTests)

    private func writeTone(to url: URL, durationSec: Double = 0.5) throws {
        let sr = 44_100.0
        let format = AVAudioFormat(
            standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(durationSec * sr)
        let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames)!
        buf.frameLength = frames
        if let ch = buf.floatChannelData {
            for c in 0..<2 {
                for i in 0..<Int(frames) {
                    ch[c][i] = Float(
                        sin(2 * .pi * 440 * Double(i) / sr) * 0.3)
                }
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        try file.write(from: buf)
    }

    private func makePack(
        packId: String, padIdxs: [Int]
    ) throws -> ResolvedSamplePack {
        var padObjs: [SamplePad] = []
        var urls: [Int: URL] = [:]
        for idx in padIdxs {
            let url = tmpDir.appendingPathComponent("\(packId)-\(idx).caf")
            try writeTone(to: url)
            padObjs.append(SamplePad(
                padIdx: idx,
                name: "P\(idx)",
                family: .pads,
                filename: "\(packId)-\(idx).caf",
                chokeGroup: nil,
                loopPointSec: 0
            ))
            urls[idx] = url
        }
        let pack = SamplePack(
            packId: packId, name: packId, family: .pads, pads: padObjs
        )
        return ResolvedSamplePack(pack: pack, padFileURLs: urls)
    }

    /// Publish a pad-down straight onto the contribution bus, the
    /// same shape the touch/Launchpad adapters produce.
    private func publishPadDown(
        row: Int, col: Int, at t: Double, isReplay: Bool = false
    ) {
        app.contributionBus.publish(ContributionEvent(
            source: .touch,
            kind: .padDown(row: row, col: col),
            timestamp: t,
            hostTime: 0,
            isReplay: isReplay
        ))
    }

    /// Install a spy on the scheduler's transform seam — the only
    /// headless observation point on the trigger path. The voice pool
    /// guards on engine attachment (`isAttached`), so `isActive`
    /// never flips without a booted engine; the transform resolver
    /// runs unconditionally right before `pool.trigger`, for live and
    /// replay (`triggerRaw`) paths alike. Chains to the real resolver
    /// so transform behavior is untouched. Returns a reader for the
    /// (packId, padIdx) pairs seen so far.
    private func spyOnTriggers() -> () -> [(packId: String, padIdx: Int)] {
        var seen: [(packId: String, padIdx: Int)] = []
        let chained = app.sampleScheduler.transformResolver
        app.sampleScheduler.transformResolver = { buffer, packId, padIdx in
            seen.append((packId: packId, padIdx: padIdx))
            return chained?(buffer, packId, padIdx) ?? buffer
        }
        return { seen }
    }

    // MARK: - Arm / stop / cancel

    func testArmCapturesSketchContextAndStopPersists() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])

        app.armSessionRecording()
        XCTAssertEqual(app.sessionRecorder.state, .armed)
        XCTAssertTrue(app.isPlaying, "arming starts the transport")

        // Arm-time snapshot: no song → sketch tempo; the pack
        // quadrant is in the mapping (padIdx 0 → grid raw 81).
        let snap = app.sessionRecorder.snapshot()
        XCTAssertNil(snap.songBackendId)
        XCTAssertEqual(snap.tempoBpm, app.sketchSettings.tempoBpm)
        XCTAssertEqual(
            snap.padMapping[PadAddress(mode: .sample, pad: PadIndex(81))],
            .packPad(packId: "packA", padIdx: 0)
        )

        publishPadDown(row: 8, col: 1, at: 0.25)
        XCTAssertEqual(app.sessionRecorder.state, .recording)

        app.stopAndSaveSessionRecording()
        XCTAssertEqual(app.sessionRecorder.state, .idle)
        XCTAssertEqual(app.savedSessions.count, 1)
        XCTAssertEqual(app.savedSessions.first?.events.count, 1)
        XCTAssertEqual(app.savedSessions.first?.sessionId, snap.sessionId,
                       "stop persists under the arm-time identity")
        XCTAssertFalse(app.isPlaying, "sketch stop parks the transport")
        XCTAssertEqual(app.songSeconds, 0)
    }

    func testStopWithoutEventsSavesNothing() {
        app.armSessionRecording()
        app.stopAndSaveSessionRecording()
        XCTAssertTrue(app.savedSessions.isEmpty)
        XCTAssertEqual(app.sessionRecorder.state, .idle)
    }

    func testCancelDiscardsTakeAndAutosavedFile() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])

        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.5)
        // Simulate the 10 s autosave having hit disk mid-take.
        try app.sessionStore.save(app.sessionRecorder.snapshot())
        XCTAssertEqual(app.sessionStore.list().count, 1)

        app.cancelSessionRecording()
        XCTAssertEqual(app.sessionRecorder.state, .idle)
        XCTAssertTrue(app.savedSessions.isEmpty)
        XCTAssertTrue(app.sessionStore.list().isEmpty,
                      "cancel must delete the autosaved file")
        XCTAssertFalse(app.isPlaying)
    }

    // MARK: - Count-in

    func testCountInHitNeitherSoundsNorRecords() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])
        app.sketchSettings.countInEnabled = true

        app.armSessionRecording()
        let now = app.audioEngine.clock.nowSongSeconds
        XCTAssertLessThan(now, 0, "count-in runs negative song time")
        XCTAssertTrue(app.isCountingIn)

        // Jump-the-gun hit inside the lead bar.
        let triggers = spyOnTriggers()
        publishPadDown(row: 8, col: 1, at: now)
        XCTAssertEqual(app.sessionRecorder.state, .armed,
                       "suppressed hit must not trip recording")
        XCTAssertEqual(app.sessionRecorder.eventCount, 0)
        XCTAssertTrue(triggers().isEmpty,
                      "suppressed hit must not sound")
    }

    // MARK: - Transport gap hooks

    func testPauseAndSeekInsertGapsOnlyWhileRecording() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])

        // The arm-time seek (count-in off → seek(to: 0)) must NOT
        // insert a gap — the recorder is armed, not yet recording.
        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.0)

        app.seek(to: 50)     // recording → signed gap ≈ +50
        app.pause()          // recording → zero gap
        app.stopAndSaveSessionRecording()

        let session = try XCTUnwrap(app.savedSessions.first)
        let gaps: [Double] = session.events.compactMap {
            if case .gap(let seconds) = $0.kind { return seconds }
            return nil
        }
        XCTAssertEqual(gaps.count, 2,
                       "exactly seek + pause; nothing from arm time")
        XCTAssertEqual(gaps[0], 50, accuracy: 1.0,
                       "seek gap carries the signed song-time jump")
        XCTAssertEqual(gaps[1], 0, "pause gap is zero seconds")
    }

    // MARK: - Replay

    func testReplayRoutesThroughSessionMappingNotLiveGrid() async throws {
        // Record against packB…
        let packB = try makePack(packId: "packB", padIdxs: [0])
        app.activateSamplePack(packB, stemFiles: [:])
        // Activation decodes buffers on a fire-and-forget task, so a
        // fast run can reach the replay tick before packB is resident
        // (triggerRaw would degrade to padNotFound silence). The sync
        // preload is the documented test/offline path; the async twin
        // re-checks residency after its decode, so racing it is safe.
        try app.sampleScheduler.preloadPack(packB, stemFiles: [:])
        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.0)
        app.stopAndSaveSessionRecording()
        let sessionId = try XCTUnwrap(app.savedSessions.first?.sessionId)

        // …then re-bind the live grid to packA.
        let packA = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(packA, stemFiles: [:])

        let triggers = spyOnTriggers()
        app.toggleSessionReplay(sessionId: sessionId)
        XCTAssertEqual(app.replayingSessionId, sessionId)
        XCTAssertTrue(app.isPlaying, "replay starts the transport")

        // Let the clock pass the event's timestamp, then pump the
        // player deterministically.
        try await Task.sleep(nanoseconds: 150_000_000)
        app.sessionPlayer.tickForTests()

        let seen = triggers()
        XCTAssertEqual(seen.count, 1,
                       "exactly the one recorded hit fires")
        XCTAssertEqual(seen.first?.packId, "packB",
                       "replay must sound the RECORDED pack via overlay,"
                       + " not the live grid's packA")
        XCTAssertEqual(seen.first?.padIdx, 0)

        // Toggling the same session again stops + unloads it.
        app.toggleSessionReplay(sessionId: sessionId)
        XCTAssertNil(app.replayingSessionId)
        XCTAssertNil(app.sessionPlayer.session)
    }

    func testDeleteSessionStopsItsReplay() throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])
        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.0)
        app.stopAndSaveSessionRecording()
        let sessionId = try XCTUnwrap(app.savedSessions.first?.sessionId)

        app.toggleSessionReplay(sessionId: sessionId)
        app.deleteSession(sessionId: sessionId)
        XCTAssertNil(app.replayingSessionId)
        XCTAssertTrue(app.savedSessions.isEmpty)
        XCTAssertTrue(app.sessionStore.list().isEmpty)
    }

    // MARK: - Bounce glue

    func testBounceWithOriginalSongRequiresAttestation() async throws {
        // Pin the persisted attestation OFF for the duration (the
        // glue reads the standard-defaults flag).
        let hadAttestation = UserDefaults.standard
            .bool(forKey: "toneforge.attestation.accepted")
        AttestationStore.resetPersisted()
        defer {
            if hadAttestation { AttestationStore().accept() }
        }

        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])
        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.0)
        app.stopAndSaveSessionRecording()
        let sessionId = try XCTUnwrap(app.savedSessions.first?.sessionId)

        let url = await app.bounceSession(
            sessionId: sessionId, includeOriginalSong: true
        )
        XCTAssertNil(url, "un-attested original-song bounce must fail")
        XCTAssertNotNil(app.layerError,
                        "the reason must surface to the UI")
        XCTAssertTrue(app.bouncingSessionIds.isEmpty)
    }

    func testBounceRendersPadMappingToWavFile() async throws {
        let pack = try makePack(packId: "packA", padIdxs: [0])
        app.activateSamplePack(pack, stemFiles: [:])
        app.armSessionRecording()
        publishPadDown(row: 8, col: 1, at: 0.0)
        app.stopAndSaveSessionRecording()
        let sessionId = try XCTUnwrap(app.savedSessions.first?.sessionId)

        let url = await app.bounceSession(sessionId: sessionId)
        defer { if let url { try? FileManager.default.removeItem(at: url) } }

        let fileURL = try XCTUnwrap(url, app.layerError ?? "")
        let size = try FileManager.default
            .attributesOfItem(atPath: fileURL.path)[.size] as? Int ?? 0
        XCTAssertGreaterThan(size, 44,
                             "bounce must write real audio past the header")
        XCTAssertEqual(fileURL.pathExtension, "wav")
        XCTAssertTrue(app.bouncingSessionIds.isEmpty)
        XCTAssertNil(app.layerError)
    }
}
