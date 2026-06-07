//
// LatencyProbe.swift
//
// Measures real audio round-trip latency by emitting a brief click on the
// output and locating it in the captured input stream. The user is expected
// to either:
//   - place the laptop mic near the speakers,
//   - use a loopback cable (interface output → input), or
//   - use an interface's built-in software loopback.
//
// The probe returns the latency in milliseconds (delta between the
// "play" host time and the impulse peak's host time).
//

import AVFoundation
import Foundation

public final class LatencyProbe {

    public struct Result {
        public let roundTripMs: Double
        public let inputPeakAmplitude: Float
        public let confidence: String  // "high" | "low" | "no_signal"
    }

    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let sampleRate: Double

    public init(sampleRate: Double = 48000.0) {
        self.sampleRate = sampleRate
        engine.attach(player)
        let format = engine.outputNode.outputFormat(forBus: 0)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    /// Run a probe. Blocks the calling thread for ~1.5 seconds.
    public func run() throws -> Result {
        let format = engine.inputNode.outputFormat(forBus: 0)
        let captureFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: format.sampleRate,
            channels: 1,
            interleaved: false
        )!

        // 1-second capture buffer
        let captureSamples = Int(format.sampleRate)
        var captured = [Float](repeating: 0, count: captureSamples)
        var captureIndex = 0
        let captureLock = NSLock()

        engine.inputNode.installTap(
            onBus: 0,
            bufferSize: 1024,
            format: captureFormat
        ) { buffer, _ in
            guard let channel = buffer.floatChannelData?[0] else { return }
            let frames = Int(buffer.frameLength)
            captureLock.lock()
            let space = captured.count - captureIndex
            let n = min(frames, space)
            for i in 0..<n {
                captured[captureIndex + i] = abs(channel[i])
            }
            captureIndex += n
            captureLock.unlock()
        }

        engine.prepare()
        try engine.start()

        // Build a brief 1 kHz tone burst (~5 ms) — narrow enough to peak,
        // wide enough to survive a noisy mic.
        let burstFrames = AVAudioFrameCount(format.sampleRate * 0.005)
        guard let burst = AVAudioPCMBuffer(
            pcmFormat: format,
            frameCapacity: burstFrames
        ) else {
            engine.inputNode.removeTap(onBus: 0)
            engine.stop()
            throw NSError(domain: "LatencyProbe", code: 1)
        }
        burst.frameLength = burstFrames
        if let ch = burst.floatChannelData?[0] {
            for i in 0..<Int(burstFrames) {
                let phase = 2.0 * .pi * 1000.0 * Double(i) / format.sampleRate
                ch[i] = Float(sin(phase)) * 0.5
            }
        }

        // Schedule a single playback ~200 ms into the future and remember
        // the host time we asked for it.
        let outputLatencyHostTicks = AVAudioTime.hostTime(forSeconds: 0.2)
        let playAt = AVAudioTime(
            hostTime: mach_absolute_time() + outputLatencyHostTicks
        )
        player.scheduleBuffer(burst, at: playAt, options: [], completionHandler: nil)
        player.play()

        // Wait long enough to capture the click + reverb tail.
        Thread.sleep(forTimeInterval: 1.0)

        player.stop()
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()

        // Find the first sample above a threshold relative to the buffer's
        // own noise floor. Compute noise floor as mean of the first 20 ms.
        captureLock.lock()
        let buf = captured
        let validSamples = captureIndex
        captureLock.unlock()

        guard validSamples > Int(format.sampleRate * 0.05) else {
            return Result(roundTripMs: 0, inputPeakAmplitude: 0, confidence: "no_signal")
        }
        let noiseWindow = min(Int(format.sampleRate * 0.02), validSamples)
        var noiseSum: Float = 0
        for i in 0..<noiseWindow { noiseSum += buf[i] }
        let noiseFloor = noiseSum / Float(noiseWindow)
        let threshold = max(noiseFloor * 8.0, 0.02)

        var firstHit = -1
        var peakAmp: Float = 0
        for i in noiseWindow..<validSamples {
            if buf[i] > threshold {
                firstHit = i
                peakAmp = buf[i]
                // Scan a small window for the actual peak.
                let scanEnd = min(i + Int(format.sampleRate * 0.005), validSamples)
                for j in i..<scanEnd {
                    if buf[j] > peakAmp { peakAmp = buf[j] }
                }
                break
            }
        }

        if firstHit < 0 {
            return Result(roundTripMs: 0, inputPeakAmplitude: 0, confidence: "no_signal")
        }

        // The capture buffer began at engine start. The burst was scheduled
        // 200 ms after start, so input sample index `i` corresponds to time
        // (i / sr) since start; subtract the scheduled output offset to get
        // the round-trip.
        let secondsSinceStart = Double(firstHit) / format.sampleRate
        let roundTripSec = secondsSinceStart - 0.2
        let confidence = roundTripSec > 0.001 && roundTripSec < 0.5 ? "high" : "low"

        return Result(
            roundTripMs: roundTripSec * 1000.0,
            inputPeakAmplitude: peakAmp,
            confidence: confidence
        )
    }
}
