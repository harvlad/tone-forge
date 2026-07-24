// TabScaffold.swift
//
// Shared chrome for the performance tabs (D-022): compact song
// header on top, tab content in the middle, transport row on the
// bottom. Deliberately NO NavigationStack and NO ScrollView — the
// tabs sign the "no scrolling" contract, and every screen must fit
// the height it's given.
//
// The gear in the header presents the Settings sheet, so each tab
// gets Settings without owning a toolbar.

import SwiftUI
import ToneForgeEngine

struct TabScaffold<Content: View, Accessory: View>: View {
    @EnvironmentObject private var appState: AppState

    var showsTransport: Bool
    var accessory: () -> Accessory
    var content: () -> Content

    init(
        showsTransport: Bool = true,
        @ViewBuilder accessory: @escaping () -> Accessory,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.showsTransport = showsTransport
        self.accessory = accessory
        self.content = content
    }

    var body: some View {
        let hasSong = appState.currentBundle != nil

        VStack(spacing: 8) {
            // No song = no Now Playing header (D-022 update: the
            // "No song loaded" placeholder is gone; Settings moved to
            // the Library tab).
            if hasSong {
                NowPlayingHeader(
                    title: appState.currentBundle?.meta.title ?? "",
                    artist: appState.currentBundle?.meta.artist,
                    durationSec: appState.currentBundle?.meta.durationSec,
                    keyLabel: appState.currentBundle?.meta.detectedKey,
                    tempoBpm: appState.currentBundle?.meta.tempoBpm,
                    analysisId: appState.currentBundle?.analysisId,
                    onEject: { appState.ejectSong() },
                    creditLine: Self.creditLine(for: appState.currentBundle?.meta),
                    creditURL: Self.creditURL(for: appState.currentBundle?.meta),
                    stemsUnavailable: appState.stemsUnavailable,
                    accessory: accessory
                )
            }

            content()

            if showsTransport {
                TransportRow()
            }
        }
        .padding(.top, 4)
        .padding(.bottom, 8)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(TFTheme.background.ignoresSafeArea())
    }

    // MARK: - Attribution (D-024)

    /// Credit line shown only for licensed songs (curated CC tracks).
    /// Verbatim attribution when the server supplied one; otherwise a
    /// synthesized "Artist — Title (LICENSE)". Nil hides the line.
    static func creditLine(for meta: BundleMeta?) -> String? {
        guard let meta, let license = meta.license, !license.isEmpty else {
            return nil
        }
        if let attribution = meta.attribution, !attribution.isEmpty {
            return attribution
        }
        let artistPart = meta.artist.isEmpty ? "" : "\(meta.artist) — "
        return "\(artistPart)\(meta.title) (\(license))"
    }

    /// Link target for the credit line: source page first, license
    /// deed as fallback. Nil renders plain text.
    static func creditURL(for meta: BundleMeta?) -> URL? {
        guard let meta, let license = meta.license, !license.isEmpty else {
            return nil
        }
        if !meta.sourceUrl.isEmpty, let url = URL(string: meta.sourceUrl) {
            return url
        }
        if let licenseUrl = meta.licenseUrl, !licenseUrl.isEmpty {
            return URL(string: licenseUrl)
        }
        return nil
    }
}

// MARK: - Convenience initializer (no accessory)

extension TabScaffold where Accessory == EmptyView {
    init(
        showsTransport: Bool = true,
        @ViewBuilder content: @escaping () -> Content
    ) {
        self.showsTransport = showsTransport
        self.accessory = { EmptyView() }
        self.content = content
    }
}
