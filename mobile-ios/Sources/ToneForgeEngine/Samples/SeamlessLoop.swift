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
}
#endif
