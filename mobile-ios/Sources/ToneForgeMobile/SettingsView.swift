// SettingsView.swift
//
// Perform-settings sheet:
//   - Levels: Voice / Chops bus gains (SampleSettingsStore)
//   - Pad synth voice knobs: brightness / strum / attack / release
//   - Shared reverb (AudioEngine.ReverbParams, D-013): wet / dry /
//     length — one reverb colours voice, chops and vocoder alike
//
// Backend URL editing lives here too so the dev can point at a
// staging server without recompiling.

import SwiftUI
import ToneForgeEngine

struct SettingsView: View {
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var backendText: String = ""

    // Legal & compliance + Data sections.
    @StateObject private var attestation = AttestationStore()
    // Storage browsers (P7). Sample + session stores live on
    // AppState; bounces are plain files, so the browser owns its
    // own store over Documents/bounces.
    @StateObject private var bounceStore = BounceStore()
    @State private var showHelp = false
    @State private var showTerms = false
    @State private var showPrivacy = false
    @State private var showDeleteAllConfirm = false
    @State private var isDeletingAll = false
    @State private var deleteAllError: String?

    /// "ToneForge Mobile 1.0 (42)" from the app bundle; the version
    /// keys are absent under SwiftPM test hosts, hence the fallback.
    /// (Moved from the deleted ProfileView, D-022.)
    static var buildLabel: String {
        let info = Bundle.main.infoDictionary
        guard let version = info?["CFBundleShortVersionString"] as? String
        else { return "ToneForge Mobile (dev)" }
        let build = info?["CFBundleVersion"] as? String
        return "ToneForge Mobile \(version)"
            + (build.map { " (\($0))" } ?? "")
    }

    var body: some View {
        NavigationStack {
            Form {
                AccountSection(
                    account: appState.accountStore,
                    baseURL: appState.backendBaseURL
                )

                Section("Server") {
                    HStack {
                        TextField("Backend URL", text: $backendText)
                            .autocorrectionDisabled()
                            #if os(iOS)
                            .textInputAutocapitalization(.never)
                            .keyboardType(.URL)
                            #endif
                        Button("Set") {
                            // Normalize to https: ATS rejects plain http
                            // on device, so an http:// override would
                            // silently break every backend fetch.
                            var text = backendText
                                .trimmingCharacters(in: .whitespacesAndNewlines)
                            if text.lowercased().hasPrefix("http://") {
                                text = "https://" + text.dropFirst("http://".count)
                            } else if !text.lowercased().hasPrefix("https://") {
                                text = "https://" + text
                            }
                            if let url = URL(string: text) {
                                appState.backendBaseURL = url
                            }
                        }
                        .buttonStyle(.borderless)
                    }
                }

                // Voice drives PadSynthParams.masterGain via the
                // wireSampleSettings sink (single writer). It replaced
                // the old direct "Master" row. Child view so the
                // nested SampleSettingsStore is actually observed.
                LevelsSection(settings: appState.sampleSettings)

                Section("Pad synth — voice") {
                    sliderRow(
                        title: "Brightness",
                        value: binding(\.brightness),
                        range: 0.5...2.0,
                        formatter: { String(format: "%.2fx", $0) }
                    )
                    sliderRow(
                        title: "Attack",
                        value: binding(\.attackMs),
                        range: 1...40,
                        formatter: { String(format: "%.0f ms", $0) }
                    )
                    sliderRow(
                        title: "Release",
                        value: binding(\.releaseSec),
                        range: 0.6...4.0,
                        formatter: { String(format: "%.2f s", $0) }
                    )
                    sliderRow(
                        title: "Strum",
                        value: binding(\.strumMs),
                        range: 0...60,
                        formatter: { String(format: "%.0f ms", $0) }
                    )
                }

                // One shared reverb on the layer bus (D-013) — drives
                // AudioEngine.ReverbParams, colouring voice, chops and
                // vocoder alike. Child view so the nested AudioEngine
                // is actually observed.
                ReverbSection(engine: appState.audioEngine)

                MIDIControllersSection(
                    settings: appState.sampleSettings,
                    transport: appState.midiKeyboard
                )

                Section {
                    Button("Reset to defaults") {
                        appState.padSynth.update(params: PadSynthParams())
                        appState.audioEngine.setReverbParams(AudioEngine.ReverbParams())
                    }
                }

                // P7 ship-gate probe: latency / load / dropout
                // measurements against the hard budgets.
                Section("Diagnostics") {
                    NavigationLink("Latency & gates") {
                        DiagnosticsView(appState: appState)
                    }
                    .accessibilityIdentifier("settings-diagnostics-link")
                }

                // Beat Capture (D-024): device-local classifier
                // corrections, exportable as training CSV. Hidden until
                // the user has logged at least one correction.
                BeatTrainingSection(store: appState.beatTrainingStore)

                // Storage browsers (P7): samples / sessions /
                // bounces, each with per-row delete + delete-all.
                StorageSection(
                    sampleStore: appState.padSampleStore,
                    bounceStore: bounceStore
                )

                Section("Help") {
                    Button("How Tone Forge works") { showHelp = true }
                        .accessibilityIdentifier("settings-help-button")
                }

                Section("About") {
                    LabeledContent("Build") {
                        Text(Self.buildLabel)
                            .foregroundStyle(.secondary)
                    }
                }

                legalSection
                dataSection
            }
            .navigationTitle("Settings")
            #if os(iOS)
            .navigationBarTitleDisplayMode(.inline)
            #endif
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                backendText = appState.backendBaseURL.absoluteString
            }
            .sheet(isPresented: $showHelp) { HelpSheet() }
            .sheet(isPresented: $showTerms) { TermsOfServiceSheet() }
            .sheet(isPresented: $showPrivacy) { PrivacyPolicySheet() }
            .confirmationDialog(
                "Delete all analyses?",
                isPresented: $showDeleteAllConfirm,
                titleVisibility: .visible
            ) {
                Button("Delete everything", role: .destructive) {
                    deleteAllServerData()
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("Removes every analysed song, its stems and layers from the server and this device. This can't be undone.")
            }
        }
    }

    // MARK: - Legal & compliance

    private var legalSection: some View {
        Section("Legal & compliance") {
            if let mailto = URL(string: "mailto:\(AppConfig.takedownEmail)") {
                Link("Report copyright issue", destination: mailto)
            }
            Button("Terms of Service") { showTerms = true }
            Button("Privacy Policy") { showPrivacy = true }
            // Aggregate attribution roll-up (D-024): licensed demo
            // tracks + sample-pack license/provenance.
            NavigationLink("Credits & licenses") { CreditsView() }
            LabeledContent(
                "Ownership attestation",
                value: attestationStatus
            )
        }
    }

    private var attestationStatus: String {
        guard attestation.isAccepted else { return "Not yet accepted" }
        guard let date = attestation.acceptedAt else { return "Accepted" }
        return "Accepted \(date.formatted(date: .abbreviated, time: .omitted))"
    }

    // MARK: - Data

    private var dataSection: some View {
        Section {
            Button(role: .destructive) {
                showDeleteAllConfirm = true
            } label: {
                if isDeletingAll {
                    HStack {
                        ProgressView()
                        Text("Deleting…")
                    }
                } else {
                    Text("Delete all analyses from server")
                }
            }
            .disabled(isDeletingAll)
            if let err = deleteAllError {
                Text(err).font(.caption).foregroundStyle(.red)
            }
        } header: {
            Text("Data")
        } footer: {
            Text("Analyses are kept on the server for at most 7 days, then deleted automatically.")
        }
    }

    private func deleteAllServerData() {
        isDeletingAll = true
        deleteAllError = nil
        Task {
            do {
                try await appState.deleteAllServerData()
            } catch {
                deleteAllError = error.localizedDescription
            }
            isDeletingAll = false
        }
    }

    // MARK: - Bindings

    /// Bridge a PadSynthParams `Double` field to a slider Binding that
    /// pushes updates through ``PadSynth.update`` on each change.
    private func binding(_ keyPath: WritableKeyPath<PadSynthParams, Double>) -> Binding<Double> {
        Binding(
            get: { appState.padSynth.params[keyPath: keyPath] },
            set: { newValue in
                var p = appState.padSynth.params
                p[keyPath: keyPath] = newValue
                appState.padSynth.update(params: p)
            }
        )
    }

    // MARK: - Row builder

    private func sliderRow(
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        formatter: @escaping (Double) -> String
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(formatter(value.wrappedValue))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Slider(value: value, in: range)
        }
    }
}

// MARK: - Levels

/// Voice + Chops level sliders, persisted via `SampleSettingsStore`.
/// Split into its own view so the nested store is `@ObservedObject`-
/// observed (the parent only observes `AppState`).
private struct LevelsSection: View {
    @ObservedObject var settings: SampleSettingsStore

    var body: some View {
        Section("Levels") {
            row(title: "Voice", value: $settings.voiceGainLinear)
            row(title: "Chops", value: $settings.chopGainLinear)
        }
    }

    private func row(title: String, value: Binding<Double>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(String(format: "%.0f%%", value.wrappedValue * 100))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Slider(value: value, in: 0...1)
        }
    }
}

// MARK: - Beat capture training data

/// Beat Capture (D-024): the "Help improve drum detection" upload
/// opt-in, plus (when non-empty) the queued-correction count and a
/// share-sheet CSV export. Split out so the nested store is
/// `@ObservedObject`-observed.
private struct BeatTrainingSection: View {
    @ObservedObject var store: BeatTrainingStore
    @AppStorage(BeatTrainingStore.shareOptInKey) private var shareOptIn = true

    var body: some View {
        Section("Beat capture") {
            Toggle("Help improve drum detection", isOn: $shareOptIn)
                .accessibilityIdentifier("settings-beat-optin")
            Text("Sends your drum-role corrections (analysis features "
                 + "only, never audio) so detection improves over time.")
                .font(.caption)
                .foregroundStyle(.secondary)

            if !store.corrections.isEmpty {
                LabeledContent("Queued corrections") {
                    Text("\(store.corrections.count)")
                        .foregroundStyle(.secondary)
                }
                if let url = try? store.exportCSVFile() {
                    ShareLink("Export training data", item: url)
                        .accessibilityIdentifier("settings-beat-export")
                }
            }
        }
    }
}

// MARK: - MIDI controllers

/// Generic MIDI note-controller settings. Lists connected inputs and
/// exposes the pad-routing toggle (synth vs. sample pack). Split out so
/// the nested store + transport are `@ObservedObject`-observed.
private struct MIDIControllersSection: View {
    @ObservedObject var settings: SampleSettingsStore
    /// nil until `bootAudio` wires the CoreMIDI transport.
    let transport: MIDIKeyboardTransport?

    var body: some View {
        Section("MIDI controllers") {
            Toggle("Pads play samples", isOn: $settings.midiPadsToSamples)
            Text("When on, an attached MIDI pad box (LPD8/MPD) triggers "
                 + "the active sample pack. When off, pads play the synth.")
                .font(.caption)
                .foregroundStyle(.secondary)

            if let transport {
                MIDIInputsList(transport: transport)
            }
        }
    }
}

/// Live list of connected MIDI inputs. Own view so the transport is
/// `@ObservedObject`-observed (updates on hot-plug).
private struct MIDIInputsList: View {
    @ObservedObject var transport: MIDIKeyboardTransport

    var body: some View {
        if transport.connectedInputs.isEmpty {
            Text("No MIDI controllers connected")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            ForEach(transport.connectedInputs, id: \.self) { name in
                Label(name, systemImage: "pianokeys")
                    .font(.caption)
            }
        }
    }
}

// MARK: - Reverb

/// Shared-reverb sliders (D-013). Drives `AudioEngine.ReverbParams`
/// via `setReverbParams` so wet/dry/length affect the whole layer
/// bus. Split into its own view so the nested AudioEngine is
/// `@ObservedObject`-observed.
private struct ReverbSection: View {
    @ObservedObject var engine: AudioEngine

    var body: some View {
        Section("Reverb") {
            row(
                title: "Wet",
                value: bind(get: { Double($0.wetGain) }, set: { $0.wetGain = Float($1) }),
                range: 0...1,
                formatter: { String(format: "%.0f%%", $0 * 100) }
            )
            row(
                title: "Dry",
                value: bind(get: { Double($0.dryGain) }, set: { $0.dryGain = Float($1) }),
                range: 0...1,
                formatter: { String(format: "%.0f%%", $0 * 100) }
            )
            row(
                title: "Length",
                value: bind(get: { $0.seconds }, set: { $0.seconds = $1 }),
                range: 0.5...4.0,
                formatter: { String(format: "%.1f s", $0) }
            )
        }
    }

    private func bind(
        get: @escaping (AudioEngine.ReverbParams) -> Double,
        set: @escaping (inout AudioEngine.ReverbParams, Double) -> Void
    ) -> Binding<Double> {
        Binding(
            get: { get(engine.reverbParams) },
            set: { newValue in
                var p = engine.reverbParams
                set(&p, newValue)
                engine.setReverbParams(p)
            }
        )
    }

    private func row(
        title: String,
        value: Binding<Double>,
        range: ClosedRange<Double>,
        formatter: @escaping (Double) -> String
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(formatter(value.wrappedValue))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            Slider(value: value, in: range)
        }
    }
}
