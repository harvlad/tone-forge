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

    public private(set) var sessionId: String
    public private(set) var serverURL: URL
    public private(set) var isRunning = false

    private var session: URLSession?
    private var task: URLSessionWebSocketTask?
    private var reconnectDelay: TimeInterval = 1.0
    private let reconnectMax: TimeInterval = 30.0
    private var shouldReconnect = true

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
        case "pong":
            break
        case "error":
            let msg = dict["message"] as? String ?? "(unspecified)"
            onStatus?("server error: \(msg)")
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
