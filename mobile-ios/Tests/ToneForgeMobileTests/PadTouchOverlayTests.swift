// PadTouchOverlayTests.swift
//
// Pins the launchpad-edit-mode gesture contract on the shared pad
// input surface (PadTouchUIView):
//
//   Edit OFF (no long-press consumer): touch-down fires the pad
//   attack synchronously and arms NOTHING else — no hold timer
//   exists, so a sustained hold can never be hijacked (voice cut +
//   radial) at the 0.5 s threshold. "Zero gesture-recognition tax"
//   means the recognition is absent, not that its callback no-ops.
//
//   Edit ON: the hold timer releases the voice first, then fires
//   onLongPress; the eventual touch-up routes to onLongPressEnd,
//   never a second pad-up. A quick tap beats the timer.
//
// The handlers are keyed (AnyHashable stands in for UITouch) so these
// run as plain unit tests — no UI test, no touch synthesis.

import XCTest
@testable import ToneForgeMobile

#if canImport(UIKit)
import UIKit

@MainActor
final class PadTouchOverlayTests: XCTestCase {

    private func makeView() -> PadTouchUIView {
        let view = PadTouchUIView(
            frame: CGRect(x: 0, y: 0, width: 400, height: 400))
        view.rows = 4
        view.cols = 4
        // Real threshold is 0.5 s; shrink it so the edit-on tests
        // don't stall the suite. The edit-off test holds PAST the
        // interval to prove absence, not just "hasn't fired yet".
        view.longPressInterval = 0.05
        return view
    }

    /// Bottom-left pad in a 400×400 4×4 grid → (row 1, col 1).
    private let bottomLeft = CGPoint(x: 50, y: 350)

    func testEditOffHoldIsNeverHijacked() {
        let view = makeView()
        var downs = 0
        var ups = 0
        view.onPadDown = { _, _ in downs += 1 }
        view.onPadUp = { _, _ in ups += 1 }
        view.onLongPress = nil   // performance mode

        view.handleTouchBegan("t1", at: bottomLeft)
        // Attack is synchronous with the touch…
        XCTAssertEqual(downs, 1)
        // …and nothing else got armed: no timer exists to hijack the hold.
        XCTAssertFalse(view.hasPendingLongPress)

        // Hold well past the long-press interval: the voice must keep
        // ringing (with the timer armed this is where onPadUp fired).
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        XCTAssertEqual(ups, 0, "edit-off hold was cut by a hold timer")

        // The FINGER releases the pad — exactly once.
        view.handleTouchEnded("t1", at: bottomLeft)
        XCTAssertEqual(ups, 1)
    }

    func testEditOnHoldReleasesVoiceThenOpensRadial() {
        let view = makeView()
        var events: [String] = []
        view.onPadDown = { _, _ in events.append("down") }
        view.onPadUp = { _, _ in events.append("up") }
        view.onLongPress = { _, _ in events.append("longPress") }
        view.onLongPressEnd = { _ in events.append("longPressEnd") }

        view.handleTouchBegan("t1", at: bottomLeft)
        XCTAssertTrue(view.hasPendingLongPress)

        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        // Voice released BEFORE the radial opens (no ringing under the
        // wheel), and the later touch-up is the radial's, not a pad-up.
        XCTAssertEqual(events, ["down", "up", "longPress"])

        view.handleTouchEnded("t1", at: bottomLeft)
        XCTAssertEqual(events, ["down", "up", "longPress", "longPressEnd"])
    }

    func testEditOnQuickTapBeatsTheTimer() {
        let view = makeView()
        var events: [String] = []
        view.onPadDown = { _, _ in events.append("down") }
        view.onPadUp = { _, _ in events.append("up") }
        view.onLongPress = { _, _ in events.append("longPress") }

        view.handleTouchBegan("t1", at: bottomLeft)
        view.handleTouchEnded("t1", at: bottomLeft)
        XCTAssertEqual(events, ["down", "up"])
        XCTAssertFalse(view.hasPendingLongPress)

        // The cancelled timer must never fire late.
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        XCTAssertEqual(events, ["down", "up"])
    }
}

#endif
