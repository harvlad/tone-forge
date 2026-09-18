// LibrarySegment.swift
//
// The Library tab's segmented control (D-022): Songs is the classic
// history/downloads list, Packs re-hosts the pack browser (shared
// with BrowsePacksSheet via PacksBrowserView), Recordings absorbs the
// saved-layer + sketch lists from the deleted ProfileView, Projects
// lists saved per-song pad workspaces (ProjectStore).

import Foundation

enum LibrarySegment: String, CaseIterable, Identifiable {
    case songs
    case packs
    case projects
    case recordings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .songs: return "Songs"
        case .packs: return "Packs"
        case .projects: return "Projects"
        case .recordings: return "Recordings"
        }
    }
}
