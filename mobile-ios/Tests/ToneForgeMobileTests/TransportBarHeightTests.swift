// TransportBarHeightTests.swift
//
// Layout-invariant regression guard (this one regressed TWICE): the
// bottom transport bar must keep a CONSTANT height no matter what state
// the audio-output RecordToggle is in. The failure mode: when the pill
// swapped from the idle red dot to the LIVE recording meter + elapsed-
// time label ("Rec 0:07"), the bar GREW and shoved the whole screen.
//
// Two frames pin it and this suite locks BOTH:
//   • TransportRow.swift  `.frame(height: 48)` — the outer bar (belt).
//     testTransportRowHeightConstantAcrossRecordStates measures the whole
//     row idle vs recording and asserts each == 48; drop the pin and the
//     row collapses to the play glyph's ~45pt, so 48 fails.
//   • RecordToggle.swift  `.frame(height: 18)` — the pill's own content
//     box (suspenders). testRecordToggleAudioOutputHeightConstant…
//     measures the pill directly idle vs recording; drop that pin and the
//     recording meter+caption grows the box past the idle dot, so the
//     idle == recording equality fails. (The outer bar clamps this in the
//     full-row test, so it needs its own measurement to be observable.)
//
// Method: host each view in a UIHostingController and read the rendered
// height via sizeThatFits under a fixed full-width, unbounded-height
// proposal — a real reflow, not a "does the modifier exist" structure
// check. The OutputRecorder is driven into .recording through a test
// seam (setPublishedStateForTesting) because there is no running
// AVAudioEngine in the unit environment to start() a real capture.
//
// UIKit-only: on macOS `swift test` there is no UIHostingController, so
// these XCTSkip. Run via `xcodebuild test` on an iOS Simulator.

import XCTest
import SwiftUI
import ToneForgeEngine
@testable import ToneForgeMobile

#if canImport(UIKit)
import UIKit
#endif

@MainActor
final class TransportBarHeightTests: XCTestCase {

    /// The transport spans the screen edge-to-edge; propose a real phone
    /// width so its trailing `Spacer` has slack to eat. iPhone 17 Pro is
    /// 393 pt wide — the destination this suite runs on.
    private static let proposedWidth: CGFloat = 393

    /// Sub-point layout drift between simulator runtimes is expected;
    /// the regressions this guards move the bar by whole points (a
    /// growing meter, a missing pin), so a tight tolerance still catches
    /// them while absorbing rasteriser noise.
    private static let tolerance: CGFloat = 0.5

    #if canImport(UIKit)
    /// Rendered height of `view` under a fixed width and unbounded
    /// height. Unbounded height is deliberate: a `.frame(height:)`
    /// ignores the proposal and reports its pinned value, so removing the
    /// pin visibly changes this number.
    private func measuredHeight(of view: some View) -> CGFloat {
        let host = UIHostingController(rootView: view)
        host.loadViewIfNeeded()
        let fitting = host.sizeThatFits(
            in: CGSize(width: Self.proposedWidth, height: .greatestFiniteMagnitude))
        return fitting.height
    }
    #endif

    // MARK: - TransportRow (the whole bar — outer 48pt pin)

    func testTransportRowHeightConstantAcrossRecordStates() throws {
        #if canImport(UIKit)
        // Idle: a fresh recorder reports .idle → the pill is just the dot.
        let idleApp = AppState()
        let idleHeight = measuredHeight(
            of: TransportRow().environmentObject(idleApp))

        // Recording: force the OutputRecorder into .recording so the pill
        // renders the live level meter + "Rec 0:07" label — the exact
        // state that used to grow the bar.
        let recApp = AppState()
        recApp.outputRecorder.setPublishedStateForTesting(.recording, elapsedSec: 7)
        let recHeight = measuredHeight(
            of: TransportRow().environmentObject(recApp))

        XCTAssertEqual(
            idleHeight, 48, accuracy: Self.tolerance,
            "TransportRow idle height must equal the pinned 48pt")
        XCTAssertEqual(
            recHeight, 48, accuracy: Self.tolerance,
            "TransportRow recording height must equal the pinned 48pt — a "
                + "growing record meter must never resize the bar")
        XCTAssertEqual(
            idleHeight, recHeight, accuracy: Self.tolerance,
            "Transport bar height must not change when a recording starts "
                + "(measured idle=\(idleHeight), recording=\(recHeight))")
        #else
        throw XCTSkip(
            "UIHostingController needs UIKit — run via xcodebuild on a simulator.")
        #endif
    }

    // MARK: - RecordToggle (the audioOutput pill — inner 18pt pin)

    func testRecordToggleAudioOutputHeightConstantAcrossStates() throws {
        #if canImport(UIKit)
        let idleApp = AppState()
        let idleHeight = measuredHeight(
            of: RecordToggle(mode: .audioOutput).environmentObject(idleApp))

        // A minutes:seconds elapsed ("1:23") is the widest/tallest label
        // the pill draws; if the meter or caption were going to push the
        // box taller than the idle dot, this is where it shows.
        let recApp = AppState()
        recApp.outputRecorder.setPublishedStateForTesting(.recording, elapsedSec: 83)
        let recHeight = measuredHeight(
            of: RecordToggle(mode: .audioOutput).environmentObject(recApp))

        XCTAssertEqual(
            idleHeight, recHeight, accuracy: Self.tolerance,
            "RecordToggle (.audioOutput) height must not change between idle "
                + "and recording (measured idle=\(idleHeight), recording=\(recHeight)) "
                + "— the meter+elapsed must stay inside the pill's fixed content box")
        #else
        throw XCTSkip(
            "UIHostingController needs UIKit — run via xcodebuild on a simulator.")
        #endif
    }
}
