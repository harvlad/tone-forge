// PerformTabView.swift
//
// Perform tab: the lean live-performance surface. Two chromes:
//
//   Normal    — TabScaffold (song header + transport row), tab bar
//               visible. The everyday layout.
//   Immersive — `AppState.isPerforming`: the 5-tab bar slides away
//               (fast play near the bottom edge can't fat-finger a
//               tab switch), the song header disappears, the
//               transport scales up and anchors to the bottom safe
//               area, and landscape unlocks (AppDelegate gate).
//               PerformView's header shows the high-contrast Done
//               that restores the tab bar.
//
// Same controllers as Jam, so the instrument configured in Jam is
// exactly what gets performed here.

import SwiftUI

struct PerformTabView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        Group {
            if appState.isPerforming {
                immersive
            } else {
                TabScaffold {
                    performView
                }
            }
        }
        // .automatic (not .visible) so the other tabs keep whatever
        // the system would do; hiding animates the bar away with the
        // isPerforming transaction.
        .tabBarHidden(appState.isPerforming)
        .animation(.easeInOut(duration: 0.25), value: appState.isPerforming)
    }

    private var performView: some View {
        PerformView(
            coordinator: appState.modeCoordinator,
            jamSettings: appState.jamSettings,
            controller: appState.jamController,
            chordPadController: appState.chordPadController
        )
    }

    /// Full takeover: content fills the screen, transport rides the
    /// bottom safe area — nothing else near the bottom edge.
    private var immersive: some View {
        performView
            .padding(.top, 4)
            .frame(maxHeight: .infinity, alignment: .top)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                TransportRow()
                    .scaleEffect(1.1)
                    .padding(.top, 12)
                    .padding(.bottom, 6)
                    .background(TFTheme.background)
            }
            .background(TFTheme.background.ignoresSafeArea())
            .transition(.opacity)
    }
}

// `for: .tabBar` doesn't exist on macOS; the package also builds
// there (tests), so the modifier is iOS-only.
private extension View {
    @ViewBuilder
    func tabBarHidden(_ hidden: Bool) -> some View {
        #if os(iOS)
        toolbar(hidden ? .hidden : .automatic, for: .tabBar)
        #else
        self
        #endif
    }
}
