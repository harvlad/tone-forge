//
// ObjCExceptionTrapTests.swift
//
// Locks the contract of the ObjC exception trap shipped in
// ``ConnectObjCBridge``. The trap is the load-bearing piece that
// keeps the helper alive when AVAudioEngine's
// ``connect(_:to:format:)`` raises ``NSInvalidArgumentException``
// from a hot-plugged audio interface format flip — without it
// the process gets SIGABRT'd and the supervisor has to bring it
// back up, which surfaces to the user as several seconds of
// "Connect: offline".
//
// We exercise the trap in isolation here rather than through the
// AudioEngine because reproducing the AVAudioEngine path requires
// a real CoreAudio device and a CI-hostile timing window. The
// trap itself is platform-neutral: an NSException raised inside
// the block must become a Swift ``throws``, and a clean run must
// return without throwing.
//

import XCTest
@testable import ConnectCore
import ConnectObjCBridge

final class ObjCExceptionTrapTests: XCTestCase {

    /// A block that does not raise must run to completion without
    /// throwing. Belt-and-braces — this is the no-op case the
    /// production graph wireup hits on every clean reconfigure.
    func testCleanBlockDoesNotThrow() {
        var ran = false
        XCTAssertNoThrow(
            try ObjCExceptionTrap.`try` {
                ran = true
            }
        )
        XCTAssertTrue(ran, "block must execute exactly once")
    }

    /// An NSException raised inside the block must surface as a
    /// Swift error. The error's localizedDescription must carry
    /// the exception ``reason`` so logs (and ``device_lost`` event
    /// reasons) are debuggable.
    func testNSExceptionIsConvertedToSwiftError() {
        do {
            try ObjCExceptionTrap.`try` {
                NSException(
                    name: .invalidArgumentException,
                    reason: "Input HW format and tap format not matching",
                    userInfo: nil
                ).raise()
            }
            XCTFail("expected the trap to throw")
        } catch {
            let desc = (error as NSError).localizedDescription
            XCTAssertTrue(
                desc.contains("Input HW format and tap format not matching"),
                "trap must propagate the exception reason — got: \(desc)"
            )
        }
    }

    /// The trapped error must carry the exception name + call-stack
    /// in userInfo so callers logging the error can include them
    /// when the reason alone is not enough.
    func testTrappedErrorCarriesExceptionMetadata() {
        do {
            try ObjCExceptionTrap.`try` {
                NSException(
                    name: NSExceptionName("ToneForgeTestException"),
                    reason: "synthetic",
                    userInfo: nil
                ).raise()
            }
            XCTFail("expected the trap to throw")
        } catch {
            let nserr = error as NSError
            XCTAssertEqual(
                nserr.domain,
                "ToneForgeConnect.ObjCException",
                "domain pins the trap as the source"
            )
            XCTAssertEqual(
                nserr.userInfo["ExceptionName"] as? String,
                "ToneForgeTestException"
            )
        }
    }
}
