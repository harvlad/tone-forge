// DiagnosticsView.swift
//
// Settings → Diagnostics: one row per P7 ship gate, driven by
// LatencyProbe. Each row shows the measured value against the
// budget with a green/red/gray verdict; "Run All" walks every gate
// and the toolbar Copy button puts the plain-text summary on the
// pasteboard for pasting into a ship-gate report.
//
// The vocoder-dropout gate needs a real device with mic permission —
// it skips (gray) on the simulator, which the row's caption calls
// out.

import SwiftUI

struct DiagnosticsView: View {
    @StateObject private var probe: LatencyProbe

    init(appState: AppState) {
        _probe = StateObject(wrappedValue: LatencyProbe(app: appState))
    }

    var body: some View {
        List {
            Section {
                Button {
                    Task { await probe.runAll() }
                } label: {
                    if probe.isRunning {
                        HStack {
                            ProgressView()
                            Text("Running…")
                        }
                    } else {
                        Text("Run All")
                    }
                }
                .disabled(probe.isRunning)
                .accessibilityIdentifier("diagnostics-run-all")
            } footer: {
                Text("The vocoder gate records a scripted 8 s take — "
                     + "it needs a device with microphone access and "
                     + "will be skipped otherwise.")
            }

            Section("Ship gates") {
                ForEach(LatencyProbe.Gate.allCases) { gate in
                    gateRow(gate)
                }
            }
        }
        .navigationTitle("Diagnostics")
        #if os(iOS)
        .navigationBarTitleDisplayMode(.inline)
        #endif
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    copySummary()
                } label: {
                    Label("Copy", systemImage: "doc.on.doc")
                }
                .disabled(probe.readings.isEmpty)
                .accessibilityIdentifier("diagnostics-copy")
            }
        }
    }

    // MARK: - Rows

    @ViewBuilder
    private func gateRow(_ gate: LatencyProbe.Gate) -> some View {
        let reading = probe.readings[gate]
        HStack(alignment: .top, spacing: 10) {
            statusIcon(reading?.status)
                .font(.title3)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(gate.title)
                    .font(.body.weight(.medium))
                Text(measureLine(gate, reading: reading))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
                if let detail = detailLine(reading) {
                    Text(detail)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
            Button("Run") {
                Task { await probe.run(gate) }
            }
            .buttonStyle(.borderless)
            .font(.callout)
            .disabled(probe.isRunning)
        }
        .padding(.vertical, 2)
        .accessibilityIdentifier("diagnostics-gate-\(gate.rawValue)")
    }

    @ViewBuilder
    private func statusIcon(_ status: LatencyProbe.Reading.Status?) -> some View {
        switch status {
        case .passed:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
        case .skipped:
            Image(systemName: "minus.circle.fill")
                .foregroundStyle(.gray)
        case nil:
            Image(systemName: "circle.dashed")
                .foregroundStyle(.secondary)
        }
    }

    private func measureLine(
        _ gate: LatencyProbe.Gate, reading: LatencyProbe.Reading?
    ) -> String {
        let budget = "budget \(LatencyProbe.format(gate.budget)) \(gate.unit)"
        guard let reading else { return "not run — \(budget)" }
        return "\(LatencyProbe.format(reading.measured)) \(gate.unit) (\(budget))"
    }

    private func detailLine(_ reading: LatencyProbe.Reading?) -> String? {
        guard let reading else { return nil }
        if case .skipped(let why) = reading.status {
            return "Skipped — \(why)"
        }
        return reading.detail.isEmpty ? nil : reading.detail
    }

    // MARK: - Copy

    private func copySummary() {
        #if os(iOS)
        UIPasteboard.general.string = probe.summary()
        #endif
    }
}
