// CreditsView.swift
//
// D-024 attribution line: title, artist, license (linked) and
// source. Prefers the sidecar attribution; falls back to bundle
// meta so the credit line renders even before the sidecar loads.

import SwiftUI
import JamDesktopCore
import ToneForgeEngine

struct CreditsView: View {
    let attribution: SessionAttribution?
    let meta: BundleMeta

    var body: some View {
        HStack(spacing: 6) {
            Text(creditLine)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)

            if let license = licenseText {
                if let url = licenseURL {
                    Link(license, destination: url)
                        .font(.caption)
                } else {
                    Text(license)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            if let source = sourceURL {
                Link("Source", destination: source)
                    .font(.caption)
            }
        }
    }

    private var creditLine: String {
        if let line = attribution?.attribution, !line.isEmpty { return line }
        let title = attribution?.title ?? meta.title
        let artist = attribution?.artist ?? meta.artist
        return artist.isEmpty ? title : "\(title) — \(artist)"
    }

    private var licenseText: String? {
        let license = attribution?.license ?? meta.license
        guard let license, !license.isEmpty else { return nil }
        return license
    }

    private var licenseURL: URL? {
        let raw = attribution?.licenseUrl ?? meta.licenseUrl
        guard let raw, !raw.isEmpty else { return nil }
        return URL(string: raw)
    }

    private var sourceURL: URL? {
        let raw = attribution?.sourceUrl ?? meta.sourceUrl
        guard !raw.isEmpty else { return nil }
        return URL(string: raw)
    }
}
