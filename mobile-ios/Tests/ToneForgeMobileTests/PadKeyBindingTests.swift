// PadKeyBindingTests.swift
//
// Pins the rule that a grid cell's playable identity (its SamplePadKey /
// on-pad waveform) is resolved from the coordinator's `padBinding` map — the
// painter's own source of truth — NOT a synthetic quadrant formula. That
// switch is what fixed blank/duplicated pad waveforms across the 4×4 quadrant,
// an 8×8 Launchpad, a top-origin borrow spread and pinned foreign-pack pads.
// SamplePadGrid4x4.padKey is a thin `padBinding → SamplePadKey?`, so pinning
// padBinding here guards the same regression the view depends on.

import XCTest
@testable import ToneForgeMobile
import ToneForgeEngine

@MainActor
final class PadKeyBindingTests: XCTestCase {

    func testPadKeyResolvesViaBinding() {
        let app = AppState()
        let coord = app.modeCoordinator

        // Bind a pad at grid (row 8, col 1) to a specific pack/padIdx — the
        // painter would have written this after activating a pack.
        let rawValue = PadIndex.at(row: 8, col: 1).rawValue
        coord.padBindings[rawValue] = (packId: "packX", padIdx: 3)

        // padBinding must return exactly what was bound (the view builds its
        // SamplePadKey straight from this).
        let b = coord.padBinding(row: 8, col: 1)
        XCTAssertEqual(b?.packId, "packX")
        XCTAssertEqual(b?.padIdx, 3)

        // The view's padKey is `binding → SamplePadKey(packId, padIdx)`; mirror
        // it to prove the identity carries through unchanged.
        let key = b.map { SamplePadKey(packId: $0.packId, padIdx: $0.padIdx) }
        XCTAssertEqual(key, SamplePadKey(packId: "packX", padIdx: 3))
    }

    func testUnboundCellHasNoPadKey() {
        let app = AppState()
        let coord = app.modeCoordinator
        // A cell with no binding must yield nil — the view renders it as an
        // empty "+" and never draws a stray waveform.
        XCTAssertNil(coord.padBinding(row: 2, col: 7))
    }

    func testBindingDoesNotFollowQuadrantFormula() {
        // Regression: identity used to be derived from the grid position via a
        // quadrant formula, so a borrow/foreign-pack pad showed the wrong
        // waveform. Bind two cells to NON-quadrant padIdxs and confirm each
        // reads back its own bound padIdx, not one implied by its position.
        let app = AppState()
        let coord = app.modeCoordinator
        coord.padBindings[PadIndex.at(row: 8, col: 1).rawValue] =
            (packId: "p", padIdx: 41)
        coord.padBindings[PadIndex.at(row: 8, col: 2).rawValue] =
            (packId: "p", padIdx: 7)
        XCTAssertEqual(coord.padBinding(row: 8, col: 1)?.padIdx, 41)
        XCTAssertEqual(coord.padBinding(row: 8, col: 2)?.padIdx, 7)
    }
}
