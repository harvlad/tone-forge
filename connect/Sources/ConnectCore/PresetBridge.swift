//
// PresetBridge.swift
//
// WebSocket client that pairs the Connect desktop helper with the
// ToneForge web app. Connects to /ws/connect-bridge on the backend,
// announces itself as role="connect" on a named session channel, and
// dispatches inbound `preset_push` payloads to a callback.
//
// Design notes:
//   * Uses URLSessionWebSocketTask — no third-party deps. We get TLS,
//     ping/pong, and Foundation-friendly callbacks out of the box.
//   * Single-shot receive() is awkward (it doesn't auto-loop), so we
//     re-arm receive after each frame inside `pumpReceive`.
//   * Reconnect is exponential up to 30 s. The Connect helper is a
//     long-running CLI process; staying connected is the default.
//

import Foundation

public final class PresetBridge {

    /// Callback fired on the main queue when a `preset_push` frame arrives.
    /// The payload is the parsed JSON object under the "preset" key —
    /// usually shape:
    ///   {
    ///     "analysis_id": "...",
    ///     "source_url": "...",
    ///     "instrument": "guitar",
    ///     "match": { "preset_name": "...", "instrument": "Analog", ... }
    ///   }
    public var onPresetPush: (([String: Any]) -> Void)?

    /// Callback fired on the main queue when a `set_gain` frame arrives.
    /// Value is clamped to [0, 1] by the server before broadcast; we
    /// clamp again locally as defense in depth.
    public var onGainChange: ((Float) -> Void)?

    /// Status callback for surfacing connection state to the CLI / UI.
    public var onStatus: ((String) -> Void)?

    /// Fired on the main queue when the server reports we are too old.
    /// The UI should present an update prompt and stop reconnecting —
    /// the loop would just hammer the same rejection.
    /// Argument is the version the server requires.
    public var onVersionMismatch: ((Int) -> Void)?

    /// Fired on the main queue when an ``apply_chain`` frame arrives
    /// with a decodable spec. The CLI/UI is expected to forward the
    /// spec to ``AudioEngine.applyChain(_:)``. Malformed frames
    /// (missing parameters, wrong types) are dropped silently — see
    /// ``ChainSpec.decode(fromWireDict:)``.
    public var onChainApply: ((ChainSpec) -> Void)?

    /// Fired on the main queue when a ``set_auto_update`` frame
    /// arrives. The argument is the new Sparkle opt-in value; the
    /// receiver (AppDelegate) is expected to write it to
    /// ``UserDefaults`` under the ``SUEnableAutomaticChecks`` key.
    /// PresetBridge intentionally does not depend on Sparkle —
    /// keeping the side effect in AppDelegate means tests can stub
    /// the callback without linking against the framework.
    public var onSetAutoUpdate: ((Bool) -> Void)?

    /// Fired on the main queue when a v2 ``measure_latency`` frame
    /// arrives. The receiver (AppDelegate / ConnectMain) is expected
    /// to call ``AudioEngine.runLatencyProbeAsync``; the resulting
    /// re-emission goes back over the wire as a ``latency_report``
    /// with the ``measured_round_trip_ms`` field populated.
    /// PresetBridge does not own the AudioEngine reference so it
    /// cannot run the probe directly.
    public var onMeasureLatencyRequest: (() -> Void)?

    /// Fired on the main queue when a v2 ``session_data`` frame
    /// arrives. Today this is a no-op pass-through; a future
    /// SessionStore consumer in Connect will subscribe to drive the
    /// in-Connect tuner / countdown / chord HUD overlay.
    public var onSessionData: (([String: Any]) -> Void)?

    /// Fired on the main queue when a v2 ``transport_state`` frame
    /// arrives. `(playing, positionSec)`. Not cached server-side.
    public var onTransportState: ((Bool, Double) -> Void)?

    /// Fired on the main queue when a v2 ``load_stems`` frame
    /// arrives. The receiver (AppDelegate / ConnectMain) is
    /// expected to feed the specs into ``StemLoader`` and then call
    /// ``AudioEngine.loadStem(name:url:)`` for each successful
    /// download. The bridge intentionally does not own the engine
    /// or the loader; we just decode and forward.
    /// Malformed entries (missing url, unparseable URL) are dropped
    /// before this callback fires.
    public var onLoadStems: (([StemLoader.Spec]) -> Void)?

    public private(set) var sessionId: String
    public private(set) var serverURL: URL
    public private(set) var isRunning = false

    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var reconnectDelay: TimeInterval = 1.0
    private let reconnectMax: TimeInterval = 30.0
    private var shouldReconnect = true

    // ----- v2 connect_state coalescing (Audio-Ownership Pivot) -----
    //
    // sendConnectState() can be called several times in quick
    // succession (a slider drag fires inputMonitorGain.didSet on every
    // value change; a chain swap fires applyChain + ampSimEnabled
    // changes). Rate-limit to ≤1 Hz on the wire: the first call goes
    // out immediately; subsequent calls within the window stash the
    // latest snapshot and arm a single trailing flush at (last + 1s).
    private let connectStateMinIntervalSec: TimeInterval = 1.0
    private var connectStateLastSentAt: Date?
    private var connectStatePending: AudioEngine.ConnectStateSnapshot?
    /// Guards `connectStateLastSentAt` and `connectStatePending`.
    /// Snapshots arrive on the main queue; the flush DispatchWorkItem
    /// also runs on main, so the lock is defense-in-depth against a
    /// future caller off the main thread rather than a real race
    /// today.
    private let connectStateLock = NSLock()
    private var connectStateFlushWorkItem: DispatchWorkItem?

    public init(serverURL: URL = URL(string: "ws://127.0.0.1:8000/ws/connect-bridge")!,
                sessionId: String = "default") {
        self.serverURL = serverURL
        self.sessionId = sessionId
    }

    public func start() {
        guard !isRunning else { return }
        isRunning = true
        shouldReconnect = true
        openSocket()
    }

    public func stop() {
        shouldReconnect = false
        isRunning = false
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil
    }

    private func openSocket() {
        let config = URLSessionConfiguration.default
        let session = URLSession(configuration: config)
        self.session = session
        let task = session.webSocketTask(with: serverURL)
        self.task = task

        onStatus?("connecting to \(serverURL.absoluteString) (session=\(sessionId))")
        task.resume()
        sendHello()
        pumpReceive()
    }

    private func sendHello() {
        let hello: [String: Any] = [
            "type": ConnectProtocol.MessageType.hello,
            "role": "connect",
            "session_id": sessionId,
            // protocol_version is the v1 addition. Server treats a
            // missing field as v0 for back-compat with older Connect
            // builds; new builds always send it.
            "protocol_version": ConnectProtocol.version,
        ]
        sendJSON(hello) { [weak self] err in
            if let err = err {
                self?.onStatus?("hello send failed: \(err.localizedDescription)")
                self?.scheduleReconnect()
            }
        }
    }

    /// Emit a one-shot `device_lost` event upstream when the audio
    /// engine's reconfig budget is exhausted (e.g. the input interface
    /// stayed gone across all retry attempts). The server's
    /// `_ConnectChannel` doesn't have an explicit handler for this
    /// frame type — it falls through to the default-broadcast branch
    /// at the WS edge, which relays the frame to every browser peer
    /// in the channel. The browser surfaces a reconnection toast
    /// (`jam.js` ws.onmessage `device_lost` branch).
    ///
    /// Fire-and-forget: if the WS isn't connected we just no-op. The
    /// engine is already in `.failed`; there's nothing more to do
    /// from here.
    public func sendDeviceLost(reason: String) {
        guard task != nil else {
            onStatus?("device_lost not sent — no active WS")
            return
        }
        let frame: [String: Any] = [
            "v": ConnectProtocol.version,
            "type": ConnectProtocol.MessageType.deviceLost,
            "reason": reason,
        ]
        sendJSON(frame) { [weak self] err in
            if let err = err {
                self?.onStatus?("device_lost send failed: \(err.localizedDescription)")
            } else {
                self?.onStatus?("device_lost sent (reason=\(reason))")
            }
        }
    }

    /// Emit a v2 `connect_state` snapshot upstream. Coalesced to ≤1 Hz
    /// so a slider drag (which fires `inputMonitorGain.didSet` on every
    /// pixel of motion) doesn't flood the wire. The first call after a
    /// quiet window goes out immediately; subsequent calls within the
    /// window stash the latest snapshot and arm a single trailing flush
    /// scheduled at (last_send_at + 1s).
    ///
    /// Safe to call from any thread; the rate-limit bookkeeping is
    /// guarded by `connectStateLock`. The actual `task.send` runs on
    /// the URLSession queue per Apple's contract.
    public func sendConnectState(_ snapshot: AudioEngine.ConnectStateSnapshot) {
        let now = Date()

        connectStateLock.lock()
        let last = connectStateLastSentAt
        let intervalElapsed: Bool
        if let last = last {
            intervalElapsed = now.timeIntervalSince(last) >= connectStateMinIntervalSec
        } else {
            intervalElapsed = true
        }

        if intervalElapsed {
            connectStateLastSentAt = now
            connectStatePending = nil
            connectStateFlushWorkItem?.cancel()
            connectStateFlushWorkItem = nil
            connectStateLock.unlock()
            transmitConnectState(snapshot)
            return
        }

        // Inside the rate-limit window: stash, then ensure a trailing
        // flush is scheduled.
        connectStatePending = snapshot
        if connectStateFlushWorkItem == nil, let last = last {
            let dueAt = last.addingTimeInterval(connectStateMinIntervalSec)
            let delay = max(0.0, dueAt.timeIntervalSince(now))
            let work = DispatchWorkItem { [weak self] in
                self?.flushPendingConnectState()
            }
            connectStateFlushWorkItem = work
            connectStateLock.unlock()
            DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
            return
        }
        connectStateLock.unlock()
    }

    private func flushPendingConnectState() {
        connectStateLock.lock()
        let pending = connectStatePending
        connectStatePending = nil
        connectStateFlushWorkItem = nil
        if pending != nil {
            connectStateLastSentAt = Date()
        }
        connectStateLock.unlock()
        if let snap = pending {
            transmitConnectState(snap)
        }
    }

    private func transmitConnectState(_ snapshot: AudioEngine.ConnectStateSnapshot) {
        guard task != nil else {
            onStatus?("connect_state not sent — no active WS")
            return
        }
        sendJSON(PresetBridge.buildConnectStateFrame(snapshot)) { [weak self] err in
            if let err = err {
                self?.onStatus?("connect_state send failed: \(err.localizedDescription)")
            }
        }
    }

    /// Build the v2 `connect_state` JSON-encodable dict. Pulled out
    /// (and `internal`) so tests can assert the wire shape without
    /// standing up a real URLSessionWebSocketTask.
    static func buildConnectStateFrame(_ snapshot: AudioEngine.ConnectStateSnapshot) -> [String: Any] {
        var device: [String: Any] = [
            "sample_rate": snapshot.sampleRate,
            "channels_in": snapshot.channelsIn,
        ]
        if let inName = snapshot.inputDeviceName  { device["input_name"]  = inName }
        if let outName = snapshot.outputDeviceName { device["output_name"] = outName }

        let monitor: [String: Any] = [
            "enabled": snapshot.monitorEnabled,
            "gain":    Double(snapshot.monitorGain),
            "muted":   snapshot.monitorMuted,
        ]

        var dsp: [String: Any] = [
            "amp_sim_enabled": snapshot.ampSimEnabled,
        ]
        if let chainId = snapshot.activeChainId {
            dsp["active_chain_id"] = chainId
        }

        return [
            "v": ConnectProtocol.version,
            "type": ConnectProtocol.MessageType.connectState,
            "state": snapshot.stateName,
            "device": device,
            "monitor": monitor,
            "dsp": dsp,
        ]
    }

    /// Emit a v2 `latency_report` upstream. Not rate-limited — the
    /// engine only fires this on state transitions to `.running` and
    /// on device/sample-rate changes, both of which are inherently
    /// low-frequency. Wire numbers are milliseconds; struct fields
    /// are seconds, so we convert here.
    public func sendLatencyReport(_ report: AudioEngine.LatencyReport) {
        guard task != nil else {
            onStatus?("latency_report not sent — no active WS")
            return
        }
        sendJSON(PresetBridge.buildLatencyReportFrame(report)) { [weak self] err in
            if let err = err {
                self?.onStatus?("latency_report send failed: \(err.localizedDescription)")
            }
        }
    }

    /// Build the v2 `latency_report` JSON-encodable dict. Internal
    /// for the same test-only reason as `buildConnectStateFrame`.
    /// Converts seconds → milliseconds at the wire boundary.
    /// `measured_round_trip_ms` + `measurement_confidence` are only
    /// included when a LatencyProbe run has populated them; absence
    /// means JAM should render the estimate alone.
    static func buildLatencyReportFrame(_ report: AudioEngine.LatencyReport) -> [String: Any] {
        var frame: [String: Any] = [
            "v": ConnectProtocol.version,
            "type": ConnectProtocol.MessageType.latencyReport,
            "input_ms":  report.inputDeviceLatencySec  * 1000.0,
            "output_ms": report.outputDeviceLatencySec * 1000.0,
            "buffer_ms": report.bufferDurationSec      * 1000.0,
            "estimated_round_trip_ms": report.estimatedRoundTripSec * 1000.0,
        ]
        if let measuredSec = report.measuredRoundTripSec {
            frame["measured_round_trip_ms"] = measuredSec * 1000.0
        }
        if let confidence = report.measurementConfidence {
            frame["measurement_confidence"] = confidence
        }
        return frame
    }

    /// Emit a v2 `input_meter` upstream for the JAM-side VU. Callers
    /// (the AudioEngine input-tap callback) are responsible for
    /// upstream rate-limiting to ~20 Hz per the Protocol.swift
    /// contract — this helper is otherwise unconditional, so a hot
    /// tap calling it on every render-quantum would flood the wire.
    /// NOT cached server-side: meter levels are stale-on-arrival.
    public func sendInputMeter(peakDbfs: Double, rmsDbfs: Double) {
        guard task != nil else { return }
        sendJSON(PresetBridge.buildInputMeterFrame(peakDbfs: peakDbfs, rmsDbfs: rmsDbfs))
    }

    /// Build the v2 `input_meter` JSON-encodable dict. Internal for
    /// the same test-only reason as `buildLatencyReportFrame`.
    static func buildInputMeterFrame(peakDbfs: Double, rmsDbfs: Double) -> [String: Any] {
        return [
            "v": ConnectProtocol.version,
            "type": ConnectProtocol.MessageType.inputMeter,
            "peak_dbfs": peakDbfs,
            "rms_dbfs":  rmsDbfs,
        ]
    }

    private func sendJSON(_ obj: [String: Any], completion: ((Error?) -> Void)? = nil) {
        guard let task = task else { completion?(nil); return }
        do {
            let data = try JSONSerialization.data(withJSONObject: obj, options: [])
            guard let str = String(data: data, encoding: .utf8) else {
                completion?(nil); return
            }
            task.send(.string(str)) { err in completion?(err) }
        } catch {
            completion?(error)
        }
    }

    /// URLSessionWebSocketTask.receive is one-shot. We chain it so the
    /// task drains frames as they arrive without spawning a thread.
    private func pumpReceive() {
        guard let task = task else { return }
        task.receive { [weak self] result in
            guard let self = self else { return }
            switch result {
            case .failure(let err):
                self.onStatus?("receive failed: \(err.localizedDescription)")
                self.scheduleReconnect()
            case .success(let message):
                self.handleMessage(message)
                self.pumpReceive()
            }
        }
    }

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        let data: Data?
        switch message {
        case .string(let s): data = s.data(using: .utf8)
        case .data(let d):   data = d
        @unknown default:    data = nil
        }
        guard let data = data,
              let obj = try? JSONSerialization.jsonObject(with: data),
              let dict = obj as? [String: Any]
        else { return }
        dispatch(dict)
    }

    /// Frame-dispatch core. Pulled out of `handleMessage` so tests can
    /// exercise every branch without standing up a real WebSocket.
    /// Marked `internal` (not `private`) so `@testable import
    /// ConnectCore` can reach it; it stays out of the public API.
    func dispatch(_ dict: [String: Any]) {
        let type = dict["type"] as? String ?? ""
        switch type {
        case ConnectProtocol.MessageType.helloAck:
            // Server confirmed it speaks our version. Nothing to do
            // beyond logging — `joined` will follow with peer count.
            let sv = dict["protocol_version"] as? Int ?? 0
            onStatus?("hello_ack (server protocol v\(sv))")
        case ConnectProtocol.MessageType.versionMismatch:
            // Server requires a newer client. Stop reconnecting and
            // hand off to the UI to prompt for an update.
            let required = dict["required"] as? Int ?? (ConnectProtocol.version + 1)
            onStatus?("version_mismatch: server requires v\(required), we speak v\(ConnectProtocol.version)")
            shouldReconnect = false
            DispatchQueue.main.async { [weak self] in
                self?.onVersionMismatch?(required)
            }
        case ConnectProtocol.MessageType.joined:
            let peers = dict["peers"] as? Int ?? 0
            self.reconnectDelay = 1.0
            onStatus?("joined session \(sessionId) (peers=\(peers))")
        case "preset_push":
            if let preset = dict["preset"] as? [String: Any] {
                let replayed = (dict["replayed"] as? Bool) ?? false
                onStatus?("preset_push received (replayed=\(replayed))")
                DispatchQueue.main.async { [weak self] in
                    self?.onPresetPush?(preset)
                }
            }
        case ConnectProtocol.MessageType.applyChain:
            // The server has resolved a chain_id into a fully-populated
            // spec. Decode it; on success forward to the audio engine.
            // We accept either an embedded "chain" envelope (current
            // protocol) or — defensively — the spec at the top level.
            let raw = (dict["chain"] as? [String: Any]) ?? dict
            if let spec = ChainSpec.decode(fromWireDict: raw) {
                let replayed = (dict["replayed"] as? Bool) ?? false
                onStatus?("apply_chain received id=\(spec.id) (replayed=\(replayed))")
                DispatchQueue.main.async { [weak self] in
                    self?.onChainApply?(spec)
                }
            } else {
                onStatus?("apply_chain frame failed to decode; dropping")
            }
        case "set_gain":
            // The server sends the gain as a JSON number; JSONSerialization
            // hands it back as NSNumber, so we accept either Float or Double.
            let raw: Float
            if let f = dict["gain"] as? Float {
                raw = f
            } else if let d = dict["gain"] as? Double {
                raw = Float(d)
            } else if let n = dict["gain"] as? NSNumber {
                raw = n.floatValue
            } else {
                onStatus?("set_gain frame had no numeric gain")
                return
            }
            let clamped = max(0.0, min(1.0, raw))
            let replayed = (dict["replayed"] as? Bool) ?? false
            onStatus?(String(format: "set_gain %.2f (replayed=%@)", clamped, replayed ? "true" : "false"))
            DispatchQueue.main.async { [weak self] in
                self?.onGainChange?(clamped)
            }
        case ConnectProtocol.MessageType.setAutoUpdate:
            // Browser flipped the Sparkle opt-in toggle (or the server
            // replayed the persisted value to a fresh helper bring-up).
            // We accept either Bool or NSNumber for "enabled" because
            // JSONSerialization may unbox a JSON `true`/`false` either
            // way depending on platform. Anything that isn't a bool is
            // a protocol violation we just log and drop.
            let enabled: Bool
            if let b = dict["enabled"] as? Bool {
                enabled = b
            } else if let n = dict["enabled"] as? NSNumber {
                enabled = n.boolValue
            } else {
                onStatus?("set_auto_update frame had no bool `enabled` field")
                return
            }
            let replayed = (dict["replayed"] as? Bool) ?? false
            onStatus?("set_auto_update enabled=\(enabled) (replayed=\(replayed))")
            DispatchQueue.main.async { [weak self] in
                self?.onSetAutoUpdate?(enabled)
            }
        case "ping":
            // Server liveness probe. The backend reaps the socket if it
            // doesn't see any frame within ~40s (30s recv + 10s pong
            // window), so we must answer immediately or get dropped and
            // forced into a reconnect loop. Best-effort — if the send
            // fails the receive loop will surface the underlying error.
            sendJSON(["type": "pong"])
        case "pong":
            break
        case "error":
            let msg = dict["message"] as? String ?? "(unspecified)"
            onStatus?("server error: \(msg)")

        // ----- v2 inbound (Audio-Ownership Pivot) ----------------------
        case ConnectProtocol.MessageType.measureLatency:
            // User explicitly asked for an impulse measurement. Hand off
            // to the AppDelegate / ConnectMain which owns the
            // AudioEngine reference. Idempotent on the engine side
            // (latencyProbeInFlight guards re-entry).
            DispatchQueue.main.async { [weak self] in
                self?.onMeasureLatencyRequest?()
            }
        case ConnectProtocol.MessageType.sessionData:
            // Future SessionStore singleton subscribes here. Today we
            // surface to a callback that defaults to no-op so the
            // receiver opts in.
            DispatchQueue.main.async { [weak self] in
                self?.onSessionData?(dict)
            }
        case ConnectProtocol.MessageType.transportState:
            let playing = dict["playing"] as? Bool ?? false
            let positionS = (dict["position_s"] as? Double)
                ?? Double(dict["position_s"] as? Int ?? 0)
            DispatchQueue.main.async { [weak self] in
                self?.onTransportState?(playing, positionS)
            }
        case ConnectProtocol.MessageType.loadStems:
            // Build typed StemLoader.Spec list from the wire dict.
            // Skip entries with missing or unparseable URLs so the
            // receiver never has to handle bad data.
            let rawList = dict["stems"] as? [[String: Any]] ?? []
            let specs: [StemLoader.Spec] = rawList.compactMap { item in
                guard
                    let id  = item["id"] as? String,
                    let raw = item["url"] as? String,
                    let url = URL(string: raw)
                else { return nil }
                return StemLoader.Spec(id: id, url: url)
            }
            DispatchQueue.main.async { [weak self] in
                self?.onLoadStems?(specs)
            }

        default:
            // Unknown typed frame — ignore but log so debugging is easy.
            onStatus?("ignored frame type=\(type)")
        }
    }

    private func scheduleReconnect() {
        // Tear down the current task before backing off.
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
        session?.invalidateAndCancel()
        session = nil

        guard shouldReconnect else { return }
        let delay = min(reconnectDelay, reconnectMax)
        reconnectDelay = min(reconnectDelay * 2, reconnectMax)
        onStatus?("reconnecting in \(Int(delay))s")
        DispatchQueue.global().asyncAfter(deadline: .now() + delay) { [weak self] in
            self?.openSocket()
        }
    }
}
