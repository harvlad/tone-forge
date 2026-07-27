// swift-tools-version: 5.9
//
// tone-forge-mobile — Swift Package manifest.
//
// Two library targets:
//   ToneForgeEngine — pure Swift port of the algorithmic slice of
//     backend/static/launchpad.js. No AVFoundation, no SwiftUI, no I/O.
//     Fully unit-testable via `swift test`.
//   ToneForgeMobile — SwiftUI views + AVAudioEngine wrapper. Depends
//     on ToneForgeEngine. Consumed by the iOS app target (created in
//     Xcode; see SETUP.md).
//
// Building for iOS from CLI:
//   swift build -Xswiftc -sdk -Xswiftc "$(xcrun --sdk iphoneos --show-sdk-path)" \
//               -Xswiftc -target -Xswiftc arm64-apple-ios17.0
//
// The iOS app target lives outside this package (created in Xcode)
// and depends on ToneForgeMobile via local package reference.

import PackageDescription

let package = Package(
    name: "ToneForgeMobile",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),  // engine target builds on macOS for CLI testing
    ],
    products: [
        .library(name: "ToneForgeEngine", targets: ["ToneForgeEngine"]),
        .library(name: "ToneForgeML", targets: ["ToneForgeML"]),
        .library(name: "ToneForgeMobile", targets: ["ToneForgeMobile"]),
    ],
    targets: [
        .target(
            name: "ToneForgeEngine",
            path: "Sources/ToneForgeEngine",
            // Raw .mlmodel is kept in git for reference/versioning but
            // never bundled — only the compiled .mlmodelc ships.
            exclude: ["Resources/BeatClassifier.mlmodel"],
            resources: [
                // Pre-compiled Beat Capture drum classifier. Ships a
                // baseline model; a fresher cached download can override
                // it at runtime (see BeatModelStore). Compiled by the
                // BeatModelTrainer tool, committed as a .mlmodelc dir.
                .copy("Resources/BeatClassifier.mlmodelc"),
            ]
        ),
        .target(
            name: "ToneForgeML",
            dependencies: ["ToneForgeEngine"],
            path: "Sources/ToneForgeML"
        ),
        .target(
            name: "ToneForgeMobile",
            dependencies: ["ToneForgeEngine", "ToneForgeML"],
            path: "Sources/ToneForgeMobile"
        ),
        .testTarget(
            name: "ToneForgeEngineTests",
            dependencies: ["ToneForgeEngine"],
            path: "Tests/ToneForgeEngineTests"
        ),
        .testTarget(
            name: "ToneForgeMLTests",
            dependencies: ["ToneForgeML"],
            path: "Tests/ToneForgeMLTests"
        ),
        .testTarget(
            name: "ToneForgeMobileTests",
            dependencies: ["ToneForgeMobile"],
            path: "Tests/ToneForgeMobileTests",
            resources: [.copy("Fixtures")]
        ),
    ]
)
