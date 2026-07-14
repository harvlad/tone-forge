// ToneCardView.swift
//
// Tone recommendation card — jam.js renderToneCard parity: tier
// badge (high/medium/low/unknown), match title, rationale, Apply
// button and clickable alternate chips. Applying goes through the
// bridge (server resolves the chain and acks with the spec, which
// programs the local DSP); dismissal reports to /api/tone/ignored.

import SwiftUI
import JamDesktopCore

struct ToneCardView: View {
    let tone: ToneRecommendation
    let activeChainId: String?
    let onApply: (String) -> Void
    let onDismiss: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                badge
                Text(tone.cardTitle ?? "Suggested tone")
                    .font(.headline)
                Spacer()
                Button(action: onDismiss) {
                    Image(systemName: "xmark")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Dismiss recommendation")
            }

            if let rationale = tone.rationale, !rationale.isEmpty {
                Text(rationale)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(spacing: 8) {
                if let chainId = tone.apply?.chainId {
                    Button(isActive(chainId) ? "Applied" : "Apply tone") {
                        onApply(chainId)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(isActive(chainId))
                }

                ForEach(alternateChainIds, id: \.self) { chainId in
                    Button(ToneRecommendation.displayName(forChainId: chainId)) {
                        onApply(chainId)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(isActive(chainId))
                }
            }
        }
        .padding(12)
        .jamCard()
    }

    private func isActive(_ chainId: String) -> Bool {
        chainId == activeChainId
    }

    private var alternateChainIds: [String] {
        (tone.alternates ?? []).compactMap(\.chainId)
    }

    /// jam.js tier -> badge text/color mapping.
    private var badge: some View {
        let (label, color): (String, Color) = {
            switch tone.tier {
            case "high": return ("High match", .green)
            case "medium": return ("Suggested", .blue)
            case "low": return ("Low confidence", .orange)
            default: return ("Default", .gray)
            }
        }()
        return Text(label)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(Capsule().fill(color.opacity(0.2)))
            .foregroundStyle(color)
    }
}
