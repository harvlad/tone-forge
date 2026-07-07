// PadSourceSheet.swift
//
// The P3 pad source flow, opened by a long-press on the grid:
//   * empty pad     → record a mic sample onto it, or assign one of
//                     the existing local samples
//   * local pad     → manage the assigned sample: classify override,
//                     preview, un-assign, delete
//
// The sheet never touches the audio graph directly: recording runs on
// MicRecorder's private engine, playback previews go through the
// contribution bus (same D-015 invariant as PadEffectsEditor), and
// all mutations route through ModeCoordinator so the grid, the
// scheduler, and the stores stay in sync.
//
// Compliance: everything recorded here is device-local forever —
// PadSampleMetadata's `neverUpload` tripwire is set at save and the
// store directory is excluded from every upload path (ComplianceTests,
// P7).

import SwiftUI
import ToneForgeEngine

struct PadSourceSheet: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    let target: PadSourceTarget
    /// Fires the pad through the contribution bus (down + short hold
    /// + up), mirroring PadEffectsEditor's preview path.
    let onPreview: () -> Void

    var body: some View {
        NavigationStack {
            Group {
                if let sample = target.sample {
                    ManageLocalPadView(
                        target: target,
                        initialSample: sample,
                        onPreview: onPreview,
                        onDone: { dismiss() }
                    )
                } else {
                    RecordOrAssignView(
                        target: target,
                        onPreview: onPreview,
                        onDone: { dismiss() }
                    )
                }
            }
            .navigationTitle(padTitle)
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
    }

    private var padTitle: String {
        "Pad \(target.gridRow)·\(target.gridCol)"
    }
}

// MARK: - Record / assign (empty pad)

private struct RecordOrAssignView: View {
    @EnvironmentObject private var appState: AppState

    let target: PadSourceTarget
    let onPreview: () -> Void
    let onDone: () -> Void

    enum Phase: Equatable {
        case idle
        case recording
        case vocoding
        case saving
        case saved(PadSampleMetadata)
        case failed(String)
    }

    @State private var phase: Phase = .idle
    @State private var vocoderMode: VocoderMode = .classic

    var body: some View {
        Form {
            Section("Record") {
                recordControls
                if case .failed(let message) = phase {
                    Text(message)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
                if case .saved(let meta) = phase {
                    savedSummary(meta)
                }
            }

            if !appState.padSampleStore.samples.isEmpty {
                Section("Assign existing sample") {
                    ForEach(appState.padSampleStore.samples, id: \.id) { meta in
                        Button {
                            appState.modeCoordinator.assignLocalSample(
                                id: meta.id, toGridPad: target.gridRaw
                            )
                            onDone()
                        } label: {
                            LocalSampleRow(meta: meta)
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .onDisappear {
            // Abandon a live take if the user swipes the sheet away.
            appState.micRecorder.onAutoStop = nil
            appState.micRecorder.cancel()
            appState.vocoderCapture.onAutoStop = nil
            appState.vocoderCapture.cancel()
        }
    }

    @ViewBuilder
    private var recordControls: some View {
        switch phase {
        case .idle, .failed, .saved:
            Button {
                startRecording()
            } label: {
                Label(
                    phase == .idle ? "Record from mic" : "Record again",
                    systemImage: "mic.fill"
                )
            }
            Picker("Vocoder", selection: $vocoderMode) {
                ForEach(VocoderMode.allCases, id: \.self) { mode in
                    Text(mode.displayName).tag(mode)
                }
            }
            Button {
                startVocoder()
            } label: {
                Label("Record with vocoder", systemImage: "waveform")
            }
            Text(vocoderMode.blurb)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Text("Up to \(Int(MicRecorder.maxDurationSec)) s. Stays on this device — never uploaded.")
                .font(.footnote)
                .foregroundStyle(.secondary)

        case .recording:
            RecordingMeter(recorder: appState.micRecorder) {
                finishRecording(appState.micRecorder.stop() ?? [])
            }

        case .vocoding:
            VocoderMeter(capture: appState.vocoderCapture) {
                Task {
                    if let take = await appState.vocoderCapture.stop() {
                        finishVocoder(take)
                    }
                }
            }

        case .saving:
            HStack(spacing: 8) {
                ProgressView()
                Text("Processing…")
                    .foregroundStyle(.secondary)
            }
        }
    }

    @ViewBuilder
    private func savedSummary(_ meta: PadSampleMetadata) -> some View {
        HStack {
            Label(
                ModeCoordinator.classLabel(meta.effectiveClass),
                systemImage: "checkmark.circle.fill"
            )
            .foregroundStyle(.green)
            Spacer()
            Text(String(format: "%.0f%% sure", meta.confidence * 100))
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        Button("Preview") { onPreview() }
    }

    private func startRecording() {
        let recorder = appState.micRecorder
        recorder.onAutoStop = { samples in
            finishRecording(samples)
        }
        Task {
            do {
                try await recorder.start()
                phase = .recording
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func finishRecording(_ samples: [Float]) {
        appState.micRecorder.onAutoStop = nil
        phase = .saving
        Task {
            do {
                let meta = try await appState.modeCoordinator.saveMicCapture(
                    samples, toGridPad: target.gridRaw
                )
                phase = .saved(meta)
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func startVocoder() {
        let capture = appState.vocoderCapture
        capture.onAutoStop = { take in
            finishVocoder(take)
        }
        Task {
            do {
                let program = await appState.modeCoordinator
                    .vocoderProgram(for: vocoderMode)
                try await capture.start(program: program)
                phase = .vocoding
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func finishVocoder(_ take: VocoderCaptureSession.Take) {
        appState.vocoderCapture.onAutoStop = nil
        phase = .saving
        Task {
            do {
                let meta = try await appState.modeCoordinator.saveVocoderTake(
                    take, toGridPad: target.gridRaw
                )
                phase = .saved(meta)
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }
}

/// Live-recording row: elapsed time vs cap, route warning, stop.
private struct RecordingMeter: View {
    @ObservedObject var recorder: MicRecorder
    let onStop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "record.circle")
                    .foregroundStyle(.red)
                Text(String(
                    format: "%.1f / %.0f s",
                    recorder.elapsedSec, MicRecorder.maxDurationSec
                ))
                .monospacedDigit()
                Spacer()
                Button("Stop", action: onStop)
                    .buttonStyle(.borderedProminent)
            }
            ProgressView(
                value: min(recorder.elapsedSec, MicRecorder.maxDurationSec),
                total: MicRecorder.maxDurationSec
            )
            if let warning = recorder.routeWarning {
                Label(Self.text(for: warning), systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
        }
    }

    static func text(for warning: MicRecorder.RouteWarning) -> String {
        switch warning {
        case .speakerFeedbackRisk:
            return "Playing through the speaker — the mic will pick up the app's own sound."
        case .bluetoothLatency:
            return "Bluetooth audio — timing may be loose (~40 ms)."
        }
    }
}

/// Live vocoder-capture row: elapsed vs cap, route warning (speaker
/// route also means the preview is muted for feedback safety), and
/// the preview dropout counter (the P7 zero-dropouts gate).
private struct VocoderMeter: View {
    @ObservedObject var capture: VocoderCaptureSession
    let onStop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: "waveform.circle")
                    .foregroundStyle(.purple)
                Text(String(
                    format: "%.1f / %.0f s",
                    capture.elapsedSec, VocoderCaptureSession.maxDurationSec
                ))
                .monospacedDigit()
                Spacer()
                Button("Stop", action: onStop)
                    .buttonStyle(.borderedProminent)
            }
            ProgressView(
                value: min(
                    capture.elapsedSec, VocoderCaptureSession.maxDurationSec
                ),
                total: VocoderCaptureSession.maxDurationSec
            )
            if let warning = capture.routeWarning {
                Label(warningText(warning), systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
            if capture.underrunCount > 0 {
                Label(
                    "Preview dropouts: \(capture.underrunCount)",
                    systemImage: "waveform.badge.exclamationmark"
                )
                .font(.footnote)
                .foregroundStyle(.orange)
            }
        }
    }

    private func warningText(_ warning: MicRecorder.RouteWarning) -> String {
        switch warning {
        case .speakerFeedbackRisk:
            return "Speaker route — the vocoded preview is muted so the mic doesn't hear it. Use headphones to monitor."
        case .bluetoothLatency:
            return "Bluetooth audio — the preview may lag (~40 ms)."
        }
    }
}

// MARK: - Manage (pad with a local sample)

private struct ManageLocalPadView: View {
    @EnvironmentObject private var appState: AppState

    let target: PadSourceTarget
    let initialSample: PadSampleMetadata
    let onPreview: () -> Void
    let onDone: () -> Void

    /// nil = trust the classifier ("Auto").
    @State private var overrideClass: SampleClass?

    init(
        target: PadSourceTarget,
        initialSample: PadSampleMetadata,
        onPreview: @escaping () -> Void,
        onDone: @escaping () -> Void
    ) {
        self.target = target
        self.initialSample = initialSample
        self.onPreview = onPreview
        self.onDone = onDone
        self._overrideClass = State(initialValue: initialSample.userClassOverride)
    }

    var body: some View {
        Form {
            Section("Sample") {
                LocalSampleRow(meta: currentMeta)
                Button("Preview") { onPreview() }
            }

            Section {
                Picker("Type", selection: $overrideClass) {
                    Text("Auto (\(ModeCoordinator.classLabel(initialSample.classification)))")
                        .tag(SampleClass?.none)
                    ForEach(SampleClass.allCases, id: \.self) { cls in
                        Text(ModeCoordinator.classLabel(cls))
                            .tag(SampleClass?.some(cls))
                    }
                }
                .onChange(of: overrideClass) { _, newValue in
                    appState.modeCoordinator.setClassOverride(
                        newValue, sampleId: initialSample.id
                    )
                }
            } header: {
                Text("Classification")
            } footer: {
                Text(String(
                    format: "Classifier said %@ (%.0f%% sure).",
                    ModeCoordinator.classLabel(initialSample.classification),
                    initialSample.confidence * 100
                ))
            }

            // P4: transform chain editor (persisted per pad+mode,
            // rendered on edit, bake → new local sample).
            PadTransformSection(gridRaw: target.gridRaw)

            Section {
                Button("Remove from pad") {
                    appState.modeCoordinator.clearLocalAssignment(
                        gridPad: target.gridRaw
                    )
                    onDone()
                }
                Button("Delete sample", role: .destructive) {
                    appState.modeCoordinator.deleteLocalSample(
                        id: initialSample.id
                    )
                    onDone()
                }
            }
        }
    }

    /// Live metadata (override edits land in the store immediately).
    private var currentMeta: PadSampleMetadata {
        appState.padSampleStore.metadata(id: initialSample.id) ?? initialSample
    }
}

// MARK: - Shared row

private struct LocalSampleRow: View {
    let meta: PadSampleMetadata

    var body: some View {
        HStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 4)
                .fill(color)
                .frame(width: 16, height: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(ModeCoordinator.classLabel(meta.effectiveClass))
                    .foregroundStyle(.primary)
                Text(subtitle)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Image(systemName: badgeSymbol)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
    }

    private var color: Color {
        let hex = meta.colorHint != 0
            ? meta.colorHint
            : ModeCoordinator.localColor(meta.source)
        return Color(
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255
        )
    }

    private var subtitle: String {
        let duration = String(format: "%.1f s", meta.durationSec)
        let date = meta.createdAt.formatted(
            date: .abbreviated, time: .shortened
        )
        return "\(duration) · \(date)"
    }

    private var badgeSymbol: String {
        switch meta.source {
        case .mic:      return "mic.fill"
        case .vocoded:  return "waveform"
        case .songChop: return "wand.and.stars"
        }
    }
}
