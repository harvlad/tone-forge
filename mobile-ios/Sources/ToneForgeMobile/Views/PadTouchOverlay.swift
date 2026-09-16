// PadTouchOverlay.swift
//
// UIKit multi-touch pad-surface input, extracted from ModeGridView
// (redesign Phase 9) and parameterized by grid size so the 8×8
// contribution grid, the 4×4 sample grid, and the Chord Pads screen
// (Phase 12) share one input path. SwiftUI gestures are single-touch;
// a pad surface must track several fingers with per-touch pad
// migration (slide off one pad onto another) — hence UIKit.
//
// Coordinates: PadIndex convention — (row, col) with row 1 at the
// BOTTOM, so callers on smaller grids remap into their own space.
// Long-press (0.5 s) releases the pad first, then fires onLongPress
// so no voice rings under whatever sheet the caller presents.
//
// Edit-mode contract (launchpad-edit-mode): `onLongPress` is OPTIONAL.
// When a host passes nil (performance mode — Jam with Edit off,
// Perform's stage), touch-down arms NOTHING besides the pad attack:
// no timer, no hold bookkeeping. The 0.5 s hold-hijack (voice cut +
// radial) simply cannot happen, and a sustained hold plays out for as
// long as the finger stays down. That absence — not a hidden menu —
// is the "zero gesture-recognition tax" the parity row promises.

import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

#if canImport(UIKit)

struct PadTouchOverlay: UIViewRepresentable {
    var rows: Int = 8
    var cols: Int = 8
    let onPadDown: (Int, Int) -> Void
    let onPadUp: (Int, Int) -> Void
    /// Hold consumer. nil = performance mode: no long-press timer is
    /// ever armed, holds are never hijacked (see header contract).
    var onLongPress: ((Int, Int) -> Void)? = nil
    /// Called with touch location (in view coords) while dragging after long-press.
    var onLongPressDrag: ((CGPoint) -> Void)?
    /// Called when the touch that triggered long-press ends.
    var onLongPressEnd: ((CGPoint) -> Void)?

    func makeUIView(context: Context) -> PadTouchUIView {
        let view = PadTouchUIView()
        apply(to: view)
        return view
    }

    func updateUIView(_ uiView: PadTouchUIView, context: Context) {
        apply(to: uiView)
    }

    private func apply(to view: PadTouchUIView) {
        view.rows = rows
        view.cols = cols
        view.onPadDown = onPadDown
        view.onPadUp = onPadUp
        view.onLongPress = onLongPress
        view.onLongPressDrag = onLongPressDrag
        view.onLongPressEnd = onLongPressEnd
    }
}

final class PadTouchUIView: UIView {
    var rows: Int = 8
    var cols: Int = 8
    var onPadDown: ((Int, Int) -> Void)?
    var onPadUp: ((Int, Int) -> Void)?
    var onLongPress: ((Int, Int) -> Void)?
    var onLongPressDrag: ((CGPoint) -> Void)?
    var onLongPressEnd: ((CGPoint) -> Void)?

    /// Hold threshold. Injectable so the edit-mode tests don't wait
    /// out the real half second.
    var longPressInterval: TimeInterval = 0.5

    /// Live touches → pad key (row * 100 + col) currently held by
    /// that touch. Base 100 keeps the encoding unambiguous for any
    /// realistic grid size. Keyed by AnyHashable (the UITouch in
    /// production) so tests can drive the same paths without
    /// synthesizing UITouch instances.
    private var touchPads: [AnyHashable: Int] = [:]
    /// Long-press timers per touch (cancelled on move/lift). Only
    /// ever populated when `onLongPress` exists — performance mode
    /// stays timer-free by construction.
    private var longPressTimers: [AnyHashable: Timer] = [:]
    /// Touches that have triggered long-press and are being tracked for drag.
    private var longPressTouches: Set<AnyHashable> = []

    /// Test hook: is any hold timer armed right now? Pins the
    /// edit-off promise ("no timer" — not "timer whose callback
    /// no-ops").
    var hasPendingLongPress: Bool { !longPressTimers.isEmpty }

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        backgroundColor = .clear
        // VoiceOver: expose the surface as a single direct-interaction
        // element (the standard pattern for instrument surfaces —
        // touches pass straight through so pads stay playable).
        isAccessibilityElement = true
        accessibilityLabel = "Pad grid"
        accessibilityHint = "Musical pad surface. Uses direct touch."
        accessibilityTraits = [.allowsDirectInteraction]
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) is not supported")
    }

    /// Point → (row, col) in PadIndex convention (row 1 = bottom).
    private func pad(at point: CGPoint) -> (row: Int, col: Int) {
        let cw = bounds.width / CGFloat(cols)
        let ch = bounds.height / CGFloat(rows)
        let col = min(max(Int(point.x / cw) + 1, 1), cols)
        let row = min(max(rows - Int(point.y / ch), 1), rows)
        return (row, col)
    }

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            handleTouchBegan(touch, at: touch.location(in: self))
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            handleTouchMoved(touch, at: touch.location(in: self))
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            handleTouchEnded(touch, at: touch.location(in: self))
        }
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            handleTouchEnded(touch, at: touch.location(in: self))
        }
    }

    // MARK: - Keyed touch handling (internal for tests)

    func handleTouchBegan(_ key: AnyHashable, at point: CGPoint) {
        let (row, col) = pad(at: point)
        touchPads[key] = row * 100 + col
        // Fire the attack immediately: real-time audio + on-screen
        // pressed state. Nothing precedes this — the attack is always
        // synchronous with the touch, in both modes.
        onPadDown?(row, col)

        // Performance mode: no hold consumer, so arm nothing. The
        // early return IS the feature — see the header contract.
        guard onLongPress != nil else { return }

        // Long-press timer - if this fires, it's a hold not a tap
        let longPressTimer = Timer.scheduledTimer(
            withTimeInterval: longPressInterval, repeats: false
        ) { [weak self] _ in
            guard let self, self.touchPads[key] != nil else { return }

            self.touchPads.removeValue(forKey: key)
            self.longPressTimers.removeValue(forKey: key)

            // Release the ringing pad first so no voice rings under
            // the sheet, then fire the long-press.
            self.onPadUp?(row, col)
            self.longPressTouches.insert(key)
            self.onLongPress?(row, col)
        }
        longPressTimers[key] = longPressTimer
    }

    func handleTouchMoved(_ key: AnyHashable, at point: CGPoint) {
        // Handle long-press drag tracking
        if longPressTouches.contains(key) {
            onLongPressDrag?(point)
            return
        }

        guard let previous = touchPads[key] else { return }
        let (row, col) = pad(at: point)
        let padKey = row * 100 + col
        guard padKey != previous else { return }

        // Slid onto a different pad: cancel long-press timer
        longPressTimers.removeValue(forKey: key)?.invalidate()

        touchPads[key] = padKey

        // Slide: release old pad, press new pad immediately
        onPadUp?(previous / 100, previous % 100)
        onPadDown?(row, col)
    }

    func handleTouchEnded(_ key: AnyHashable, at point: CGPoint) {
        // Handle long-press touch release
        if longPressTouches.remove(key) != nil {
            onLongPressEnd?(point)
            return
        }

        longPressTimers.removeValue(forKey: key)?.invalidate()

        guard let padKey = touchPads.removeValue(forKey: key) else {
            return
        }

        // Pad was pressed on touch-down; release it now.
        onPadUp?(padKey / 100, padKey % 100)
    }
}

#else

/// Non-UIKit hosts (macOS SwiftPM test build) compile pad surfaces
/// as paint-only — the overlay is never exercised there.
struct PadTouchOverlay: View {
    var rows: Int = 8
    var cols: Int = 8
    let onPadDown: (Int, Int) -> Void
    let onPadUp: (Int, Int) -> Void
    var onLongPress: ((Int, Int) -> Void)? = nil
    var onLongPressDrag: ((CGPoint) -> Void)?
    var onLongPressEnd: ((CGPoint) -> Void)?

    var body: some View { Color.clear }
}

#endif
