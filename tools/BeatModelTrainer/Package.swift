// swift-tools-version: 5.9
//
// BeatModelTrainer — macOS-only CreateML command-line trainer for the
// Beat Capture drum classifier. Generates a synthetic labeled corpus
// (optionally merged with server-exported real corrections), trains an
// MLBoostedTreeClassifier over OnsetFeatures.featureNames, and writes a
// .mlmodel. Never shipped in an app; runs on Mac/CI only (CreateML is
// macOS-only).

import PackageDescription

let package = Package(
    name: "BeatModelTrainer",
    platforms: [.macOS(.v14)],
    dependencies: [
        .package(name: "ToneForgeMobile", path: "../../mobile-ios"),
    ],
    targets: [
        .executableTarget(
            name: "BeatModelTrainer",
            dependencies: [
                .product(name: "ToneForgeEngine", package: "ToneForgeMobile"),
            ],
            path: "Sources/BeatModelTrainer"
        ),
        .testTarget(
            name: "BeatModelTrainerTests",
            dependencies: [
                "BeatModelTrainer",
                .product(name: "ToneForgeEngine", package: "ToneForgeMobile"),
            ],
            path: "Tests/BeatModelTrainerTests"
        ),
    ]
)
