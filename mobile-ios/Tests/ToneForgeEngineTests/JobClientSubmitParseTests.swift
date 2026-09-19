// JobClientSubmitParseTests.swift
//
// The /api/analyze-upload response parse must route a content-hash
// dedupe to "open the existing song" instead of a null-job dead card,
// while staying backward-compatible with the legacy {"job_id": ...}
// shape (parity with jam.js's duplicate handling).

import XCTest
import ToneForgeEngine

final class JobClientSubmitParseTests: XCTestCase {

    private func parse(_ json: String) -> JobSubmission? {
        BackendJobClient.parseSubmitResponse(Data(json.utf8))
    }

    func testFreshJobDecodesAsJob() {
        let sub = parse(#"{"job_id":"job-7","engine_online":true}"#)
        XCTAssertEqual(sub, JobSubmission(jobId: "job-7"))
        XCTAssertFalse(sub?.duplicate ?? true)
    }

    func testCompletedDuplicateOpensExistingSong() {
        // Backend dedupe against a finished analysis: no job, history id.
        let sub = parse(#"{"job_id":null,"history_id":"hist-42","duplicate":true}"#)
        XCTAssertEqual(
            sub, JobSubmission(jobId: nil, duplicate: true, historyId: "hist-42"))
        // The caller keys off these two to open instead of enqueue.
        XCTAssertTrue(sub?.duplicate == true)
        XCTAssertEqual(sub?.historyId, "hist-42")
        XCTAssertNil(sub?.jobId)
    }

    func testInFlightDuplicateStillFollowsRealJob() {
        // Dedupe against an in-flight job hands back a real job id — the
        // client follows it transparently (no open-existing shortcut).
        let sub = parse(#"{"job_id":"job-9","history_id":"hist-9","duplicate":true}"#)
        XCTAssertEqual(sub?.jobId, "job-9")
    }

    func testEmptyJobIdWithoutDuplicateIsRejected() {
        XCTAssertNil(parse(#"{"job_id":""}"#))
        XCTAssertNil(parse(#"{"engine_online":true}"#))
        XCTAssertNil(parse("not json"))
    }
}
