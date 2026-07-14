// ModeGridView.swift
//
// The 8×8 contribution pad surface — the on-screen mirror of the
// Launchpad Pro grid. A single SwiftUI Canvas paints 64 cells from
// ModeCoordinator.padVisuals (repainted whenever the coordinator's
// @Published state changes), and a UIKit multi-touch overlay feeds
// touches into the coordinator's touch adapter, which publishes
// ContributionEvents on the bus. The view NEVER triggers audio
// directly — everything goes bus → ModeRouter → ModeCoordinator.
//
// Coordinates: PadIndex convention throughout (row 1..8 BOTTOM-up,
// col 1..8), so screen y for row r is (8 - r) * cellHeight.
//
// Long-press (0.5 s) opens the pad sheet: PadEffectsEditor for bound
// pack pads, PadSourceSheet (record / assign / classify, P3) for
// empty and local-sample pads. Sheet previews re-fire the pad through
// the bus via the coordinator so the D-015 invariant holds even from
// the sheet.

import SwiftUI
import ToneForgeEngine

struct ModeGridView: View {
    @ObservedObject var coordinator: ModeCoordinator
    @EnvironmentObject private var appState: AppState

    /// Long-press target; `.sheet(item:)` presents the effects editor
    /// (pack pads) or the source sheet (empty / local pads, P3).
    @State private var sheetTarget: PadSheetTarget?

    var body: some View {
        // The UIKit touch view sits UNDER the Canvas (which opts out
        // of hit testing) so multi-touch lands on the UIView while
        // the paint stays a pure SwiftUI Canvas — this also keeps
        // ImageRenderer snapshots showing the real grid instead of a
        // "can't flatten UIViewRepresentable" placeholder.
        ZStack {
            PadTouchOverlay(
                onPadDown: { row, col in
                    coordinator.touchPadDown(row: row, col: col)
                },
                onPadUp: { row, col in
                    coordinator.touchPadUp(row: row, col: col)
                },
                onLongPress: { row, col in
                    sheetTarget = coordinator.padSheetTarget(
                        row: row, col: col
                    )
                }
            )
            GridCanvas(
                visuals: coordinator.padVisuals,
                pressed: coordinator.pressedPads,
                ringing: coordinator.ringingGridPads(
                    from: appState.ringingPadKeys
                )
            )
            .allowsHitTesting(false)
        }
        .aspectRatio(1, contentMode: .fit)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .sheet(item: $sheetTarget) { target in
            switch target {
            case .effects(let target):
                PadEffectsEditor(
                    packId: target.packId,
                    padIdx: target.padIdx,
                    padName: target.padName,
                    manifestBaseline: target.manifestBaseline,
                    gridRaw: target.gridRow * 10 + target.gridCol,
                    onPreview: preview(row: target.gridRow, col: target.gridCol)
                )
            case .source(let target):
                PadSourceSheet(
                    target: target,
                    onPreview: preview(row: target.gridRow, col: target.gridCol)
                )
            case .trimmer(let target):
                SampleTrimmerSheet(
                    target: target,
                    onPreview: previewTrimmed(target: target)
                )
            }
        }
    }

    /// Fire the pad through the bus (down + short hold + up) so sheet
    /// previews follow the same path as a real tap; the delayed up
    /// releases hold-mode/looping pads.
    private func preview(row: Int, col: Int) -> () -> Void {
        { [coordinator] in
            coordinator.touchPadDown(row: row, col: col)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
                coordinator.touchPadUp(row: row, col: col)
            }
        }
    }

    /// Preview a trimmed portion of a sample. Used by the waveform trimmer.
    private func previewTrimmed(target: SampleTrimmerTarget) -> (Double, Double) -> Void {
        { [coordinator] startFraction, endFraction in
            coordinator.previewTrimmed(
                packId: target.packId,
                padIdx: target.padIdx,
                startFraction: startFraction,
                endFraction: endFraction
            )
        }
    }
}

// MARK: - Canvas painter

/// Pure painter for the 64-cell grid. Split out so the Canvas closure
/// captures plain values (repaints only when they change).
private struct GridCanvas: View {
    let visuals: [PadVisual]
    let pressed: Set<Int>
    /// Pads with a ringing looping voice (active pack only) — drawn
    /// with a persistent outline so the user can see what's still
    /// sounding after lifting their finger.
    let ringing: Set<Int>

    var body: some View {
        Canvas { context, size in
            // Opaque surface so nothing behind the grid (the touch
            // view, snapshot placeholders) shows through cell gaps.
            context.fill(
                Path(CGRect(origin: .zero, size: size)),
                with: .color(Color(white: 0.05))
            )
            let cw = size.width / 8
            let ch = size.height / 8
            for row in 1...8 {
                for col in 1...8 {
                    let rect = CGRect(
                        x: CGFloat(col - 1) * cw,
                        y: CGFloat(8 - row) * ch,
                        width: cw,
                        height: ch
                    ).insetBy(dx: 2, dy: 2)
                    let visual = visuals[(row - 1) * 8 + (col - 1)]
                    let raw = row * 10 + col
                    draw(visual, in: rect,
                         pressed: pressed.contains(raw),
                         ringing: ringing.contains(raw),
                         context: &context)
                }
            }
        }
    }

    private func draw(
        _ visual: PadVisual,
        in rect: CGRect,
        pressed: Bool,
        ringing: Bool,
        context: inout GraphicsContext
    ) {
        let shape = Path(roundedRect: rect, cornerRadius: 6)

        if visual.colorHint == 0 {
            // Empty slot: dark placeholder cell.
            context.fill(shape, with: .color(Color(white: 0.12)))
        } else {
            let color = Self.color(fromHex: visual.colorHint)
            // A ringing loop stays at full brightness even when the
            // layout says dim — it's audibly "on".
            let opacity = (visual.isBright || ringing) ? 0.95 : 0.4
            context.fill(shape, with: .color(color.opacity(opacity)))
        }

        if ringing, !pressed {
            // Persistent "still sounding" outline — thinner than the
            // pressed border so the two states read differently.
            context.stroke(
                shape, with: .color(.white.opacity(0.85)), lineWidth: 1.5
            )
        }

        if pressed {
            context.fill(shape, with: .color(.white.opacity(0.35)))
            context.stroke(shape, with: .color(.white), lineWidth: 2)
        }

        if let label = visual.label, !label.isEmpty {
            // Single line, bottom-aligned, manually ellipsized:
            // Canvas text wraps mid-word when drawn into a rect
            // ("Shim/mer…") and a centered label collides with the
            // top-right badge, so measure-and-trim instead.
            let inset = rect.insetBy(dx: 3, dy: 3)
            let styled: (String) -> Text = { s in
                Text(s)
                    .font(.system(size: 9, weight: .medium))
                    .foregroundColor(.white.opacity(0.9))
            }
            let unconstrained = CGSize(width: 1000, height: 100)
            var display = label
            var resolved = context.resolve(styled(display))
            while resolved.measure(in: unconstrained).width > inset.width,
                  display.count > 1 {
                display = String(display.dropLast())
                resolved = context.resolve(styled(display + "…"))
            }
            context.draw(
                resolved,
                at: CGPoint(x: inset.minX, y: inset.maxY),
                anchor: .bottomLeading
            )
        }

        if let badge = visual.badge {
            let symbol = Text(Image(systemName: Self.symbolName(badge)))
                .font(.system(size: 8, weight: .semibold))
                .foregroundColor(.white.opacity(0.85))
            let badgeRect = CGRect(
                x: rect.maxX - 14, y: rect.minY + 3,
                width: 11, height: 11
            )
            context.draw(symbol, in: badgeRect)
        }
    }

    private static func symbolName(_ badge: PadBadge) -> String {
        switch badge {
        case .mic:         return "mic.fill"
        case .vocoded:     return "waveform"
        case .transformed: return "wand.and.stars"
        case .loop:        return "repeat"
        case .edited:      return "pencil"
        }
    }

    private static func color(fromHex hex: UInt32) -> Color {
        Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }
}

