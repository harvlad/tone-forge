// PadRadialMenu.swift
//
// Radial context menu for pad actions (D-023 Phase 2). Appears on
// long-press of a sample pad, offering quick access to:
//   - Effects: per-pad FX (filter, reverb, pan)
//   - Chop: waveform editor (adjust boundaries, split/merge)
//   - Loop: toggle loop transform
//   - Delete: clear pad assignment
//
// Interaction: hold to invoke, drag toward a segment to highlight,
// release to confirm. Drag to center or outside to cancel.
// Haptic feedback on segment changes.

import SwiftUI
#if canImport(UIKit)
import UIKit
#endif

// MARK: - Actions

/// Actions available in the radial menu.
public enum PadRadialAction: String, CaseIterable, Sendable {
    case effects
    case chop
    case loop
    case reset
    case delete
    case sequence
    case addSound
    case voiceRecord

    /// Actions shown on a pad that already holds a sound. Order fixes
    /// the ring layout: index 0 centers at 0° (right), each +60°.
    public static let assigned: [PadRadialAction] =
        [.delete, .chop, .reset, .effects, .loop, .sequence]

    /// Actions shown on an empty pad — add a sound, record a voice
    /// sample, or build a sequence.
    public static let empty: [PadRadialAction] = [.addSound, .voiceRecord, .sequence]

    var label: String {
        switch self {
        case .effects:     return "Effects"
        case .chop:        return "Chop"
        case .loop:        return "Loop"
        case .reset:       return "Reset"
        case .delete:      return "Delete"
        case .sequence:    return "Sequence"
        case .addSound:    return "Add Sound"
        case .voiceRecord: return "Voice"
        }
    }

    var systemImage: String {
        switch self {
        case .effects:     return "slider.horizontal.3"
        case .chop:        return "waveform"
        case .loop:        return "repeat"
        case .reset:       return "arrow.uturn.backward"
        case .delete:      return "trash"
        case .sequence:    return "square.grid.3x3.fill"
        case .addSound:    return "plus.circle.fill"
        case .voiceRecord: return "mic.fill"
        }
    }

    /// Start/end angles for the slice at `index` of `count` evenly-sized
    /// segments, in SCREEN coordinates (0 = right, clockwise positive).
    /// Index 0 is centered at 0°.
    static func angles(index: Int, count: Int) -> (start: Double, end: Double) {
        let slice = 360.0 / Double(max(count, 1))
        let start = -slice / 2 + Double(index) * slice
        return (start, start + slice)
    }

    /// Resolve a screen angle to an action within a specific ring layout.
    static func action(atAngle angle: Double, in actions: [PadRadialAction]) -> PadRadialAction? {
        let count = actions.count
        guard count > 0 else { return nil }
        let slice = 360.0 / Double(count)
        var a = (angle + slice / 2).truncatingRemainder(dividingBy: 360)
        if a < 0 { a += 360 }
        let idx = min(count - 1, max(0, Int(a / slice)))
        return actions[idx]
    }
}

// MARK: - State

/// State for displaying the radial menu.
public struct PadRadialMenuState: Equatable {
    /// Grid coordinates of the pad.
    public let gridRow: Int
    public let gridCol: Int
    /// Screen position where the menu is centered.
    public let center: CGPoint
    /// Pack ID for the pad (for effects/delete).
    public let packId: String?
    /// Pad index within the pack.
    public let padIdx: Int
    /// Whether the pad currently has the loop transform.
    public let hasLoop: Bool
    /// Ordered ring actions for this pad (assigned vs empty layout).
    public let actions: [PadRadialAction]

    public init(
        gridRow: Int,
        gridCol: Int,
        center: CGPoint,
        packId: String?,
        padIdx: Int,
        hasLoop: Bool = false,
        actions: [PadRadialAction] = PadRadialAction.assigned
    ) {
        self.gridRow = gridRow
        self.gridCol = gridCol
        self.center = center
        self.packId = packId
        self.padIdx = padIdx
        self.hasLoop = hasLoop
        self.actions = actions
    }
}

// MARK: - View

/// The radial menu overlay.
public struct PadRadialMenu: View {
    let state: PadRadialMenuState
    let onAction: (PadRadialAction) -> Void
    let onDismiss: () -> Void
    /// External drag position (in view coordinates). When set, overrides internal gesture.
    var externalDragPosition: CGPoint?
    /// Called when external drag ends to determine final action.
    var onExternalDragEnd: (() -> Void)?

    @State private var highlighted: PadRadialAction?
    @State private var dragOffset: CGSize = .zero
    @GestureState private var isDragging = false

    private let outerRadius: CGFloat = 150
    private let innerRadius: CGFloat = 52
    private let deadZoneRadius: CGFloat = 28

    public init(
        state: PadRadialMenuState,
        onAction: @escaping (PadRadialAction) -> Void,
        onDismiss: @escaping () -> Void,
        externalDragPosition: CGPoint? = nil,
        onExternalDragEnd: (() -> Void)? = nil
    ) {
        self.state = state
        self.onAction = onAction
        self.onDismiss = onDismiss
        self.externalDragPosition = externalDragPosition
        self.onExternalDragEnd = onExternalDragEnd
    }

    /// Compute which action (if any) is at the given point relative to menu
    /// center, resolved against the given ring layout.
    public static func action(
        at point: CGPoint,
        center: CGPoint,
        actions: [PadRadialAction]
    ) -> PadRadialAction? {
        let dx = point.x - center.x
        let dy = point.y - center.y  // Screen coords: positive = down
        let distance = sqrt(dx * dx + dy * dy)

        let deadZoneRadius: CGFloat = 28
        let outerRadius: CGFloat = 150

        if distance < deadZoneRadius || distance > outerRadius + 20 {
            return nil
        }

        // atan2 with screen coords gives angle where 0=right, positive=clockwise
        let angle = atan2(dy, dx) * 180 / .pi
        return PadRadialAction.action(atAngle: angle, in: actions)
    }

    public var body: some View {
        ZStack {
            // Dim background
            Color.black.opacity(0.4)
                .ignoresSafeArea()
                .onTapGesture { onDismiss() }

            // Menu positioned at pad center
            menuContent
                .position(state.center)
        }
        .gesture(externalDragPosition == nil ? dragGesture : nil)
        .onChange(of: externalDragPosition) { _, newPosition in
            if let pos = newPosition {
                let action = Self.action(
                    at: pos, center: state.center, actions: state.actions)
                updateHighlight(action)
            }
        }
    }

    /// Handle external drag end - confirm action or dismiss.
    public func handleExternalDragEnd(at position: CGPoint) {
        if let action = Self.action(
            at: position, center: state.center, actions: state.actions) {
            #if canImport(UIKit)
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
            #endif
            onAction(action)
        } else {
            onDismiss()
        }
    }

    private var menuContent: some View {
        ZStack {
            // Outer ring with segments (dynamic layout from state.actions)
            ForEach(Array(state.actions.enumerated()), id: \.element) { index, action in
                segment(for: action, index: index, count: state.actions.count)
            }

            // Inner circle (cancel zone / pad preview)
            Circle()
                .fill(Color(white: 0.15))
                .frame(width: innerRadius * 2, height: innerRadius * 2)
                .overlay(
                    Circle()
                        .stroke(Color.white.opacity(0.3), lineWidth: 1)
                )

            // Center icon (waveform thumbnail placeholder)
            Image(systemName: "waveform")
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(.white.opacity(0.6))
        }
        .frame(width: outerRadius * 2, height: outerRadius * 2)
    }

    @ViewBuilder
    private func segment(for action: PadRadialAction, index: Int, count: Int) -> some View {
        let isHighlighted = highlighted == action
        let angles = PadRadialAction.angles(index: index, count: count)

        SegmentShape(
            startAngle: .degrees(angles.start),
            endAngle: .degrees(angles.end),
            innerRadius: innerRadius,
            outerRadius: outerRadius
        )
        .fill(isHighlighted ? segmentHighlightColor(action) : Color(white: 0.2))
        .overlay(
            SegmentShape(
                startAngle: .degrees(angles.start),
                endAngle: .degrees(angles.end),
                innerRadius: innerRadius,
                outerRadius: outerRadius
            )
            .stroke(Color.white.opacity(0.1), lineWidth: 1)
        )
        .overlay(
            segmentLabel(action, highlighted: isHighlighted)
                .position(labelPosition(index: index, count: count))
        )
    }

    private func segmentHighlightColor(_ action: PadRadialAction) -> Color {
        switch action {
        case .effects: return .blue.opacity(0.6)
        case .chop:    return .orange.opacity(0.6)
        case .loop:    return state.hasLoop ? .green.opacity(0.6) : .purple.opacity(0.6)
        case .reset:   return .gray.opacity(0.6)
        case .delete:  return .red.opacity(0.6)
        case .sequence: return Color(
            red: 0x30 / 255, green: 0xD5 / 255, blue: 0xC8 / 255
        ).opacity(0.6)
        case .addSound: return .green.opacity(0.6)
        case .voiceRecord: return .pink.opacity(0.6)
        }
    }

    @ViewBuilder
    private func segmentLabel(_ action: PadRadialAction, highlighted: Bool) -> some View {
        VStack(spacing: 2) {
            Image(systemName: action.systemImage)
                .font(.system(size: highlighted ? 18 : 14, weight: .semibold))
            Text(action.label)
                .font(.system(size: highlighted ? 10 : 8, weight: .semibold))
        }
        .foregroundStyle(highlighted ? .white : .white.opacity(0.7))
        .animation(.easeOut(duration: 0.15), value: highlighted)
    }

    private func labelPosition(index: Int, count: Int) -> CGPoint {
        let midRadius = (innerRadius + outerRadius) / 2
        // A single-action wheel is one full ring; anchor its label at the
        // top instead of the right edge so it reads centered.
        if count == 1 {
            return CGPoint(x: outerRadius, y: outerRadius - midRadius)
        }
        let a = PadRadialAction.angles(index: index, count: count)
        let angle = Angle.degrees((a.start + a.end) / 2)
        // Screen coordinates: 0=right, clockwise positive, y increases downward
        let x = outerRadius + cos(angle.radians) * midRadius
        let y = outerRadius + sin(angle.radians) * midRadius
        return CGPoint(x: x, y: y)
    }

    // MARK: - Gesture

    private var dragGesture: some Gesture {
        DragGesture(minimumDistance: 0)
            .updating($isDragging) { _, state, _ in
                state = true
            }
            .onChanged { value in
                dragOffset = value.translation

                // Calculate angle from center in screen coords
                let dx = value.location.x - state.center.x
                let dy = value.location.y - state.center.y  // Screen coords: positive = down
                let distance = sqrt(dx * dx + dy * dy)

                if distance < deadZoneRadius {
                    // In dead zone - no selection
                    updateHighlight(nil)
                } else if distance > outerRadius + 20 {
                    // Outside menu - no selection
                    updateHighlight(nil)
                } else {
                    // In segment zone - find which segment
                    let angle = atan2(dy, dx) * 180 / .pi
                    updateHighlight(
                        PadRadialAction.action(atAngle: angle, in: state.actions))
                }
            }
            .onEnded { value in
                let dx = value.location.x - state.center.x
                let dy = value.location.y - state.center.y
                let distance = sqrt(dx * dx + dy * dy)

                if distance < deadZoneRadius || distance > outerRadius + 20 {
                    // Cancel
                    onDismiss()
                } else if let action = highlighted {
                    // Confirm action
                    #if canImport(UIKit)
                    UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                    #endif
                    onAction(action)
                } else {
                    onDismiss()
                }
            }
    }

    private func updateHighlight(_ action: PadRadialAction?) {
        guard action != highlighted else { return }
        highlighted = action
        #if canImport(UIKit)
        if action != nil {
            UISelectionFeedbackGenerator().selectionChanged()
        }
        #endif
    }
}

// MARK: - Segment Shape

/// A pie-slice segment shape for the radial menu.
/// Angles are in screen coordinates (0 = right, clockwise positive).
private struct SegmentShape: Shape {
    let startAngle: Angle
    let endAngle: Angle
    let innerRadius: CGFloat
    let outerRadius: CGFloat

    func path(in rect: CGRect) -> Path {
        let center = CGPoint(x: rect.midX, y: rect.midY)

        // SwiftUI uses 0=right, clockwise positive - same as our screen coords
        var path = Path()
        path.addArc(
            center: center,
            radius: outerRadius,
            startAngle: startAngle,
            endAngle: endAngle,
            clockwise: false
        )
        path.addArc(
            center: center,
            radius: innerRadius,
            startAngle: endAngle,
            endAngle: startAngle,
            clockwise: true
        )
        path.closeSubpath()
        return path
    }
}

// MARK: - Preview

#if DEBUG
struct PadRadialMenu_Previews: PreviewProvider {
    static var previews: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            PadRadialMenu(
                state: PadRadialMenuState(
                    gridRow: 5,
                    gridCol: 2,
                    center: CGPoint(x: 200, y: 400),
                    packId: "starter",
                    padIdx: 34,
                    hasLoop: false
                ),
                onAction: { action in
                    print("Selected: \(action)")
                },
                onDismiss: {
                    print("Dismissed")
                }
            )
        }
    }
}
#endif
