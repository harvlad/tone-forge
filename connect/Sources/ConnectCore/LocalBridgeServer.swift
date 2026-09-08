//
// LocalBridgeServer.swift
//
// Loopback WebSocket LISTENER that lets the jamn.app web page talk to
// Connect directly (ws://127.0.0.1:17995) instead of round-tripping
// every frame through the backend relay hub. Speaks the exact same
// versioned JSON frames as PresetBridge (Protocol.swift, v2) — the
// listener is additive; the relay client keeps running for session
// continuity, server-side caching/replay, and chain_id resolution.
//
// Port choice: 17995 is fixed (the web page has to know where to dial)
// and picked from the unassigned high range — no IANA registration,
// clear of common dev servers (3000/5173/8000/8080), macOS AirPlay
// (7000/5000) and the backend relay itself. Tests pass port 0 to get
// an ephemeral port so parallel test runs can't collide.
//
// Security model — this port is reachable by ANY web page the user
// visits, because Chrome permits ws:// to loopback even from https
// origins. Three mandatory gates, in order:
//   1. Bind strictly to 127.0.0.1 (requiredLocalEndpoint), so nothing
//      off-machine can even complete a TCP handshake.
//   2. Validate the WebSocket Origin header at HTTP-upgrade time
//      against a fixed allowlist (jamn.app + local dev hosts) and
//      reject the upgrade on mismatch. Browsers always send Origin on
//      WS connects and script cannot forge it; a missing Origin (i.e.
//      a non-browser client) is also rejected — non-browser local
//      clients have no reason to use this path over the relay.
//   3. Require the FIRST frame to be a `hello` carrying the session id
//      the page received — the same id Connect got via the
//      toneforge://pair deeplink — and drop the connection on
//      mismatch, or if no valid hello arrives within 3 s. Frames are
//      only dispatched (and telemetry only mirrored) to peers that
//      passed the hello gate.
//

import Foundation
import Network

public final class LocalBridgeServer {

    public static let defaultPort: UInt16 = 17995

    /// Origins allowed to complete the WS upgrade. Fixed, not
    /// configurable at runtime — a configurable allowlist would just
    /// be an injection surface for whatever configures it.
    public static let allowedOrigins: Set<String> = [
        "https://jamn.app",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ]

    /// Authenticated inbound frame (post-hello). Wired by the owner to
    /// `PresetBridge.dispatch` so local frames drive the exact same
    /// gain/chain/stems/session handling as relay frames.
    public var onFrame: (([String: Any]) -> Void)?

    /// Status/log line callback, mirrors PresetBridge.onStatus.
    public var onStatus: ((String) -> Void)?

    /// The session id a peer's hello must present. Updated on every
    /// (re)pair — see `updateExpectedSession`. Guarded by `stateLock`
    /// because pairing happens on the main queue while hello frames
    /// arrive on the listener queue.
    private var expectedSessionId: String
    private let stateLock = NSLock()

    /// Actual bound port — differs from the requested one only when
    /// the caller asked for 0 (ephemeral, tests).
    public private(set) var port: UInt16 = 0

    private let requestedPort: UInt16
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "com.toneforge.connect.local-bridge")

    /// How long an accepted connection may sit without a valid hello
    /// before we drop it. Keeps a hostile page that passed the Origin
    /// check (e.g. a compromised allowlisted origin) from parking
    /// sockets, and bounds the token-guessing window per connection.
    private let helloDeadlineSec: TimeInterval = 3.0

    private final class Peer {
        let connection: NWConnection
        var authenticated = false
        init(_ connection: NWConnection) { self.connection = connection }
    }
    /// All live peers, keyed by connection identity. Mutated only on
    /// `queue`.
    private var peers: [ObjectIdentifier: Peer] = [:]

    public init(port: UInt16 = LocalBridgeServer.defaultPort,
                sessionId: String = "default") {
        self.requestedPort = port
        self.expectedSessionId = sessionId
    }

    /// Re-arm the token gate when the user pairs a new session (a
    /// second toneforge://pair deeplink with a different session id).
    /// Existing authenticated peers are kept — they proved knowledge
    /// of the session that was current when they connected, and the
    /// browser tab that re-paired will reconnect with the new id.
    public func updateExpectedSession(_ sessionId: String) {
        stateLock.lock()
        expectedSessionId = sessionId
        stateLock.unlock()
    }

    // MARK: - Static validators (unit-testable without sockets)

    /// Origin gate. `nil` (no Origin header → non-browser client) is
    /// rejected: browsers always send Origin on WebSocket upgrades,
    /// and anything that isn't a browser should use the relay.
    static func isOriginAllowed(_ origin: String?) -> Bool {
        guard let origin = origin else { return false }
        return allowedOrigins.contains(origin)
    }

    /// Hello-frame gate: first frame must be a `hello` whose
    /// session_id matches the currently paired session.
    static func isValidHello(_ dict: [String: Any], expectedSessionId: String) -> Bool {
        guard (dict["type"] as? String) == ConnectProtocol.MessageType.hello else { return false }
        guard let sid = dict["session_id"] as? String, !sid.isEmpty else { return false }
        return sid == expectedSessionId
    }

    // MARK: - Lifecycle

    public func start() {
        guard listener == nil else { return }

        let params = NWParameters.tcp
        // Gate 1: loopback only. requiredLocalEndpoint pins the bind
        // address — NWListener without it binds every interface, which
        // would expose the port to the LAN.
        params.requiredLocalEndpoint = NWEndpoint.hostPort(
            host: NWEndpoint.Host("127.0.0.1"),
            port: NWEndpoint.Port(rawValue: requestedPort)!
        )
        // Loopback never has more than one interface path; disallow
        // fancy multipath behavior outright.
        params.allowLocalEndpointReuse = true

        let wsOptions = NWProtocolWebSocket.Options()
        wsOptions.autoReplyPing = true
        // Gate 2: Origin allowlist, enforced at HTTP-upgrade time so a
        // disallowed page never even gets a WS connection to probe.
        // Handler signature is (subprotocols, additionalHeaders).
        wsOptions.setClientRequestHandler(queue) { _, headers in
            let origin = headers
                .first(where: { $0.name.lowercased() == "origin" })?.value
            if LocalBridgeServer.isOriginAllowed(origin) {
                return NWProtocolWebSocket.Response(status: .accept, subprotocol: nil)
            }
            return NWProtocolWebSocket.Response(status: .reject, subprotocol: nil)
        }
        params.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)

        let listener: NWListener
        do {
            listener = try NWListener(using: params)
        } catch {
            // Port already taken (second Connect instance, or an
            // unrelated process squatting). Non-fatal by design: the
            // relay path still works, the fast path just isn't there.
            onStatus?("local bridge failed to bind :\(requestedPort): \(error.localizedDescription)")
            return
        }
        self.listener = listener

        listener.stateUpdateHandler = { [weak self] state in
            guard let self = self else { return }
            switch state {
            case .ready:
                self.port = listener.port?.rawValue ?? self.requestedPort
                self.onStatus?("local bridge listening on 127.0.0.1:\(self.port)")
            case .failed(let error):
                self.onStatus?("local bridge failed: \(error.localizedDescription)")
                self.stopLocked()
            default:
                break
            }
        }
        listener.newConnectionHandler = { [weak self] connection in
            self?.accept(connection)
        }
        listener.start(queue: queue)
    }

    public func stop() {
        queue.async { [weak self] in
            self?.stopLocked()
        }
    }

    /// Must run on `queue`.
    private func stopLocked() {
        for peer in peers.values {
            peer.connection.cancel()
        }
        peers.removeAll()
        listener?.cancel()
        listener = nil
    }

    // MARK: - Connections

    /// Runs on `queue` (newConnectionHandler contract).
    private func accept(_ connection: NWConnection) {
        let peer = Peer(connection)
        peers[ObjectIdentifier(connection)] = peer

        connection.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled:
                self?.remove(connection)
            default:
                break
            }
        }
        connection.start(queue: queue)
        receiveLoop(peer)

        // Gate 3 deadline: no valid hello within the window → drop.
        queue.asyncAfter(deadline: .now() + helloDeadlineSec) { [weak self, weak peer] in
            guard let self = self, let peer = peer else { return }
            if !peer.authenticated {
                self.onStatus?("local peer dropped: no hello within \(Int(self.helloDeadlineSec))s")
                peer.connection.cancel()
                self.remove(peer.connection)
            }
        }
    }

    private func remove(_ connection: NWConnection) {
        peers.removeValue(forKey: ObjectIdentifier(connection))
    }

    private func receiveLoop(_ peer: Peer) {
        peer.connection.receiveMessage { [weak self, weak peer] data, _, _, error in
            guard let self = self, let peer = peer else { return }
            if error != nil {
                peer.connection.cancel()
                self.remove(peer.connection)
                return
            }
            if let data = data, !data.isEmpty {
                self.handleFrame(data, from: peer)
            }
            // Re-arm unless the frame handler cancelled us.
            if self.peers[ObjectIdentifier(peer.connection)] != nil {
                self.receiveLoop(peer)
            }
        }
    }

    /// Runs on `queue`.
    private func handleFrame(_ data: Data, from peer: Peer) {
        guard
            let obj = try? JSONSerialization.jsonObject(with: data),
            let dict = obj as? [String: Any]
        else {
            // Not JSON. From an unauthenticated peer this is a probe —
            // drop the connection. From an authenticated one it's a
            // bug; drop the frame but keep the (proven) peer.
            if !peer.authenticated {
                peer.connection.cancel()
                remove(peer.connection)
            }
            return
        }

        if !peer.authenticated {
            // Gate 3: the first frame MUST be a matching hello. Any
            // other first frame — including a hello with the wrong
            // session id — costs the caller its connection, so token
            // guessing pays one TCP+WS handshake per attempt.
            stateLock.lock()
            let expected = expectedSessionId
            stateLock.unlock()
            guard LocalBridgeServer.isValidHello(dict, expectedSessionId: expected) else {
                onStatus?("local peer rejected: bad hello")
                peer.connection.cancel()
                remove(peer.connection)
                return
            }
            peer.authenticated = true
            onStatus?("local peer authenticated (session=\(expected))")
            // Ack mirrors the relay's handshake so jam.js can treat
            // hello_ack as the single "transport is live" signal on
            // both paths. `transport` is an additive v2 field.
            send([
                "v": ConnectProtocol.version,
                "type": ConnectProtocol.MessageType.helloAck,
                "protocol_version": ConnectProtocol.version,
                "transport": "local",
            ], to: peer)
            return
        }

        // Authenticated traffic. Answer pings locally (the relay's
        // ping/pong in PresetBridge.dispatch would answer over the
        // wrong socket); everything else goes to the shared dispatcher.
        if (dict["type"] as? String) == ConnectProtocol.MessageType.ping {
            send(["type": ConnectProtocol.MessageType.pong], to: peer)
            return
        }
        onFrame?(dict)
    }

    // MARK: - Outbound

    /// Broadcast a frame to every authenticated local peer. Safe to
    /// call from any thread; no-op with zero peers, so PresetBridge
    /// can mirror telemetry unconditionally.
    public func broadcast(_ dict: [String: Any]) {
        queue.async { [weak self] in
            guard let self = self else { return }
            let targets = self.peers.values.filter { $0.authenticated }
            guard !targets.isEmpty else { return }
            for peer in targets {
                self.send(dict, to: peer)
            }
        }
    }

    /// Must run on `queue`.
    private func send(_ dict: [String: Any], to peer: Peer) {
        guard let data = try? JSONSerialization.data(withJSONObject: dict) else { return }
        let metadata = NWProtocolWebSocket.Metadata(opcode: .text)
        let context = NWConnection.ContentContext(identifier: "text", metadata: [metadata])
        peer.connection.send(
            content: data,
            contentContext: context,
            isComplete: true,
            completion: .contentProcessed { _ in }
        )
    }
}
