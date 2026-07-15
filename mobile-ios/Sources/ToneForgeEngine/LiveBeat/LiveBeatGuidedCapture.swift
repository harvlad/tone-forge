// LiveBeatGuidedCapture.swift
//
// Deterministic segmentation for guided ("tap-along") Live Beat
// calibration. The live onset detector is flaky at capture time — quiet
// taps get missed, loud rooms retrigger — so calibration that leans on it
// is unreliable. Instead the UI shows a VISUAL metronome; the user taps
// each beat; the platform records the mic continuously; and this function
// segments that buffer by the KNOWN beat times. No dependence on live
// onset detection, no audible click (so nothing bleeds into the capture).
//
// For each expected beat time we search a window around it for the loudest
// sample (the tap's attack), then extract a `LiveBeatFeatures` window from
// the sound's *body* — one buffer past the attack, matching the runtime
// deferred-body capture so training and inference see the same slice.

import Accelerate
import Foundation

/// Windows a continuous capture by known beat times into per-hit features.
public enum LiveBeatGuidedCapture {

    /// One segmented hit: its features and attack RMS (for the onset
    /// threshold the template stores).
    public struct Hit: Sendable, Equatable {
        public let features: LiveBeatFeatures
        public let rms: Float
    }

    /// Samples past the detected attack peak where the body window begins.
    /// Mirrors the runtime one-buffer deferral (~21 ms @ 48 kHz) so the
    /// low-frequency body — not the attack click — drives the features.
    static let bodyOffsetSamples = 1024

    /// Half-width (seconds) of the search window around each beat. The tap
    /// lands within human reaction latency of the visual beat; ±0.25 s
    /// tolerates that without straying into the neighbouring beat at
    /// tempos down to ~120 BPM (0.5 s/beat).
    static let searchRadiusSec = 0.25

    /// Attack window (samples) over which the reported RMS is measured.
    static let attackRMSWindow = 512

    /// Segment `samples` into one `Hit` per expected beat time.
    /// - Parameters:
    ///   - samples: Continuous mono capture (calibration take).
    ///   - sampleRate: Capture sample rate.
    ///   - expectedTimes: Beat times (seconds from buffer start) the user
    ///     was tapping to.
    /// - Returns: One hit per beat that held a resolvable tap. Beats whose
    ///   search window was silent (missed tap) are dropped.
    public static func extractHits(
        from samples: [Float],
        sampleRate: Double,
        expectedTimes: [Double]
    ) -> [Hit] {
        guard sampleRate > 0, !samples.isEmpty, !expectedTimes.isEmpty else {
            return []
        }

        let radius = Int(searchRadiusSec * sampleRate)
        let total = samples.count
        var hits: [Hit] = []
        hits.reserveCapacity(expectedTimes.count)

        // Silence gate: a beat window whose peak is below this is a missed
        // tap, not a hit. Relative to the loudest tap across all beats so a
        // quiet-but-real kick isn't gated beneath a loud snare.
        var beatPeaks: [(center: Int, lo: Int, hi: Int, peakIdx: Int, peak: Float)] = []
        for t in expectedTimes {
            let center = Int(t * sampleRate)
            let lo = max(0, center - radius)
            let hi = min(total - 1, center + radius)
            guard lo < hi else { continue }
            var peak: Float = 0
            var peakIdx = lo
            for i in lo...hi {
                let a = abs(samples[i])
                if a > peak { peak = a; peakIdx = i }
            }
            beatPeaks.append((center, lo, hi, peakIdx, peak))
        }
        guard !beatPeaks.isEmpty else { return [] }

        let globalPeak = beatPeaks.map(\.peak).max() ?? 0
        let floor = globalPeak * 0.15

        for beat in beatPeaks {
            guard beat.peak >= floor, beat.peak > 1e-5 else { continue }

            // Body window: one buffer past the attack, clamped to the
            // capture end. Fall back toward the peak if we'd run off.
            let start = min(beat.peakIdx + bodyOffsetSamples, total - 1)
            let end = min(start + LiveBeatFeatures.windowSize, total)
            let bodyStart = end - start >= LiveBeatFeatures.windowSize
                ? start
                : max(0, total - LiveBeatFeatures.windowSize)
            let body = Array(samples[bodyStart..<min(bodyStart + LiveBeatFeatures.windowSize, total)])

            guard let features = LiveBeatFeatures.extract(
                from: body, sampleRate: sampleRate
            ) else { continue }

            // Attack RMS over a short window at the peak.
            let aStart = beat.peakIdx
            let aEnd = min(aStart + attackRMSWindow, total)
            var rms: Float = 0
            samples.withUnsafeBufferPointer { buf in
                vDSP_rmsqv(buf.baseAddress! + aStart, 1, &rms, vDSP_Length(aEnd - aStart))
            }

            hits.append(Hit(features: features, rms: rms))
        }

        return hits
    }
}
