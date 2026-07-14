// ToneCardsView.swift
//
// Tone recommendation cards (amp / cab / effects / guitar), ported
// from studio.html renderToneCards. Formatting mirrors the web:
// knob floats scaled ×10 to one decimal, percentages ×100, ms as-is,
// snake_case shown with spaces, amp family uppercased.

import SwiftUI
import JamDesktopCore

struct ToneCardsView: View {
    let descriptor: ToneDescriptor
    let detectedType: String?

    private let columns = [
        GridItem(.adaptive(minimum: 240), spacing: 12, alignment: .top)
    ]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
            if let amp = descriptor.amp {
                ampCard(amp)
            }
            if let cab = descriptor.cab {
                cabCard(cab)
            }
            effectsCard(descriptor.effects)
            if detectedType == "guitar", let guitar = descriptor.guitar {
                guitarCard(guitar)
            }
        }
    }

    // MARK: - amp

    private func ampCard(_ amp: AmpDescriptor) -> some View {
        card("Amp", icon: "amplifier") {
            if let family = amp.family {
                headline(spaced(family).uppercased())
            }
            if let gain = amp.gain {
                labelled("Gain", knob(gain))
            }
            if let voicing = amp.voicing {
                if let bass = voicing.bass { labelled("Bass", knob(bass)) }
                if let mid = voicing.mid { labelled("Mid", knob(mid)) }
                if let treble = voicing.treble { labelled("Treble", knob(treble)) }
                if let presence = voicing.presence {
                    labelled("Presence", knob(presence))
                }
                if let scoop = voicing.midScoop {
                    labelled("Mid scoop", knob(scoop))
                }
            }
            if let alternates = amp.alternates, !alternates.isEmpty {
                labelled("Also try", alternates.compactMap { alt in
                    alt.family.map { family in
                        alt.score.map { "\(spaced(family)) (\(percent($0)))" }
                            ?? spaced(family)
                    }
                }.joined(separator: ", "))
            }
            if let confidence = descriptor.confidence?.ampFamily {
                confidenceRow(confidence)
            }
        }
    }

    // MARK: - cab

    private func cabCard(_ cab: CabDescriptor) -> some View {
        card("Cabinet", icon: "hifispeaker") {
            if let configuration = cab.configuration {
                headline(configuration.uppercased())
            }
            if let speaker = cab.speakerCharacter {
                labelled("Speaker", spaced(speaker))
            }
            if let mic = cab.micPosition {
                labelled("Mic", spaced(mic))
            }
            if let confidence = descriptor.confidence?.cab {
                confidenceRow(confidence)
            }
        }
    }

    // MARK: - effects

    private func effectsCard(_ effects: EffectsDescriptor?) -> some View {
        card("Effects chain", icon: "slider.horizontal.3") {
            if let overdrive = effects?.overdrivePedal {
                effectRow("Overdrive", parts: [
                    overdrive.style.map(spaced),
                    overdrive.drive.map { "drive \(knob($0))" },
                ])
            }
            if let compressor = effects?.compressor {
                effectRow("Compressor", parts: [
                    compressor.character.map(spaced),
                    compressor.amount.map { "amount \(percent($0))" },
                ])
            }
            if let modulation = effects?.modulation {
                effectRow("Modulation", parts: [
                    modulation.type.map(spaced),
                    modulation.rate.map { String(format: "%.1f Hz", $0) },
                    modulation.depth.map { "depth \(percent($0))" },
                ])
            }
            if let delay = effects?.delay {
                effectRow("Delay", parts: [
                    delay.type.map(spaced),
                    delay.timeMs.map { String(format: "%.0f ms", $0) },
                    delay.mix.map { "mix \(percent($0))" },
                ])
            }
            if let reverb = effects?.reverb {
                effectRow("Reverb", parts: [
                    reverb.type.map(spaced),
                    reverb.size.map { "size \(knob($0))" },
                    reverb.mix.map { "mix \(percent($0))" },
                ])
            }
            if !hasAnyEffect(effects) {
                Text("No effects detected")
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
            if let confidence = descriptor.confidence?.effects {
                confidenceRow(confidence)
            }
        }
    }

    private func hasAnyEffect(_ effects: EffectsDescriptor?) -> Bool {
        guard let effects else { return false }
        return effects.overdrivePedal != nil
            || effects.compressor != nil
            || effects.modulation != nil
            || effects.delay != nil
            || effects.reverb != nil
    }

    private func effectRow(_ name: String, parts: [String?]) -> some View {
        labelled(name, parts.compactMap { $0 }.joined(separator: " · "))
    }

    // MARK: - guitar

    private func guitarCard(_ guitar: GuitarDescriptor) -> some View {
        card("Guitar", icon: "guitars") {
            if let brightness = guitar.pickupBrightness {
                labelled("Pickup brightness", percent(brightness))
            }
            if let style = guitar.playingStyle {
                labelled("Playing style", spaced(style))
            }
            let tuning = guitar.estimatedTuning
            labelled(
                "Tuning",
                (tuning == nil || tuning == "unknown")
                    ? "Standard" : spaced(tuning!))
        }
    }

    // MARK: - shared pieces

    private func card(
        _ title: String, icon: String,
        @ViewBuilder content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Label(title, systemImage: icon)
                .font(.subheadline.bold())
                .foregroundStyle(JamTheme.textSecondary)
            content()
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func headline(_ text: String) -> some View {
        Text(text).font(.title3.bold())
    }

    private func labelled(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
                .frame(width: 110, alignment: .leading)
            Text(value)
                .font(.caption.monospacedDigit())
        }
    }

    private func confidenceRow(_ value: Double) -> some View {
        HStack(spacing: 8) {
            Text("Confidence")
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
                .frame(width: 110, alignment: .leading)
            ProgressView(value: min(1, max(0, value)))
                .controlSize(.small)
                .frame(maxWidth: 100)
            Text(percent(value))
                .font(.caption.monospacedDigit())
                .foregroundStyle(JamTheme.textSecondary)
        }
    }

    /// 0–1 float shown as a 0–10 knob value (web: (v*10).toFixed(1)).
    private func knob(_ value: Double) -> String {
        String(format: "%.1f", value * 10)
    }

    private func percent(_ value: Double) -> String {
        String(format: "%.0f%%", value * 100)
    }

    private func spaced(_ text: String) -> String {
        text.replacingOccurrences(of: "_", with: " ")
    }
}
