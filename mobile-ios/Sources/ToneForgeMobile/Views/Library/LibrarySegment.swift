// LibrarySegment.swift
//
// The Library tab's segmented control (D-022): Songs is the classic
// history/downloads list, Packs re-hosts the pack browser (shared
// with BrowsePacksSheet via PacksBrowserView), Recordings absorbs the
// saved-layer + sketch lists from the deleted ProfileView.

import Foundation

enum LibrarySegment: String, CaseIterable, Identifiable {
    case songs
    case packs
    case recordings

    var id: String { rawValue }

    var title: String {
        switch self {
        case .songs: return "Songs"
        case .packs: return "Packs"
        case .recordings: return "Recordings"
        }
    }
}
