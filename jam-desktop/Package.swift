// swift-tools-version: 5.9
//
// jam-desktop — native SwiftUI macOS Jam app (Phase 3 desktop).
//
// Replaces the web jam UI (backend/static/jam.js) with a native app.
// The Python backend stays authoritative for analysis, history and
// bundles; this app owns local audio (in-process ConnectCore graph)
// and mirrors state over /ws/connect-bridge exactly like the web
// client does.
//
// Three-layer split (repo pattern, mirrors mobile-ios):
//   JamDesktopCore  — pure logic: view models, WS frame codec,
//                     transport state, ribbon math. No AVFoundation,
//                     no SwiftUI → headless `swift test`.
//   JamDesktopAudio — AVFoundation + CoreMIDI. Hosts ConnectCore's
//                     AudioEngine and the stem-playback subgraph.
//   JamDesktop      — SwiftUI executable.
//
// Path deps: `connect` (ConnectCore) and `mobile-ios`
// (ToneForgeEngine: HTTP clients, chord theory, section math,
// Launchpad logic, auth). Sparkle is a dep of the Connect
// *executable* only — it never links into this app.

import PackageDescription

let package = Package(
    name: "JamDesktop",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "JamDesktop", targets: ["JamDesktop"]),
        .library(name: "JamDesktopCore", targets: ["JamDesktopCore"]),
    ],
    dependencies: [
        .package(path: "../connect"),
        .package(path: "../mobile-ios"),
    ],
    targets: [
        .target(
            name: "JamDesktopCore",
            dependencies: [
                .product(name: "ToneForgeEngine", package: "mobile-ios"),
            ],
            path: "Sources/JamDesktopCore"
        ),
        .target(
            name: "JamDesktopAudio",
            dependencies: [
                "JamDesktopCore",
                .product(name: "ConnectCore", package: "connect"),
                .product(name: "ToneForgeEngine", package: "mobile-ios"),
            ],
            path: "Sources/JamDesktopAudio"
        ),
        .executableTarget(
            name: "JamDesktop",
            dependencies: [
                "JamDesktopCore",
                "JamDesktopAudio",
            ],
            path: "Sources/JamDesktop"
        ),
        .testTarget(
            name: "JamDesktopCoreTests",
            dependencies: ["JamDesktopCore"],
            path: "Tests/JamDesktopCoreTests",
            resources: [.copy("Fixtures")]
        ),
        .testTarget(
            name: "JamDesktopAudioTests",
            dependencies: ["JamDesktopAudio"],
            path: "Tests/JamDesktopAudioTests"
        ),
    ]
)
