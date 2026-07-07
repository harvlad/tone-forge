// AnalyzeClientTests.swift
//
// Pure-function coverage for AnalyzeClient: golden multipart bytes for
// a fixed boundary, and the SSE line parser against the exact frame
// shapes tone_forge_api.py emits. No network involved.

import XCTest
@testable import ToneForgeEngine

final class AnalyzeClientTests: XCTestCase {

    // MARK: - Multipart golden

    func testMultipartBodyMatchesGoldenBytes() {
        let body = AnalyzeClient.multipartBody(
            fileData: Data([0x01, 0x02, 0x03]),
            filename: "song.wav",
            contentType: "audio/wav",
            fields: [
                (name: "source_kind", value: "upload"),
                (name: "analysis_mode", value: "deep"),
            ],
            boundary: "BOUNDARY"
        )

        var want = Data()
        want.append(Data((
            "--BOUNDARY\r\n"
            + "Content-Disposition: form-data; name=\"source_kind\"\r\n\r\n"
            + "upload\r\n"
            + "--BOUNDARY\r\n"
            + "Content-Disposition: form-data; name=\"analysis_mode\"\r\n\r\n"
            + "deep\r\n"
            + "--BOUNDARY\r\n"
            + "Content-Disposition: form-data; name=\"file\"; filename=\"song.wav\"\r\n"
            + "Content-Type: audio/wav\r\n\r\n"
        ).utf8))
        want.append(Data([0x01, 0x02, 0x03]))
        want.append(Data("\r\n--BOUNDARY--\r\n".utf8))

        XCTAssertEqual(body, want)
    }

    func testMultipartBodyBinaryFilePayloadSurvivesVerbatim() {
        // Bytes that would be mangled by any string round-trip.
        let payload = Data([0x00, 0xff, 0x0d, 0x0a, 0x80])
        let body = AnalyzeClient.multipartBody(
            fileData: payload,
            filename: "x.wav",
            contentType: "audio/wav",
            fields: [],
            boundary: "B"
        )
        XCTAssertNotNil(body.range(of: payload))
        // Empty fields → body starts directly with the file part.
        XCTAssertTrue(body.starts(with: Data("--B\r\nContent-Disposition".utf8)))
    }

    func testDefaultFieldsForceStemSeparation() {
        // The JAM needs stems: deep mode + fast_mode=false (jam.js parity).
        let fields = Dictionary(
            uniqueKeysWithValues: AnalyzeClient.defaultFields.map { ($0.name, $0.value) }
        )
        XCTAssertEqual(fields["fast_mode"], "false")
        XCTAssertEqual(fields["analysis_mode"], "deep")
        XCTAssertEqual(fields["extract_midi"], "true")
        XCTAssertEqual(fields["source_kind"], "upload")
    }

    // MARK: - SSE parsing

    func testParsesProgressWithIntPercent() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "progress", "message": "Separating stems...", "percent": 42}"#
        )
        XCTAssertEqual(frame, .progress(message: "Separating stems...", percent: 42))
    }

    func testParsesProgressWithFloatPercent() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "progress", "message": "x", "percent": 12.5}"#
        )
        XCTAssertEqual(frame, .progress(message: "x", percent: 12.5))
    }

    func testParsesProgressWithoutPercent() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "progress", "message": "Uploading file..."}"#
        )
        XCTAssertEqual(frame, .progress(message: "Uploading file...", percent: nil))
    }

    func testParsesResultHistoryId() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "result", "data": {"history_id": "abc123", "filename": "song.wav"}}"#
        )
        XCTAssertEqual(frame, .result(historyId: "abc123"))
    }

    func testParsesResultWithoutHistoryId() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "result", "data": {"filename": "song.wav"}}"#
        )
        XCTAssertEqual(frame, .result(historyId: nil))
    }

    func testParsesErrorFrame() {
        let frame = AnalyzeClient.parseSSELine(
            #"data: {"type": "error", "message": "Unsupported file type .ogg"}"#
        )
        XCTAssertEqual(frame, .error(message: "Unsupported file type .ogg"))
    }

    func testIgnoresNonFrameLines() {
        XCTAssertNil(AnalyzeClient.parseSSELine(""))
        XCTAssertNil(AnalyzeClient.parseSSELine(": keep-alive"))
        XCTAssertNil(AnalyzeClient.parseSSELine("event: message"))
        XCTAssertNil(AnalyzeClient.parseSSELine("data: not json"))
        XCTAssertNil(AnalyzeClient.parseSSELine(#"data: {"type": "unknown"}"#))
        XCTAssertNil(AnalyzeClient.parseSSELine(#"data: {"no_type": 1}"#))
    }
}
