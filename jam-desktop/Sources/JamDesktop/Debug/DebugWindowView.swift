// DebugWindowView.swift
//
// Dev-tool window (⌘⇧D) porting backend/static/debug.js: three tabs —
// Inspector / Corpus / History. Corpus and History fetch lazily on
// first activation, like the web page.

import SwiftUI
import JamDesktopCore

struct DebugWindowView: View {

    enum Tab: String, CaseIterable, Identifiable {
        case inspector = "Inspector"
        case corpus = "Corpus"
        case history = "History"
        var id: String { rawValue }
    }

    @EnvironmentObject private var model: AppModel

    @State private var inspector = DebugInspectorModel()
    @State private var corpus = DebugCorpusModel()
    @State private var history = DebugHistoryModel()
    @State private var tab: Tab = .inspector
    @State private var corpusLoaded = false
    @State private var historyLoaded = false

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $tab) {
                ForEach(Tab.allCases) { t in
                    Text(t.rawValue).tag(t)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(12)

            Divider().overlay(JamTheme.stroke)

            Group {
                switch tab {
                case .inspector:
                    InspectorTabView(model: inspector, baseURL: model.backendBaseURL)
                case .corpus:
                    CorpusTabView(model: corpus) { sessionId in
                        openInInspector(sessionId)
                    }
                case .history:
                    DebugHistoryTabView(model: history) { sessionId in
                        openInInspector(sessionId)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .background(JamTheme.background)
        .task {
            await inspector.loadSessions(baseURL: model.backendBaseURL)
        }
        .onChange(of: tab) { _, newTab in
            switch newTab {
            case .corpus where !corpusLoaded:
                corpusLoaded = true
                Task {
                    await corpus.load(
                        baseURL: model.backendBaseURL, sessions: inspector.sessions)
                }
            case .history where !historyLoaded:
                historyLoaded = true
                Task { await history.load(baseURL: model.backendBaseURL) }
            default:
                break
            }
        }
    }

    private func openInInspector(_ sessionId: String) {
        tab = .inspector
        Task {
            await inspector.loadBundle(baseURL: model.backendBaseURL, id: sessionId)
        }
    }
}

// MARK: - shared debug formatting

enum DebugFormat {
    /// Localized timestamp like the web's toLocaleString; raw string
    /// if it doesn't parse (backend sends naive ISO).
    static func timestamp(_ iso: String?) -> String {
        guard let iso, !iso.isEmpty else { return "" }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime]
        var date = parser.date(from: iso)
        if date == nil {
            parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            date = parser.date(from: iso)
        }
        if date == nil {
            // Naive "2026-07-01T12:00:00" — treat as local.
            let df = DateFormatter()
            df.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
            date = df.date(from: String(iso.prefix(19)))
        }
        guard let date else { return iso }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    /// debug.js fmtNum: integers as-is, else 3 decimals, nil → "—".
    static func num(_ value: Double?) -> String {
        guard let value, value.isFinite else { return "—" }
        if value == value.rounded() { return String(Int(value)) }
        return String(format: "%.3f", value)
    }

    static func num(_ value: Int?) -> String {
        guard let value else { return "—" }
        return String(value)
    }
}
