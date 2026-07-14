// BridgeClient.swift
//
// /ws/connect-bridge session socket. Desktop is the audio owner and
// transport authority: it emits transport_state / session_data /
// load_stems / connect_state / latency_report / input_meter, and
// applies inbound apply_chain / set_gain / measure_latency /
// transport_state (last-writer-wins) from a co-open browser.
//
// Pattern follows ConnectCore.PresetBridge, adapted to the typed
// BridgeFrames codec and async/await:
//   - reconnect backoff 1s doubling to 30s, reset on "joined";
//   - version_mismatch stops reconnecting entirely;
//   - inbound "ping" answered with "pong" immediately (server reaps
//     after 30s silence + 10s pong window);
//   - connect_state coalesced to <=1 Hz with a trailing flush so the
//     latest snapshot always lands;
//   - transport_state throttled (~4 Hz) for continuous position
//     updates; discrete changes (play/pause/seek/loop/tempo) bypass.
//
// The socket is a protocol seam so tests drive the full state machine
// with an in-process stub — no network.

import Foundation
import Observation

// MARK: - Socket seam

public protocol BridgeSocketConnection: AnyObject, Sendable {
    func sendText(_ text: String) async throws
    /// Waits for the next text frame. Throws when the socket dies.
    func receiveText() async throws -> String
    func close()
}

public protocol BridgeSocketConnecting: Sendable {
    func open(url: URL) async throws -> BridgeSocketConnection
}

/// Real implementation over URLSessionWebSocketTask. Connection
/// failures surface on the first send/receive (same as PresetBridge).
public struct URLSessionBridgeConnector: BridgeSocketConnecting {
    public init() {}

    public func open(url: URL) async throws -> BridgeSocketConnection {
        let task = URLSession.shared.webSocketTask(with: url)
        task.resume()
        return URLSessionBridgeSocket(task: task)
    }
}

final class URLSessionBridgeSocket: BridgeSocketConnection, @unchecked Sendable {
    private let task: URLSessionWebSocketTask

    init(task: URLSessionWebSocketTask) { self.task = task }

    func sendText(_ text: String) async throws {
        try await task.send(.string(text))
    }

    func receiveText() async throws -> String {
        switch try await task.receive() {
        case .string(let text): return text
        case .data(let data): return String(decoding: data, as: UTF8.self)
        @unknown default: return ""
        }
    }

    func close() {
        task.cancel(with: .goingAway, reason: nil)
    }
}

// MARK: - Client

public struct BridgeClientConfig: Sendable {
    public var reconnectBaseDelay: TimeInterval = 1.0
    public var reconnectMaxDelay: TimeInterval = 30.0
    /// connect_state coalescing window (server caches latest anyway).
    public var connectStateInterval: TimeInterval = 1.0
    /// Continuous transport_state cadence (~4 Hz).
    public var transportStateInterval: TimeInterval = 0.25

    public init() {}
}

@Observable
@MainActor
public final class BridgeClient {

    public enum Status: Equatable, Sendable {
        case idle
        case connecting
        case connected(peers: Int)
        /// Terminal: reconnecting would be pointless (version mismatch).
        case failed(String)
    }

    public private(set) var status: Status = .idle

    // Inbound intents (browser -> audio owner). Fired on main actor.
    /// Raw frame data rides along: the server embeds the resolved
    /// chain spec under a "chain" key (heterogeneous dict) that the
    /// typed codec doesn't model — ConnectCore's
    /// ChainSpec.decode(fromWireDict:) consumes it downstream.
    public var onApplyChain: ((ApplyChainFrame, Data) -> Void)?
    /// Server ack for a request_id-carrying intent. For apply_chain
    /// acks the raw data carries the resolved "chain" dict (the
    /// broadcast excludes the sender, so this is how the desktop's own
    /// tone-card apply reaches its DSP).
    public var onAck: ((AckFrame, Data) -> Void)?
    public var onSetMonitorGain: ((Double) -> Void)?
    public var onMeasureLatency: (() -> Void)?
    public var onTransportState: ((TransportStateFrame) -> Void)?
    public var onPeerLeft: (() -> Void)?
    /// Inbound connect_state means ANOTHER connect-role client owns
    /// audio on this session (the broadcast excludes the sender) —
    /// e.g. a real Connect.app sharing our session id. Surfaced so
    /// settings can warn about dueling audio owners.
    public var onPeerConnectState: ((ConnectStateFrame) -> Void)?

    private let connector: BridgeSocketConnecting
    private let config: BridgeClientConfig

    private var runTask: Task<Void, Never>?
    private var socket: BridgeSocketConnection?
    private var shouldReconnect = false
    private var reconnectDelay: TimeInterval

    private var lastTransportSend: Date = .distantPast
    private var lastConnectStateSend: Date = .distantPast
    private var pendingConnectState: ConnectStateFrame?
    private var connectStateFlushTask: Task<Void, Never>?

    public init(
        connector: BridgeSocketConnecting = URLSessionBridgeConnector(),
        config: BridgeClientConfig = BridgeClientConfig()
    ) {
        self.connector = connector
        self.config = config
        self.reconnectDelay = config.reconnectBaseDelay
    }

    /// wss URL for the bridge endpoint derived from the backend base
    /// URL (http -> ws, https -> wss).
    public static func bridgeURL(backendBaseURL: URL) -> URL {
        var components = URLComponents(
            url: backendBaseURL, resolvingAgainstBaseURL: false)!
        components.scheme = components.scheme == "http" ? "ws" : "wss"
        components.path = "/ws/connect-bridge"
        return components.url!
    }

    // MARK: Lifecycle

    public func start(sessionId: String, url: URL) {
        stop()
        shouldReconnect = true
        reconnectDelay = config.reconnectBaseDelay
        runTask = Task { [weak self] in
            await self?.run(url: url, sessionId: sessionId)
        }
    }

    public func stop() {
        shouldReconnect = false
        runTask?.cancel()
        runTask = nil
        connectStateFlushTask?.cancel()
        connectStateFlushTask = nil
        pendingConnectState = nil
        socket?.close()
        socket = nil
        status = .idle
    }

    private func run(url: URL, sessionId: String) async {
        while shouldReconnect && !Task.isCancelled {
            status = .connecting
            do {
                let socket = try await connector.open(url: url)
                self.socket = socket
                try await send(HelloFrame(sessionId: sessionId), over: socket)
                while shouldReconnect && !Task.isCancelled {
                    let text = try await socket.receiveText()
                    let raw = Data(text.utf8)
                    if let frame = try? BridgeFrameCodec.decode(raw) {
                        await handle(frame, raw: raw)
                    }
                }
            } catch {
                // Fall through to reconnect.
            }
            socket?.close()
            socket = nil
            guard shouldReconnect && !Task.isCancelled else { break }
            status = .connecting
            try? await Task.sleep(nanoseconds: UInt64(reconnectDelay * 1_000_000_000))
            reconnectDelay = min(reconnectDelay * 2, config.reconnectMaxDelay)
        }
        // Terminal status (.idle from stop(), .failed from
        // version_mismatch) was already set by whoever ended the loop.
    }

    private func handle(_ frame: BridgeFrame, raw: Data) async {
        switch frame {
        case .helloAck:
            break
        case .joined(let joined):
            reconnectDelay = config.reconnectBaseDelay
            status = .connected(peers: joined.peers ?? 1)
        case .versionMismatch(let mismatch):
            shouldReconnect = false
            status = .failed(
                "Protocol version mismatch (server requires \(mismatch.required.map(String.init) ?? "?"))")
            socket?.close()
        case .ping(let ping):
            if let socket {
                try? await send(PongFrame(nonce: ping.nonce), over: socket)
            }
        case .applyChain(let chain):
            onApplyChain?(chain, raw)
        case .ack(let ack):
            onAck?(ack, raw)
        case .setGain(let gain), .setMonitorGain(let gain):
            if let value = gain.gain { onSetMonitorGain?(value) }
        case .measureLatency:
            onMeasureLatency?()
        case .transportState(let transport):
            onTransportState?(transport)
        case .peerLeft:
            onPeerLeft?()
        case .connectState(let state):
            onPeerConnectState?(state)
        case .error, .pong, .sessionData, .loadStems,
             .latencyReport, .inputMeter, .unknown:
            break
        }
    }

    // MARK: Outbound

    /// Continuous updates are throttled to ~4 Hz; pass discrete: true
    /// for play/pause/seek/loop/tempo changes to send immediately.
    public func sendTransportState(_ frame: TransportStateFrame, discrete: Bool = false) {
        let now = Date()
        guard discrete
            || now.timeIntervalSince(lastTransportSend) >= config.transportStateInterval
        else { return }
        lastTransportSend = now
        fire(frame)
    }

    public func sendSessionData(_ frame: SessionDataFrame) {
        fire(frame)
    }

    public func sendLoadStems(_ frame: LoadStemsFrame) {
        fire(frame)
    }

    /// Coalesced to <=1 Hz: first call in a window sends immediately,
    /// later calls stash the latest snapshot for a trailing flush.
    public func sendConnectState(_ frame: ConnectStateFrame) {
        let now = Date()
        let elapsed = now.timeIntervalSince(lastConnectStateSend)
        if elapsed >= config.connectStateInterval {
            lastConnectStateSend = now
            fire(frame)
            return
        }
        pendingConnectState = frame
        guard connectStateFlushTask == nil else { return }
        let wait = config.connectStateInterval - elapsed
        connectStateFlushTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
            guard let self, !Task.isCancelled else { return }
            self.connectStateFlushTask = nil
            if let pending = self.pendingConnectState {
                self.pendingConnectState = nil
                self.lastConnectStateSend = Date()
                self.fire(pending)
            }
        }
    }

    /// Tone-card apply. Returns the request_id so the caller can
    /// correlate the ack (jam.js: 'apply_' + Date.now().toString(36)).
    @discardableResult
    public func sendApplyChain(chainId: String) -> String {
        let requestId = "apply_"
            + String(Int(Date().timeIntervalSince1970 * 1000), radix: 36)
        fire(ApplyChainRequestFrame(chainId: chainId, requestId: requestId))
        return requestId
    }

    /// Transition-driven; never rate-limited (PresetBridge parity).
    public func sendLatencyReport(_ frame: LatencyReportFrame) {
        fire(frame)
    }

    /// Caller rate-limits (~20 Hz from the audio tap).
    public func sendInputMeter(_ frame: InputMeterFrame) {
        fire(frame)
    }

    private func fire<F: Encodable>(_ frame: F) {
        guard let socket else { return }
        guard let data = try? BridgeFrameCodec.encode(frame),
              let text = String(data: data, encoding: .utf8) else { return }
        Task {
            try? await socket.sendText(text)
        }
    }

    private func send<F: Encodable>(
        _ frame: F, over socket: BridgeSocketConnection
    ) async throws {
        let data = try BridgeFrameCodec.encode(frame)
        try await socket.sendText(String(decoding: data, as: UTF8.self))
    }
}
