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
    let onLongPress: (Int, Int) -> Void
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

    /// Live touches → pad key (row * 100 + col) currently held by
    /// that touch. Base 100 keeps the encoding unambiguous for any
    /// realistic grid size.
    private var touchPads: [UITouch: Int] = [:]
    /// Long-press timers per touch (cancelled on move/lift).
    private var longPressTimers: [UITouch: Timer] = [:]
    /// Touches that have triggered long-press and are being tracked for drag.
    private var longPressTouches: Set<UITouch> = []

    override init(frame: CGRect) {
        super.init(frame: frame)
        isMultipleTouchEnabled = true
        backgroundColor = .clear
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
            let (row, col) = pad(at: touch.location(in: self))
            let key = row * 100 + col
            touchPads[touch] = key
            // Fire the attack immediately: real-time audio + on-screen
            // pressed state. Long-press releases it before the sheet.
            onPadDown?(row, col)

            // Long-press timer - if this fires, it's a hold not a tap
            let longPressTimer = Timer.scheduledTimer(
                withTimeInterval: 0.5, repeats: false
            ) { [weak self] _ in
                guard let self, self.touchPads[touch] != nil else { return }

                self.touchPads.removeValue(forKey: touch)
                self.longPressTimers.removeValue(forKey: touch)

                // Release the ringing pad first so no voice rings under
                // the sheet, then fire the long-press.
                self.onPadUp?(row, col)
                self.longPressTouches.insert(touch)
                self.onLongPress?(row, col)
            }
            longPressTimers[touch] = longPressTimer
        }
    }

    override func touchesMoved(_ touches: Set<UITouch>, with event: UIEvent?) {
        for touch in touches {
            // Handle long-press drag tracking
            if longPressTouches.contains(touch) {
                onLongPressDrag?(touch.location(in: self))
                continue
            }

            guard let previous = touchPads[touch] else { continue }
            let (row, col) = pad(at: touch.location(in: self))
            let key = row * 100 + col
            guard key != previous else { continue }

            // Slid onto a different pad: cancel long-press timer
            longPressTimers.removeValue(forKey: touch)?.invalidate()

            touchPads[touch] = key

            // Slide: release old pad, press new pad immediately
            onPadUp?(previous / 100, previous % 100)
            onPadDown?(row, col)
        }
    }

    override func touchesEnded(_ touches: Set<UITouch>, with event: UIEvent?) {
        endTouches(touches)
    }

    override func touchesCancelled(_ touches: Set<UITouch>, with event: UIEvent?) {
        endTouches(touches)
    }

    private func endTouches(_ touches: Set<UITouch>) {
        for touch in touches {
            // Handle long-press touch release
            if longPressTouches.remove(touch) != nil {
                onLongPressEnd?(touch.location(in: self))
                continue
            }

            longPressTimers.removeValue(forKey: touch)?.invalidate()

            guard let key = touchPads.removeValue(forKey: touch) else {
                continue
            }

            // Pad was pressed on touch-down; release it now.
            onPadUp?(key / 100, key % 100)
        }
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
    let onLongPress: (Int, Int) -> Void
    var onLongPressDrag: ((CGPoint) -> Void)?
    var onLongPressEnd: ((CGPoint) -> Void)?

    var body: some View { Color.clear }
}

#endif
