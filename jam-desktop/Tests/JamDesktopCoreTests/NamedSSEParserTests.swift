// NamedSSEParserTests.swift
//
// Named SSE event parsing for analyze-deep stream.

import XCTest
@testable import JamDesktopCore

final class NamedSSEParserTests: XCTestCase {

    func testParseStartEvent() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: start\ndata: {\"filename\": \"test.mp3\", \"file_size\": 1024}\n\n")

        XCTAssertEqual(events.count, 1)
        if case let .start(payload) = events[0] {
            XCTAssertEqual(payload.filename, "test.mp3")
            XCTAssertEqual(payload.fileSize, 1024)
        } else {
            XCTFail("Expected start event")
        }
    }

    func testParseProgressEvent() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: progress\ndata: {\"stage\": \"loading\", \"message\": \"Loading audio...\", \"percent\": 25.5}\n\n")

        XCTAssertEqual(events.count, 1)
        if case let .progress(payload) = events[0] {
            XCTAssertEqual(payload.stage, "loading")
            XCTAssertEqual(payload.message, "Loading audio...")
            XCTAssertEqual(payload.percent, 25.5)
        } else {
            XCTFail("Expected progress event")
        }
    }

    func testParseCompleteEvent() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: complete\ndata: {\"history_id\": \"abc123\", \"admin_url\": \"/studio?analysis=abc123\", \"tempo_bpm\": 120}\n\n")

        XCTAssertEqual(events.count, 1)
        if case let .complete(payload) = events[0] {
            XCTAssertEqual(payload.historyId, "abc123")
            XCTAssertEqual(payload.adminUrl, "/studio?analysis=abc123")
        } else {
            XCTFail("Expected complete event")
        }
    }

    func testParseErrorEvent() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: error\ndata: {\"message\": \"Analysis failed\"}\n\n")

        XCTAssertEqual(events.count, 1)
        if case let .error(message) = events[0] {
            XCTAssertEqual(message, "Analysis failed")
        } else {
            XCTFail("Expected error event")
        }
    }

    func testParseMultipleEventsInStream() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: start\ndata: {\"filename\": \"test.mp3\"}\n\nevent: progress\ndata: {\"message\": \"Working...\", \"percent\": 50}\n\nevent: complete\ndata: {\"history_id\": \"xyz789\"}\n\n")

        XCTAssertEqual(events.count, 3)
        XCTAssertNotNil(events[0] as? NamedSSEEvent)
        if case .start = events[0] {} else { XCTFail("Expected start") }
        if case .progress = events[1] {} else { XCTFail("Expected progress") }
        if case .complete = events[2] {} else { XCTFail("Expected complete") }
    }

    func testChunkedInput() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        // Feed in chunks
        parser.feed("event: prog")
        XCTAssertEqual(events.count, 0)
        parser.feed("ress\ndata: {")
        XCTAssertEqual(events.count, 0)
        parser.feed("\"message\": \"test\"}\n\n")
        XCTAssertEqual(events.count, 1)
    }

    func testReset() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: start\ndata: {")
        parser.reset()
        parser.feed("event: progress\ndata: {\"message\": \"new\"}\n\n")

        XCTAssertEqual(events.count, 1)
        if case .progress = events[0] {} else { XCTFail("Expected progress after reset") }
    }

    func testUnknownEventType() {
        let parser = NamedSSEParser()
        var events: [NamedSSEEvent] = []
        parser.onEvent = { events.append($0) }

        parser.feed("event: custom_event\ndata: {\"foo\": \"bar\"}\n\n")

        XCTAssertEqual(events.count, 1)
        if case let .unknown(name, _) = events[0] {
            XCTAssertEqual(name, "custom_event")
        } else {
            XCTFail("Expected unknown event")
        }
    }
}
