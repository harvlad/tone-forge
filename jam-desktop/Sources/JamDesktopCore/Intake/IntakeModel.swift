// IntakeModel.swift
//
// Form state behind the Intake view: instrument picker, ownership
// attestation, engine status banner, demo CC-track catalog. The actual
// analysis flows live in AnalysisQueueModel — this type never runs a
// job.
//
// Network clients sit behind protocols so this is testable headless
// with stubs.

import Combine
import Foundation
import ToneForgeEngine

/// Instrument selector parity with jam.html: guitar live, bass/keys
/// visible but disabled ("coming soon").
public struct IntakeInstrument: Identifiable, Equatable, Sendable {
    public let id: String
    public let label: String
    public let enabled: Bool

    public static let all: [IntakeInstrument] = [
        IntakeInstrument(id: "guitar", label: "Guitar", enabled: true),
        IntakeInstrument(id: "bass", label: "Bass (coming soon)", enabled: false),
        IntakeInstrument(id: "keys", label: "Keys (coming soon)", enabled: false),
    ]
}

@MainActor
public final class IntakeModel: ObservableObject {
    // MARK: - Observable state

    @Published public var instrument: String = "guitar"
    /// Ownership-attestation checkbox (server 400s uploads without it).
    @Published public var attested = false
    @Published public private(set) var engineStatus: EngineStatus?
    @Published public private(set) var demoTracks: [CCTrack] = []
    @Published public private(set) var demoTracksError: String?

    // MARK: - Injected clients

    private let engineClient: EngineStatusFetching
    private let ccClient: CCTrackProviding

    public init(
        engineClient: EngineStatusFetching = EngineStatusClient(),
        ccClient: CCTrackProviding = BackendCCTrackClient()
    ) {
        self.engineClient = engineClient
        self.ccClient = ccClient
    }

    // MARK: - Passive data

    public func refreshEngineStatus(baseURL: URL) async {
        engineStatus = try? await engineClient.status(baseURL: baseURL)
    }

    public func loadDemoTracks(baseURL: URL) async {
        demoTracksError = nil
        do {
            demoTracks = try await ccClient.fetchCatalog(baseURL: baseURL)
        } catch {
            demoTracksError = error.localizedDescription
        }
    }
}
