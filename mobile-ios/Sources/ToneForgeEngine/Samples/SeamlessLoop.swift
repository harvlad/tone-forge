// SeamlessLoop.swift  (ToneForgeEngine)
//
// Turn a loop-region buffer into one that loops with NO audible seam.
//
// A hard buffer loop (AVAudioPlayerNodeBufferOptions.loops) plays the last
// sample then jumps straight back to the first — any amplitude/phase mismatch
// at that boundary is an audible click. The Performance-Intelligence loop
// scorer measures that seam and proposes a crossfade length; this applies it.
//
// Technique: equal-power overlap-add. Build a buffer of length (N - x) where the
// first x frames are a crossfade of the region's HEAD (fading in) with its TAIL
// (fading out). Looping the result is continuous: its end is the pre-crossfade
// tail, its start already blends into that tail. Pure DSP — unit-testable.

#if canImport(AVFoundation)
import AVFoundation

public enum SeamlessLoop {

    /// Default seam crossfade (ms) for a looping voice that carries no
    /// measured `loopScore`-derived length. A hard buffer loop clicks at
    /// the wrap; a short equal-power overlap hides it. 12 ms is long
    /// enough to mask a boundary mismatch yet short enough to stay
    /// rhythmically tight on a bar-synced loop.
    public static let defaultLoopCrossfadeMs: Double = 12.0

    /// Ramp the first `attackMs` and last `releaseMs` of `buf` in place so
    /// a one-shot's start and tail don't begin/end on a non-zero sample —
    /// the click you hear on stabs, drum hits, and the first pass of a
    /// loop whose slice boundary isn't a zero-crossing. Linear ramps are
    /// inaudible at a few ms but kill the DC step. No-op on silent/short
    /// buffers. Safe before a loop crossfade: the fade regions (a few ms)
    /// are far shorter than the default seam crossfade, so the seam math
    /// is unaffected.
    public static func applyEdgeFades(
        _ buf: AVAudioPCMBuffer, attackMs: Double = 3.0, releaseMs: Double = 5.0
    ) {
        let n = Int(buf.frameLength)
        let sr = buf.format.sampleRate
        guard n > 8, sr > 0, let data = buf.floatChannelData else { return }
        var a = Int((attackMs / 1000.0) * sr)
        var r = Int((releaseMs / 1000.0) * sr)
        // Never overlap the two ramps, and always leave a body sample.
        a = max(0, min(a, (n - 1) / 2))
        r = max(0, min(r, (n - 1) / 2))
        let channels = Int(buf.format.channelCount)
        for c in 0..<channels {
            let d = data[c]
            if a > 0 {
                for i in 0..<a { d[i] *= Float(i) / Float(a) }
            }
            if r > 0 {
                for i in 0..<r { d[n - 1 - i] *= Float(i) / Float(r) }
            }
        }
    }

    /// A crossfaded, seamlessly-loopable copy of `src`.
    ///
    /// - Parameters:
    ///   - src: the loop-region buffer (already sliced to [loopStart, loopEnd]).
    ///   - crossfadeMs: seam crossfade length; clamped to at most half the
    ///     region. `<= 0` returns `src` unchanged (caller may still hard-loop).
    /// - Returns: a new buffer of length `src.frameLength - xfadeFrames`, or
    ///   `src` when a crossfade can't help (too short / no data).
    public static func crossfaded(_ src: AVAudioPCMBuffer, crossfadeMs: Double) -> AVAudioPCMBuffer {
        let n = Int(src.frameLength)
        let sr = src.format.sampleRate
        guard crossfadeMs > 0, n > 8, sr > 0,
              let srcData = src.floatChannelData else { return src }

        var x = Int((crossfadeMs / 1000.0) * sr)
        x = max(1, min(x, n / 2 - 1))
        let outFrames = n - x
        guard outFrames > 1,
              let out = AVAudioPCMBuffer(pcmFormat: src.format, frameCapacity: AVAudioFrameCount(outFrames))
        else { return src }
        out.frameLength = AVAudioFrameCount(outFrames)
        guard let dst = out.floatChannelData else { return src }

        let channels = Int(src.format.channelCount)
        for c in 0..<channels {
            let s = srcData[c]
            let d = dst[c]
            // Crossfade region [0, x): head fades in, tail fades out (equal power).
            for i in 0..<x {
                let t = Float(i) / Float(x)                 // 0 → 1
                let head = s[i]
                let tail = s[n - x + i]
                let gIn = sinf(0.5 * .pi * t)               // equal-power in
                let gOut = cosf(0.5 * .pi * t)              // equal-power out
                d[i] = head * gIn + tail * gOut
            }
            // Body [x, n - x): copy straight through.
            for i in x..<outFrames {
                d[i] = s[i]
            }
        }
        return out
    }

    /// EXACT-LENGTH seamless-loop bake. `crossfaded(_:crossfadeMs:)` above
    /// returns `n - x` frames, so a hard-looped result plays a period 8–30 ms
    /// SHORT of the bar-snapped region — held loops drift against the
    /// loop-lock grid and against each other (each pad trims a different x).
    /// The jamn Kit plugin fixed the same bug with a runtime dual-read ("the
    /// old baked seam trimmed the buffer and made every wrap skip"); this is
    /// the baked-buffer equivalent for AVAudioPlayerNode `.loops`.
    ///
    /// `src` holds the loop region plus optional CONTINUATION audio: extra
    /// frames read past the region's end from the same source (`loopFrames`
    /// marks the split; `src.frameLength - loopFrames` are continuation).
    /// The seam is baked into the head at full length: out[0..<x) is an
    /// equal-power blend of the continuation (fading out) into the head
    /// (fading in). Every wrap is then continuous — the tail flows into
    /// audio that genuinely followed it in the source — and the loop period
    /// stays EXACTLY `loopFrames`.
    ///
    /// Without continuation frames (region ends at the source's end, or the
    /// caller couldn't supply them) it falls back to equal-power edge ramps
    /// at exact length: a brief level dip at the seam instead of a click,
    /// but never a shortened period.
    ///
    /// - Parameters:
    ///   - src: loop region + continuation frames (if any) in one buffer.
    ///   - loopFrames: the loop body length; the returned buffer is exactly
    ///     this long (clamped to `src.frameLength`).
    ///   - crossfadeMs: seam crossfade length; clamped to at most half the
    ///     loop body and to the available continuation.
    /// - Returns: a new buffer of exactly `loopFrames` frames, or `src`
    ///   unchanged when the input is degenerate (too short / no data).
    /// Frame shift (within ±searchFrames) that puts `centerFrame` a small
    /// preroll BEFORE the strongest energy rise near it. 0 when no clear
    /// transient exists (sustained material must not be nudged).
    ///
    /// Why: the beat grid's downbeat timestamps land a few tens of ms
    /// AFTER the audible drum attack (tracker phase vs perceptual onset),
    /// so a grid-cut loop region starts just past its own kick — the wrap
    /// plays tail → kickless head and the loop audibly pauses even when
    /// the period is exact. Snapping the cut just ahead of the attack
    /// keeps the hit inside the loop; both region edges shift together so
    /// the period is untouched.
    public static func onsetAlignedShift(
        _ scan: AVAudioPCMBuffer, centerFrame: Int,
        searchFrames: Int, prerollFrames: Int
    ) -> Int {
        let n = Int(scan.frameLength)
        let sr = scan.format.sampleRate
        guard n > 0, sr > 0, searchFrames > 0,
              let ch0 = scan.floatChannelData?.pointee else { return 0 }
        let lo = max(0, centerFrame - searchFrames)
        let hi = min(n, centerFrame + searchFrames)
        let hop = max(32, Int(0.002 * sr))
        guard hi - lo > hop * 4 else { return 0 }
        var env: [Float] = []
        var i = lo
        while i + hop <= hi {
            var e: Float = 0
            for j in i..<(i + hop) { e += ch0[j] * ch0[j] }
            env.append((e / Float(hop)).squareRoot())
            i += hop
        }
        guard env.count > 2 else { return 0 }
        var rises: [Float] = []
        for k in 1..<env.count { rises.append(max(0, env[k] - env[k - 1])) }
        guard let maxRise = rises.max(), maxRise > 1e-4 else { return 0 }
        let sorted = rises.sorted()
        let median = sorted[sorted.count / 2]
        guard maxRise > 2 * median else { return 0 }
        let best = rises.firstIndex(of: maxRise) ?? 0
        // rises[k] describes the step INTO env window k+1.
        let onsetFrame = lo + (best + 1) * hop
        let shift = (onsetFrame - prerollFrames) - centerFrame
        return max(-searchFrames, min(searchFrames, shift))
    }

    public static func exactCrossfaded(
        _ src: AVAudioPCMBuffer, loopFrames: Int, crossfadeMs: Double
    ) -> AVAudioPCMBuffer {
        let total = Int(src.frameLength)
        let n = min(loopFrames, total)
        let sr = src.format.sampleRate
        guard n > 8, sr > 0, let srcData = src.floatChannelData,
              let out = AVAudioPCMBuffer(
                  pcmFormat: src.format, frameCapacity: AVAudioFrameCount(n)),
              let dst = out.floatChannelData
        else { return src }
        out.frameLength = AVAudioFrameCount(n)

        var x = Int((max(0, crossfadeMs) / 1000.0) * sr)
        x = max(1, min(x, n / 2 - 1))
        let continuation = total - n
        let channels = Int(src.format.channelCount)
        // Transient-aware fade: when the loop head IS an attack (kick/snare
        // on the downbeat — energy front-loaded in the first 10 ms), a long
        // equal-power blend plays that attack at reduced gain every pass:
        // an audible energy dip at the wrap ("in time but not seamless").
        // Percussive heads get a ~3 ms declick instead; sustained heads
        // keep the full requested fade.
        if let ch0 = src.floatChannelData?.pointee {
            let a = Int(0.010 * sr), b = Int(0.060 * sr)
            if b <= n {
                var e0: Float = 0, e1: Float = 0
                for i in 0..<a { e0 += ch0[i] * ch0[i] }
                for i in a..<b { e1 += ch0[i] * ch0[i] }
                let rms0 = (e0 / Float(a)).squareRoot()
                let rms1 = (e1 / Float(b - a)).squareRoot()
                if rms0 > 2 * rms1, rms0 > 1e-4 {
                    x = max(1, min(x, Int(0.003 * sr)))
                }
            }
        }
        for c in 0..<channels {
            let s = srcData[c]
            let d = dst[c]
            // Exact-length body first; the seam only rewrites the head.
            d.update(from: s, count: n)
            if continuation > 0 {
                // Baked seam: continuation (what really follows the loop's
                // end) fades out while the head fades back in.
                let xe = min(x, continuation)
                for i in 0..<xe {
                    let t = Float(i) / Float(xe)
                    let gIn = sinf(0.5 * .pi * t)
                    let gOut = cosf(0.5 * .pi * t)
                    d[i] = s[i] * gIn + s[n + i] * gOut
                }
            } else {
                // No continuation available: equal-power edge ramps. The
                // wrap meets at ~zero on both sides — dip, not click.
                for i in 0..<x {
                    let t = Float(i) / Float(x)
                    d[i] *= sinf(0.5 * .pi * t)          // head fade-in
                    d[n - x + i] *= cosf(0.5 * .pi * t)  // tail fade-out
                }
            }
        }
        return out
    }

    /// Tile an already-seam-baked loop body up to `targetFrames` by repeating
    /// it — the shared-cycle lock: a short loop repeats INSIDE the common cycle
    /// so every latched pad shares ONE period and stays in unison instead of
    /// running on its own section length and drifting off the others. `src` is
    /// the output of `exactCrossfaded`, so `i % srcFrames` is continuous at
    /// every body wrap; only the final cycle wrap (targetFrames → 0) can land
    /// off a body boundary when the body doesn't divide the cycle evenly — one
    /// seam per cycle, the accepted trade for guaranteed phase-lock. The
    /// longest pad already fills the cycle (targetFrames == body → no tiling).
    /// Returns `src` unchanged for degenerate args (target <= body / no data).
    /// Swift twin of padengine.js `tileChannels()` (web commit c726ba58).
    public static func tileToLength(
        _ src: AVAudioPCMBuffer, targetFrames: Int
    ) -> AVAudioPCMBuffer {
        let srcFrames = Int(src.frameLength)
        guard srcFrames > 0, targetFrames > srcFrames,
              let srcData = src.floatChannelData,
              let out = AVAudioPCMBuffer(
                  pcmFormat: src.format,
                  frameCapacity: AVAudioFrameCount(targetFrames)),
              let dst = out.floatChannelData
        else { return src }
        out.frameLength = AVAudioFrameCount(targetFrames)
        let channels = Int(src.format.channelCount)
        for c in 0..<channels {
            let s = srcData[c]
            let d = dst[c]
            for i in 0..<targetFrames { d[i] = s[i % srcFrames] }
        }
        return out
    }
}
#endif
