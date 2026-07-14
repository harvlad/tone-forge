// MicListener.swift
//
// Input-level tap on the shared engine's input node: computes
// peak/rms dBFS per buffer and rate-limits callbacks to ~20 Hz
// (the input_meter cadence PresetBridge expects callers to hold).
//
// Web parity note: jam.js's rehearsal mic-verify does all analysis
// locally (WebAudio analyser) — nothing is posted to the server.
// Pitch detection for practice verification lands with the mastery
// machine (post-M4); this class deliberately stops at levels.

import Foundation
import AVFoundation

public final class MicListener: @unchecked Sendable {

    /// Silence floor for log conversion.
    public static let floorDbfs: Double = -120

    /// (peakDbfs, rmsDbfs) at most every `minInterval` seconds.
    /// Fired from the audio tap thread — hop actors in the handler.
    public var onLevels: ((Double, Double) -> Void)?

    private let minInterval: TimeInterval
    private let lock = NSLock()
    private var lastEmit: Date = .distantPast
    private weak var tappedEngine: AVAudioEngine?
    private var tapInstalled = false

    public init(minInterval: TimeInterval = 0.05) {
        self.minInterval = minInterval
    }

    deinit { stop() }

    /// Installs the tap on the engine's input node. Call again after
    /// a graph rebuild — taps don't survive reconfiguration.
    public func start(on engine: AVAudioEngine) {
        stop()
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { return }
        input.installTap(onBus: 0, bufferSize: 2048, format: format) { [weak self] buffer, _ in
            self?.process(buffer)
        }
        tappedEngine = engine
        tapInstalled = true
    }

    public func stop() {
        if tapInstalled, let engine = tappedEngine {
            engine.inputNode.removeTap(onBus: 0)
        }
        tapInstalled = false
        tappedEngine = nil
    }

    private func process(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let due = Date().timeIntervalSince(lastEmit) >= minInterval
        if due { lastEmit = Date() }
        lock.unlock()
        guard due, let handler = onLevels else { return }

        guard let channel = buffer.floatChannelData?[0] else { return }
        let samples = UnsafeBufferPointer(start: channel, count: Int(buffer.frameLength))
        let (peak, rms) = Self.levels(samples: Array(samples))
        handler(peak, rms)
    }

    /// Pure: peak/rms in dBFS, clamped to the silence floor.
    public static func levels(samples: [Float]) -> (peakDbfs: Double, rmsDbfs: Double) {
        guard !samples.isEmpty else { return (floorDbfs, floorDbfs) }
        var peak: Float = 0
        var sumSquares: Double = 0
        for sample in samples {
            let magnitude = abs(sample)
            if magnitude > peak { peak = magnitude }
            sumSquares += Double(sample) * Double(sample)
        }
        let rms = (sumSquares / Double(samples.count)).squareRoot()
        return (dbfs(Double(peak)), dbfs(rms))
    }

    private static func dbfs(_ linear: Double) -> Double {
        guard linear > 0 else { return floorDbfs }
        return max(floorDbfs, 20 * log10(linear))
    }
}
