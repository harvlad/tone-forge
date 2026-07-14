// VocoderCaptureSheet.swift
//
// Desktop vocoder capture UI: mode picker, record button, level
// meter, and status display. Opens from pad context menu "Record
// with Vocoder...".
//
// Port of iOS PadSourceSheet's vocoder section.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore
import JamDesktopAudio

/// Target pad for the vocoder capture.
struct VocoderCaptureTarget: Identifiable {
    let id = UUID()
    let padIndex: Int
}

struct VocoderCaptureSheet: View {
    let target: VocoderCaptureTarget

    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    @StateObject private var model = VocoderCaptureModel()

    var body: some View {
        VStack(spacing: 16) {
            header
            Divider()
            modePicker
            recordingSection
            Spacer()
        }
        .padding(16)
        .frame(minWidth: 400, minHeight: 360)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
    }

    // MARK: - Header

    private var header: some View {
        HStack {
            VStack(alignment: .leading, spacing: 2) {
                Text("Record with Vocoder")
                    .font(.title3.bold())
                Text("Pad \(target.padIndex + 1)")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("Done") { dismiss() }
                .keyboardShortcut(.defaultAction)
        }
    }

    // MARK: - Mode picker

    private var modePicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Mode")
                .font(.headline)

            Picker("Mode", selection: $model.selectedMode) {
                ForEach(VocoderMode.allCases, id: \.self) { mode in
                    Text(VocoderCaptureModel.displayName(for: mode))
                        .tag(mode)
                }
            }
            .pickerStyle(.segmented)

            Text(VocoderCaptureModel.blurb(for: model.selectedMode))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    // MARK: - Recording section

    @ViewBuilder
    private var recordingSection: some View {
        switch model.state {
        case .idle:
            idleView

        case .recording:
            recordingView

        case .processing:
            processingView

        case .saved(let metadata):
            savedView(metadata)

        case .failed(let message):
            failedView(message)
        }
    }

    private var idleView: some View {
        VStack(spacing: 12) {
            Button {
                startRecording()
            } label: {
                Label("Record with Vocoder", systemImage: "waveform.and.mic")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)

            Text("Up to 8 seconds. Stays on device — never uploaded.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var recordingView: some View {
        VStack(spacing: 12) {
            // Recording meter
            VStack(spacing: 4) {
                HStack {
                    Text(String(format: "%.1f s", session.vocoderCapture.elapsedSec))
                        .font(.title2.monospacedDigit())
                    Text("/ 8.0 s")
                        .foregroundStyle(.secondary)
                }

                ProgressView(
                    value: session.vocoderCapture.elapsedSec / 8.0
                )
                .tint(.red)
            }

            Button {
                stopRecording()
            } label: {
                Label("Stop", systemImage: "stop.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
            .controlSize(.large)
        }
    }

    private var processingView: some View {
        VStack(spacing: 8) {
            ProgressView()
                .controlSize(.large)
            Text("Processing...")
                .foregroundStyle(.secondary)
        }
    }

    private func savedView(_ metadata: PadSampleMetadata) -> some View {
        VStack(spacing: 12) {
            Label("Saved", systemImage: "checkmark.circle.fill")
                .font(.title2)
                .foregroundStyle(.green)

            Text(String(format: "%.1f s • %@",
                        metadata.durationSec,
                        metadata.effectiveClass.rawValue.replacingOccurrences(of: "_", with: " ")))
                .foregroundStyle(.secondary)

            Button("Record Another") {
                model.reset()
            }
        }
    }

    private func failedView(_ message: String) -> some View {
        VStack(spacing: 12) {
            Label("Failed", systemImage: "exclamationmark.triangle.fill")
                .font(.title2)
                .foregroundStyle(.red)

            Text(message)
                .font(.caption)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            Button("Try Again") {
                model.reset()
            }
        }
    }

    // MARK: - Actions

    private func startRecording() {
        model.startRecording()
        Task {
            do {
                let program = await session.buildVocoderProgram(for: model.selectedMode)
                try await session.vocoderCapture.start(program: program)
            } catch {
                model.didFail(error)
            }
        }
    }

    private func stopRecording() {
        model.stopRecording()
        Task {
            guard let take = await session.vocoderCapture.stop() else {
                model.didFail(VocoderCaptureSession.CaptureError.noInputAvailable)
                return
            }
            do {
                let metadata = try await session.saveVocoderTake(
                    take, toGridPad: target.padIndex
                )
                model.didSave(metadata)
            } catch {
                model.didFail(error)
            }
        }
    }
}
