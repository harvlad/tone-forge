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
        .library(name: "ToneForgeMobile", targets: ["ToneForgeMobile"]),
    ],
    targets: [
        .target(
            name: "ToneForgeEngine",
            path: "Sources/ToneForgeEngine"
        ),
        .target(
            name: "ToneForgeMobile",
            dependencies: ["ToneForgeEngine"],
            path: "Sources/ToneForgeMobile"
        ),
        .testTarget(
            name: "ToneForgeEngineTests",
            dependencies: ["ToneForgeEngine"],
            path: "Tests/ToneForgeEngineTests"
        ),
        .testTarget(
            name: "ToneForgeMobileTests",
            dependencies: ["ToneForgeMobile"],
            path: "Tests/ToneForgeMobileTests",
            resources: [.copy("Fixtures")]
        ),
    ]
)
