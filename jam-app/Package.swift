// swift-tools-version:5.9
//
// JamApp — a minimal native macOS shell for the Jam web UI.
//
// This is intentionally tiny: one executable target that hosts the
// existing Jam frontend (served by the Python backend on
// http://localhost:8000/jam) inside a WKWebView. No Sparkle, no
// external deps, no ObjC bridge — just an entry point you can run
// with `swift run JamApp` once the backend is up.
//
// If/when this graduates beyond a prototype, fold it into the
// connect/ package alongside ConnectCore so the app shell can talk
// to the audio engine directly.

import PackageDescription

let package = Package(
    name: "JamApp",
    platforms: [
        .macOS(.v12),
    ],
    products: [
        .executable(name: "JamApp", targets: ["JamApp"]),
    ],
    targets: [
        .executableTarget(
            name: "JamApp",
            path: "Sources/JamApp"
        ),
    ]
)
