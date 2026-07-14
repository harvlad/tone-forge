// GoalTimerView.swift
//
// "Today's goal" strip: 15-minute soft target with a progress bar
// and elapsed label, green when complete (web _updateSessionGoal
// parity). Refreshes on a coarse timer — the web uses 30s; a 1s
// tick here keeps the minute label honest without meaningful cost.

import SwiftUI
import JamDesktopCore

struct GoalTimerView: View {
    let rehearsal: RehearsalModel

    @State private var now = Date()

    private let tick = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    var body: some View {
        let timer = rehearsal.goalTimer
        let complete = timer.isComplete(now: now)

        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Today's goal")
                    .font(.caption.weight(.semibold))
                Spacer()
                if complete {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .font(.caption)
                }
            }

            ProgressView(value: timer.progress(now: now))
                .tint(complete ? .green : .accentColor)

            Text(timer.label(now: now))
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .onReceive(tick) { now = $0 }
    }
}
