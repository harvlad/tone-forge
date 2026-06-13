// swift-tools-version:5.9
//
// ToneForge Connect — macOS audio companion (prototype).
//
// Two targets:
//   ConnectCore  — reusable audio-graph + device-IO library
//   Connect      — CLI entry point that exercises ConnectCore for latency
//                  testing and live monitoring.
//
// macOS only for the prototype. The one external dependency is
// Sparkle — the de-facto standard auto-update framework for Mac
// apps distributed outside the App Store. Sparkle 2.x ships as a
// SwiftPM package and handles EdDSA-signed appcast validation,
// background download, install-on-quit, and the Check-for-Updates
// menu item. See connect/ONBOARDING_AUDIT.md §F6.

import PackageDescription

let package = Package(
    name: "Connect",
    platforms: [
        // macOS 12 (Monterey) floor — covers ~3 years more hardware
        // than the original macOS 13 minimum. See
        // connect/ONBOARDING_AUDIT.md §F1.5.
        .macOS(.v12),
    ],
    products: [
        .executable(name: "Connect", targets: ["Connect"]),
        .library(name: "ConnectCore", targets: ["ConnectCore"]),
    ],
    dependencies: [
        // Sparkle 2.x — pinned to a known-good minor so a surprise
        // breaking release in their 2.7 line can't break our update
        // pipeline. Bump deliberately when we verify a new minor.
        .package(url: "https://github.com/sparkle-project/Sparkle", from: "2.6.0"),
    ],
    targets: [
        // ObjC support target. Houses the NSException trap that
        // ConnectCore uses to catch AVAudioEngine's synchronous
        // ``NSInvalidArgumentException`` on format-mismatched
        // graph wiring. Kept as a separate target because SwiftPM
        // does not allow mixed-language targets and the bridge is
        // tiny (one .h + one .m).
        .target(
            name: "ConnectObjCBridge",
            path: "Sources/ConnectObjCBridge",
            publicHeadersPath: "include"
        ),
        .target(
            name: "ConnectCore",
            dependencies: ["ConnectObjCBridge"],
            path: "Sources/ConnectCore"
        ),
        .executableTarget(
            name: "Connect",
            dependencies: [
                "ConnectCore",
                .product(name: "Sparkle", package: "Sparkle"),
            ],
            path: "Sources/Connect"
        ),
        .testTarget(
            name: "ConnectCoreTests",
            dependencies: ["ConnectCore"],
            path: "Tests/ConnectCoreTests"
        ),
    ]
)
