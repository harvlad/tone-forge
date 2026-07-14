// ClickTrack.swift
//
// Rolling look-ahead click scheduler, trimmed port of the mobile
// Metronome (sine sound only, no subdivision). Walks the pure
// ClickGrid, schedules synthesized buffers at rate-scaled host times
// via TransportTimeMath, re-anchors after seeks/stalls.
//
// Routing note: the click player connects straight to the main mixer,
// NOT through the stem submix→timePitch. Practice-rate correctness
// comes from the rate-aware host-delay math (clicks land on the
// stretched beat grid); routing through timePitch would double-scale
// timing and tie the click level to the Song fader. This matches the
// proven mobile design.

import Foundation
import AVFoundation
import JamDesktopCore

@MainActor
public final class ClickTrack {

    static let lookAheadSec = 1.2
    static let refillIntervalSec = 0.1

    private let avEngine: AVAudioEngine
    private let clock: TransportClock

    /// Injectable for tests; production uses mach_absolute_time.
    private let hostTimeNow: () -> UInt64

    private var grid = ClickGrid(bpm: 120, beatsPerBar: 4)
    private var attached = false

    /// Next click index to schedule; nil = needs re-anchoring against
    /// the current song time (fresh start, tempo change, seek).
    private var nextClickIndex: Int?
    private var refillTask: Task<Void, Never>?

    public private(set) var isRunning = false

    private let player = AVAudioPlayerNode()
    private var accentBuffer: AVAudioPCMBuffer?
    private var normalBuffer: AVAudioPCMBuffer?

    public init(
        avEngine: AVAudioEngine,
        clock: TransportClock,
        hostTimeNow: (() -> UInt64)? = nil
    ) {
        self.avEngine = avEngine
        self.clock = clock
        self.hostTimeNow = hostTimeNow ?? { mach_absolute_time() }
    }

    /// Click level, 0…1. Default sits under the stems.
    public var volume: Float {
        get { player.volume }
        set { player.volume = max(0, min(1, newValue)) }
    }

    /// Attach the player to the engine graph (idempotent). Re-run
    /// from onGraphRebuilt via `reattach()`.
    public func attach() {
        guard !attached else { return }
        let format = avEngine.mainMixerNode.outputFormat(forBus: 0)
        avEngine.attach(player)
        avEngine.connect(player, to: avEngine.mainMixerNode, format: format)
        rebuildBuffers(format: format)
        player.volume = 0.6
        attached = true
    }

    /// Reconnect after a ConnectCore graph rebuild (device flap).
    public func reattach() {
        guard attached else { return }
        player.stop()
        let format = avEngine.mainMixerNode.outputFormat(forBus: 0)
        avEngine.connect(player, to: avEngine.mainMixerNode, format: format)
        rebuildBuffers(format: format)
        nextClickIndex = nil
    }

    private func rebuildBuffers(format: AVAudioFormat) {
        let sr = format.sampleRate
        accentBuffer = Self.makeClickBuffer(
            format: format,
            frequency: 1567.98,   // G6 — bright, cuts through stems
            amplitude: 0.85,
            durationSec: 0.05,
            sampleRate: sr
        )
        normalBuffer = Self.makeClickBuffer(
            format: format,
            frequency: 1046.50,   // C6
            amplitude: 0.65,
            durationSec: 0.04,
            sampleRate: sr
        )
    }

    /// Replace the beat grid. While running, flushes queued clicks and
    /// re-anchors on the new grid at the current song time.
    public func update(grid: ClickGrid) {
        guard grid != self.grid else { return }
        self.grid = grid
        if isRunning {
            flushQueued()
            nextClickIndex = nil
            refill()
        }
    }

    /// Begin clicking, aligned to the transport's song-time beat grid.
    /// No-op when already running or never attached.
    public func start() {
        guard attached, !isRunning else { return }
        isRunning = true
        nextClickIndex = nil
        refill()
        refillTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(
                    nanoseconds: UInt64(Self.refillIntervalSec * 1_000_000_000)
                )
                guard let self, self.isRunning else { return }
                self.refill()
            }
        }
    }

    /// Stop clicking and flush anything queued.
    public func stop() {
        guard isRunning else { return }
        isRunning = false
        refillTask?.cancel()
        refillTask = nil
        flushQueued()
        nextClickIndex = nil
    }

    /// Flush + re-anchor without stopping — call on seek so queued
    /// clicks for the old position don't fire.
    public func resync() {
        guard isRunning else { return }
        flushQueued()
        nextClickIndex = nil
        refill()
    }

    // MARK: - Scheduling

    private func flushQueued() {
        player.stop()
    }

    /// Schedule every click inside the look-ahead window that isn't
    /// scheduled yet. Re-anchors when the transport was seeked/paused
    /// out from under us (song time regressed or jumped past window).
    private func refill() {
        guard isRunning, let accent = accentBuffer, let normal = normalBuffer else { return }
        guard avEngine.isRunning else { return }
        if !player.isPlaying { player.play() }

        let nowSong = clock.nowSongSeconds
        let windowEnd = nowSong + Self.lookAheadSec

        var next = nextClickIndex ?? grid.clickIndex(onOrAfter: nowSong)
        let nextTime = grid.time(ofClickIndex: next)
        if nextTime < nowSong - 0.05 || nextTime > windowEnd + grid.secondsPerClick {
            player.stop()
            player.play()
            next = grid.clickIndex(onOrAfter: nowSong)
        }

        let hostNow = hostTimeNow()
        let ticksPerSecond = TransportClock.ticksPerSecond()
        let rate = clock.rate
        for click in grid.clicks(fromClickIndex: next, before: windowEnd) {
            let delaySec = click.timeSeconds - nowSong
            // Clicks effectively "now" play immediately (at: nil). The
            // delay is rate-scaled (practice speed); staleness keys
            // off the unscaled song-time delta.
            var at: AVAudioTime? = nil
            if let ticks = TransportTimeMath.hostDelayTicks(
                targetSong: click.timeSeconds, nowSong: nowSong,
                rate: rate, ticksPerSecond: ticksPerSecond
            ) {
                at = AVAudioTime(hostTime: hostNow &+ ticks)
            } else if delaySec < -0.05 {
                next += 1
                continue // stale — don't burst-fire missed clicks
            }
            player.scheduleBuffer(click.isAccent ? accent : normal, at: at, options: [])
            next += 1
        }
        nextClickIndex = next
    }

    // MARK: - Click synthesis

    /// Short sine burst with a squared-decay envelope + 2 ms attack
    /// ramp (kills the onset discontinuity click-on-the-click).
    private static func makeClickBuffer(
        format: AVAudioFormat,
        frequency: Double,
        amplitude: Float,
        durationSec: Double,
        sampleRate: Double
    ) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(durationSec * sampleRate)
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let channels = buffer.floatChannelData
        else { return nil }
        buffer.frameLength = frames

        let attackFrames = max(1, Int(0.002 * sampleRate))
        let total = Int(frames)
        for frame in 0..<total {
            let t = Double(frame) / sampleRate
            let sine = sin(2.0 * .pi * frequency * t)
            let decay = 1.0 - Double(frame) / Double(total)
            let attack = min(1.0, Double(frame) / Double(attackFrames))
            let sample = Float(sine * decay * decay * attack) * amplitude
            for ch in 0..<Int(format.channelCount) {
                channels[ch][frame] = sample
            }
        }
        return buffer
    }
}
