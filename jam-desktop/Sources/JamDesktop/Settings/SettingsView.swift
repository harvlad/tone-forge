// SettingsView.swift
//
// App settings scene: backend base URL, bridge session id (defaults
// to the device id; overridable so two machines can share a room),
// monitor gain / amp sim, latency probe, and the dueling-audio-owner
// warning (another connect-role client on the same session id).

import SwiftUI
import JamDesktopCore

/// One-tap backend endpoint presets. `custom` is inferred for any URL
/// that isn't one of the known endpoints and never overwrites the field.
enum BackendPreset: String, CaseIterable, Identifiable {
    case hosted
    case local
    case custom

    var id: String { rawValue }

    var label: String {
        switch self {
        case .hosted: return "Hosted (jamn.app)"
        case .local: return "This Mac"
        case .custom: return "Custom"
        }
    }

    var url: String? {
        switch self {
        case .hosted: return "https://jamn.app"
        case .local: return "http://127.0.0.1:8300"
        case .custom: return nil
        }
    }

    init(url: String) {
        switch url.trimmingCharacters(in: .whitespaces).lowercased() {
        case "https://jamn.app", "https://jamn.app/": self = .hosted
        case "http://127.0.0.1:8300", "http://127.0.0.1:8300/",
             "http://localhost:8300", "http://localhost:8300/": self = .local
        default: self = .custom
        }
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var backendText = ""
    @State private var sessionIdText = ""
    @State private var adminTokenText = ""

    var body: some View {
        Form {
            Section("Backend") {
                // One-tap endpoint presets — no typing. The text field
                // stays for ad-hoc URLs (e.g. another machine on the LAN).
                Picker("Endpoint", selection: endpointPresetBinding) {
                    ForEach(BackendPreset.allCases) { preset in
                        Text(preset.label).tag(preset)
                    }
                }
                .pickerStyle(.segmented)
                TextField("Base URL", text: $backendText)
                    .onSubmit(commitBackend)
                Text("Hosted: https://jamn.app — local: http://127.0.0.1:8300")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Session bridge") {
                TextField("Session id", text: $sessionIdText)
                    .onSubmit(commitSessionId)
                HStack {
                    Button("Reset to device id") {
                        sessionIdText = AppModel.defaultBridgeSessionId
                        commitSessionId()
                    }
                    Button("Reconnect") { reconnect() }
                    Spacer()
                    ConnectStatusPill(status: session.bridge.status)
                }

                if session.foreignAudioOwnerSeen {
                    Label(
                        "Another Connect client is active on this session id — both will apply tone and gain changes. Give this app its own session id.",
                        systemImage: "exclamationmark.triangle"
                    )
                    .font(.caption)
                    .foregroundStyle(.orange)
                }
            }

            Section("Monitor") {
                Slider(value: monitorGainBinding, in: 0...1) {
                    Text("Monitor gain")
                }
                Toggle("Amp simulation", isOn: ampSimBinding)

                HStack {
                    Button("Measure latency") { session.monitor.measureLatency() }
                    Spacer()
                    if let report = session.monitor.latestLatency,
                       let roundTrip = report.measuredRoundTripMs
                        ?? report.estimatedRoundTripMs {
                        Text(String(format: "%.1f ms round trip", roundTrip))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                LabeledContent("Engine", value: session.monitor.engineStateName)
            }

            Section("Studio (admin)") {
                SecureField("Admin token", text: $adminTokenText)
                    .onSubmit(commitAdminToken)
                Text("Needed for Studio quality analysis and the Debug "
                    + "window against a hosted backend. Local backends "
                    + "work without one.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Section("Account") {
                AccountView()
            }
        }
        .formStyle(.grouped)
        .frame(width: 480)
        .onAppear {
            backendText = model.backendBaseURL.absoluteString
            sessionIdText = model.bridgeSessionId
            adminTokenText = AdminCredentials.token() ?? ""
        }
    }

    private var monitorGainBinding: Binding<Double> {
        Binding(
            get: { Double(session.monitor.monitorGain) },
            set: { session.monitor.monitorGain = Float($0) }
        )
    }

    private var ampSimBinding: Binding<Bool> {
        Binding(
            get: { session.monitor.ampSimEnabled },
            set: { session.monitor.ampSimEnabled = $0 }
        )
    }

    private var endpointPresetBinding: Binding<BackendPreset> {
        Binding(
            get: { BackendPreset(url: model.backendBaseURL.absoluteString) },
            set: { preset in
                guard let url = preset.url else { return }  // .custom: keep field
                backendText = url
                commitBackend()
            }
        )
    }

    private func commitBackend() {
        let trimmed = backendText.trimmingCharacters(in: .whitespaces)
        guard let url = URL(string: trimmed), url.scheme != nil else {
            backendText = model.backendBaseURL.absoluteString
            return
        }
        guard url != model.backendBaseURL else { return }
        model.backendBaseURL = url
        reconnect()
    }

    private func commitSessionId() {
        let trimmed = sessionIdText.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else {
            sessionIdText = model.bridgeSessionId
            return
        }
        guard trimmed != model.bridgeSessionId else { return }
        model.bridgeSessionId = trimmed
        reconnect()
    }

    private func commitAdminToken() {
        AdminCredentials.setToken(adminTokenText)
        adminTokenText = AdminCredentials.token() ?? ""
    }

    private func reconnect() {
        session.startBridge(
            sessionId: model.bridgeSessionId,
            backendBaseURL: model.backendBaseURL
        )
    }
}
