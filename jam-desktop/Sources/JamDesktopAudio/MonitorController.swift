// MonitorController.swift
//
// Monitor/tone control surface over the shared ConnectCore
// AudioEngine, and the glue between the engine and the session
// bridge. Desktop genuinely owns audio, so it emits
// connect_state / latency_report / input_meter exactly like the
// standalone Connect.app, and applies inbound browser intents
// (apply_chain / set_gain / measure_latency).
//
// All engine work stays in ConnectCore (passthrough, DSP chain,
// latency probe); this class only maps between engine types and
// BridgeFrames — the mapping functions are static + pure so
// JamDesktopAudioTests exercises them without starting audio.

import Foundation
import Observation
import ConnectCore
import JamDesktopCore

@Observable
@MainActor
public final class MonitorController {

    private let engine: AudioEngine
    private let micListener: MicListener

    /// Mirrors for SwiftUI (engine properties aren't observable).
    public private(set) var activeChainId: String?
    public private(set) var latestLatency: LatencyReportFrame?
    public private(set) var engineStateName: String = "stopped"

    public var monitorGain: Float {
        get { engine.inputMonitorGain }
        set { engine.inputMonitorGain = newValue }
    }

    public var ampSimEnabled: Bool {
        get { engine.ampSimEnabled }
        set { engine.ampSimEnabled = newValue }
    }

    public init(engine: AudioEngine) {
        self.engine = engine
        self.micListener = MicListener()
        self.activeChainId = engine.currentChainId()
    }

    // MARK: - Bridge wiring

    /// Bind engine emissions and inbound browser intents to the
    /// bridge. Call once after both exist; safe before engine start.
    public func bind(to bridge: BridgeClient) {
        engine.onConnectStateSnapshot = { [weak self, weak bridge] snapshot in
            Task { @MainActor in
                guard let self else { return }
                self.engineStateName = snapshot.stateName
                self.activeChainId = snapshot.activeChainId
                bridge?.sendConnectState(Self.frame(from: snapshot))
            }
        }
        engine.onLatencyReportReady = { [weak self, weak bridge] report in
            Task { @MainActor in
                let frame = Self.frame(from: report)
                self?.latestLatency = frame
                bridge?.sendLatencyReport(frame)
            }
        }

        bridge.onApplyChain = { [weak self] _, raw in
            self?.applyChain(fromFrameData: raw)
        }
        // Ack for our own tone-card apply: the broadcast excludes the
        // sender, so the server embeds the resolved chain in the ack
        // instead. Same "chain"-dict extraction as inbound apply_chain.
        bridge.onAck = { [weak self] _, raw in
            self?.applyChain(fromFrameData: raw)
        }
        bridge.onSetMonitorGain = { [weak self] gain in
            self?.monitorGain = Float(max(0, min(1, gain)))
        }
        bridge.onMeasureLatency = { [weak self] in
            self?.measureLatency()
        }

        micListener.onLevels = { [weak bridge] peak, rms in
            Task { @MainActor in
                bridge?.sendInputMeter(InputMeterFrame(peakDbfs: peak, rmsDbfs: rms))
            }
        }
    }

    /// Start the input meter tap. Requires a running engine; call
    /// again after onGraphRebuilt (taps die with the old graph).
    public func startInputMeter() {
        micListener.start(on: engine.avEngine)
    }

    public func stopInputMeter() {
        micListener.stop()
    }

    // MARK: - Intents

    /// Extracts the embedded chain spec from a raw apply_chain frame
    /// and programs the DSP chain.
    public func applyChain(fromFrameData data: Data) {
        guard let spec = Self.chainSpec(fromApplyChainData: data) else { return }
        applyChain(spec)
    }

    public func applyChain(_ spec: ChainSpec) {
        engine.applyChain(spec)
        activeChainId = engine.currentChainId()
    }

    public func measureLatency() {
        engine.runLatencyProbeAsync(onStatus: { _ in })
    }

    /// Push the current snapshot (used right after bridge join so a
    /// co-open browser lights its pill without waiting for a change).
    public func publishSnapshot(to bridge: BridgeClient) {
        bridge.sendConnectState(Self.frame(from: engine.currentSnapshot()))
    }

    // MARK: - Pure mappings (tested headless)

    static func chainSpec(fromApplyChainData data: Data) -> ChainSpec? {
        guard let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let chain = dict["chain"] as? [String: Any] else { return nil }
        return ChainSpec.decode(fromWireDict: chain)
    }

    static func frame(from snapshot: AudioEngine.ConnectStateSnapshot) -> ConnectStateFrame {
        ConnectStateFrame(
            state: snapshot.stateName,
            device: .init(
                inputName: snapshot.inputDeviceName,
                outputName: snapshot.outputDeviceName,
                sampleRate: snapshot.sampleRate,
                channelsIn: snapshot.channelsIn
            ),
            monitor: .init(
                enabled: snapshot.monitorEnabled,
                gain: Double(snapshot.monitorGain),
                muted: snapshot.monitorMuted
            ),
            dsp: .init(
                ampSimEnabled: snapshot.ampSimEnabled,
                activeChainId: snapshot.activeChainId
            )
        )
    }

    /// Engine reports seconds; the wire is the *_ms family.
    static func frame(from report: AudioEngine.LatencyReport) -> LatencyReportFrame {
        LatencyReportFrame(
            inputMs: report.inputDeviceLatencySec * 1000,
            outputMs: report.outputDeviceLatencySec * 1000,
            bufferMs: report.bufferDurationSec * 1000,
            estimatedRoundTripMs: report.estimatedRoundTripSec * 1000,
            measuredRoundTripMs: report.measuredRoundTripSec.map { $0 * 1000 },
            measurementConfidence: report.measurementConfidence
        )
    }
}
