// DebugCorpusModel.swift
//
// Corpus tab: ground-truth vs predicted guidance modes across
// song_trial_corpus.json. Matching (slug then fuzzy title) and the
// confusion-matrix / macro-F1 math are pure static functions so they
// test headless; the model orchestrates fetches.
//
// Unlike the web's unbounded Promise.all, bundle fetches run
// sequentially — corpus is small (~10 songs) and this keeps the
// backend load polite.

import Foundation
import Observation

/// 3x3 confusion matrix over guidance modes, predicted x actual.
public struct ConfusionMatrix: Sendable, Equatable {
    public static let modes = ["chord", "riff", "lead"]

    /// counts[predicted][actual]
    public private(set) var counts: [String: [String: Int]]
    public private(set) var total: Int
    public private(set) var correct: Int

    public init() {
        var c: [String: [String: Int]] = [:]
        for p in Self.modes {
            c[p] = Dictionary(uniqueKeysWithValues: Self.modes.map { ($0, 0) })
        }
        counts = c
        total = 0
        correct = 0
    }

    /// Pairwise by section index up to min(count); non-mode labels
    /// skipped (debug.js:749-756).
    public mutating func add(predicted: [String?], actual: [String?]) {
        let n = min(predicted.count, actual.count)
        for i in 0..<n {
            guard let p = predicted[i], let a = actual[i],
                  Self.modes.contains(p), Self.modes.contains(a) else { continue }
            counts[p]?[a, default: 0] += 1
            total += 1
            if p == a { correct += 1 }
        }
    }

    public func count(predicted: String, actual: String) -> Int {
        counts[predicted]?[actual] ?? 0
    }

    public var accuracy: Double {
        total > 0 ? Double(correct) / Double(total) : 0
    }

    /// Unweighted mean of per-mode F1 (debug.js:760-770).
    public var macroF1: Double {
        var f1Sum = 0.0
        for m in Self.modes {
            let tp = Double(count(predicted: m, actual: m))
            let fp = Self.modes.filter { $0 != m }
                .reduce(0.0) { $0 + Double(count(predicted: m, actual: $1)) }
            let fn = Self.modes.filter { $0 != m }
                .reduce(0.0) { $0 + Double(count(predicted: $1, actual: m)) }
            let prec = tp + fp > 0 ? tp / (tp + fp) : 0
            let rec = tp + fn > 0 ? tp / (tp + fn) : 0
            let f1 = prec + rec > 0 ? (2 * prec * rec) / (prec + rec) : 0
            f1Sum += f1
        }
        return f1Sum / Double(Self.modes.count)
    }
}

@Observable
@MainActor
public final class DebugCorpusModel {

    public struct Row: Identifiable, Sendable {
        public let song: CorpusSong
        public let session: DebugSessionSummary?
        /// Title matched by substring rather than exact slug.
        public let fuzzy: Bool
        public let bundle: DebugBundle?

        public var id: String { song.slug ?? song.title ?? UUID().uuidString }
    }

    public private(set) var rows: [Row] = []
    public private(set) var matrix = ConfusionMatrix()
    public private(set) var isLoading = false
    public private(set) var error: String?

    private let client: DebugFetching

    public init(client: DebugFetching = DebugClient()) {
        self.client = client
    }

    public func load(baseURL: URL, sessions: [DebugSessionSummary]) async {
        isLoading = true
        error = nil
        do {
            let corpus = try await client.fetchCorpus(baseURL: baseURL)
            let known = sessions.isEmpty
                ? try await client.fetchSessions(baseURL: baseURL)
                : sessions
            var built: [Row] = []
            var m = ConfusionMatrix()
            for song in corpus.songs {
                let match = Self.matchSong(song, sessions: known)
                var bundle: DebugBundle?
                if let match, match.session.hasDebugFeatures == true {
                    // Best effort: an unmatched fetch shows as "no
                    // features" rather than failing the whole tab.
                    bundle = try? await client.fetchBundle(
                        baseURL: baseURL, id: match.session.id)
                }
                if let bundle {
                    m.add(
                        predicted: bundle.sections.map { $0.guidanceMode },
                        actual: (song.groundTruthSections ?? []).map { $0.guidanceMode })
                }
                built.append(Row(
                    song: song, session: match?.session,
                    fuzzy: match?.fuzzy ?? false, bundle: bundle))
            }
            rows = built
            matrix = m
        } catch {
            self.error = error.localizedDescription
        }
        isLoading = false
    }

    public var analyzedCount: Int { rows.filter { $0.bundle != nil }.count }

    // MARK: pure matching (debug.js slugify/matchSong)

    public static func slugify(_ s: String?) -> String {
        let lowered = (s ?? "").lowercased()
        var out = ""
        var lastWasUnderscore = true  // trims leading runs
        for ch in lowered {
            if ch.isLetter && ch.isASCII || ch.isNumber && ch.isASCII {
                out.append(ch)
                lastWasUnderscore = false
            } else if !lastWasUnderscore {
                out.append("_")
                lastWasUnderscore = true
            }
        }
        if out.hasSuffix("_") { out.removeLast() }
        return out
    }

    public static func matchSong(
        _ song: CorpusSong, sessions: [DebugSessionSummary]
    ) -> (session: DebugSessionSummary, fuzzy: Bool)? {
        let corpusSlug = slugify(song.slug ?? song.title)
        for s in sessions where slugify(s.name) == corpusSlug {
            return (s, false)
        }
        let corpusTitle = (song.title ?? "").lowercased()
        guard !corpusTitle.isEmpty else { return nil }
        for s in sessions {
            let name = s.name.lowercased()
            if name.contains(corpusTitle) || corpusTitle.contains(name) {
                return (s, true)
            }
        }
        return nil
    }
}
