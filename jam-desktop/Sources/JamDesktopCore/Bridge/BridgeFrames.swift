// BridgeFrames.swift
//
// Codable models for the /ws/connect-bridge v2 protocol, matching
// the wire byte-for-byte:
//
//   - field names mirror backend/tone_forge/session/protocol.py and
//     jam.js emission sites (snake_case, explicit CodingKeys — no
//     keyDecodingStrategy magic);
//   - latency_report uses the *_ms field family that Connect actually
//     sends and jam.js actually reads (jam.js:897). The protocol.py
//     TypedDict's *_sec spelling is stale — never on the wire.
//
// M1 scope: codec only. BridgeClient (M4) will own the socket.

import Foundation

public enum BridgeProtocolInfo {
    /// Mirrors CONNECT_BRIDGE_PROTOCOL_VERSION (tone_forge_api.py).
    public static let version = 2
}

// MARK: - Framing

public struct HelloFrame: Codable, Equatable, Sendable {
    public var type = "hello"
    public var role: String
    public var sessionId: String
    public var protocolVersion: Int
    public var clientKind: String

    public init(
        role: String = "connect",
        sessionId: String,
        protocolVersion: Int = BridgeProtocolInfo.version,
        clientKind: String = "jam-desktop"
    ) {
        self.role = role
        self.sessionId = sessionId
        self.protocolVersion = protocolVersion
        self.clientKind = clientKind
    }

    enum CodingKeys: String, CodingKey {
        case type, role
        case sessionId = "session_id"
        case protocolVersion = "protocol_version"
        case clientKind = "client_kind"
    }
}

public struct HelloAckFrame: Codable, Equatable, Sendable {
    public var protocolVersion: Int?

    enum CodingKeys: String, CodingKey {
        case protocolVersion = "protocol_version"
    }
}

public struct VersionMismatchFrame: Codable, Equatable, Sendable {
    public var required: Int?
    public var client: Int?
}

public struct JoinedFrame: Codable, Equatable, Sendable {
    public var peers: Int?
    public var sessionId: String?

    enum CodingKeys: String, CodingKey {
        case peers
        case sessionId = "session_id"
    }
}

public struct ErrorFrame: Codable, Equatable, Sendable {
    public var code: String?
    public var message: String?
    public var retriable: Bool?
}

public struct PingFrame: Codable, Equatable, Sendable {
    public var type = "ping"
    public var nonce: String?

    public init(nonce: String? = nil) { self.nonce = nonce }

    enum CodingKeys: String, CodingKey { case type, nonce }
}

public struct PongFrame: Codable, Equatable, Sendable {
    public var type = "pong"
    public var nonce: String?

    public init(nonce: String? = nil) { self.nonce = nonce }

    enum CodingKeys: String, CodingKey { case type, nonce }
}

// MARK: - Transport

/// Canonical transport snapshot. jam.js emits only
/// {playing, position_s}; the desktop app (transport authority)
/// additionally mirrors tempo/loop so a co-open browser can render
/// them. All fields optional on decode — peers send subsets.
public struct TransportStateFrame: Codable, Equatable, Sendable {
    public var type = "transport_state"
    public var v = BridgeProtocolInfo.version
    public var playing: Bool?
    public var positionS: Double?
    public var tempoPct: Double?
    public var loopInS: Double?
    public var loopOutS: Double?

    public init(
        playing: Bool? = nil,
        positionS: Double? = nil,
        tempoPct: Double? = nil,
        loopInS: Double? = nil,
        loopOutS: Double? = nil
    ) {
        self.playing = playing
        self.positionS = positionS
        self.tempoPct = tempoPct
        self.loopInS = loopInS
        self.loopOutS = loopOutS
    }

    /// Tolerant: jam.js emits {type, playing, position_s} without a
    /// `v` field — synthesized decoding would reject the frame.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = try container.decodeIfPresent(String.self, forKey: .type) ?? "transport_state"
        v = try container.decodeIfPresent(Int.self, forKey: .v) ?? BridgeProtocolInfo.version
        playing = try container.decodeIfPresent(Bool.self, forKey: .playing)
        positionS = try container.decodeIfPresent(Double.self, forKey: .positionS)
        tempoPct = try container.decodeIfPresent(Double.self, forKey: .tempoPct)
        loopInS = try container.decodeIfPresent(Double.self, forKey: .loopInS)
        loopOutS = try container.decodeIfPresent(Double.self, forKey: .loopOutS)
    }

    enum CodingKeys: String, CodingKey {
        case type, v, playing
        case positionS = "position_s"
        case tempoPct = "tempo_pct"
        case loopInS = "loop_in_s"
        case loopOutS = "loop_out_s"
    }
}

// MARK: - Session data (B↔C after analysis / on load)

public struct SessionDataFrame: Codable, Equatable, Sendable {
    public struct Song: Codable, Equatable, Sendable {
        public var id: String?
        public var title: String?

        public init(id: String? = nil, title: String? = nil) {
            self.id = id
            self.title = title
        }
    }

    public struct Key: Codable, Equatable, Sendable {
        public var root: Int?
        public var scale: String?

        public init(root: Int? = nil, scale: String? = nil) {
            self.root = root
            self.scale = scale
        }
    }

    public struct ChordSpan: Codable, Equatable, Sendable {
        public var symbol: String
        public var startS: Double
        public var endS: Double

        public init(symbol: String, startS: Double, endS: Double) {
            self.symbol = symbol
            self.startS = startS
            self.endS = endS
        }

        enum CodingKeys: String, CodingKey {
            case symbol
            case startS = "start_s"
            case endS = "end_s"
        }
    }

    public struct SectionMarker: Codable, Equatable, Sendable {
        public var name: String
        public var startS: Double
        public var endS: Double

        public init(name: String, startS: Double, endS: Double) {
            self.name = name
            self.startS = startS
            self.endS = endS
        }

        enum CodingKeys: String, CodingKey {
            case name
            case startS = "start_s"
            case endS = "end_s"
        }
    }

    public var type = "session_data"
    public var v = BridgeProtocolInfo.version
    public var sessionId: String?
    public var song: Song?
    public var bpm: Double?
    public var key: Key?
    public var chordProgression: [ChordSpan]?
    public var sectionMarkers: [SectionMarker]?

    public init(
        sessionId: String? = nil,
        song: Song? = nil,
        bpm: Double? = nil,
        key: Key? = nil,
        chordProgression: [ChordSpan]? = nil,
        sectionMarkers: [SectionMarker]? = nil
    ) {
        self.sessionId = sessionId
        self.song = song
        self.bpm = bpm
        self.key = key
        self.chordProgression = chordProgression
        self.sectionMarkers = sectionMarkers
    }

    enum CodingKeys: String, CodingKey {
        case type, v, song, bpm, key
        case sessionId = "session_id"
        case chordProgression = "chord_progression"
        case sectionMarkers = "section_markers"
    }
}

// MARK: - Stems handoff

public struct LoadStemsFrame: Codable, Equatable, Sendable {
    public struct Stem: Codable, Equatable, Sendable {
        public var id: String
        public var url: String
        public var displayName: String?

        public init(id: String, url: String, displayName: String? = nil) {
            self.id = id
            self.url = url
            self.displayName = displayName
        }

        enum CodingKeys: String, CodingKey {
            case id, url
            case displayName = "display_name"
        }
    }

    public var type = "load_stems"
    public var v = BridgeProtocolInfo.version
    public var stems: [Stem]

    public init(stems: [Stem]) { self.stems = stems }

    enum CodingKeys: String, CodingKey { case type, v, stems }
}

// MARK: - Tone / gain intents (browser → audio owner)

public struct ApplyChainFrame: Codable, Equatable, Sendable {
    public var chainId: String?

    enum CodingKeys: String, CodingKey {
        case chainId = "chain_id"
    }
}

/// Outbound apply_chain (desktop tone card -> server). Mirrors
/// jam.js applyToneChain: {type, chain_id, request_id}. The server
/// resolves the id to a full spec, broadcasts to peers and acks the
/// sender with the resolved chain embedded.
public struct ApplyChainRequestFrame: Codable, Equatable, Sendable {
    public var type = "apply_chain"
    public var chainId: String
    public var requestId: String

    public init(chainId: String, requestId: String) {
        self.chainId = chainId
        self.requestId = requestId
    }

    enum CodingKeys: String, CodingKey {
        case type
        case chainId = "chain_id"
        case requestId = "request_id"
    }
}

/// Server acknowledgement for request_id-carrying intents. For
/// apply_chain the resolved spec rides along under a "chain" key
/// (heterogeneous dict — extracted from the raw frame data, like
/// inbound apply_chain).
public struct AckFrame: Codable, Equatable, Sendable {
    public var requestId: String?
    public var chainId: String?

    enum CodingKeys: String, CodingKey {
        case requestId = "request_id"
        case chainId = "chain_id"
    }
}

/// Legacy pre-v1 spelling of set_monitor_gain; jam.js still emits it.
public struct SetGainFrame: Codable, Equatable, Sendable {
    public var gain: Double?
}

public struct MeasureLatencyFrame: Codable, Equatable, Sendable {
    public var type = "measure_latency"
    public var v = BridgeProtocolInfo.version

    public init() {}

    enum CodingKeys: String, CodingKey { case type, v }
}

// MARK: - Audio-owner state (this app → browser)

public struct ConnectStateFrame: Codable, Equatable, Sendable {
    public struct Device: Codable, Equatable, Sendable {
        public var inputName: String?
        public var outputName: String?
        public var sampleRate: Double?
        public var channelsIn: Int?

        public init(
            inputName: String? = nil,
            outputName: String? = nil,
            sampleRate: Double? = nil,
            channelsIn: Int? = nil
        ) {
            self.inputName = inputName
            self.outputName = outputName
            self.sampleRate = sampleRate
            self.channelsIn = channelsIn
        }

        enum CodingKeys: String, CodingKey {
            case inputName = "input_name"
            case outputName = "output_name"
            case sampleRate = "sample_rate"
            case channelsIn = "channels_in"
        }
    }

    public struct Monitor: Codable, Equatable, Sendable {
        public var enabled: Bool?
        public var gain: Double?
        public var muted: Bool?

        public init(enabled: Bool? = nil, gain: Double? = nil, muted: Bool? = nil) {
            self.enabled = enabled
            self.gain = gain
            self.muted = muted
        }
    }

    public struct DSP: Codable, Equatable, Sendable {
        public var ampSimEnabled: Bool?
        public var activeChainId: String?

        public init(ampSimEnabled: Bool? = nil, activeChainId: String? = nil) {
            self.ampSimEnabled = ampSimEnabled
            self.activeChainId = activeChainId
        }

        enum CodingKeys: String, CodingKey {
            case ampSimEnabled = "amp_sim_enabled"
            case activeChainId = "active_chain_id"
        }
    }

    public var type = "connect_state"
    public var v = BridgeProtocolInfo.version
    public var state: String?
    public var device: Device?
    public var monitor: Monitor?
    public var dsp: DSP?

    public init(
        state: String? = nil,
        device: Device? = nil,
        monitor: Monitor? = nil,
        dsp: DSP? = nil
    ) {
        self.state = state
        self.device = device
        self.monitor = monitor
        self.dsp = dsp
    }

    enum CodingKeys: String, CodingKey { case type, v, state, device, monitor, dsp }
}

/// Wire truth is the *_ms family (Connect sends it, jam.js:897 reads
/// it). measured_* only appears after a successful impulse probe.
public struct LatencyReportFrame: Codable, Equatable, Sendable {
    public var type = "latency_report"
    public var v = BridgeProtocolInfo.version
    public var inputMs: Double?
    public var outputMs: Double?
    public var bufferMs: Double?
    public var estimatedRoundTripMs: Double?
    public var measuredRoundTripMs: Double?
    public var measurementConfidence: String?

    public init(
        inputMs: Double? = nil,
        outputMs: Double? = nil,
        bufferMs: Double? = nil,
        estimatedRoundTripMs: Double? = nil,
        measuredRoundTripMs: Double? = nil,
        measurementConfidence: String? = nil
    ) {
        self.inputMs = inputMs
        self.outputMs = outputMs
        self.bufferMs = bufferMs
        self.estimatedRoundTripMs = estimatedRoundTripMs
        self.measuredRoundTripMs = measuredRoundTripMs
        self.measurementConfidence = measurementConfidence
    }

    enum CodingKeys: String, CodingKey {
        case type, v
        case inputMs = "input_ms"
        case outputMs = "output_ms"
        case bufferMs = "buffer_ms"
        case estimatedRoundTripMs = "estimated_round_trip_ms"
        case measuredRoundTripMs = "measured_round_trip_ms"
        case measurementConfidence = "measurement_confidence"
    }
}

public struct InputMeterFrame: Codable, Equatable, Sendable {
    public var type = "input_meter"
    public var v = BridgeProtocolInfo.version
    public var peakDbfs: Double?
    public var rmsDbfs: Double?

    public init(peakDbfs: Double? = nil, rmsDbfs: Double? = nil) {
        self.peakDbfs = peakDbfs
        self.rmsDbfs = rmsDbfs
    }

    enum CodingKeys: String, CodingKey {
        case type, v
        case peakDbfs = "peak_dbfs"
        case rmsDbfs = "rms_dbfs"
    }
}

// MARK: - Decoder

/// A decoded inbound frame, dispatched on the wire `type` string.
/// Unknown types are preserved (relay channel carries frames this
/// client doesn't consume — never treat them as errors).
public enum BridgeFrame: Equatable, Sendable {
    case helloAck(HelloAckFrame)
    case versionMismatch(VersionMismatchFrame)
    case joined(JoinedFrame)
    case error(ErrorFrame)
    case ping(PingFrame)
    case pong(PongFrame)
    case transportState(TransportStateFrame)
    case sessionData(SessionDataFrame)
    case loadStems(LoadStemsFrame)
    case applyChain(ApplyChainFrame)
    case ack(AckFrame)
    case setGain(SetGainFrame)
    case setMonitorGain(SetGainFrame)
    case measureLatency
    case connectState(ConnectStateFrame)
    case latencyReport(LatencyReportFrame)
    case inputMeter(InputMeterFrame)
    case peerLeft
    case unknown(type: String)
}

public enum BridgeFrameCodec {

    public enum CodecError: Error, Equatable {
        case missingType
    }

    private struct Envelope: Decodable { let type: String? }

    public static func decode(_ data: Data) throws -> BridgeFrame {
        let decoder = JSONDecoder()
        guard let type = try decoder.decode(Envelope.self, from: data).type else {
            throw CodecError.missingType
        }
        switch type {
        case "hello_ack":
            return .helloAck(try decoder.decode(HelloAckFrame.self, from: data))
        case "version_mismatch":
            return .versionMismatch(try decoder.decode(VersionMismatchFrame.self, from: data))
        case "joined":
            return .joined(try decoder.decode(JoinedFrame.self, from: data))
        case "error":
            return .error(try decoder.decode(ErrorFrame.self, from: data))
        case "ping":
            return .ping(try decoder.decode(PingFrame.self, from: data))
        case "pong":
            return .pong(try decoder.decode(PongFrame.self, from: data))
        case "transport_state":
            return .transportState(try decoder.decode(TransportStateFrame.self, from: data))
        case "session_data":
            return .sessionData(try decoder.decode(SessionDataFrame.self, from: data))
        case "load_stems":
            return .loadStems(try decoder.decode(LoadStemsFrame.self, from: data))
        case "apply_chain":
            return .applyChain(try decoder.decode(ApplyChainFrame.self, from: data))
        case "ack":
            return .ack(try decoder.decode(AckFrame.self, from: data))
        case "set_gain":
            return .setGain(try decoder.decode(SetGainFrame.self, from: data))
        case "set_monitor_gain":
            return .setMonitorGain(try decoder.decode(SetGainFrame.self, from: data))
        case "measure_latency":
            return .measureLatency
        case "connect_state":
            return .connectState(try decoder.decode(ConnectStateFrame.self, from: data))
        case "latency_report":
            return .latencyReport(try decoder.decode(LatencyReportFrame.self, from: data))
        case "input_meter":
            return .inputMeter(try decoder.decode(InputMeterFrame.self, from: data))
        case "peer_left":
            return .peerLeft
        default:
            return .unknown(type: type)
        }
    }

    public static func encode<F: Encodable>(_ frame: F) throws -> Data {
        try JSONEncoder().encode(frame)
    }
}
