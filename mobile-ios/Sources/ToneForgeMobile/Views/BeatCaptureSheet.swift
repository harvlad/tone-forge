// BeatCaptureSheet.swift
//
// Beat Capture (D-024): tap / beatbox / clap a rhythm into the mic and
// turn it into an editable drum pattern. Phases: idle → recording →
// analyzing → review. Audio is analysis-only — nothing is written to
// PadSampleStore. On "Open in Sequencer" the built pattern is saved
// and handed to the sequencer via a pending id.
//
// Live Beat mode (added D-025): real-time percussion input. Tap any
// surface to trigger drum samples immediately. User calibrates profiles
// mapping their physical sounds to drum roles.

import SwiftUI
import ToneForgeEngine

/// Beat capture mode selector: Record Beat (original) or Live Beat (real-time).
enum BeatCaptureMode: String, CaseIterable {
    case record = "Record Beat"
    case live = "Live Beat"
}

struct BeatCaptureSheet: View {
    @ObservedObject var coordinator: ModeCoordinator
    /// Commit id of the pattern to open in the sequencer.
    let onOpenInSequencer: (UUID) -> Void

    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    /// Mode toggle: Record Beat (analyze after) or Live Beat (real-time).
    @State private var beatMode: BeatCaptureMode = .record

    /// Longer than the 8 s pad cap — allowed because Beat Capture never
    /// persists the audio (only the derived pattern).
    static let captureDurationSec: Double = 16

    private enum Phase: Equatable {
        case idle, recording, analyzing, review, failed(String)
    }

    @State private var phase: Phase = .idle
    @State private var hits: [DetectedHit] = []
    @State private var bpm: Double = 120
    @State private var tempoConfidence: Double = 1
    @State private var needsManualTempo = false
    @State private var songSynced = false
    @State private var quantize: BeatQuantize = .keep
    @State private var pattern = SequencerPattern()

    /// Standalone player used to audition the built pattern (through
    /// beatkit) before committing to the sequencer. Nil when not previewing.
    @State private var previewPlayer: SequencerPlayer?
    @State private var isPreviewing = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Mode picker
                Picker("Mode", selection: $beatMode) {
                    ForEach(BeatCaptureMode.allCases, id: \.self) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .padding()

                // Content based on mode
                Group {
                    if beatMode == .live {
                        liveBeatContent
                    } else {
                        recordBeatContent
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
            .background(TFTheme.background)
            .navigationTitle("Beat Capture")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { cancelAndDismiss() }
                }
            }
        }
    }

    // MARK: - Live Beat content

    private var liveBeatContent: some View {
        LiveBeatView(
            controller: appState.liveBeatController,
            profileStore: appState.liveBeatProfileStore
        )
    }

    // MARK: - Record Beat content (existing flow)

    @ViewBuilder
    private var recordBeatContent: some View {
        switch phase {
        case .idle: idleView
        case .recording: recordingView
        case .analyzing: analyzingView
        case .review: reviewView
        case .failed(let msg): failedView(msg)
        }
    }

    private var idleView: some View {
        VStack(spacing: 20) {
            Image(systemName: "figure.dance")
                .font(.system(size: 44))
                .foregroundStyle(Color.accentColor)
            Text("Tap, clap, or beatbox a rhythm. We'll detect the hits and build an editable drum pattern.")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
                .multilineTextAlignment(.center)
            if appState.currentBundle != nil {
                Label("Following the song's tempo", systemImage: "metronome")
                    .font(.footnote)
                    .foregroundStyle(TFTheme.textSecondary)
            }
            Button {
                startRecording()
            } label: {
                Label("Record", systemImage: "record.circle")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(.top, 40)
    }

    private var recordingView: some View {
        // Observe the recorder DIRECTLY (child view) so its @Published
        // elapsedSec / levels drive live updates — the sheet itself only
        // holds micRecorder via a plain `let` on AppState, which SwiftUI
        // would not re-render on.
        RecordingView(
            recorder: appState.micRecorder,
            captureDurationSec: Self.captureDurationSec,
            warningText: text(for:),
            onStop: { stopRecording() }
        )
    }

    private var analyzingView: some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Finding the beat…")
                .font(.subheadline)
                .foregroundStyle(TFTheme.textSecondary)
        }
        .padding(.top, 60)
    }

    private var reviewView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                tempoSection
                roleCountsSection
                quantizeSection
                hitListSection
            }
        }
        .safeAreaInset(edge: .bottom) { reviewActions }
    }

    private func failedView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundStyle(.orange)
            Text(msg)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundStyle(TFTheme.textSecondary)
            Button("Try Again") { phase = .idle }
                .buttonStyle(.borderedProminent)
        }
        .padding(.top, 50)
    }

    // MARK: - Review sections

    private var tempoSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Tempo").font(.headline)
            if songSynced {
                Label("\(Int(bpm)) BPM · following song", systemImage: "metronome")
                    .font(.subheadline)
                    .foregroundStyle(TFTheme.textSecondary)
            } else if needsManualTempo {
                Text("Couldn't lock the tempo — set it manually.")
                    .font(.footnote)
                    .foregroundStyle(.orange)
                Stepper(value: $bpm, in: 60...200, step: 1) {
                    Text("\(Int(bpm)) BPM").monospacedDigit()
                }
                .onChange(of: bpm) { _, _ in rebuild() }
            } else {
                Label("\(Int(bpm)) BPM (estimated)", systemImage: "waveform.path")
                    .font(.subheadline)
                    .foregroundStyle(TFTheme.textSecondary)
                Stepper(value: $bpm, in: 60...200, step: 1) {
                    Text("Adjust: \(Int(bpm)) BPM").monospacedDigit()
                }
                .onChange(of: bpm) { _, _ in rebuild() }
            }
        }
    }

    private var roleCountsSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Detected").font(.headline)
            let counts = roleCounts
            if counts.isEmpty {
                Text("No hits").foregroundStyle(TFTheme.textSecondary)
            } else {
                ForEach(counts, id: \.role) { entry in
                    Label(
                        "\(entry.role.displayName) ×\(entry.count)",
                        systemImage: "circle.fill"
                    )
                    .font(.subheadline)
                }
            }
        }
    }

    private var quantizeSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Quantize").font(.headline)
            Picker("Quantize", selection: $quantize) {
                ForEach(BeatQuantize.allCases, id: \.self) { q in
                    Text(q.displayName).tag(q)
                }
            }
            .pickerStyle(.segmented)
            .onChange(of: quantize) { _, _ in rebuild() }
        }
    }

    private var hitListSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Hits").font(.headline)
            Text("Tap a role to correct it.")
                .font(.footnote)
                .foregroundStyle(TFTheme.textSecondary)
            ForEach(Array(hits.enumerated()), id: \.offset) { idx, hit in
                HStack {
                    Text(String(format: "%.2fs", hit.timeSec))
                        .monospacedDigit()
                        .font(.caption)
                        .foregroundStyle(TFTheme.textSecondary)
                    Spacer()
                    Menu {
                        ForEach(DrumRole.allCases, id: \.self) { role in
                            Button(role.displayName) { correct(index: idx, to: role) }
                        }
                    } label: {
                        Text(hit.role.displayName)
                            .font(.subheadline)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 4)
                            .background(TFTheme.surface)
                            .clipShape(Capsule())
                    }
                }
            }
        }
    }

    private var reviewActions: some View {
        HStack(spacing: 12) {
            Button { stopPreview(); phase = .idle } label: {
                Text("Discard")
            }
            .buttonStyle(.bordered)
            Button {
                togglePreview()
            } label: {
                Label(
                    isPreviewing ? "Stop" : "Preview",
                    systemImage: isPreviewing ? "stop.fill" : "play.fill"
                )
            }
            .buttonStyle(.bordered)
            .disabled(hits.isEmpty)
            Button {
                openInSequencer()
            } label: {
                Label("Sequencer", systemImage: "pianokeys")
                    .lineLimit(1)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .disabled(hits.isEmpty)
        }
        .padding()
        .background(.thinMaterial)
    }

    // MARK: - Derived

    private var roleCounts: [(role: DrumRole, count: Int)] {
        DrumRole.allCases.compactMap { role in
            let n = hits.filter { $0.role == role }.count
            return n > 0 ? (role, n) : nil
        }
    }

    // MARK: - Actions

    private func startRecording() {
        let recorder = appState.micRecorder
        recorder.onAutoStop = { samples in
            Task { await analyze(samples) }
        }
        Task {
            do {
                try await recorder.start(maxDurationSec: Self.captureDurationSec)
                phase = .recording
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func stopRecording() {
        let samples = appState.micRecorder.stop() ?? []
        Task { await analyze(samples) }
    }

    private func analyze(_ samples: [Float]) async {
        appState.micRecorder.onAutoStop = nil
        phase = .analyzing
        let result = await coordinator.analyzeBeatTake(samples, quantize: quantize)
        hits = result.hits
        bpm = result.bpm
        tempoConfidence = result.tempoConfidence
        needsManualTempo = result.needsManualTempo
        songSynced = result.songSynced
        pattern = result.pattern
        phase = result.hits.isEmpty
            ? .failed("No beats detected. Try tapping a little louder and leave space between hits.")
            : .review
    }

    private func rebuild() {
        stopPreview()  // pattern changed — drop the stale preview player
        pattern = coordinator.buildBeatPattern(
            hits: hits, bpm: bpm, quantize: quantize, songSynced: songSynced
        )
    }

    // MARK: - Preview

    private func togglePreview() {
        isPreviewing ? stopPreview() : startPreview()
    }

    /// Audition the built pattern standalone (looped) through beatkit.
    /// Routes pack pads via the same delegate the sequencer uses.
    private func startPreview() {
        guard !hits.isEmpty else { return }
        var looping = pattern
        looping.isLooping = true
        let player = SequencerPlayer(
            pattern: looping, eventBus: appState.contributionBus
        )
        player.delegate = appState
        player.songBPM = bpm
        previewPlayer = player
        isPreviewing = true
        player.play()  // standalone wall-clock driver
    }

    private func stopPreview() {
        previewPlayer?.stop()
        previewPlayer = nil
        isPreviewing = false
    }

    private func correct(index: Int, to role: DrumRole) {
        guard hits.indices.contains(index) else { return }
        let old = hits[index]
        guard old.role != role else { return }
        coordinator.logBeatCorrection(hit: old, corrected: role)
        hits[index] = DetectedHit(
            timeSec: old.timeSec, role: role, confidence: 1,
            velocity: old.velocity, features: old.features
        )
        rebuild()
    }

    private func openInSequencer() {
        stopPreview()
        let id = coordinator.commitBeatPattern(pattern)
        onOpenInSequencer(id)
        dismiss()
    }

    private func cancelAndDismiss() {
        stopPreview()
        if phase == .recording {
            appState.micRecorder.onAutoStop = nil
            appState.micRecorder.cancel()
        }
        dismiss()
    }

    private func text(for warning: MicRecorder.RouteWarning) -> String {
        switch warning {
        case .speakerFeedbackRisk:
            return "Playing through the speaker — the mic will pick up the app's own sound."
        case .bluetoothLatency:
            return "Bluetooth audio — timing may be loose (~40 ms)."
        }
    }
}

// MARK: - Recording view (observes MicRecorder directly)

/// Live recording UI. Takes the recorder as an `@ObservedObject` so its
/// `@Published` elapsedSec / levels drive per-frame updates — the parent
/// sheet only holds it via a plain `let` on AppState.
private struct RecordingView: View {
    @ObservedObject var recorder: MicRecorder
    let captureDurationSec: Double
    let warningText: (MicRecorder.RouteWarning) -> String
    let onStop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "record.circle").foregroundStyle(.red)
                Text(String(
                    format: "%.1f / %.0f s",
                    recorder.elapsedSec, captureDurationSec
                ))
                .monospacedDigit()
                Spacer()
                Button("Stop") { onStop() }
                    .buttonStyle(.borderedProminent)
            }
            WaveformMeter(levels: recorder.levels, capacity: MicRecorder.maxLevels)
                .frame(height: 72)
            ProgressView(
                value: min(recorder.elapsedSec, captureDurationSec),
                total: captureDurationSec
            )
            if let warning = recorder.routeWarning {
                Label(warningText(warning), systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.orange)
            }
        }
        .padding(.top, 40)
    }
}

// MARK: - Waveform meter

/// Scrolling bar meter of recent input peaks. Newest bar on the right;
/// empty slots pad the left until the window fills.
private struct WaveformMeter: View {
    let levels: [Float]
    let capacity: Int

    var body: some View {
        GeometryReader { geo in
            let count = max(capacity, 1)
            let spacing: CGFloat = 2
            let barWidth = max(
                1, (geo.size.width - spacing * CGFloat(count - 1)) / CGFloat(count)
            )
            HStack(alignment: .center, spacing: spacing) {
                ForEach(0..<count, id: \.self) { i in
                    let pad = count - levels.count
                    let level = i >= pad ? CGFloat(levels[i - pad]) : 0
                    Capsule()
                        .fill(Color.accentColor.opacity(level > 0.001 ? 0.9 : 0.15))
                        .frame(
                            width: barWidth,
                            height: max(2, level * geo.size.height)
                        )
                }
            }
            .frame(
                width: geo.size.width, height: geo.size.height, alignment: .center
            )
        }
    }
}
