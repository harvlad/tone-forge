// LocalEngineClient.swift
//
// Client for the local GPU engine (127.0.0.1:7777). Provides:
//   - health probe to detect if engine is running
//   - analyze-deep SSE stream with named event parsing
//
// The local engine runs the same analysis pipeline as the hosted
// backend but with GPU acceleration for faster processing.

import Foundation

/// Local engine availability status.
public enum LocalEngineStatus: Equatable, Sendable {
    case unknown
    case available
    case unavailable
}

/// Progress update during deep analysis.
public struct DeepAnalysisProgress: Equatable, Sendable {
    public let stage: String?
    public let message: String
    public let percent: Double?
}

/// Result of a deep analysis run.
public struct DeepAnalysisResult: Equatable, Sendable {
    public let historyId: String
}

/// Protocol for local engine operations.
public protocol LocalEngineProbing: Sendable {
    func checkHealth() async -> LocalEngineStatus
}

public protocol DeepAnalyzing: Sendable {
    func analyzeDeep(
        fileURL: URL,
        filename: String,
        startTime: Double?,
        endTime: Double?
    ) -> AsyncThrowingStream<DeepAnalysisEvent, Error>
}

public enum DeepAnalysisEvent: Sendable {
    case started
    case progress(DeepAnalysisProgress)
    case completed(DeepAnalysisResult)
}

/// Default local engine URL.
public let localEngineBaseURL = URL(string: "http://127.0.0.1:7777")!

public struct LocalEngineClient: LocalEngineProbing, DeepAnalyzing {
    private let baseURL: URL
    private let session: URLSession
    private let healthTimeout: TimeInterval
    private let analyzeTimeout: TimeInterval

    public init(
        baseURL: URL = localEngineBaseURL,
        session: URLSession = .shared,
        healthTimeout: TimeInterval = 2,
        analyzeTimeout: TimeInterval = 600
    ) {
        self.baseURL = baseURL
        self.session = session
        self.healthTimeout = healthTimeout
        self.analyzeTimeout = analyzeTimeout
    }

    public func checkHealth() async -> LocalEngineStatus {
        let url = baseURL.appendingPathComponent("health")
        var request = URLRequest(url: url, timeoutInterval: healthTimeout)
        request.httpMethod = "GET"

        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse,
                  http.statusCode == 200 else {
                return .unavailable
            }
            // Verify it's our engine
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               json["service"] as? String == "toneforge-local" {
                return .available
            }
            return .unavailable
        } catch {
            return .unavailable
        }
    }

    public func analyzeDeep(
        fileURL: URL,
        filename: String,
        startTime: Double?,
        endTime: Double?
    ) -> AsyncThrowingStream<DeepAnalysisEvent, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    let url = baseURL.appendingPathComponent("api/analyze-deep")
                    var request = URLRequest(url: url, timeoutInterval: analyzeTimeout)
                    request.httpMethod = "POST"

                    // Build multipart form
                    let boundary = UUID().uuidString
                    request.setValue(
                        "multipart/form-data; boundary=\(boundary)",
                        forHTTPHeaderField: "Content-Type"
                    )

                    var body = Data()
                    let fileData = try Data(contentsOf: fileURL)

                    // File part
                    body.append("--\(boundary)\r\n".data(using: .utf8)!)
                    body.append(
                        "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n"
                            .data(using: .utf8)!
                    )
                    body.append("Content-Type: application/octet-stream\r\n\r\n".data(using: .utf8)!)
                    body.append(fileData)
                    body.append("\r\n".data(using: .utf8)!)

                    // Optional trim fields
                    if let start = startTime {
                        body.append("--\(boundary)\r\n".data(using: .utf8)!)
                        body.append(
                            "Content-Disposition: form-data; name=\"start_time\"\r\n\r\n"
                                .data(using: .utf8)!
                        )
                        body.append("\(start)\r\n".data(using: .utf8)!)
                    }
                    if let end = endTime {
                        body.append("--\(boundary)\r\n".data(using: .utf8)!)
                        body.append(
                            "Content-Disposition: form-data; name=\"end_time\"\r\n\r\n"
                                .data(using: .utf8)!
                        )
                        body.append("\(end)\r\n".data(using: .utf8)!)
                    }

                    body.append("--\(boundary)--\r\n".data(using: .utf8)!)
                    request.httpBody = body

                    let (bytes, response) = try await session.bytes(for: request)
                    guard let http = response as? HTTPURLResponse,
                          http.statusCode == 200 else {
                        let code = (response as? HTTPURLResponse)?.statusCode ?? -1
                        throw URLError(
                            .badServerResponse,
                            userInfo: [NSLocalizedDescriptionKey: "HTTP \(code)"]
                        )
                    }

                    let parser = NamedSSEParser()
                    parser.onEvent = { event in
                        switch event {
                        case .start:
                            continuation.yield(.started)
                        case let .progress(payload):
                            continuation.yield(.progress(DeepAnalysisProgress(
                                stage: payload.stage,
                                message: payload.message ?? "Processing...",
                                percent: payload.percent
                            )))
                        case let .complete(payload):
                            if let historyId = payload.historyId {
                                continuation.yield(.completed(DeepAnalysisResult(
                                    historyId: historyId
                                )))
                            }
                        case let .error(message):
                            continuation.finish(throwing: NSError(
                                domain: "LocalEngine",
                                code: -1,
                                userInfo: [NSLocalizedDescriptionKey: message]
                            ))
                        case .unknown:
                            break
                        }
                    }

                    for try await chunk in bytes {
                        parser.feed(Data([chunk]))
                    }
                    continuation.finish()

                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }
}
