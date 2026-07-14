// RecordToggle.swift
//
// Layer-record button for the transport bar (P4). Reflects the
// engine recorder's state machine: idle → armed (first pad press
// starts the take) → recording (live event count). Click arms;
// click again stops and saves the take.
//
// The recorder is an ObservableObject (engine type) — observed here
// via @ObservedObject so @Published state/eventCount redraw the
// button without polling.

import SwiftUI
import ToneForgeEngine
import JamDesktopCore

struct RecordToggle: View {
    @EnvironmentObject private var session: SessionController
    @ObservedObject var recorder: SessionCaptureRecorder

    var body: some View {
        Button {
            switch recorder.state {
            case .idle:
                session.armRecording()
            case .armed, .recording:
                session.recording.stopRecording()
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: "record.circle")
                    .foregroundStyle(iconColor)
                if recorder.state == .recording {
                    Text("\(recorder.eventCount)")
                        .font(.caption.monospacedDigit())
                }
            }
        }
        .help(helpText)
    }

    private var iconColor: Color {
        switch recorder.state {
        case .idle: return .secondary
        case .armed: return .orange
        case .recording: return .red
        }
    }

    private var helpText: String {
        switch recorder.state {
        case .idle: return "Record a layer (arms; first pad press starts)"
        case .armed: return "Armed — first pad press starts the take"
        case .recording: return "Stop and save the take"
        }
    }
}
