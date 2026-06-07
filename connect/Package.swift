// swift-tools-version:5.9
//
// ToneForge Connect — macOS audio companion (prototype).
//
// Two targets:
//   ConnectCore  — reusable audio-graph + device-IO library
//   Connect      — CLI entry point that exercises ConnectCore for latency
//                  testing and live monitoring.
//
// macOS only for the prototype. We deliberately do not depend on any
// third-party packages so the build stays trivial: `swift run Connect`.

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
    dependencies: [],
    targets: [
        .target(
            name: "ConnectCore",
            path: "Sources/ConnectCore"
        ),
        .executableTarget(
            name: "Connect",
            dependencies: ["ConnectCore"],
            path: "Sources/Connect"
        ),
    ]
)
