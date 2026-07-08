// AudioEngineTopologyTests.swift
//
// Structural checks for the D-013 contribution graph + D-017 48 kHz
// canonical format on `AudioEngine`:
//   - canonicalFormat is 48 kHz stereo;
//   - buildContributionGraph is idempotent (bus nodes stable across
//     repeated calls / accessor hits);
//   - voice/chop/vocoder bus inputs are distinct mixers that all
//     route into the shared bus at the canonical format;
//   - sharedBus fans out to BOTH the dry and reverb branches (the
//     sequential-connect trap drops one edge — see the fan-out note
//     in buildContributionGraph);
//   - both branches terminate at mainMixer;
//   - gain setters clamp (voice/chop/vocoder to [0,1], layer to
//     [0,2]) and land on the right node;
//   - setReverbParams pushes dry/wet volumes into the graph.
//
// These run on the idle engine (never started) — connection points
// are inspectable without rendering.

import XCTest
import AVFoundation
@testable import ToneForgeMobile

@MainActor
final class AudioEngineTopologyTests: XCTestCase {

    private var engine: AudioEngine!

    override func setUp() {
        super.setUp()
        engine = AudioEngine()
    }

    override func tearDown() {
        engine = nil
        super.tearDown()
    }

    // MARK: - Canonical format

    func testCanonicalFormatIs48kStereo() {
        XCTAssertEqual(AudioEngine.canonicalSampleRate, 48_000)
        XCTAssertEqual(engine.canonicalFormat.sampleRate, 48_000)
        XCTAssertEqual(engine.canonicalFormat.channelCount, 2)
        XCTAssertEqual(engine.canonicalFormat.commonFormat, .pcmFormatFloat32)
    }

    // MARK: - Graph construction

    func testBuildContributionGraphIsIdempotent() {
        engine.buildContributionGraph()
        let voice1 = engine.voiceBusInput
        let chop1 = engine.chopBusInput
        let vocoder1 = engine.vocoderBusInput

        engine.buildContributionGraph()

        XCTAssertTrue(voice1 === engine.voiceBusInput)
        XCTAssertTrue(chop1 === engine.chopBusInput)
        XCTAssertTrue(vocoder1 === engine.vocoderBusInput)
    }

    func testBusAccessorsBuildTheGraphLazily() {
        // No explicit buildContributionGraph — first accessor builds.
        let voice = engine.voiceBusInput
        XCTAssertNotNil(voice as? AVAudioMixerNode)
    }

    func testBusInputsAreDistinctNodes() {
        let voice = engine.voiceBusInput
        let chop = engine.chopBusInput
        let vocoder = engine.vocoderBusInput
        XCTAssertFalse(voice === chop)
        XCTAssertFalse(voice === vocoder)
        XCTAssertFalse(chop === vocoder)
    }

    func testThreeBusesFanIntoOneSharedBus() throws {
        let voice = engine.voiceBusInput
        let chop = engine.chopBusInput
        let vocoder = engine.vocoderBusInput

        let voiceDest = try destinations(of: voice)
        let chopDest = try destinations(of: chop)
        let vocoderDest = try destinations(of: vocoder)

        XCTAssertEqual(voiceDest.count, 1)
        XCTAssertEqual(chopDest.count, 1)
        XCTAssertEqual(vocoderDest.count, 1)

        let shared = try XCTUnwrap(voiceDest.first?.node)
        XCTAssertTrue(chopDest.first?.node === shared)
        XCTAssertTrue(vocoderDest.first?.node === shared)
        // Shared bus is an intermediate mixer, NOT mainMixer — stems
        // bypass it, "Your Layer" fader rides it.
        XCTAssertFalse(shared === engine.engine.mainMixerNode)

        // All fan-in edges at the canonical format.
        XCTAssertFalse(voiceDest.isEmpty)
        XCTAssertEqual(voice.outputFormat(forBus: 0).sampleRate, 48_000)
        XCTAssertEqual(chop.outputFormat(forBus: 0).sampleRate, 48_000)
        XCTAssertEqual(vocoder.outputFormat(forBus: 0).sampleRate, 48_000)
    }

    func testSharedBusFansOutToDryReverbAndFXSend() throws {
        let voice = engine.voiceBusInput
        let shared = try XCTUnwrap(try destinations(of: voice).first?.node)

        let branches = try destinations(of: shared)
        XCTAssertEqual(
            branches.count, 3,
            "sharedBus must feed dry, reverb, AND fxSend — a dropped edge means the sequential-connect trap regressed"
        )
        let hasReverb = branches.contains { $0.node is AVAudioUnitReverb }
        // Two mixers expected: dryMixer and fxSendMixer
        let mixerCount = branches.filter { $0.node is AVAudioMixerNode }.count
        XCTAssertTrue(hasReverb, "one fan-out edge must enter the shared reverb")
        XCTAssertEqual(mixerCount, 2, "two fan-out edges must enter mixers (dry + fxSend)")
    }

    func testAllBranchesTerminateAtMainMixer() throws {
        let voice = engine.voiceBusInput
        let shared = try XCTUnwrap(try destinations(of: voice).first?.node)
        let branches = try destinations(of: shared)

        for point in branches {
            let node = try XCTUnwrap(point.node)
            var current = node
            // Walk at most 5 hops (dry→main, verb→wet→main, fxSend→mVerb→mDelay→fxReturn→main).
            var reachedMain = false
            for _ in 0..<5 {
                if current === engine.engine.mainMixerNode { reachedMain = true; break }
                guard let next = try destinations(of: current).first?.node else { break }
                current = next
            }
            XCTAssertTrue(reachedMain, "branch starting at \(node) must reach mainMixer")
        }
    }

    // MARK: - Gain setters

    func testGainSettersClampAndLandOnBusNodes() throws {
        let voice = try XCTUnwrap(engine.voiceBusInput as? AVAudioMixerNode)
        let chop = try XCTUnwrap(engine.chopBusInput as? AVAudioMixerNode)
        let vocoder = try XCTUnwrap(engine.vocoderBusInput as? AVAudioMixerNode)
        let shared = try XCTUnwrap(
            try destinations(of: voice).first?.node as? AVAudioMixerNode
        )

        engine.setVoiceGain(2.0)
        XCTAssertEqual(voice.outputVolume, 1.0)
        engine.setVoiceGain(-0.5)
        XCTAssertEqual(voice.outputVolume, 0.0)
        engine.setVoiceGain(0.35)
        XCTAssertEqual(voice.outputVolume, 0.35, accuracy: 1e-6)

        engine.setChopGain(0.6)
        XCTAssertEqual(chop.outputVolume, 0.6, accuracy: 1e-6)

        engine.setVocoderGain(0.25)
        XCTAssertEqual(vocoder.outputVolume, 0.25, accuracy: 1e-6)

        // Layer fader allows the advertised +6 dB (≈2.0 linear).
        engine.setLayerGain(1.9953)
        XCTAssertEqual(shared.outputVolume, 1.9953, accuracy: 1e-4)
        engine.setLayerGain(5.0)
        XCTAssertEqual(shared.outputVolume, 2.0)
    }

    func testDefaultBusVolumesAreLoudnessNeutral() throws {
        let voice = try XCTUnwrap(engine.voiceBusInput as? AVAudioMixerNode)
        let chop = try XCTUnwrap(engine.chopBusInput as? AVAudioMixerNode)
        let vocoder = try XCTUnwrap(engine.vocoderBusInput as? AVAudioMixerNode)

        XCTAssertEqual(voice.outputVolume, 0.9, accuracy: 1e-6)
        XCTAssertEqual(chop.outputVolume, 0.55, accuracy: 1e-6)
        XCTAssertEqual(vocoder.outputVolume, 0.4, accuracy: 1e-6)
    }

    // MARK: - Shared reverb

    func testSetReverbParamsPushesGainsIntoGraph() throws {
        let voice = engine.voiceBusInput
        let shared = try XCTUnwrap(try destinations(of: voice).first?.node)
        let branches = try destinations(of: shared)
        let dry = try XCTUnwrap(
            branches.first { $0.node is AVAudioMixerNode }?.node as? AVAudioMixerNode
        )
        let verb = try XCTUnwrap(
            branches.first { $0.node is AVAudioUnitReverb }?.node as? AVAudioUnitReverb
        )
        let wet = try XCTUnwrap(
            try destinations(of: verb).first?.node as? AVAudioMixerNode
        )

        engine.setReverbParams(.init(dryGain: 0.7, wetGain: 0.5, seconds: 3.0))

        XCTAssertEqual(dry.outputVolume, 0.7, accuracy: 1e-6)
        XCTAssertEqual(wet.outputVolume, 0.5, accuracy: 1e-6)
        XCTAssertEqual(verb.wetDryMix, 100)
        XCTAssertEqual(engine.reverbParams.dryGain, 0.7)
        XCTAssertEqual(engine.reverbParams.wetGain, 0.5)
        XCTAssertEqual(engine.reverbParams.seconds, 3.0)
    }

    func testPresetForSecondsMapping() {
        XCTAssertEqual(AudioEngine.presetForSeconds(0.5), .smallRoom)
        XCTAssertEqual(AudioEngine.presetForSeconds(1.0), .mediumRoom)
        XCTAssertEqual(AudioEngine.presetForSeconds(2.0), .largeRoom)
        XCTAssertEqual(AudioEngine.presetForSeconds(2.5), .mediumHall)
        XCTAssertEqual(AudioEngine.presetForSeconds(3.0), .largeHall)
        XCTAssertEqual(AudioEngine.presetForSeconds(4.0), .cathedral)
    }

    // MARK: - Helpers

    /// Output connection points of `node`'s bus 0 on the shared engine.
    private func destinations(of node: AVAudioNode) throws -> [AVAudioConnectionPoint] {
        engine.engine.outputConnectionPoints(for: node, outputBus: 0)
    }
}
