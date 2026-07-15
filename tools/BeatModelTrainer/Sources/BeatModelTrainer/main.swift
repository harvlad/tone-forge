// main.swift
//
// Trains the Beat Capture drum classifier. Builds a synthetic labeled
// corpus (SyntheticCorpus), optionally merges server-exported real
// corrections (--corrections CSV), trains an MLBoostedTreeClassifier
// over OnsetFeatures.featureNames, evaluates a holdout, prints accuracy
// + confusion, and writes a .mlmodel. Exits non-zero when holdout
// accuracy is below --min-accuracy so CI never publishes a bad model.
//
// Usage:
//   swift run -c release BeatModelTrainer \
//     --out path/BeatClassifier.mlmodel \
//     [--per-role 4000] [--corrections corpus.csv] [--min-accuracy 0.83]

import Foundation
import CreateML
import ToneForgeEngine

// MARK: - Args

func arg(_ name: String) -> String? {
    guard let i = CommandLine.arguments.firstIndex(of: name),
          i + 1 < CommandLine.arguments.count else { return nil }
    return CommandLine.arguments[i + 1]
}

// MARK: - Subcommand: harvest
//
// `BeatModelTrainer harvest --harvest-manifest <csv> [--audio-root <dir>]
//  --out <csv>` runs Stage B of the E-GMD pipeline (audio -> features)
// and exits without touching CreateML. Anything else falls through to
// the training flow below, byte-compatible with the old entrypoint.

if CommandLine.arguments.count > 1, CommandLine.arguments[1] == "harvest" {
    guard let manifest = arg("--harvest-manifest"), let out = arg("--out") else {
        FileHandle.standardError.write(Data(
            "error: harvest requires --harvest-manifest <csv> --out <csv>\n".utf8))
        exit(2)
    }
    Harvester.run(manifestPath: manifest, audioRoot: arg("--audio-root"), outPath: out)
    exit(0)
}

guard let outPath = arg("--out") else {
    FileHandle.standardError.write(Data("error: --out <path.mlmodel> required\n".utf8))
    exit(2)
}
let perRole = Int(arg("--per-role") ?? "4000") ?? 4000
// Default gate at 0.83: real E-GMD electronic-drum audio trains to ~0.84
// holdout (kick/hi-hat bleed makes some slices genuinely ambiguous), well
// below the synthetic-only 1.0. 0.83 blocks regressions without rejecting an
// honest real-data model. Override with --min-accuracy.
let minAccuracy = Double(arg("--min-accuracy") ?? "0.83") ?? 0.83
let correctionsPath = arg("--corrections")

// MARK: - Build corpus

print("Generating synthetic corpus (\(perRole)/role, \(DrumRole.allCases.count) roles)...")
var examples = SyntheticCorpus.generate(perRole: perRole)

if let cp = correctionsPath {
    let real = loadCorrections(csvPath: cp)
    print("Merged \(real.count) real correction rows from \(cp)")
    examples.append(contentsOf: real)
}

// Harvested dataset rows (E-GMD, same CSV contract as corrections but
// its own bucket — high volume, so it's kept distinct from the small,
// higher-value stream of real user corrections). See harvest_egmd.py.
if let hp = arg("--harvest") {
    let harvested = loadCorrections(csvPath: hp)
    print("Merged \(harvested.count) harvested rows from \(hp)")
    examples.append(contentsOf: harvested)
}

print("Total examples: \(examples.count)")
var roleHistogram: [String: Int] = [:]
for e in examples { roleHistogram[e.role, default: 0] += 1 }
print("Per-role: " + DrumRole.allCases
    .map { "\($0.rawValue)=\(roleHistogram[$0.rawValue] ?? 0)" }
    .joined(separator: "  "))

// MARK: - MLDataTable

let names = OnsetFeatures.featureNames
var columns: [String: MLDataValueConvertible] = [:]
for (col, name) in names.enumerated() {
    columns[name] = examples.map { $0.features[col] }
}
columns["role"] = examples.map { $0.role }

let table: MLDataTable
do {
    table = try MLDataTable(dictionary: columns)
} catch {
    FileHandle.standardError.write(Data("error: MLDataTable build failed: \(error)\n".utf8))
    exit(1)
}

let (trainData, testData) = table.randomSplit(by: 0.85, seed: 42)

// MARK: - Train

print("Training MLBoostedTreeClassifier...")
let classifier: MLBoostedTreeClassifier
do {
    classifier = try MLBoostedTreeClassifier(
        trainingData: trainData, targetColumn: "role"
    )
} catch {
    FileHandle.standardError.write(Data("error: training failed: \(error)\n".utf8))
    exit(1)
}

// MARK: - Evaluate

let metrics = classifier.evaluation(on: testData)
let accuracy = 1.0 - metrics.classificationError
print(String(format: "Holdout accuracy: %.4f (error %.4f)", accuracy, metrics.classificationError))
print("Confusion matrix:")
print(metrics.confusion)

// MARK: - Write

let outURL = URL(fileURLWithPath: outPath)
try? FileManager.default.createDirectory(
    at: outURL.deletingLastPathComponent(), withIntermediateDirectories: true
)
let meta = MLModelMetadata(
    author: "ToneForge",
    shortDescription: "Beat Capture drum onset classifier over OnsetFeatures.featureNames.",
    version: ISO8601DateFormatter().string(from: Date())
)
do {
    try classifier.write(to: outURL, metadata: meta)
    print("Wrote model to \(outURL.path)")
} catch {
    FileHandle.standardError.write(Data("error: model write failed: \(error)\n".utf8))
    exit(1)
}

if accuracy < minAccuracy {
    FileHandle.standardError.write(Data(
        String(format: "error: accuracy %.4f below --min-accuracy %.4f\n", accuracy, minAccuracy).utf8
    ))
    exit(1)
}
print("OK")

// MARK: - Corrections CSV

/// Parse a corrections CSV exported by the backend. Expected header:
/// `<featureNames...>,original,corrected,timestamp`. Label = corrected.
func loadCorrections(csvPath: String) -> [LabeledExample] {
    guard let text = try? String(contentsOfFile: csvPath, encoding: .utf8) else {
        FileHandle.standardError.write(Data("warn: cannot read \(csvPath)\n".utf8))
        return []
    }
    let lines = text.split(whereSeparator: \.isNewline)
    guard lines.count > 1 else { return [] }
    let header = lines[0].split(separator: ",").map(String.init)
    let names = OnsetFeatures.featureNames
    // Map each feature name to its column index in the CSV header.
    var idx: [Int] = []
    for n in names {
        guard let c = header.firstIndex(of: n) else {
            FileHandle.standardError.write(Data("warn: CSV missing column \(n)\n".utf8))
            return []
        }
        idx.append(c)
    }
    guard let correctedCol = header.firstIndex(of: "corrected") else { return [] }
    var out: [LabeledExample] = []
    for line in lines.dropFirst() {
        let cols = line.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        guard cols.count > correctedCol else { continue }
        var vec: [Double] = []
        var ok = true
        for c in idx {
            guard c < cols.count, let v = Double(cols[c]) else { ok = false; break }
            vec.append(v)
        }
        let label = cols[correctedCol]
        guard ok, DrumRole(rawValue: label) != nil else { continue }
        out.append(LabeledExample(features: vec, role: label))
    }
    return out
}
