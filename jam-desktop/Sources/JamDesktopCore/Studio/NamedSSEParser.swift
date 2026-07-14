// NamedSSEParser.swift
//
// Parse named SSE events from the local engine's analyze-deep stream.
// Format: "event: <name>\ndata: <json>\n\n"
//
// The standard parseSSELine (IntakeClients.swift) handles data-only
// lines. analyze-deep uses named events:
//   event: start     - analysis began
//   event: progress  - { stage, message, percent }
//   event: complete  - full result + history_id
//   event: error     - { message }

import Foundation

/// Parsed named SSE event.
public enum NamedSSEEvent: Equatable, Sendable {
    case start(StartPayload)
    case progress(ProgressPayload)
    case complete(CompletePayload)
    case error(String)
    case unknown(name: String, data: String)

    public struct StartPayload: Codable, Equatable, Sendable {
        public let filename: String?
        public let fileSize: Int?
        public let timestamp: String?

        enum CodingKeys: String, CodingKey {
            case filename
            case fileSize = "file_size"
            case timestamp
        }
    }

    public struct ProgressPayload: Codable, Equatable, Sendable {
        public let stage: String?
        public let message: String?
        public let percent: Double?
    }

    public struct CompletePayload: Equatable, Sendable {
        public let historyId: String?
        public let adminUrl: String?
        public let rawJSON: [String: Any]

        public static func == (lhs: CompletePayload, rhs: CompletePayload) -> Bool {
            lhs.historyId == rhs.historyId && lhs.adminUrl == rhs.adminUrl
        }
    }
}

/// Stateful parser for named SSE streams. Call `feed(_:)` with each
/// chunk; parsed events arrive via the `onEvent` callback.
public final class NamedSSEParser: @unchecked Sendable {
    public var onEvent: ((NamedSSEEvent) -> Void)?

    private var buffer = ""
    private var currentEvent: String?
    private var currentData: String?

    public init() {}

    /// Feed a chunk of bytes from the stream.
    public func feed(_ chunk: Data) {
        guard let text = String(data: chunk, encoding: .utf8) else { return }
        buffer += text
        processBuffer()
    }

    /// Feed a string chunk directly (for testing).
    public func feed(_ text: String) {
        buffer += text
        processBuffer()
    }

    private func processBuffer() {
        // Split on double newlines (event boundaries)
        while let range = buffer.range(of: "\n\n") {
            let block = String(buffer[..<range.lowerBound])
            buffer = String(buffer[range.upperBound...])
            parseBlock(block)
        }
    }

    private func parseBlock(_ block: String) {
        var eventName: String?
        var dataLine: String?

        for line in block.components(separatedBy: "\n") {
            if line.hasPrefix("event:") {
                eventName = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
            } else if line.hasPrefix("data:") {
                dataLine = String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)
            }
        }

        guard let name = eventName, let data = dataLine else { return }
        let event = parseEvent(name: name, data: data)
        onEvent?(event)
    }

    private func parseEvent(name: String, data: String) -> NamedSSEEvent {
        guard let jsonData = data.data(using: .utf8) else {
            return .unknown(name: name, data: data)
        }

        switch name {
        case "start":
            if let payload = try? JSONDecoder().decode(
                NamedSSEEvent.StartPayload.self, from: jsonData
            ) {
                return .start(payload)
            }
        case "progress":
            if let payload = try? JSONDecoder().decode(
                NamedSSEEvent.ProgressPayload.self, from: jsonData
            ) {
                return .progress(payload)
            }
        case "complete":
            if let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any] {
                let payload = NamedSSEEvent.CompletePayload(
                    historyId: json["history_id"] as? String,
                    adminUrl: json["admin_url"] as? String,
                    rawJSON: json
                )
                return .complete(payload)
            }
        case "error":
            if let json = try? JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
               let message = json["message"] as? String {
                return .error(message)
            }
            return .error(data)
        default:
            break
        }
        return .unknown(name: name, data: data)
    }

    /// Reset parser state (between streams).
    public func reset() {
        buffer = ""
        currentEvent = nil
        currentData = nil
    }
}
