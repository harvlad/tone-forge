// Config.swift
//
// Central app configuration. The backend base URL is compiled in per
// build configuration: debug builds default to the dev host's Bonjour
// address (still overridable via the DEBUG-only editors in Profile /
// Library / Settings), release builds point at production and expose
// no editor at all.

import Foundation

public enum AppConfig {

    /// Default backend base URL used when the user has never set one.
    #if DEBUG
    public static let defaultBackendURL = URL(string: "http://Matts-MacBook-Pro.local:8000")!
    #else
    // Placeholder production host — update before shipping.
    public static let defaultBackendURL = URL(string: "https://api.toneforge.app")!
    #endif

    /// DMCA / copyright takedown contact surfaced in Settings → Legal.
    public static let takedownEmail = "copyright@toneforge.app"

    /// Request timeout for the analyze upload + SSE stream. Analyses
    /// can take minutes on long songs, so this is generous.
    public static let analyzeTimeout: TimeInterval = 15 * 60
}
