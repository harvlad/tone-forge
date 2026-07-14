// AppModel.swift
//
// Top-level observable app state: which of the four jam views is
// showing, which backend the app talks to, and the loaded session.
//
// View inventory mirrors jam.js showView(): intake ("What are we
// playing?"), band room (analysis progress + CTAs), rehearsal
// (section practice) and perform (full transport + mixer + ribbon).

import Foundation
import Combine
import ToneForgeEngine

/// Top-level views. The first four are 1:1 with the web app's
/// showView() ids; studio ports the separate studio.html page
/// (results deep-dive on past analyses).
public enum JamView: String, CaseIterable, Sendable {
    case intake
    case bandRoom
    case rehearsal
    case perform
    case studio
}

/// Using ObservableObject (not @Observable) to avoid Swift 6
/// observation crashes with @MainActor isolation checks.
@MainActor
public final class AppModel: ObservableObject {

    /// Currently visible top-level view. Starts at intake, exactly
    /// like the web app; SessionLoader flips to perform/bandRoom.
    @Published public var view: JamView = .intake

    /// Backend base URL. Defaults to the hosted backend; settings can
    /// point it at a local uvicorn (http://127.0.0.1:8000).
    @Published public var backendBaseURL: URL

    /// The loaded session (bundle + local stems); nil until a song
    /// is picked from history/intake.
    @Published public var session: LoadedSession?

    /// Legacy sidecars for the loaded session (tone recommendation,
    /// decoded MIDI stems, attribution). Best-effort: nil while
    /// loading or when the fetch fails — never blocks playback.
    @Published public var sidecar: SessionSidecar?

    /// Loading state for the session-load flow.
    @Published public var isLoadingSession = false
    @Published public var sessionError: String?

    /// Bridge session id — the /ws/connect-bridge room key. Defaults
    /// to this device's stable id (so a co-open browser with the same
    /// id pairs up); settings can override it. Persisted.
    @Published public var bridgeSessionId: String {
        didSet {
            UserDefaults.standard.set(
                bridgeSessionId, forKey: Self.bridgeSessionIdKey)
        }
    }

    private static let bridgeSessionIdKey = "jamdesktop.bridgeSessionId"

    /// The device-derived default for `bridgeSessionId`.
    public static var defaultBridgeSessionId: String { DeviceIdentity.id() }

    public init(backendBaseURL: URL = URL(string: "https://jamn.app")!) {
        self.backendBaseURL = backendBaseURL
        self.bridgeSessionId = UserDefaults.standard
            .string(forKey: Self.bridgeSessionIdKey)
            ?? Self.defaultBridgeSessionId
    }

    /// Load a song end-to-end and flip to Perform, mirroring the web
    /// app's history-click flow. Sidecars load after the bundle and
    /// never fail the session.
    public func loadSession(
        analysisId: String,
        loader: SessionLoader = SessionLoader(),
        sidecarFetcher: SessionSidecarFetching = SessionSidecarClient()
    ) async {
        isLoadingSession = true
        sessionError = nil
        sidecar = nil
        defer { isLoadingSession = false }
        do {
            session = try await loader.load(
                analysisId: analysisId, backend: backendBaseURL
            )
            view = .perform
        } catch {
            sessionError = error.localizedDescription
            return
        }
        sidecar = try? await sidecarFetcher.fetch(
            analysisId: analysisId, backend: backendBaseURL
        )
    }
}
