// ChopPlayerBurstTests.swift
//
// Same-pad hammering regression. Hardware measurement showed rapid
// down/up cycles on one pad exploding to 138/314/516 ms press→trigger
// (and 433 ms padUp→release) while single taps stayed at 2–11 ms. Root
// cause: AVAudioPlayerNode.play() blocks its caller for up to one
// render quantum (~10.7 ms measured at 512f/48k), and the press path
// paid it — plus a stop() — on EVERY press because the old claim
// policy stole the same slot per key and only ever examined the FIRST
// nil-key slot before growing the pool. At finger-drumming rates that
// serialized presses into a main-queue pileup.
//
// The fix is parked-voice rotation: warmUpPool() keeps pool nodes
// `play()`ing with an empty queue ("parked"), so a press only
// scheduleBuffer()s (starts at the next render cycle, no control
// call); a same-key retrigger claims a FRESH parked voice and the
// prior voice release-fades off the press path (web padengine
// behavior: re-trigger fades the old source and starts a new one).
// These tests pin the mechanics: zero play() calls on the press path
// after warm-up, bounded press cost, exactly one keyed voice across a
// retrigger, and the fade terminal re-parking drained voices.

import AVFoundation
import os
import XCTest
import ToneForgeEngine
import JamDesktopCore
@testable import JamDesktopAudio

@MainActor
final class ChopPlayerBurstTests: XCTestCase {

    private func makeWAV(seconds: Double, sr: Double = 44_100) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("tf_hammer_\(UUID().uuidString).wav")
        let fmt = AVAudioFormat(standardFormatWithSampleRate: sr, channels: 2)!
        let frames = AVAudioFrameCount(sr * seconds)
        let buf = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames)!
        buf.frameLength = frames
        for c in 0..<2 {
            let ch = buf.floatChannelData![c]
            for i in 0..<Int(frames) {
                ch[i] = sinf(Float(i) * 0.031 + Float(c)) * 0.3
            }
        }
        let file = try AVAudioFile(forWriting: url, settings: fmt.settings)
        try file.write(from: buf)
        return url
    }

    private func makePlayer() throws -> (AVAudioEngine, ChopPlayer, URL) {
        let engine = AVAudioEngine()
        let player = ChopPlayer(avEngine: engine)
        let url = try makeWAV(seconds: 12)
        _ = engine.mainMixerNode
        try engine.start()
        return (engine, player, url)
    }

    private let chop = Chop(idx: 0, startSec: 1.0, endSec: 5.0, durationSec: 4.0,
                            kind: "chord", loopable: true, loopScore: 0.9)

    func testHammerRidesParkedVoicesWithNoPlayCallsOnPressPath() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        // Production warm-up (session attach does both).
        await player.prewarm([(chop: chop, stem: "other")],
                             loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()
        XCTAssertEqual(player.parkedVoiceCount, 16, "warm-up parks the whole pool")
        XCTAssertEqual(player.immediatePlayCount, 0)

        // 12 zero-gap down/up cycles: worse than any human hammer. Each
        // press must claim a parked voice (12 < 16 available, so this is
        // deterministic) and never call play() on the press path.
        var worst = 0.0
        var total = 0.0
        for _ in 0..<12 {
            let t0 = CFAbsoluteTimeGetCurrent()
            player.trigger(a, afterSeconds: 0, loop: true,
                           crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                           loopBarSeconds: 2.0, cycleSeconds: 4.0)
            let dt = (CFAbsoluteTimeGetCurrent() - t0) * 1000
            worst = max(worst, dt)
            total += dt
            player.release(a)
        }
        XCTAssertEqual(player.immediatePlayCount, 0,
                       "presses must ride parked voices — a press-path play() "
                       + "blocks up to a render quantum, the burst serializer")
        // Generous CI bounds: pre-fix every press blocked ~10.7 ms inside
        // play(); parked presses measure ~0.1 ms.
        XCTAssertLessThan(total / 12, 8.0, "mean press cost regressed")
        XCTAssertLessThan(worst, 30.0, "worst press cost regressed")
    }

    func testRetriggerRotatesVoicesKeepingExactlyOneKeyed() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()

        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.soundingVoiceCount, 1)
        // Retrigger WITHOUT a release (sequencer/replay do this): the
        // prior voice must rotate out (fade, unkeyed) and exactly one
        // voice carries the key — the release path keys on it.
        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.soundingVoiceCount, 1,
                       "rotation keys exactly one voice; the prior fades unkeyed")
        player.release(a)
        XCTAssertEqual(player.soundingVoiceCount, 0)
    }

    // MARK: - Receive-thread fast release

    /// The audible release must not wait for the MIDI→main hop: on
    /// hardware, the SwiftUI commit each press provokes swallowed
    /// pad-up deliveries for 50–145 ms under same-pad hammering while
    /// the main-path release handling itself measured ~0.1 ms.
    /// padReleased(tag:) begins the fade from ANY thread; here the
    /// main actor is deliberately blocked the whole time, so a fade
    /// observed at ~0 volume afterwards ran entirely without main.
    func testFastReleaseFadesWhileMainActorIsBlocked() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()

        let tag = 7
        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
        XCTAssertEqual(player.fastReleaseVolume(tag: tag), 1.0)

        // Fire the fast release from off-main, then BLOCK the main
        // actor synchronously for 3× the fade length.
        Thread.detachNewThread { player.padReleased(tag: tag) }
        usleep(60_000)   // main actor stalled; the fade must still run

        let v = player.fastReleaseVolume(tag: tag) ?? 1.0
        XCTAssertLessThanOrEqual(v, 0.01,
            "the 20 ms fade must complete while main is stalled")
        // Bookkeeping is deliberately untouched — the authoritative
        // main-path release does it when the hop finally lands.
        XCTAssertEqual(player.soundingVoiceCount, 1)
        player.release(a)
        XCTAssertEqual(player.soundingVoiceCount, 0)
    }

    /// Composition with the main path + retrigger: the fast fade is
    /// epoch-guarded, so a re-press re-registers the pad at full
    /// volume and any stale fast ramp cannot drag the NEW voice down.
    func testFastReleaseComposesWithRetrigger() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()

        let tag = 3
        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
        player.padReleased(tag: tag)         // fast fade begins
        player.release(a)                    // main path follows
        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)

        // Let any stale ramp (from the first cycle) run out.
        try? await Task.sleep(nanoseconds: 60_000_000)
        XCTAssertEqual(player.fastReleaseVolume(tag: tag), 1.0,
            "the re-pressed voice keeps its full volume; stale fast "
            + "ramps die on the epoch gate")
        XCTAssertEqual(player.soundingVoiceCount, 1)
        player.release(a)
    }

    /// The receive-thread entry itself must be ~free — it runs on the
    /// MIDI thread. Hammer cycles: every padReleased call bounded.
    func testFastReleaseCallCostBoundedUnderHammer() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.prewarm([(chop: chop, stem: "other")],
                             loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()

        let tag = 12
        var worst = 0.0
        for _ in 0..<12 {
            player.trigger(a, afterSeconds: 0, loop: true,
                           crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                           loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
            let t0 = CFAbsoluteTimeGetCurrent()
            player.padReleased(tag: tag)
            worst = max(worst, (CFAbsoluteTimeGetCurrent() - t0) * 1000)
            player.release(a)   // main-path bookkeeping
        }
        // Dict lookup + task spawn ≈ µs; 10 ms is a generous CI bound
        // (pre-fix the audible release start waited on the main hop —
        // 50–145 ms measured on hardware).
        XCTAssertLessThan(worst, 10.0, "fast release must stay ~free on the MIDI thread")
    }

    // MARK: - Flood-mounted grid (64-pad kit) rides the same fast paths

    /// A SERVER-flood kit pad fixture: stemSlice windows all inside the
    /// test WAV, stems cycling the real roles so the mount spans stems
    /// like a genuine /kit?pads=64 manifest.
    private func floodPack(pads: Int) -> SamplePack {
        let stems = ["drums", "bass", "other", "vocals"]
        let padList = (0..<pads).map { i in
            SamplePad(
                padIdx: i, name: "Pad \(i)", family: .mixed,
                colorHint: "#EF4444",
                stemSlice: StemSlice(
                    stemRole: stems[i % stems.count],
                    startSec: Double(i % 8) * 0.5,
                    endSec: Double(i % 8) * 0.5 + 2.0),
                loopScore: 0.8, loopable: true,
                contentType: "rhythm_loop", category: "DRUMS",
                assetId: "asset-\(i)")
        }
        return SamplePack(packId: "flood", name: "Flood", family: .mixed,
                          pads: padList)
    }

    /// SessionController's onTrigger routing, reduced to its audio calls:
    /// `drumfile:`/`borrowfile:` assignments play their downloaded FILE,
    /// everything else the stem chop — both through ChopPlayer.schedule
    /// with the pad's tag. Keeping the twin here pins that EVERY mount
    /// route's press registers the receive-thread fast-release ref and
    /// rides parked voices.
    private func wire(
        _ controller: LaunchpadController, to player: ChopPlayer,
        sampleFiles: [Int: URL] = [:]
    ) {
        controller.onTrigger = { pad, assignment, _, _ in
            let tag = pad.row * 8 + pad.col
            if let aid = assignment.chop.assetId,
               aid.hasPrefix("drumfile:") || aid.hasPrefix("borrowfile:"),
               let url = sampleFiles[assignment.chop.idx] {
                player.trigger(file: url, startSec: nil, endSec: nil,
                               afterSeconds: 0, loop: false, padTag: tag)
                return
            }
            player.trigger(assignment, afterSeconds: 0, loop: true,
                           crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                           loopBarSeconds: 2.0, cycleSeconds: 4.0,
                           padTag: tag)
        }
        controller.onRelease = { _, assignment in
            if let aid = assignment.chop.assetId,
               aid.hasPrefix("drumfile:") || aid.hasPrefix("borrowfile:"),
               let url = sampleFiles[assignment.chop.idx] {
                player.release(fileURL: url)
            } else {
                player.release(assignment)
            }
        }
        controller.onStopAllVoices = { player.stopAll() }
    }

    /// Burst across a FLOOD-mounted 64-pad grid (not the auto-kit
    /// fixture): presses ride parked voices — zero play() on the press
    /// path — and every pressed pad lands in the fast-release registry,
    /// so the MIDI receive thread can begin its fade. Pins that the
    /// 64-flood mount route (KitGridMapper → adoptAssignments →
    /// padDown → onTrigger) kept every latency guarantee the 16-pad
    /// auto kit had (b3727853 parked rotation + 40625901 fast release).
    func testFloodMountedGridBurstNoPressPathPlayAndFastReleaseRegistered() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: [
            "drums": url, "bass": url, "other": url, "vocals": url,
        ])
        let controller = LaunchpadController(nowProvider: { 0 })
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: floodPack(pads: 64)))
        controller.playbackMode = .oneShot   // instant gate: fires NOW
        wire(controller, to: player)
        await player.prewarm(
            controller.assignments.values.map { (chop: $0.chop, stem: $0.stem) },
            loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()
        XCTAssertEqual(player.immediatePlayCount, 0)

        // 12 different flood pads (parked pool holds 16): press each via
        // the pipeline, assert registration, then release both lanes.
        for i in 0..<12 {
            let pad = LaunchpadPad(row: i / 8, col: i % 8)
            controller.padDown(pad, pressSongSeconds: 0, pressHostTime: 1)
            let tag = pad.row * 8 + pad.col
            XCTAssertNotNil(player.fastReleaseVolume(tag: tag),
                "flood pad \(i) press must register its fast-release ref")
            player.padReleased(tag: tag)          // receive-thread lane
            controller.padUp(pad, pressHostTime: 2)   // main lane follows
        }
        XCTAssertEqual(player.immediatePlayCount, 0,
            "flood presses must ride parked voices — zero play() on the press path")
    }

    /// EVERY mount route registers padTags at trigger: chop grids
    /// (setChops), kit flood incl. `drumfile:` file pads
    /// (adoptAssignments), and borrow file pads
    /// (adoptBorrowAssignments). A route whose press skips registration
    /// leaves its pad-up waiting on the main hop — the exact class of
    /// slow lane this suite exists to forbid.
    func testEveryMountRouteRegistersPadTags() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        let fileURL = try makeWAV(seconds: 2)
        defer { try? FileManager.default.removeItem(at: fileURL) }
        await player.load(stemURLs: ["drums": url, "other": url])
        await player.warmUpPool()
        let controller = LaunchpadController(nowProvider: { 0 })
        controller.playbackMode = .oneShot
        wire(controller, to: player, sampleFiles: [0: fileURL, 1: fileURL])

        func pressAndAssert(_ pad: LaunchpadPad, route: String) {
            let tag = pad.row * 8 + pad.col
            controller.padDown(pad, pressSongSeconds: 0, pressHostTime: 1)
            XCTAssertNotNil(player.fastReleaseVolume(tag: tag),
                "\(route) press must register the fast-release padTag")
            controller.padUp(pad, pressHostTime: 2)
        }

        // 1) Chop grid (panel stem/sliceMode load).
        controller.setChops(
            [Chop(idx: 0, startSec: 0, endSec: 2, durationSec: 2,
                  kind: "phrase", loopable: true, loopScore: 0.9)],
            stem: "other", sliceMode: "chord")
        pressAndAssert(LaunchpadPad(row: 0, col: 0), route: "chop grid")

        // 2) Kit flood with a downloaded composite: pad 1 gets the
        //    drumfile: sentinel and must register through the FILE lane.
        let pack = floodPack(pads: 2)
        controller.adoptAssignments(
            KitGridMapper.pairs(pack: pack, sampleFiles: [1: fileURL]))
        pressAndAssert(LaunchpadPad(row: 0, col: 0), route: "kit stem pad")
        pressAndAssert(LaunchpadPad(row: 0, col: 1), route: "kit drumfile pad")

        // 3) Borrow mount (file pads, capacity-aware layout).
        controller.adoptBorrowAssignments([
            .init(chop: Chop(idx: 0, startSec: 0, endSec: 2, durationSec: 2,
                             kind: "phrase", sectionLabel: "Loop",
                             loopable: true, loopScore: 1.0,
                             assetId: "borrowfile:0"),
                  stem: "drums", sourceLabel: "This song", source: .initial)
        ])
        pressAndAssert(LaunchpadPad(row: 0, col: 0), route: "borrow pad")
    }

    /// prewarmFiles: the FILE twin of prewarm — after it, a file pad's
    /// first press is a cache hit (no read/bake on the press path) and
    /// its reader is already open.
    func testPrewarmFilesWarmsReaderAndLoopBake() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        let fileURL = try makeWAV(seconds: 2)
        defer { try? FileManager.default.removeItem(at: fileURL) }
        await player.load(stemURLs: [:])
        await player.warmUpPool()

        await player.prewarmFiles([fileURL])
        XCTAssertEqual(player.cachedFileCount, 1, "reader opened off-path")
        let bakesAfterPrewarm = player.loopBakeCount

        // The live press derives the SAME loop-bake key (whole file,
        // 12 ms crossfade) — it must be a cache hit.
        player.trigger(file: fileURL, startSec: nil, endSec: nil,
                       afterSeconds: 0, loop: true, padTag: 5)
        XCTAssertEqual(player.loopBakeCount, bakesAfterPrewarm,
            "first press of a prewarmed file pad must not re-bake")
        XCTAssertNotNil(player.fastReleaseVolume(tag: 5))
        XCTAssertEqual(player.immediatePlayCount, 0)
        player.release(fileURL: fileURL)
    }

    // MARK: - Receive-thread fast press (D-038)

    private func armOne(
        _ player: ChopPlayer, tag: Int,
        join: ChopPlayer.FastPressJoin = .zero
    ) async -> PadAssignment {
        let a = PadAssignment(chop: chop, stem: "other")
        await player.armFastPresses([
            .init(padTag: tag, assignment: a, fileURL: nil,
                  effects: .neutral,
                  crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                  loopBarSeconds: 2.0, cycleSeconds: 4.0),
        ], join: join)
        return a
    }

    /// The audible press must not wait for the MIDI→main hop: real
    /// hardware capture (2026-09-18, 188 events) measured press hops of
    /// 15–44 ms typical with 81/138 ms spikes under same-pad hammering —
    /// SwiftUI repaint storms queue the hop at finger rates. Here the
    /// main actor is deliberately blocked for ~6× the render quantum
    /// while the receive thread fires the armed plan: the parked-voice
    /// schedule AND the fast-release registration must complete without
    /// main. The blocked-main mirror of the D-033 fast-release test.
    func testFastPressFiresWhileMainActorIsBlocked() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        await player.prewarm([(chop: chop, stem: "other")],
                             loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()
        let tag = 9
        let a = await armOne(player, tag: tag)
        XCTAssertTrue(player.hasFastPlan(tag: tag))

        let fired = OSAllocatedUnfairLock(initialState: false)
        Thread.detachNewThread {
            let ok = player.padPressed(
                tag: tag, songSeconds: 0, hostTime: mach_absolute_time())
            fired.withLock { $0 = ok }
        }
        usleep(60_000)   // main actor stalled the whole time

        XCTAssertTrue(fired.withLock { $0 },
                      "the press must fire on the receive thread")
        XCTAssertNotNil(player.fastReleaseVolume(tag: tag),
            "the fire registers its fast-release ref pre-hop — a pad-up "
            + "can beat the press's own main hop")
        XCTAssertEqual(player.pendingFastFireCount, 1)

        // The trailing main hop ADOPTS: bookkeeping only, exactly one
        // keyed voice, no second schedule, no play() anywhere.
        player.trigger(a, afterSeconds: 0, loop: true,
                       crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
        XCTAssertEqual(player.soundingVoiceCount, 1,
                       "adoption, never a double-trigger")
        XCTAssertEqual(player.pendingFastFireCount, 0)
        XCTAssertEqual(player.immediatePlayCount, 0)
        player.padReleased(tag: tag)
        player.release(a)
    }

    /// Armed-plan invalidation: a remount/mode change bumps the plan
    /// generation SYNCHRONOUSLY, so a press with the old surface's plan
    /// can never sound stale content — pre-fire (padPressed refuses)
    /// AND post-fire (the main trigger refuses adoption and replays the
    /// press against the new grid, killing the fast voice).
    func testStalePlanNeverFiresAndStaleFireIsSuperseded() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        await player.warmUpPool()
        let tag = 4

        // Pre-fire: disarm (what every grid remount does first) → the
        // press falls to the main path, claiming nothing.
        _ = await armOne(player, tag: tag)
        player.disarmFastPresses()
        let parked = player.fastParkedCount
        XCTAssertFalse(player.padPressed(
            tag: tag, songSeconds: 0, hostTime: mach_absolute_time()))
        XCTAssertNil(player.fastReleaseVolume(tag: tag))
        XCTAssertEqual(player.fastParkedCount, parked,
                       "a dead plan must not claim a parked voice")

        // Post-fire: fire, THEN remount (generation bump) before the
        // main hop lands — adoption must refuse and the normal press
        // must supersede the fast voice.
        let a = await armOne(player, tag: tag)
        XCTAssertTrue(player.padPressed(
            tag: tag, songSeconds: 0, hostTime: mach_absolute_time()))
        player.disarmFastPresses()   // the remount's synchronous half
        player.trigger(a, afterSeconds: 0, loop: true,
                       crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
        XCTAssertEqual(player.soundingVoiceCount, 1,
            "exactly the fresh voice sounds; the stale fire was aborted")
        XCTAssertEqual(player.pendingFastFireCount, 0)
        player.release(a)
    }

    /// The receive-thread entry must stay ~free (it runs on the MIDI
    /// thread): burst across 12 armed pads, every padPressed call
    /// bounded well under one render quantum (~10.7 ms at 512f/48k).
    func testFastPressReceiveThreadCostBoundedUnderBurst() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        await player.prewarm([(chop: chop, stem: "other")],
                             loopBarSeconds: 2.0, cycleSeconds: 4.0)
        await player.warmUpPool()
        let items = (0..<12).map { tag in
            ChopPlayer.FastPressArming(
                padTag: tag,
                assignment: PadAssignment(chop: chop, stem: "other"),
                fileURL: nil, effects: .neutral,
                crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                loopBarSeconds: 2.0, cycleSeconds: 4.0)
        }
        await player.armFastPresses(items, join: .zero)

        var worst = 0.0
        var total = 0.0
        for tag in 0..<12 {
            let t0 = CFAbsoluteTimeGetCurrent()
            let ok = player.padPressed(
                tag: tag, songSeconds: 0, hostTime: mach_absolute_time())
            let dt = (CFAbsoluteTimeGetCurrent() - t0) * 1000
            XCTAssertTrue(ok, "pad \(tag) must fire (12 < 16 parked)")
            worst = max(worst, dt)
            total += dt
        }
        print(String(format:
            "[FastPress] receive-thread cost mean %.3f ms worst %.3f ms",
            total / 12, worst))
        XCTAssertLessThan(worst, 5.0,
            "fast press must stay well under a render quantum")
        player.stopAll()   // aborts the un-adopted fires
        XCTAssertEqual(player.pendingFastFireCount, 0)
    }

    /// Follow join on the receive thread: with a free-run era anchored,
    /// the fired voice starts mid-body at the era phase (not at zero) —
    /// the same join the main path computes.
    func testFastPressEraJoinStartsMidBody() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        await player.warmUpPool()
        let tag = 6
        _ = await armOne(player, tag: tag, join: .era)
        // Free-run era anchored 1.0 s ago (host clock domain).
        let nowTicks = mach_absolute_time()
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        let tick = Double(info.numer) / Double(info.denom) / 1_000_000_000
        player.updateFastPressEra(
            freerunAnchorHostSeconds: Double(nowTicks) * tick - 1.0,
            lockAnchorSongSeconds: nil, transportRolling: false)
        XCTAssertTrue(player.padPressed(
            tag: tag, songSeconds: 0, hostTime: nowTicks))
        // 1.0 s into a 4 s body @48k = phase 48000 — visible via the
        // adopted voice's phaseFrames → loopProgress baseline. Adoption:
        let a = PadAssignment(chop: chop, stem: "other")
        player.trigger(a, afterSeconds: 0, loop: true,
                       crossfadeMs: ChopPlayer.defaultPadCrossfadeMs,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0, padTag: tag)
        XCTAssertEqual(player.soundingVoiceCount, 1)
        // The join phase survives into main bookkeeping (playhead ring).
        XCTAssertEqual(player.voicePhaseFrames(stem: "other", idx: 0), 48_000)
        player.release(a)
    }

    func testFadeTerminalReparksDrainedVoices() async throws {
        let (engine, player, url) = try makePlayer()
        defer { engine.stop(); try? FileManager.default.removeItem(at: url) }
        await player.load(stemURLs: ["other": url])
        let a = PadAssignment(chop: chop, stem: "other")
        await player.warmUpPool()
        XCTAssertEqual(player.parkedVoiceCount, 16)

        player.trigger(a, afterSeconds: 0, loop: true, crossfadeMs: 15,
                       loopBarSeconds: 2.0, cycleSeconds: 4.0)
        XCTAssertEqual(player.parkedVoiceCount, 15, "the press drains one parked voice")
        player.release(a)

        // The 20 ms release fade's terminal stops AND re-parks the voice
        // (off the press path), so a sustained jam never starves the
        // parked pool. Poll generously for CI scheduling noise.
        for _ in 0..<100 where player.parkedVoiceCount < 16 {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        XCTAssertEqual(player.parkedVoiceCount, 16,
                       "released voice must return to the parked pool")
    }
}
