// BeatOnsetExtractorTests.swift
//
// Beat Capture (D-024): onset detection + classification + velocity
// over a synthetic performance buffer with known hit positions.

import XCTest
@testable import ToneForgeEngine

final class BeatOnsetExtractorTests: XCTestCase {

    private let sr: Double = 48_000
    private let classifier = HeuristicBeatClassifier()

    private func seededNoise(_ n: Int, seed: UInt64 = 5) -> [Float] {
        var rng = SplitMix64(seed: seed)
        return (0..<n).map { _ in Float(rng.nextSymmetricDouble()) }
    }

    /// Low sine burst (kick-like).
    private func kick(_ amp: Float) -> [Float] {
        let n = Int(0.14 * sr)
        return (0..<n).map { i in
            let t = Double(i) / sr
            return amp * Float(sin(2 * .pi * 55 * t) * exp(-t / 0.05))
        }
    }

    /// Short bright noise burst (hat-like).
    private func hat(_ amp: Float, seed: UInt64) -> [Float] {
        let n = Int(0.05 * sr)
        let noise = seededNoise(n, seed: seed)
        return (0..<n).map { i in
            noise[i] * amp * Float(exp(-Double(i) / sr / 0.012))
        }
    }

    private func silence(_ seconds: Double) -> [Float] {
        [Float](repeating: 0, count: Int(seconds * sr))
    }

    /// Voiced speech-like segment: slow attack (~60 ms ramp) into a
    /// sustained harmonic tone with a little noise — a vowel, roughly.
    private func vowel(_ amp: Float, seconds: Double = 0.3, seed: UInt64 = 9) -> [Float] {
        let n = Int(seconds * sr)
        let noise = seededNoise(n, seed: seed)
        return (0..<n).map { i in
            let t = Double(i) / sr
            let ramp = Float(min(1.0, t / 0.06))
            let tone = Float(
                sin(2 * .pi * 180 * t) + 0.5 * sin(2 * .pi * 360 * t))
            return amp * ramp * (0.8 * tone + 0.2 * noise[i])
        }
    }

    func testDetectsAllOnsets() {
        // 4 hits with wide gaps so onset detection is unambiguous.
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)
        buf += hat(0.5, seed: 1); buf += silence(0.4)
        buf += kick(0.9); buf += silence(0.4)
        buf += hat(0.5, seed: 2); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 4)
    }

    func testOnsetTimesApproximate() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += kick(0.9); buf += silence(0.4)
        buf += kick(0.9); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2)
        // First hit ≈ 0.1 s, second ≈ 0.1 + 0.14 + 0.4 = 0.64 s.
        XCTAssertEqual(hits[0].timeSec, 0.10, accuracy: 0.03)
        XCTAssertEqual(hits[1].timeSec, 0.64, accuracy: 0.03)
    }

    /// detectKick=false declares a single-drum take: kick verdicts
    /// become snares (soft hits stay ghost notes of the same voice).
    func testDetectKickOffForcesKickToSnare() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)
        buf += kick(0.9); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier,
            detectKick: false
        )
        XCTAssertEqual(hits.count, 2)
        XCTAssertTrue(hits.allSatisfy { $0.role == .snare })
    }

    func testVelocityReflectsLoudness() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)   // loud
        buf += kick(0.25); buf += silence(0.3)   // quiet

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2)
        XCTAssertGreaterThan(hits[0].velocity, hits[1].velocity)
        // Loudest hit normalises to 1.
        XCTAssertEqual(hits[0].velocity, 1.0, accuracy: 0.001)
    }

    /// Velocity normalises per role: one role's mic level must not set
    /// another role's accent scale. The quieter role's loudest hit is
    /// still that role's accent (velocity 1), and a soft same-role
    /// embellishment lands as a ghost note.
    func testVelocityNormalisedPerRole() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)         // loud kick accent
        buf += hat(0.5, seed: 3); buf += silence(0.4) // quieter hat accent
        buf += hat(0.2, seed: 4); buf += silence(0.3) // soft hat ghost

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 3, "hits: \(hits.map { "\($0.role)@\($0.timeSec)" })")
        let kicks = hits.filter { $0.role == .kick }
        let hats = hits.filter { $0.role != .kick }
        XCTAssertEqual(kicks.count, 1)
        XCTAssertEqual(hats.count, 2)
        XCTAssertEqual(kicks.first?.velocity ?? 0, 1.0, accuracy: 0.001)
        // Quieter hat accent still reaches full velocity for its role
        // (global normalisation would cap it well below 1); soft hat
        // is a ghost note.
        let hatVels = hats.map(\.velocity).sorted()
        XCTAssertEqual(hatVels.last ?? 0, 1.0, accuracy: 0.001)
        XCTAssertLessThan(hatVels.first ?? 1, 0.6)
    }

    func testEmptyBufferNoHits() {
        let hits = BeatOnsetExtractor.extract(
            silence(0.5), sampleRate: sr, classifier: classifier
        )
        XCTAssertTrue(hits.isEmpty)
    }

    func testKickClassifiedInContext() {
        var buf: [Float] = []
        buf += silence(0.05)
        buf += kick(0.9); buf += silence(0.4)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.first?.role, .kick)
    }

    /// Background speech alone (slow-attack sustained vowels) must not
    /// register any hits — the percussive gate rejects it.
    func testSpeechOnlyProducesNoHits() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += vowel(0.7, seed: 3); buf += silence(0.15)
        buf += vowel(0.6, seconds: 0.4, seed: 4); buf += silence(0.15)
        buf += vowel(0.8, seed: 5); buf += silence(0.2)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertTrue(
            hits.isEmpty,
            "speech-only buffer produced \(hits.count) hits"
        )
    }

    /// Beatbox-style kick: a quiet lip-plosive click, then a voiced
    /// "boom" that ramps over ~50 ms (much slower than a stick hit) and
    /// decays hard. The RMS envelope peaks at the boom, so `attackSec`
    /// lands in the 30–120 ms rescue band; the click triggers onset
    /// detection like a real "b" plosive.
    private func beatboxKick(_ amp: Float, seed: UInt64 = 11) -> [Float] {
        let n = Int(0.14 * sr)
        let click = seededNoise(n, seed: seed)
        return (0..<n).map { i in
            let t = Double(i) / sr
            let clickEnv = Float(exp(-t / 0.003))
            let boomEnv = t < 0.05 ? t / 0.05 : exp(-(t - 0.05) / 0.03)
            let boom = Float(sin(2 * .pi * 80 * t) * boomEnv)
            return amp * (boom + 0.2 * click[i] * clickEnv)
        }
    }

    /// A slow-attack beatbox kick must survive the gate (attack > 30 ms
    /// but tail clearly decaying) and classify as kick.
    func testSlowAttackBeatboxKickSurvivesGate() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += beatboxKick(0.9); buf += silence(0.4)
        buf += hat(0.5, seed: 8); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2, "expected kick+hat, got \(hits.count)")
        XCTAssertEqual(hits.first?.role, .kick)
    }

    // MARK: - Relative role refinement

    private func feat(
        centroid: Float, low: Float = 0.05, peak: Float = 0.05
    ) -> OnsetFeatures {
        OnsetFeatures(
            centroidHz: centroid, zcr: 0.03, attackSec: 0.01,
            durationSec: 0.1, pitchedness: 0.3, lowBandRatio: low,
            peakRMS: peak
        )
    }

    /// Real desktop chest-beat capture: dark cluster (~1500 Hz, labeled
    /// snare/perc) vs bright cluster (~2900 Hz, real snares). The dark
    /// cluster must be upgraded to kick; the bright one untouched.
    func testRelativeRefinementUpgradesDarkClusterToKick() {
        let centroids: [Float] = [
            3142, 1694, 2961, 1539, 1631, 2747, 2988, 1528,
            3218, 1528, 2738, 1510, 1462, 1438, 2686, 2061,
        ]
        let roles: [DrumRole] = [
            .snare, .snare, .snare, .snare, .snare, .kick, .snare, .snare,
            .kick, .snare, .snare, .perc, .perc, .perc, .snare, .snare,
        ]
        let feats = centroids.map { feat(centroid: $0) }
        let verdicts = roles.map { BeatClassification(role: $0, confidence: 0.6) }

        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        for (i, c) in centroids.enumerated() {
            if c < 2100 {
                XCTAssertEqual(out[i].role, .kick, "dark hit \(c) Hz should be kick")
            } else {
                XCTAssertEqual(out[i].role, roles[i], "bright hit \(c) Hz should keep verdict")
            }
        }
    }

    /// Real chest-beat capture where the kicks arrived bright (~2900 Hz
    /// vs ~3600 Hz snares, separation only 1.37x): the split must still
    /// fire and upgrade the dark cluster.
    func testRelativeRefinementFiresOnNarrowSeparation() {
        let centroids: [Float] = [
            3734, 3311, 3334, 1744, 2882, 4419,
            3699, 2966, 3616, 3251, 2978,
        ]
        let feats = centroids.map { feat(centroid: $0) }
        let verdicts = centroids.map { _ in
            BeatClassification(role: .snare, confidence: 0.6)
        }
        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        // 2-means converges to dark ~2643 / bright ~3623 with the
        // boundary at ~3133 Hz.
        for (i, c) in centroids.enumerated() {
            if c < 3100 {
                XCTAssertEqual(out[i].role, .kick, "dark hit \(c) Hz should be kick")
            } else {
                XCTAssertEqual(out[i].role, .snare, "bright hit \(c) Hz should stay snare")
            }
        }
    }

    /// Real all-snare take with ghost notes (chest+stomach): the ghosts
    /// cluster dark (soft hit = less HF) at ratio ~1.38 — same range as
    /// a genuine kick take — but they're half as loud as the accented
    /// snares. The loudness guard must veto the kick upgrade.
    func testRelativeRefinementSkipsQuietGhostCluster() {
        let hits: [(c: Float, p: Float)] = [
            (2766, 0.039), (3206, 0.033), (2198, 0.012), (3034, 0.024),
            (2137, 0.031), (1978, 0.016), (3432, 0.041), (2199, 0.012),
            (2466, 0.071), (2168, 0.015), (2330, 0.021), (2856, 0.061),
            (2072, 0.013), (2597, 0.023), (2297, 0.047), (2899, 0.036),
            (3011, 0.060), (2509, 0.027), (1844, 0.045), (1744, 0.016),
            (1705, 0.012), (2641, 0.022),
        ]
        let feats = hits.map { feat(centroid: $0.c, peak: $0.p) }
        let verdicts = hits.map { _ in
            BeatClassification(role: .snare, confidence: 0.6)
        }
        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        XCTAssertTrue(
            out.allSatisfy { $0.role == .snare },
            "quiet dark cluster is ghosts, not kicks"
        )
    }

    /// One tight cluster (all snares around 1600 Hz) — no relabel.
    func testRelativeRefinementSkipsUnimodalCohort() {
        let centroids: [Float] = [1567, 1585, 1586, 1600, 1715, 1727, 1892]
        let feats = centroids.map { feat(centroid: $0) }
        let verdicts = centroids.map { _ in
            BeatClassification(role: .snare, confidence: 0.6)
        }
        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        XCTAssertTrue(out.allSatisfy { $0.role == .snare })
    }

    /// Too few hits for clustering to mean anything — no relabel.
    func testRelativeRefinementSkipsSmallCohort() {
        let centroids: [Float] = [1500, 1520, 2900, 2950]
        let feats = centroids.map { feat(centroid: $0) }
        let verdicts = centroids.map { _ in
            BeatClassification(role: .snare, confidence: 0.6)
        }
        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        XCTAssertTrue(out.allSatisfy { $0.role == .snare })
    }

    /// Hats are not part of the cohort: a bright hat cluster must not
    /// drag mid-band snares into a fake "dark cluster" kick relabel.
    func testRelativeRefinementIgnoresHats() {
        let feats = [
            feat(centroid: 1500), feat(centroid: 1550), feat(centroid: 1600),
            feat(centroid: 8500), feat(centroid: 8700), feat(centroid: 8900),
        ]
        let verdicts: [BeatClassification] = [
            .init(role: .snare, confidence: 0.6),
            .init(role: .snare, confidence: 0.6),
            .init(role: .snare, confidence: 0.6),
            .init(role: .closedHat, confidence: 0.8),
            .init(role: .closedHat, confidence: 0.8),
            .init(role: .closedHat, confidence: 0.8),
        ]
        let out = BeatOnsetExtractor.refineRelativeRoles(
            feats: feats, verdicts: verdicts
        )
        XCTAssertEqual(out.map(\.role), verdicts.map(\.role))
    }

    /// Real hits survive the percussive gate even with louder speech
    /// in between — and speech must not set the noise floor that would
    /// otherwise gate out the quiet hit.
    func testHitsSurviveInterleavedSpeech() {
        var buf: [Float] = []
        buf += silence(0.1)
        buf += kick(0.9); buf += silence(0.3)
        buf += vowel(0.95, seconds: 0.4, seed: 6); buf += silence(0.2)
        buf += hat(0.4, seed: 7); buf += silence(0.3)

        let hits = BeatOnsetExtractor.extract(
            buf, sampleRate: sr, classifier: classifier
        )
        XCTAssertEqual(hits.count, 2, "expected kick+hat only, got \(hits.count)")
        XCTAssertEqual(hits.first?.timeSec ?? -1, 0.10, accuracy: 0.03)
    }
}
