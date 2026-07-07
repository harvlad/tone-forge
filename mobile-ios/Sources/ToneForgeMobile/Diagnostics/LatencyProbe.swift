// LatencyProbe.swift
//
// P7 ship-gate measurements, one per hard gate:
//
//   pad-tap → attack   ≤ 8 ms    (touch grid, live trigger path)
//   Launchpad → attack ≤ 12 ms   (adds the MIDI-thread → main hop)
//   session load       ≤ 2 s     (1000-event take, disk → decoded)
//   mic → playable pad ≤ 5 s     (process → classify → save →
//                                 assign → buffer resident)
//   vocoder capture    0 dropouts (scripted 8 s classic-mode take,
//                                 preview-ring underrun counter)
//
// Methodology for the two attack gates: the probe publishes real
// ContributionEvents on the bus against a temporary local sample and
// spies on `SampleScheduler.transformResolver` — the last main-actor
// call before `pool.trigger` schedules the voice — so the measured
// span covers bus fan-out, ModeRouter, coordinator execute and the
// scheduler up to the point the buffer is handed to the engine. The
// output chain (IO buffer + reported output latency) is added on top
// since the scheduled voice still waits for the next render cycle.
// The Launchpad variant stamps `hostTime` OFF the main actor first,
// exactly like the CoreMIDI transport does pre-hop, so the actor hop
// is inside the measured span. 20 iterations, median.
//
// All probes clean up after themselves: the temporary sample, its
// pad assignment, the synthetic session and any hijacked seams are
// restored on every exit path.

@preconcurrency import AVFAudio
import Foundation
import ToneForgeEngine

@MainActor
public final class LatencyProbe: ObservableObject {

    // MARK: - Gates

    public enum Gate: String, CaseIterable, Identifiable, Sendable {
        case padTap, launchpad, sessionLoad, micPipeline, vocoderCapture

        public var id: String { rawValue }

        public var title: String {
            switch self {
            case .padTap:         return "Pad tap → attack"
            case .launchpad:      return "Launchpad → attack"
            case .sessionLoad:    return "Session load"
            case .micPipeline:    return "Mic → playable pad"
            case .vocoderCapture: return "Vocoder capture dropouts"
            }
        }

        /// Budget in the gate's own unit (ms, or underruns for the
        /// vocoder gate).
        public var budget: Double {
            switch self {
            case .padTap:         return 8
            case .launchpad:      return 12
            case .sessionLoad:    return 2_000
            case .micPipeline:    return 5_000
            case .vocoderCapture: return 0
            }
        }

        public var unit: String {
            self == .vocoderCapture ? "underruns" : "ms"
        }
    }

    public struct Reading: Equatable, Sendable {
        public enum Status: Equatable, Sendable {
            case passed
            case failed
            /// Not measurable here (e.g. mic permission denied) —
            /// the associated string says why.
            case skipped(String)
        }
        public let measured: Double
        public let detail: String
        public let status: Status
    }

    // MARK: - State

    @Published public private(set) var readings: [Gate: Reading] = [:]
    @Published public private(set) var isRunning = false

    private let app: AppState

    /// Grid pad the probe borrows: raw 18 = row 1, col 8 (bottom-
    /// right). Whatever the user had there is saved and restored.
    static let probePadRaw = 18
    private static let iterations = 20

    public init(app: AppState) {
        self.app = app
    }

    // MARK: - Entry points

    public func run(_ gate: Gate) async {
        guard !isRunning else { return }
        isRunning = true
        defer { isRunning = false }
        readings[gate] = await measure(gate)
    }

    public func runAll() async {
        guard !isRunning else { return }
        isRunning = true
        defer { isRunning = false }
        for gate in Gate.allCases {
            readings[gate] = await measure(gate)
        }
    }

    /// Plain-text summary for the copy button.
    public func summary() -> String {
        Gate.allCases.map { gate in
            let budget = "budget \(Self.format(gate.budget)) \(gate.unit)"
            guard let r = readings[gate] else {
                return "\(gate.title): not run (\(budget))"
            }
            let verdict: String
            switch r.status {
            case .passed:           verdict = "PASS"
            case .failed:           verdict = "FAIL"
            case .skipped(let why): verdict = "SKIPPED — \(why)"
            }
            return "\(gate.title): \(Self.format(r.measured)) \(gate.unit)"
                + " (\(budget)) \(verdict)"
        }.joined(separator: "\n")
    }

    static func format(_ v: Double) -> String {
        v == v.rounded() && abs(v) < 10_000
            ? String(format: "%.0f", v)
            : String(format: "%.2f", v)
    }

    private func measure(_ gate: Gate) async -> Reading {
        switch gate {
        case .padTap:
            return await measureTapPath(source: .touch, gate: .padTap)
        case .launchpad:
            return await measureTapPath(source: .launchpad, gate: .launchpad)
        case .sessionLoad:
            return measureSessionLoad()
        case .micPipeline:
            return await measureMicPipeline()
        case .vocoderCapture:
            return await measureVocoderCapture()
        }
    }

    // MARK: - Attack gates

    private func measureTapPath(
        source: ContributionEvent.Source, gate: Gate
    ) async -> Reading {
        let ctx = beginPadContext()
        defer { endPadContext(ctx) }

        guard await ensureProbeSample(ctx) else {
            return Reading(
                measured: 0,
                detail: "could not stage the probe sample",
                status: .skipped("probe sample failed to load")
            )
        }

        // Spy on the resolver seam — the last stop before
        // pool.trigger. Chains to the real resolver so any user
        // transform state is untouched.
        let chained = app.sampleScheduler.transformResolver
        defer { app.sampleScheduler.transformResolver = chained }
        var resolverHost: UInt64 = 0
        app.sampleScheduler.transformResolver = { buffer, packId, padIdx in
            resolverHost = mach_absolute_time()
            return chained?(buffer, packId, padIdx) ?? buffer
        }

        var deltasMs: [Double] = []
        for _ in 0..<Self.iterations {
            resolverHost = 0
            let stamp: UInt64
            if source == .launchpad {
                // Stamp OFF the main actor (the CoreMIDI transport
                // stamps on the MIDI thread pre-hop); the hop back
                // into the actor is part of the measured span.
                stamp = await Task.detached(priority: .userInitiated) {
                    mach_absolute_time()
                }.value
            } else {
                stamp = mach_absolute_time()
            }
            publish(source: source, down: true, hostTime: stamp)
            if resolverHost > stamp {
                deltasMs.append(Self.ms(resolverHost - stamp))
            }
            publish(source: source, down: false,
                    hostTime: mach_absolute_time())
        }

        guard !deltasMs.isEmpty else {
            return Reading(
                measured: 0,
                detail: "no trigger reached the scheduler",
                status: .failed
            )
        }
        let median = deltasMs.sorted()[deltasMs.count / 2]
        let chain = Self.outputChainMs()
        let measured = median + chain
        return Reading(
            measured: measured,
            detail: String(
                format: "median of %d taps: %.2f ms path + %.2f ms output chain",
                deltasMs.count, median, chain
            ),
            status: measured <= gate.budget ? .passed : .failed
        )
    }

    private func publish(
        source: ContributionEvent.Source, down: Bool, hostTime: UInt64
    ) {
        // probePadRaw 18 → row 1, col 8.
        app.contributionBus.publish(ContributionEvent(
            source: source,
            kind: down ? .padDown(row: 1, col: 8) : .padUp(row: 1, col: 8),
            timestamp: app.audioEngine.clock.nowSongSeconds,
            hostTime: hostTime
        ))
    }

    /// Scheduled voices still wait for the next render cycle plus the
    /// route's reported output latency. Zero off-device: macOS test
    /// hosts have no AVAudioSession, and the simulator reports the
    /// host Mac's audio-stack latency — not the hardware claim the
    /// budgets gate (docs/mobile-testing.md: run on the device).
    private static func outputChainMs() -> Double {
        #if os(iOS) && !targetEnvironment(simulator)
        let session = AVAudioSession.sharedInstance()
        return (session.ioBufferDuration + session.outputLatency) * 1_000
        #else
        return 0
        #endif
    }

    // MARK: - Session-load gate

    private func measureSessionLoad() -> Reading {
        // 1000 pad events + a full 64-pad mapping — a heavier take
        // than any real performance we've captured.
        var events: [ContributionEvent] = []
        events.reserveCapacity(2_000)
        for i in 0..<1_000 {
            let t = Double(i) * 0.05
            let row = 1 + (i % 8), col = 1 + ((i / 8) % 8)
            events.append(ContributionEvent(
                source: .touch, kind: .padDown(row: row, col: col),
                timestamp: t, hostTime: 0))
            events.append(ContributionEvent(
                source: .touch, kind: .padUp(row: row, col: col),
                timestamp: t + 0.02, hostTime: 0))
        }
        var mapping: [PadAddress: PadSampleReference] = [:]
        for row in 1...8 {
            for col in 1...8 {
                let raw = row * 10 + col
                mapping[PadAddress(mode: .sample, pad: PadIndex(raw))] =
                    .packPad(packId: "probe", padIdx: raw)
            }
        }
        let session = SessionCapture(
            sessionId: UUID(), songBackendId: nil, appMode: .sample,
            capturedAt: Date(), tempoBpm: 120,
            events: events, padMapping: mapping
        )
        do {
            try app.sessionStore.save(session)
        } catch {
            return Reading(
                measured: 0,
                detail: "could not stage the probe session",
                status: .skipped(error.localizedDescription)
            )
        }
        defer { try? app.sessionStore.delete(sessionId: session.sessionId) }

        let t0 = mach_absolute_time()
        let loaded = try? app.sessionStore.load(sessionId: session.sessionId)
        let measured = Self.ms(mach_absolute_time() - t0)
        guard loaded?.events.count == events.count else {
            return Reading(
                measured: measured,
                detail: "probe session decoded incompletely",
                status: .failed
            )
        }
        return Reading(
            measured: measured,
            detail: "1000-event take, disk → decoded",
            status: measured <= Gate.sessionLoad.budget ? .passed : .failed
        )
    }

    // MARK: - Mic-pipeline gate

    private func measureMicPipeline() async -> Reading {
        let ctx = beginPadContext()
        defer { endPadContext(ctx) }

        // The 8 s cap makes the capture itself real-time by
        // definition; the gate covers everything AFTER the take
        // lands: condition → classify → save → assign → resident.
        let tone = Self.probeTone()
        let t0 = mach_absolute_time()
        do {
            ctx.sampleId = try await app.modeCoordinator.saveMicCapture(
                tone, toGridPad: Self.probePadRaw
            ).id
        } catch {
            return Reading(
                measured: 0,
                detail: "pipeline threw",
                status: .skipped(error.localizedDescription)
            )
        }
        let resident = await waitUntil(timeoutSec: 6) {
            self.app.sampleScheduler
                .localMetadata(for: Self.probePadRaw) != nil
        }
        let measured = Self.ms(mach_absolute_time() - t0)
        guard resident else {
            return Reading(
                measured: measured,
                detail: "buffer never became resident",
                status: .failed
            )
        }
        return Reading(
            measured: measured,
            detail: "2 s take: process → classify → save → assign → resident",
            status: measured <= Gate.micPipeline.budget ? .passed : .failed
        )
    }

    // MARK: - Vocoder gate

    private func measureVocoderCapture() async -> Reading {
        let capture = app.vocoderCapture
        guard !capture.isCapturing else {
            return Reading(
                measured: 0,
                detail: "a vocoder capture is already running",
                status: .skipped("capture in progress")
            )
        }
        let program = await app.modeCoordinator.vocoderProgram(for: .classic)
        // Silence the auto-stop handler for the scripted run — the
        // 8 s take is measurement fodder, never saved.
        let savedAutoStop = capture.onAutoStop
        capture.onAutoStop = nil
        defer { capture.onAutoStop = savedAutoStop }
        do {
            try await capture.start(program: program)
        } catch {
            return Reading(
                measured: 0,
                detail: "requires a device with microphone access",
                status: .skipped(error.localizedDescription)
            )
        }
        // The 8 s cap auto-stops the capture; poll with headroom.
        let stopped = await waitUntil(timeoutSec: 12) {
            !capture.isCapturing
        }
        guard stopped else {
            capture.cancel()
            return Reading(
                measured: 0,
                detail: "capture did not auto-stop",
                status: .failed
            )
        }
        let underruns = Double(capture.underrunCount)
        return Reading(
            measured: underruns,
            detail: "scripted 8 s classic-mode capture",
            status: underruns == 0 ? .passed : .failed
        )
    }

    // MARK: - Probe pad staging

    /// Mutable probe context: everything borrowed from the live app
    /// that must be handed back on exit, whatever the path.
    private final class PadContext {
        let savedMode: AppMode
        let savedSlot: PadSlot?
        let savedSections: Set<String>?
        var sampleId: UUID?
        init(savedMode: AppMode, savedSlot: PadSlot?,
             savedSections: Set<String>?) {
            self.savedMode = savedMode
            self.savedSlot = savedSlot
            self.savedSections = savedSections
        }
    }

    private func beginPadContext() -> PadContext {
        let ctx = PadContext(
            savedMode: app.modeCoordinator.appMode,
            savedSlot: app.padAssignmentStore.slot(
                mode: .sample, padIdx: Self.probePadRaw
            ),
            savedSections: app.sampleScheduler.allowedSections
        )
        if ctx.savedMode != .sample {
            app.modeCoordinator.setMode(.sample)
        }
        // Section gates could swallow probe triggers outright.
        app.sampleScheduler.allowedSections = nil
        return ctx
    }

    private func endPadContext(_ ctx: PadContext) {
        if let id = ctx.sampleId {
            app.modeCoordinator.deleteLocalSample(id: id)
        }
        if let slot = ctx.savedSlot {
            app.padAssignmentStore.assign(
                slot, mode: .sample, padIdx: Self.probePadRaw
            )
        }
        app.modeCoordinator.syncLocalBuffers()
        app.sampleScheduler.allowedSections = ctx.savedSections
        if app.modeCoordinator.appMode != ctx.savedMode {
            app.modeCoordinator.setMode(ctx.savedMode)
        }
        app.modeCoordinator.refreshLayout()
    }

    /// Stage the probe tone on the probe pad through the REAL mic
    /// pipeline (unmeasured here — the mic gate times its own run).
    private func ensureProbeSample(_ ctx: PadContext) async -> Bool {
        guard ctx.sampleId == nil else { return true }
        do {
            ctx.sampleId = try await app.modeCoordinator.saveMicCapture(
                Self.probeTone(), toGridPad: Self.probePadRaw
            ).id
        } catch {
            return false
        }
        return await waitUntil(timeoutSec: 6) {
            self.app.sampleScheduler
                .localMetadata(for: Self.probePadRaw) != nil
        }
    }

    /// 2 s decaying 440 Hz tone at the canonical rate — loud enough
    /// to survive the RMS trim, pitched enough to classify.
    static func probeTone() -> [Float] {
        let rate = AudioEngine.canonicalSampleRate
        let frames = Int(2.0 * rate)
        var out = [Float](repeating: 0, count: frames)
        for i in 0..<frames {
            let t = Double(i) / rate
            let env = exp(-t * 1.5)
            out[i] = Float(sin(2 * .pi * 440 * t) * 0.5 * env)
        }
        return out
    }

    // MARK: - Timing helpers

    private static let timebase: mach_timebase_info_data_t = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        return info
    }()

    static func ms(_ hostDelta: UInt64) -> Double {
        Double(hostDelta) * Double(timebase.numer)
            / Double(timebase.denom) / 1_000_000
    }

    private func waitUntil(
        timeoutSec: Double, _ condition: () -> Bool
    ) async -> Bool {
        let deadline = Date().addingTimeInterval(timeoutSec)
        while Date() < deadline {
            if condition() { return true }
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        return condition()
    }
}
