// IntakeClientsTests.swift
//
// Wire-shape pins for the intake clients: SSE line parsing, upload
// response decode, engine status decode, and the multipart form
// fields the server requires.

import XCTest
import ToneForgeEngine
@testable import JamDesktopCore

final class IntakeClientsTests: XCTestCase {

    // MARK: - SSE line parsing

    func testParsesProgressLine() {
        let line = #"data: {"type":"progress","message":"Separating stems","percent":42}"#
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(line),
            .event(.progress(message: "Separating stems", percent: 42))
        )
    }

    func testParsesProgressWithoutPercent() {
        let line = #"data: {"type":"progress","message":"Downloading audio"}"#
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(line),
            .event(.progress(message: "Downloading audio", percent: nil))
        )
    }

    func testParsesResultWithHistoryId() {
        let line = #"data: {"type":"result","data":{"history_id":"abc123","summary":"x"}}"#
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(line),
            .event(.completed(historyId: "abc123"))
        )
    }

    func testResultFallsBackToAnalysisId() {
        let line = #"data: {"type":"result","data":{"analysis_id":"a9"}}"#
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(line),
            .event(.completed(historyId: "a9"))
        )
    }

    func testParsesErrorLine() {
        let line = #"data: {"type":"error","message":"Download failed"}"#
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(line),
            .serverError("Download failed")
        )
    }

    func testIgnoresNonDataAndGarbageLines() {
        XCTAssertEqual(URLAnalyzeClient.parseSSELine(": heartbeat"), .none)
        XCTAssertEqual(URLAnalyzeClient.parseSSELine(""), .none)
        XCTAssertEqual(URLAnalyzeClient.parseSSELine("data: not-json"), .none)
        XCTAssertEqual(
            URLAnalyzeClient.parseSSELine(#"data: {"no_type":true}"#), .none)
    }

    // MARK: - Response decodes

    func testUploadStartDecodesSnakeCase() throws {
        let json = #"{"job_id":"job-7","engine_online":true}"#.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(UploadStart.self, from: json)
        XCTAssertEqual(decoded, UploadStart(jobId: "job-7", engineOnline: true))
    }

    func testEngineStatusDecodes() throws {
        let json = #"{"online":true,"device":"cuda"}"#.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(EngineStatus.self, from: json)
        XCTAssertEqual(decoded, EngineStatus(online: true, device: "cuda"))

        let offline = #"{"online":false,"device":null}"#.data(using: .utf8)!
        let decodedOffline = try JSONDecoder().decode(EngineStatus.self, from: offline)
        XCTAssertEqual(decodedOffline, EngineStatus(online: false, device: nil))
    }

    // MARK: - Upload form shape

    func testUploadFormFieldsMatchServerContract() {
        // Server 400s without attested=true; extract_midi mirrors the
        // web jam upload path.
        let fields = Dictionary(
            uniqueKeysWithValues: UploadClient.formFields.map { ($0.name, $0.value) })
        XCTAssertEqual(fields["attested"], "true")
        XCTAssertEqual(fields["extract_midi"], "true")
    }
}
