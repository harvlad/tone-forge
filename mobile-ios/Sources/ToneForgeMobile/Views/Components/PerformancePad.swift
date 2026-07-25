// PerformancePad.swift
//
// The one reusable pad tile for Jam + Perform. Screen-agnostic: give it a
// family (colour), a title, optional icon/waveform, and a state — it
// renders the JamN pad treatment. State is carried by brightness + border
// + a glyph badge (never colour alone) so it reads in peripheral vision
// during performance and stays accessible.
//
// It is pure presentation: taps/long-press are wired by the host grid so
// the audio path stays synchronous and this view never gates a trigger.

import SwiftUI
import ToneForgeEngine

/// Visual state of a pad. Distinct treatments per the guide's state row.
enum PadState: Equatable {
    case empty        // quieter, dashed, "tap or hold to add"
    case idle
    case pressed
    case playing      // ringing (one-shot / held)
    case looping      // ringing + loops
    case latched      // held on via toggle
    case armed        // queued for the next boundary
    case disabled
}

struct PerformancePad: View {
    let title: String
    var family: SampleFamily = .mixed
    /// SF Symbol shown top-leading (sound-family glyph). Nil hides it.
    var icon: String? = nil
    /// Optional normalized waveform samples (0…1) for the mini preview.
    var waveform: [Float]? = nil
    var state: PadState = .idle

    private var tint: Color { TFTheme.familyTint(family) }

    var body: some View {
        if state == .empty {
            emptyBody
        } else {
            filledBody
        }
    }

    // MARK: - Filled

    private var isActive: Bool {
        state == .pressed || state == .playing || state == .looping || state == .latched
    }

    private var fillOpacity: Double {
        switch state {
        case .pressed:  return 0.95
        case .playing, .looping, .latched: return 0.85
        case .armed:    return 0.6
        case .disabled: return 0.25
        default:        return 0.42
        }
    }

    private var borderColor: Color {
        switch state {
        case .armed:   return .orange
        case .latched: return tint
        default:       return isActive ? tint : TFTheme.border
        }
    }

    private var borderWidth: CGFloat {
        (state == .playing || state == .looping || state == .latched || state == .armed) ? 2 : 1
    }

    @ViewBuilder private var stateBadge: some View {
        switch state {
        case .armed:
            Image(systemName: "hourglass").foregroundStyle(.orange)
        case .latched:
            Image(systemName: "lock.fill").foregroundStyle(TFTheme.textPrimary)
        case .looping:
            Image(systemName: "repeat").foregroundStyle(TFTheme.textPrimary)
        case .playing:
            Image(systemName: "waveform").foregroundStyle(TFTheme.textPrimary)
        default:
            EmptyView()
        }
    }

    private var filledBody: some View {
        ZStack(alignment: .topLeading) {
            VStack(alignment: .leading, spacing: TFTheme.Spacing.xs) {
                HStack {
                    if let icon {
                        Image(systemName: icon)
                            .font(.callout)
                            .foregroundStyle(TFTheme.textPrimary)
                    }
                    Spacer(minLength: 0)
                    stateBadge.font(.caption2)
                }
                Spacer(minLength: 0)
                Text(title)
                    .font(TFTheme.padTitle)
                    .foregroundStyle(TFTheme.textPrimary)
                    .lineLimit(2)
                if let waveform, !waveform.isEmpty {
                    MiniWaveform(samples: waveform, color: TFTheme.textPrimary.opacity(0.75))
                        .frame(height: 12)
                }
            }
            .padding(TFTheme.Spacing.md)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(tint.opacity(fillOpacity), in: RoundedRectangle(cornerRadius: TFTheme.Radius.large))
        .overlay(
            RoundedRectangle(cornerRadius: TFTheme.Radius.large)
                .stroke(borderColor, lineWidth: borderWidth)
        )
        .opacity(state == .disabled ? 0.5 : 1)
        .contentShape(RoundedRectangle(cornerRadius: TFTheme.Radius.large))
        .accessibilityLabel(Text(title))
        .accessibilityValue(Text(accessibilityStateText))
    }

    // MARK: - Empty

    private var emptyBody: some View {
        VStack(spacing: TFTheme.Spacing.xs) {
            Image(systemName: "plus")
                .font(.title3)
                .foregroundStyle(TFTheme.textSecondary)
            Text("Tap or hold to add")
                .font(TFTheme.metadata)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(TFTheme.surface2.opacity(0.5), in: RoundedRectangle(cornerRadius: TFTheme.Radius.large))
        .overlay(
            RoundedRectangle(cornerRadius: TFTheme.Radius.large)
                .strokeBorder(TFTheme.border, style: StrokeStyle(lineWidth: 1, dash: [4, 4]))
        )
        .contentShape(RoundedRectangle(cornerRadius: TFTheme.Radius.large))
        .accessibilityLabel(Text("Empty pad"))
        .accessibilityHint(Text("Tap or hold to add a sound"))
    }

    private var accessibilityStateText: String {
        switch state {
        case .empty: return "empty"
        case .idle: return "ready"
        case .pressed: return "pressed"
        case .playing: return "playing"
        case .looping: return "looping"
        case .latched: return "latched"
        case .armed: return "queued"
        case .disabled: return "unavailable"
        }
    }
}

/// Tiny static waveform preview drawn with a Canvas — cheap, no per-frame
/// work, safe on the tile grid.
struct MiniWaveform: View {
    let samples: [Float]
    var color: Color = .white

    var body: some View {
        Canvas { ctx, size in
            guard !samples.isEmpty else { return }
            let w = size.width, h = size.height, mid = h / 2
            let step = w / CGFloat(samples.count)
            var path = Path()
            for (i, s) in samples.enumerated() {
                let x = CGFloat(i) * step
                let amp = CGFloat(max(0, min(1, s))) * mid
                path.move(to: CGPoint(x: x, y: mid - amp))
                path.addLine(to: CGPoint(x: x, y: mid + amp))
            }
            ctx.stroke(path, with: .color(color), lineWidth: max(1, step * 0.6))
        }
        .allowsHitTesting(false)
    }
}
