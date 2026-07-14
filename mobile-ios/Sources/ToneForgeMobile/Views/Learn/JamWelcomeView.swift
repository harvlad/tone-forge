// JamWelcomeView.swift
//
// The LEARN tab's song-less welcome screen: the Jam waveform logo and
// wordmark, a short "what you can do" list, and the Open Library CTA.
// Shown by LearnView whenever no song is loaded.

import SwiftUI

struct JamWelcomeView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 24)

            JamLogo()
                .frame(width: 104, height: 104)

            Text("jamn")
                .font(.system(size: 44, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
                .padding(.top, 2)

            Spacer(minLength: 28)

            VStack(alignment: .leading, spacing: 12) {
                welcomeLine(icon: "book", accent: "Learn", rest: " songs.")
                welcomeLine(
                    icon: "square.grid.3x3.fill",
                    accent: "Jam",
                    rest: " along."
                )
                welcomeLine(
                    icon: "music.note",
                    accent: "Create",
                    rest: " something new."
                )
            }
            .fixedSize(horizontal: true, vertical: false)

            Spacer(minLength: 28)

            Button {
                appState.selectedTab = .library
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "music.note.list")
                        .font(.subheadline.weight(.semibold))
                    Text("Open Library")
                        .font(.subheadline.weight(.bold))
                }
                .foregroundStyle(.black)
                .padding(.horizontal, 28)
                .padding(.vertical, 12)
                .background(TFTheme.brandGradient, in: Capsule())
            }
            .buttonStyle(.plain)

            Text("Your songs, packs, and more.")
                .font(.footnote)
                .foregroundStyle(TFTheme.textSecondary)
                .padding(.top, 10)

            Spacer(minLength: 24)

            Text(versionLine)
                .font(.footnote)
                .foregroundStyle(TFTheme.textSecondary)
                .padding(.bottom, 8)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func welcomeLine(
        icon: String, accent: String, rest: String
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.body)
                .foregroundStyle(TFTheme.brandGreenDark)
                .frame(width: 24, alignment: .center)
            (Text(accent)
                .foregroundStyle(TFTheme.brandGreenLight)
                .fontWeight(.semibold)
                + Text(rest)
                .foregroundStyle(.white))
                .font(.body)
        }
    }

    private var versionLine: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "1.0"
        let build = info?["CFBundleVersion"] as? String ?? "1"
        return "v\(short) (\(build))"
    }
}

/// The Jam mark: a rising/falling column of rounded waveform bars with
/// a play triangle on the right, painted in the brand gradient.
private struct JamLogo: View {
    /// Relative heights (0…1) of the six waveform bars, left to right.
    private let bars: [CGFloat] = [0.4, 0.72, 1.0, 0.82, 0.58, 0.42]

    var body: some View {
        GeometryReader { geo in
            let h = geo.size.height
            let barWidth = geo.size.width * 0.1
            let spacing = geo.size.width * 0.045
            HStack(alignment: .center, spacing: spacing) {
                ForEach(bars.indices, id: \.self) { i in
                    Capsule()
                        .frame(width: barWidth, height: h * bars[i])
                }
                Triangle()
                    .frame(width: barWidth * 1.4, height: barWidth * 1.6)
            }
            .frame(width: geo.size.width, height: h, alignment: .center)
            .foregroundStyle(TFTheme.brandGradient)
        }
    }
}

/// Right-pointing play triangle.
private struct Triangle: Shape {
    func path(in rect: CGRect) -> Path {
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.maxX, y: rect.midY))
        p.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        p.closeSubpath()
        return p
    }
}
