// LoopCycleStrip.swift
//
// The visible musical clock for the Jam launchpad (UX audit fix #1).
// The sample engine phase-locks every loop to a shared cycle (the 8 s
// kit window bar-snapped to tempo — SampleScheduler.loopLengthSeconds),
// but that grid was invisible: a locked pad waiting for the boundary
// read as "broken/laggy". This strip shows:
//   - a sweep of the current cycle position,
//   - a flash on each cycle start (the shared downbeat),
//   - a countdown to the next boundary while Lock is on.
// Hidden when no song clock is running (nothing to lock to).

import SwiftUI

struct LoopCycleStrip: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        let scheduler = appState.sampleScheduler
        let length = scheduler.loopLengthSeconds
        if length > 0, appState.isPlaying {
            SwiftUI.TimelineView(.animation(minimumInterval: 1.0 / 30.0)) { _ in
                let t = appState.songSeconds
                let phase = (t.truncatingRemainder(dividingBy: length)) / length
                let remaining = length - (t.truncatingRemainder(dividingBy: length))
                HStack(spacing: 8) {
                    GeometryReader { geo in
                        ZStack(alignment: .leading) {
                            Capsule().fill(TFTheme.chipFill)
                            // Elapsed sweep.
                            Capsule()
                                .fill(TFTheme.accent.opacity(0.55))
                                .frame(width: max(4, geo.size.width * phase))
                            // Cycle-start flash: bright for the first ~8%.
                            if phase < 0.08 {
                                Capsule().fill(Color.white.opacity(0.35))
                            }
                        }
                    }
                    .frame(height: 6)
                    // Countdown to the next shared boundary (what a locked
                    // pad is waiting for).
                    if scheduler.loopLock {
                        Text(String(format: "%.1fs", remaining))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(TFTheme.textSecondary)
                            .frame(width: 38, alignment: .trailing)
                    }
                }
                .padding(.horizontal, 12)
            }
            .frame(height: 10)
            .accessibilityLabel("Loop cycle position")
        }
    }
}
