// QualityMetricsView.swift
//
// Quality-analysis result panels, ported from studio.html's admin
// quality view: verdict header, stem-quality/contamination/artifact
// metric grids, role + confidence map, priors, and warnings.

import SwiftUI
import JamDesktopCore

struct QualityMetricsView: View {
    let quality: QualityAnalysis

    private let columns = [
        GridItem(.adaptive(minimum: 260), spacing: 12, alignment: .top)
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let report = quality.qualityReport {
                verdictCard(report)
            }
            LazyVGrid(columns: columns, alignment: .leading, spacing: 12) {
                if let sq = quality.stemQuality {
                    stemQualityCard(sq)
                }
                if let ct = quality.contamination {
                    contaminationCard(ct)
                }
                if let af = quality.artifacts {
                    artifactsCard(af)
                }
                if let role = quality.role {
                    roleCard(role)
                }
                if let cm = quality.confidenceMap {
                    confidenceMapCard(cm)
                }
                if let priors = quality.priors {
                    priorsCard(priors)
                }
                if let detected = quality.detected {
                    detectedCard(detected)
                }
            }
            if quality.reconstructionAvailable == false {
                Text("Reconstruction pipeline unavailable on this backend — "
                    + "only basic file facts and tone confidence returned.")
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
        }
    }

    // MARK: - verdict

    private func verdictCard(_ report: QualityReport) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: report.shouldProceed == true
                    ? "checkmark.seal.fill" : "xmark.seal.fill")
                    .foregroundStyle(report.shouldProceed == true
                        ? .green : JamTheme.error)
                Text((report.qualityLevel ?? "unknown").capitalized)
                    .font(.title3.bold())
                if let confidence = report.overallConfidence {
                    Text(percent(confidence))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(JamTheme.textSecondary)
                }
                Spacer()
                if let time = quality.analysisTimeMs {
                    Text(String(format: "%.1fs analysis", time / 1000))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(JamTheme.textSecondary)
                }
            }
            if let warnings = report.warnings, !warnings.isEmpty {
                ForEach(Array(warnings.enumerated()), id: \.offset) { _, warning in
                    warningRow(warning)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .jamCard()
    }

    private func warningRow(_ warning: QualityWarning) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: warning.level == "error"
                ? "exclamationmark.octagon" : "exclamationmark.triangle")
                .font(.caption)
                .foregroundStyle(warning.level == "error"
                    ? JamTheme.error : .orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(warning.message ?? warning.category ?? "Warning")
                    .font(.caption)
                if let recommendation = warning.recommendation {
                    Text(recommendation)
                        .font(.caption2)
                        .foregroundStyle(JamTheme.textSecondary)
                }
            }
        }
    }

    // MARK: - metric cards

    private func stemQualityCard(_ sq: StemQualityMetrics) -> some View {
        card("Stem quality", icon: "waveform") {
            bar("Overall", sq.overallQuality)
            bar("Transients", sq.transientIntegrity)
            bar("Harmonic purity", sq.harmonicPurity)
            bar("Reverb density", sq.reverbDensity)
            bar("Stereo coherence", sq.stereoCoherence)
            if let snr = sq.snrEstimate {
                labelled("SNR", String(format: "%.1f dB", snr))
            }
        }
    }

    private func contaminationCard(_ ct: ContaminationMetrics) -> some View {
        card("Contamination", icon: "drop.triangle") {
            bar("Overall", ct.overallContamination, inverted: true)
            bar("Bass bleed", ct.bassBleed, inverted: true)
            bar("Drum bleed", ct.drumBleed, inverted: true)
            bar("Vocal bleed", ct.vocalBleed, inverted: true)
            bar("Reverb", ct.reverbContamination, inverted: true)
        }
    }

    private func artifactsCard(_ af: ArtifactMetrics) -> some View {
        card("Artifacts", icon: "bolt.trianglebadge.exclamationmark") {
            labelled(
                "Clipping",
                af.clippingDetected == true
                    ? "yes" + (af.clippingSeverity.map {
                        String(format: " (%.0f%%)", $0 * 100)
                    } ?? "")
                    : "none")
            if let noise = af.noiseFloorDb {
                labelled("Noise floor", String(format: "%.1f dB", noise))
            }
            if let dc = af.dcOffset {
                labelled("DC offset", String(format: "%.4f", dc))
            }
            labelled("Phase issues", af.phaseIssues == true ? "yes" : "none")
        }
    }

    private func roleCard(_ role: RoleClassification) -> some View {
        card("Role", icon: "person.wave.2") {
            if let primary = role.primaryRole {
                labelled("Primary", spaced(primary))
            }
            if let confidence = role.confidence {
                bar("Confidence", confidence)
            }
            labelled("Spectral", role.spectralProfile.map(spaced) ?? "—")
            labelled("Temporal", role.temporalProfile.map(spaced) ?? "—")
        }
    }

    private func confidenceMapCard(_ cm: ConfidenceMapSummary) -> some View {
        card("Confidence map", icon: "map") {
            if let global = cm.globalConfidence {
                bar("Global", global)
            }
            if let count = cm.regionCount {
                labelled("Regions", "\(count)")
            }
            labelled("Low confidence", "\(cm.lowConfidenceRegions ?? 0)")
            labelled("High confidence", "\(cm.highConfidenceRegions ?? 0)")
        }
    }

    private func priorsCard(_ priors: ArchetypePriors) -> some View {
        card("Archetype priors", icon: "dial.medium") {
            if let archetype = priors.sourceArchetype {
                labelled("Archetype", spaced(archetype))
            }
            if let onset = priors.onsetThreshold {
                labelled("Onset threshold", String(format: "%.2f", onset))
            }
            if let frame = priors.frameThreshold {
                labelled("Frame threshold", String(format: "%.2f", frame))
            }
            if let minNote = priors.minNoteMs {
                labelled("Min note", String(format: "%.0f ms", minNote))
            }
            if let strength = priors.quantizationStrength {
                labelled("Quantization", String(format: "%.2f", strength))
            }
        }
    }

    private func detectedCard(_ detected: DetectedTone) -> some View {
        card("Detected tone", icon: "amplifier") {
            if let family = detected.ampFamily {
                labelled("Amp", spaced(family).uppercased())
            }
            if let gain = detected.gain {
                labelled("Gain", String(format: "%.1f", gain * 10))
            }
            if let scores = quality.confidenceScores {
                if let amp = scores.ampFamily { bar("Amp confidence", amp) }
                if let gain = scores.gain { bar("Gain confidence", gain) }
                if let cab = scores.cab { bar("Cab confidence", cab) }
                if let fx = scores.effects { bar("FX confidence", fx) }
            }
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

    private func labelled(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
                .frame(width: 120, alignment: .leading)
            Text(value)
                .font(.caption.monospacedDigit())
        }
    }

    /// Metric bar: green-good by default; `inverted` for metrics where
    /// high is bad (contamination/bleed).
    private func bar(
        _ label: String, _ value: Double?, inverted: Bool = false
    ) -> some View {
        HStack(spacing: 8) {
            Text(label)
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
                .frame(width: 120, alignment: .leading)
            if let value {
                let clamped = min(1, max(0, value))
                let bad = inverted ? clamped > 0.5 : clamped < 0.5
                ProgressView(value: clamped)
                    .controlSize(.small)
                    .tint(bad ? JamTheme.error : JamTheme.accent)
                    .frame(maxWidth: 110)
                Text(percent(clamped))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(JamTheme.textSecondary)
            } else {
                Text("—")
                    .font(.caption)
                    .foregroundStyle(JamTheme.textSecondary)
            }
        }
    }

    private func percent(_ value: Double) -> String {
        String(format: "%.0f%%", value * 100)
    }

    private func spaced(_ text: String) -> String {
        text.replacingOccurrences(of: "_", with: " ")
    }
}
