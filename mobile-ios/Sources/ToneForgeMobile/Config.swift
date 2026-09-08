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
    /// Production: Hetzner VPS behind nginx + Let's Encrypt TLS.
    public static let defaultBackendURL = URL(string: "https://jamn.app")!

    /// DMCA / copyright takedown contact surfaced in Settings → Legal.
    public static let takedownEmail = "copyright@jamn.app"

    /// Hosted Terms + Privacy (backend /legal). The App Store privacy
    /// URL points here and the in-app Legal sheets link to it, so the
    /// canonical copy lives server-side and updates without a release.
    public static let legalURL = defaultBackendURL.appendingPathComponent("legal")

    /// Request timeout for the analyze upload + SSE stream. Analyses
    /// can take minutes on long songs, so this is generous.
    public static let analyzeTimeout: TimeInterval = 15 * 60

    /// Request timeout for the Library history fetch. The remote VPS
    /// over cellular needs more headroom than the old 5s fast-fail
    /// (tuned for an unreachable LAN mDNS host), but stays short enough
    /// to surface a real outage promptly.
    public static let historyTimeout: TimeInterval = 20
}
