// PadRadialMenu.swift
//
// Desktop port of iOS radial context menu for pad actions.
// Appears on right-click of a pad, offering quick access to:
//   - Effects: per-pad FX (filter, reverb, pan)
//   - Chop: waveform editor (adjust boundaries)
//   - Loop: toggle loop transform
//   - Reset: clear transforms
//   - Delete: clear pad assignment
//   - Sequence: assign a pattern
//
// Interaction: click to invoke, hover to highlight, click segment to confirm.
// Click center or outside to cancel.

import SwiftUI
#if canImport(AppKit)
import AppKit
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
    case edit
    case addSound
    case voiceRecord

    /// Actions shown on a pad that already holds a sound.
    public static let assigned: [PadRadialAction] =
        [.delete, .chop, .reset, .effects, .loop, .sequence]

    /// Actions shown on an empty pad.
    public static let empty: [PadRadialAction] = [.addSound, .voiceRecord, .sequence]

    /// Actions shown on a sequence pad.
    public static let sequencePad: [PadRadialAction] = [.edit, .delete, .effects, .sequence]

    /// Actions shown on a pack pad (sample from curated pack).
    public static let packPad: [PadRadialAction] = [.delete, .addSound, .effects, .sequence]

    var label: String {
        switch self {
        case .effects:     return "Effects"
        case .chop:        return "Chop"
        case .loop:        return "Loop"
        case .reset:       return "Reset"
        case .delete:      return "Delete"
        case .sequence:    return "Sequence"
        case .edit:        return "Edit"
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
        case .edit:        return "pencil"
        case .addSound:    return "plus.circle.fill"
        case .voiceRecord: return "mic.fill"
        }
    }

    /// Start/end angles for the slice at `index` of `count` evenly-sized
    /// segments. Index 0 is centered at 0° (right).
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
    /// Pad index (row * 8 + col).
    public let padIdx: Int
    /// Screen position where the menu is centered.
    public let center: CGPoint
    /// Whether the pad has a chop assignment.
    public let hasAssignment: Bool
    /// Whether the pad has a sequence assignment.
    public let isSequencePad: Bool
    /// Whether the pad has a pack pad assignment.
    public let isPackPad: Bool
    /// Whether the pad currently has the loop transform.
    public let hasLoop: Bool
    /// Ordered ring actions for this pad.
    public var actions: [PadRadialAction] {
        if hasAssignment {
            return PadRadialAction.assigned
        } else if isSequencePad {
            return PadRadialAction.sequencePad
        } else if isPackPad {
            return PadRadialAction.packPad
        } else {
            return PadRadialAction.empty
        }
    }

    public init(
        gridRow: Int,
        gridCol: Int,
        center: CGPoint,
        hasAssignment: Bool,
        isSequencePad: Bool,
        isPackPad: Bool = false,
        hasLoop: Bool = false
    ) {
        self.gridRow = gridRow
        self.gridCol = gridCol
        self.padIdx = gridRow * 8 + gridCol
        self.center = center
        self.hasAssignment = hasAssignment
        self.isSequencePad = isSequencePad
        self.isPackPad = isPackPad
        self.hasLoop = hasLoop
    }
}

// MARK: - View

/// The radial menu overlay.
struct PadRadialMenu: View {
    let state: PadRadialMenuState
    let onAction: (PadRadialAction) -> Void
    let onDismiss: () -> Void

    @State private var highlighted: PadRadialAction?
    @State private var mouseLocation: CGPoint = .zero

    private let outerRadius: CGFloat = 120
    private let innerRadius: CGFloat = 44
    private let deadZoneRadius: CGFloat = 24

    var body: some View {
        ZStack {
            // Dim background
            Color.black.opacity(0.4)
                .ignoresSafeArea()
                .onTapGesture { onDismiss() }

            // Menu positioned at pad center
            menuContent
                .position(state.center)
        }
        .onContinuousHover { phase in
            switch phase {
            case .active(let location):
                mouseLocation = location
                updateHighlight(from: location)
            case .ended:
                break
            }
        }
        .onTapGesture { location in
            handleClick(at: location)
        }
    }

    private var menuContent: some View {
        ZStack {
            // Outer ring with segments
            ForEach(Array(state.actions.enumerated()), id: \.element) { index, action in
                segment(for: action, index: index, count: state.actions.count)
            }

            // Inner circle (cancel zone)
            Circle()
                .fill(Color(white: 0.15))
                .frame(width: innerRadius * 2, height: innerRadius * 2)
                .overlay(
                    Circle()
                        .stroke(Color.white.opacity(0.3), lineWidth: 1)
                )

            // Center icon
            Image(systemName: "waveform")
                .font(.system(size: 14, weight: .medium))
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
        case .edit:     return .yellow.opacity(0.6)
        case .addSound: return .green.opacity(0.6)
        case .voiceRecord: return .pink.opacity(0.6)
        }
    }

    @ViewBuilder
    private func segmentLabel(_ action: PadRadialAction, highlighted: Bool) -> some View {
        VStack(spacing: 2) {
            Image(systemName: action.systemImage)
                .font(.system(size: highlighted ? 16 : 12, weight: .semibold))
            Text(action.label)
                .font(.system(size: highlighted ? 9 : 7, weight: .semibold))
        }
        .foregroundStyle(highlighted ? .white : .white.opacity(0.7))
        .animation(.easeOut(duration: 0.12), value: highlighted)
    }

    private func labelPosition(index: Int, count: Int) -> CGPoint {
        let midRadius = (innerRadius + outerRadius) / 2
        if count == 1 {
            return CGPoint(x: outerRadius, y: outerRadius - midRadius)
        }
        let a = PadRadialAction.angles(index: index, count: count)
        let angle = Angle.degrees((a.start + a.end) / 2)
        let x = outerRadius + cos(angle.radians) * midRadius
        let y = outerRadius + sin(angle.radians) * midRadius
        return CGPoint(x: x, y: y)
    }

    // MARK: - Interaction

    private func updateHighlight(from location: CGPoint) {
        let dx = location.x - state.center.x
        let dy = location.y - state.center.y
        let distance = sqrt(dx * dx + dy * dy)

        if distance < deadZoneRadius || distance > outerRadius + 20 {
            highlighted = nil
        } else {
            let angle = atan2(dy, dx) * 180 / .pi
            highlighted = PadRadialAction.action(atAngle: angle, in: state.actions)
        }
    }

    private func handleClick(at location: CGPoint) {
        let dx = location.x - state.center.x
        let dy = location.y - state.center.y
        let distance = sqrt(dx * dx + dy * dy)

        if distance < deadZoneRadius || distance > outerRadius + 20 {
            onDismiss()
        } else {
            let angle = atan2(dy, dx) * 180 / .pi
            if let action = PadRadialAction.action(atAngle: angle, in: state.actions) {
                onAction(action)
            } else {
                onDismiss()
            }
        }
    }
}

// MARK: - Segment Shape

/// A pie-slice segment shape for the radial menu.
private struct SegmentShape: Shape {
    let startAngle: Angle
    let endAngle: Angle
    let innerRadius: CGFloat
    let outerRadius: CGFloat

    func path(in rect: CGRect) -> Path {
        let center = CGPoint(x: rect.midX, y: rect.midY)

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
                    gridRow: 2,
                    gridCol: 3,
                    center: CGPoint(x: 200, y: 200),
                    hasAssignment: true,
                    isSequencePad: false,
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
        .frame(width: 400, height: 400)
    }
}
#endif
