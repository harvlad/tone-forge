// SettingsView.swift
//
// App settings scene: backend base URL, bridge session id (defaults
// to the device id; overridable so two machines can share a room),
// monitor gain / amp sim, latency probe, and the dueling-audio-owner
// warning (another connect-role client on the same session id).

import SwiftUI
import JamDesktopCore

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var session: SessionController

    @State private var backendText = ""
    @State private var sessionIdText = ""
    @State private var adminTokenText = ""

    var body: some View {
        Form {
            Section("Backend") {
                TextField("Base URL", text: $backendText)
                    .onSubmit(commitBackend)
                Text("Hosted: https://jamn.app — local: http://127.0.0.1:8000")
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
