// Diag.swift
//
// Unified-log taps for the pad-timing path — the Swift twin of the web
// engine's [phaselock]/[padsync] console instrumentation. The web saga
// proved these launches are undebuggable from symptoms alone; the log
// stream is the ground truth. Read with:
//   xcrun simctl spawn booted log show --predicate \
//     'subsystem == "app.jamn.padsync"' --last 5m

import Foundation
import os

enum Diag {
    private static let padsyncLog = Logger(
        subsystem: "app.jamn.padsync", category: "padsync")

    static func padsync(_ message: String) {
        padsyncLog.info("\(message, privacy: .public)")
    }
}
