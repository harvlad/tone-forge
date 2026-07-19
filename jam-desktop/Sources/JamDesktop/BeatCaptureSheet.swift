// BeatCaptureSheet.swift
//
// Beat Capture (D-024) desktop UI: tap / beatbox / clap a rhythm into
// the mic and turn it into an editable drum pattern. Phases: idle →
// recording → analyzing → review. Audio is analysis-only — nothing is
// persisted. On "Open in Sequencer" the built pattern seeds the live
// sequencer (through the bundled beatkit) and the panel is presented.
//
// Desktop port of iOS BeatCaptureSheet.

import SwiftUI
import ToneForgeEngine
import JamDesktopAudio
import JamDesktopCore

struct BeatCaptureSheet: View {
    @EnvironmentObject private var session: SessionController
    @Environment(\.dismiss) private var dismiss

    /// Called after the pattern seeds the sequencer — the parent flips
    /// to the sequencer panel.
    let onOpenInSequencer: () -> Void

    /// Mode selector: Record (existing) or Live Beat (real-time).
    private enum CaptureMode: String, CaseIterable {
        case record = "Record"
        case liveBeat = "Live Beat"
    }

    private enum Phase: Equatable {
        case idle, recording, analyzing, review, failed(String)
        case liveBeat  // Active Live Beat mode
    }

    @State private var captureMode: CaptureMode = .record
    @State private var phase: Phase = .idle
    @State private var hits: [DetectedHit] = []
    @State private var bpm: Double = 120
    @State private var tempoConfidence: Double = 1
    @State private var needsManualTempo = false
    @State private var songSynced = false
    @State private var quantize: BeatQuantize = .keep
    @State private var pattern = SequencerPattern()

    /// Standalone player used to audition the built pattern (through
    /// beatkit) before committing. Nil when not previewing.
    @State private var previewPlayer: SequencerPlayer?
    @State private var isPreviewing = false

    /// "Help improve drum detection" opt-in (mirrors the shared
    /// UserDefaults flag the upload gate reads).
    @AppStorage(BeatTrainingStore.shareOptInKey) private var shareOptIn = true

    /// Kick detection intent: body-percussion "kicks" and soft ghost
    /// snares are acoustically identical, so the performer declares
    /// whether the take has a kick voice. Off = single-drum take (soft
    /// hits stay ghost notes).
    @AppStorage("beatCaptureDetectKick") private var detectKick = true

    /// Raw samples of the analysed take, kept so flipping "Detect kick"
    /// in review can re-analyse without re-recording.
    @State private var lastSamples: [Float] = []

    /// Live Beat calibration + profile sheets.
    @State private var showCalibration = false
    @State private var showProfilePicker = false

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            content
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
        .frame(minWidth: 420, minHeight: 420)
        .background(JamTheme.background)
        .preferredColorScheme(.dark)
        .tint(JamTheme.accent)
        .sheet(isPresented: $showCalibration) {
            LiveBeatCalibrationView(
                calibrator: session.liveBeatCalibrator,
                profileStore: session.liveBeatProfileStore
            )
        }
        .sheet(isPresented: $showProfilePicker) {
            LiveBeatProfilePicker(
                store: session.liveBeatProfileStore,
                onSelect: { _ in }
            )
        }
    }

    private var header: some View {
        HStack {
            Text("Beat Capture").font(.headline)
            Spacer()
            Button { cancelAndDismiss() } label: {
                Image(systemName: "xmark.circle.fill")
                    .font(.title3)
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.escape, modifiers: [])
        }
        .padding(12)
    }

    // MARK: - Phase content

    @ViewBuilder
    private var content: some View {
        switch phase {
        case .idle: idleView
        case .recording: recordingView
        case .analyzing: analyzingView
        case .review: reviewView
        case .failed(let msg): failedView(msg)
        case .liveBeat: liveBeatView
        }
    }

    private var idleView: some View {
        VStack(spacing: 20) {
            Image(systemName: captureMode == .record ? "figure.dance" : "hand.tap.fill")
                .font(.system(size: 44))
                .foregroundStyle(JamTheme.accent)

            // Mode picker
            Picker("Mode", selection: $captureMode) {
                ForEach(CaptureMode.allCases, id: \.self) { mode in
                    Text(mode.rawValue).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 240)

            Text(captureMode == .record
                ? "Tap, clap, or beatbox a rhythm. We'll detect the hits and build an editable drum pattern."
                : "Tap surfaces to trigger drum samples in real-time. Desk = kick, thigh = snare, clap = clap.")
                .font(.subheadline)
                .foregroundStyle(JamTheme.textSecondary)
                .multilineTextAlignment(.center)

            if captureMode == .record, session.currentSongTempoBpm != nil {
                Label("Following the song's tempo", systemImage: "metronome")
                    .font(.footnote)
                    .foregroundStyle(JamTheme.textSecondary)
            }

            if captureMode == .record {
                detectKickToggle
                    .frame(maxWidth: 280)
            }

            if captureMode == .liveBeat {
                liveBeatProfileRow
            }

            Button {
                if captureMode == .record {
                    startRecording()
                } else {
                    startLiveBeat()
                }
            } label: {
                Label(captureMode == .record ? "Record" : "Start Live Beat",
                      systemImage: captureMode == .record ? "record.circle" : "hand.tap.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(.top, 40)
    }

    /// Active-profile chip + Calibrate button shown on the Live Beat intro.
    private var liveBeatProfileRow: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Text("Profile")
                    .font(.subheadline)
                    .foregroundStyle(JamTheme.textSecondary)
                Button {
                    showProfilePicker = true
                } label: {
                    HStack(spacing: 6) {
                        Text(session.liveBeatProfileStore.activeProfile?.name ?? "Default")
                            .font(.subheadline)
                        Image(systemName: "chevron.down")
                            .font(.caption)
                    }
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(JamTheme.surface)
                    .clipShape(Capsule())
                }
                .buttonStyle(.plain)
            }

            Button {
                showCalibration = true
            } label: {
                Label("Calibrate…", systemImage: "waveform.badge.mic")
            }
            .buttonStyle(.bordered)
        }
    }

    private var recordingView: some View {
        RecordingView(
            capture: session.beatCapture,
            onStop: { stopRecording() }
        )
    }

    private var analyzingView: some View {
        VStack(spacing: 16) {
            ProgressView()
            Text("Finding the beat…")
                .font(.subheadline)
                .foregroundStyle(JamTheme.textSecondary)
        }
        .padding(.top, 60)
    }

    private var reviewView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                tempoSection
                roleCountsSection
                detectKickSection
                quantizeSection
                hitListSection
                shareSection
            }
        }
        .safeAreaInset(edge: .bottom) { reviewActions }
    }

    /// Kick-intent toggle. Flipping it in review re-analyses the kept
    /// take (manual role corrections are re-derived).
    private var detectKickToggle: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("Detect kick", isOn: $detectKick)
            Text("Off: treat every low thump as a soft snare "
                 + "(one-drum take with ghost notes).")
                .font(.footnote)
                .foregroundStyle(JamTheme.textSecondary)
        }
    }

    private var detectKickSection: some View {
        detectKickToggle
            .onChange(of: detectKick) { _, _ in
                guard !lastSamples.isEmpty else { return }
                stopPreview()
                Task { await analyze(lastSamples) }
            }
    }

    /// "Help improve drum detection" opt-in. When on, correcting a hit
    /// uploads the correction (analysis features only, never audio) to
    /// the backend training corpus.
    private var shareSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            Toggle("Help improve drum detection", isOn: $shareOptIn)
                .onChange(of: shareOptIn) { _, newValue in
                    BeatTrainingStore.shareOptIn = newValue
                }
            Text("Sends your role corrections (analysis features only, "
                 + "never audio) so detection improves over time.")
                .font(.footnote)
                .foregroundStyle(JamTheme.textSecondary)
        }
    }

    private func failedView(_ msg: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundStyle(.orange)
            Text(msg)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .foregroundStyle(JamTheme.textSecondary)
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
                    .foregroundStyle(JamTheme.textSecondary)
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
                    .foregroundStyle(JamTheme.textSecondary)
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
                Text("No hits").foregroundStyle(JamTheme.textSecondary)
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
            Text("Pick a role to correct it.")
                .font(.footnote)
                .foregroundStyle(JamTheme.textSecondary)
            ForEach(Array(hits.enumerated()), id: \.offset) { idx, hit in
                HStack {
                    Text(String(format: "%.2fs", hit.timeSec))
                        .monospacedDigit()
                        .font(.caption)
                        .foregroundStyle(JamTheme.textSecondary)
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
                            .background(JamTheme.surface)
                            .clipShape(Capsule())
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                }
            }
        }
    }

    private var reviewActions: some View {
        HStack(spacing: 12) {
            Button { stopPreview(); lastSamples = []; phase = .idle } label: {
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
        session.beatCapture.onAutoStop = { take in
            Task { await analyze(take.raw) }
        }
        Task {
            do {
                try await session.beatCapture.start()
                phase = .recording
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func stopRecording() {
        let samples = session.beatCapture.stop()?.raw ?? []
        Task { await analyze(samples) }
    }

    private func analyze(_ samples: [Float]) async {
        session.beatCapture.onAutoStop = nil
        phase = .analyzing
        lastSamples = samples
        let result = await session.analyzeBeatTake(
            samples, quantize: quantize, detectKick: detectKick
        )
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
        pattern = session.buildBeatPattern(
            hits: hits, bpm: bpm, quantize: quantize, songSynced: songSynced
        )
    }

    // MARK: - Preview

    private func togglePreview() {
        isPreviewing ? stopPreview() : startPreview()
    }

    /// Audition the built pattern standalone (looped) through beatkit.
    /// Routes pack pads via the same adapter the sequencer uses.
    private func startPreview() {
        guard !hits.isEmpty else { return }
        session.ensureBeatKitRegistered()
        session.ensureEngineStarted()
        var looping = pattern
        looping.isLooping = true
        let player = SequencerPlayer(
            pattern: looping, eventBus: session.eventBus
        )
        player.delegate = session.sequencerAdapter
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
        session.logBeatCorrection(hit: old, corrected: role)
        hits[index] = DetectedHit(
            timeSec: old.timeSec, role: role, confidence: 1,
            velocity: old.velocity, features: old.features
        )
        rebuild()
    }

    private func openInSequencer() {
        stopPreview()
        session.openBeatPatternInSequencer(pattern)
        onOpenInSequencer()
        dismiss()
    }

    private func cancelAndDismiss() {
        stopPreview()
        if phase == .recording {
            session.beatCapture.onAutoStop = nil
            session.beatCapture.cancel()
        }
        if phase == .liveBeat {
            stopLiveBeat()
        }
        dismiss()
    }

    // MARK: - Live Beat

    private var liveBeatView: some View {
        LiveBeatActiveView(
            controller: session.liveBeatController,
            tap: session.liveBeatTap,
            onStop: { stopLiveBeat() }
        )
    }

    private func startLiveBeat() {
        Task {
            do {
                try await session.startLiveBeat()
                phase = .liveBeat
            } catch {
                phase = .failed(error.localizedDescription)
            }
        }
    }

    private func stopLiveBeat() {
        session.stopLiveBeat()
        phase = .idle
    }
}

// MARK: - Live Beat Active View

/// UI shown while Live Beat is active.
private struct LiveBeatActiveView: View {
    @ObservedObject var controller: LiveBeatController
    @ObservedObject var tap: LiveBeatTap
    let onStop: () -> Void

    var body: some View {
        VStack(spacing: 24) {
            // Envelope meter
            VStack(spacing: 8) {
                Text("Listening...")
                    .font(.headline)
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 4)
                            .fill(JamTheme.surface)
                        RoundedRectangle(cornerRadius: 4)
                            .fill(JamTheme.accent)
                            .frame(width: geo.size.width * CGFloat(min(1, tap.envelopeLevel * 10)))
                    }
                }
                .frame(height: 24)
            }

            // Hit count
            HStack(spacing: 16) {
                VStack {
                    Text("\(controller.hitCount)")
                        .font(.system(size: 48, weight: .bold, design: .rounded))
                        .foregroundStyle(JamTheme.accent)
                    Text("Hits")
                        .font(.caption)
                        .foregroundStyle(JamTheme.textSecondary)
                }

                // Recent hits
                if !controller.recentHits.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 4) {
                            ForEach(controller.recentHits.suffix(8), id: \.timeSec) { hit in
                                Text(hit.role.emoji)
                                    .font(.title2)
                            }
                        }
                    }
                    .frame(maxWidth: 200)
                }
            }

            // Instructions
            Text("Tap surfaces to trigger drums. Desk = kick, thigh = snare, clap = clap, table = hi-hat.")
                .font(.caption)
                .foregroundStyle(JamTheme.textSecondary)
                .multilineTextAlignment(.center)

            Button {
                onStop()
            } label: {
                Label("Stop", systemImage: "stop.fill")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.red)
        }
        .padding(.top, 20)
    }
}

private extension DrumRole {
    var emoji: String {
        switch self {
        case .kick: return "🥁"
        case .snare: return "🪘"
        case .closedHat, .openHat: return "🎩"
        case .clap: return "👏"
        case .rim, .perc: return "🔔"
        }
    }
}

// MARK: - Recording view (observes BeatCaptureSession directly)

/// Live recording UI. Observes the capture session so its `@Published`
/// elapsedSec / level drive per-frame updates.
private struct RecordingView: View {
    @ObservedObject var capture: BeatCaptureSession
    let onStop: () -> Void

    /// Rolling window of recent peak levels for a scrolling meter.
    @State private var levels: [Float] = []
    private static let meterCapacity = 48

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "record.circle").foregroundStyle(.red)
                Text(String(
                    format: "%.1f / %.0f s",
                    capture.elapsedSec, BeatCaptureSession.maxDurationSec
                ))
                .monospacedDigit()
                Spacer()
                Button("Stop") { onStop() }
                    .buttonStyle(.borderedProminent)
            }
            WaveformMeter(levels: levels, capacity: Self.meterCapacity)
                .frame(height: 72)
            ProgressView(
                value: min(capture.elapsedSec, BeatCaptureSession.maxDurationSec),
                total: BeatCaptureSession.maxDurationSec
            )
        }
        .padding(.top, 40)
        .onChange(of: capture.level) { _, level in
            levels.append(level)
            if levels.count > Self.meterCapacity {
                levels.removeFirst(levels.count - Self.meterCapacity)
            }
        }
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
                        .fill(JamTheme.accent.opacity(level > 0.001 ? 0.9 : 0.15))
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
